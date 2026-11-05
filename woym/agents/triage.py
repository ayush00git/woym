"""@triage (owner: dev). Runs the case state machine of docs/contracts.md §3.

Humans command only @triage, and @triage checks the sender in code:
- a report (top-level message mentioning @triage) from the customer or vendor human opens a case;
- "@triage patch it" counts only from the vendor human, after CONFIRMED BOTH.

Tagged replies are accepted only from the agent that owns that step
(runner / acme-env / fixer), in the case thread. Every hop is recorded on the
case (loop guard: once per agent and step, hop cap).
"""

from __future__ import annotations

import re

from woym import config
from woym.agents import wire
from woym.agents.base import Agent, from_agent
from woym.case import card, state, tags
from woym.case.state import CaseStore, GuardError
from woym.contracts import (ENV_KEYS, BisectReport, BisectStep, Case, Env, FixResult, MessageEvent, RunResult, Step,
                            SwitchClient)

PATCH_RE = re.compile(r"\b(patch|fix)\b", re.I)
MENTION = "@" + config.AGENT_TRIAGE

# which agent may send triage each step
STEP_SENDER = {
    Step.SANDBOX_RUN: config.AGENT_RUNNER,
    Step.BISECTING: config.AGENT_RUNNER,
    Step.MATRIX_GREEN: config.AGENT_RUNNER,
    Step.CONSENT_PENDING: config.AGENT_CUSTOMER,
    Step.ENV_RECEIVED: config.AGENT_CUSTOMER,
    Step.CONFIRMED_BOTH: config.AGENT_CUSTOMER,
    Step.FIXED_ON_CUSTOMER: config.AGENT_CUSTOMER,
    Step.FIX_PUSHED: config.AGENT_FIXER,
}


class TriageAgent(Agent):
    name = config.AGENT_TRIAGE

    def __init__(self, client: SwitchClient, store: CaseStore | None = None, vendor_env: Env = config.VENDOR_ENV,
                 vendor_human: str = config.VENDOR_HUMAN, customer_human: str = config.CUSTOMER_HUMAN,
                 human_ids: dict[str, str] | None = None, **kw) -> None:
        super().__init__(client, **kw)
        self.store = store or CaseStore()
        self.vendor_env = vendor_env
        self.vendor_human = vendor_human
        self.customer_human = customer_human
        self.human_ids = human_ids or {}  # handle -> sender id, checked when known

    # ------------------------------------------------------------- humans

    def _is_human(self, event: MessageEvent, handle: str) -> bool:
        if event.sender_kind != "human":
            return False
        if handle in self.human_ids:
            return event.sender_id == self.human_ids[handle]
        return event.sender_name == handle

    async def on_chat(self, event: MessageEvent) -> None:
        if event.sender_kind != "human":
            return
        mentioned = event.addressed or MENTION in event.text.lower()
        thread_case = self.store.by_thread(event.room_id, event.thread_id) if event.thread_id else None
        patch_in_thread = thread_case is not None and PATCH_RE.search(event.text) is not None
        if not (mentioned or patch_in_thread):
            return
        is_vendor = self._is_human(event, self.vendor_human)
        is_customer = self._is_human(event, self.customer_human)

        if not (is_vendor or is_customer):
            await self.say(event.room_id, event.thread_id or event.message_id,
                           f"I only take commands from @{self.vendor_human} and @{self.customer_human}. Ignoring.")
            return

        if PATCH_RE.search(event.text) and (thread_case or event.thread_id is None):
            case = thread_case or self._latest_patchable()
            if case is None:
                return
            if not is_vendor:
                await self.say(case.room_id, case.thread_id, f"Only @{self.vendor_human} (vendor) can ask for a patch.")
                return
            await self._request_fix(case)
            return

        if event.thread_id is None:
            await self._open_case(event)

    def _latest_patchable(self) -> Case | None:
        cases = [c for c in self.store.all() if c.step in (Step.CONFIRMED_BOTH, Step.REPRODUCED)]
        return max(cases, key=lambda c: c.id) if cases else None

    async def _open_case(self, event: MessageEvent) -> None:
        case = self.store.open_case(event.room_id, event.message_id, event.sender_name)
        case.vendor_env = self.vendor_env
        self._advance(case, Step.SANDBOX_RUN)
        await self._hop_out(case, Step.SANDBOX_RUN, config.AGENT_RUNNER,
                            f"Case #{case.id} opened. Running the repro in our CI env first.",
                            **wire.env_kv("v_", self.vendor_env))

    # --------------------------------------------------------- agent replies

    async def on_tag(self, event: MessageEvent, case_id: int, step: Step, kv: dict[str, str]) -> None:
        owner = STEP_SENDER.get(step)
        if owner is None or not from_agent(event, owner):
            return
        case = self.store.get(case_id)
        if case is None or case.room_id != event.room_id or case.thread_id != event.thread_id:
            return
        if case.step in state.TERMINAL:
            return
        try:
            state.record_hop(case, event.sender_name, step)
        except GuardError as e:
            self.store.save(case)
            if "hop limit" in str(e):
                await self._fail(case, f"Loop guard: {e}")
            return  # duplicate: already handled
        self.store.save(case)

        if "error" in kv:
            await self._fail(case, f"{event.sender_name} reported an error at `{step.value}`: {kv['error']}")
            return
        handler = {
            Step.SANDBOX_RUN: self._on_sandbox_run,
            Step.CONSENT_PENDING: self._on_consent,
            Step.ENV_RECEIVED: self._on_env,
            Step.BISECTING: self._on_bisect,
            Step.CONFIRMED_BOTH: self._on_confirmed,
            Step.FIX_PUSHED: self._on_fix,
            Step.MATRIX_GREEN: self._on_matrix,
            Step.FIXED_ON_CUSTOMER: self._on_fixed,
        }[step]
        await handler(case, kv)

    async def _on_sandbox_run(self, case: Case, kv: dict[str, str]) -> None:
        run = wire.run_from("", kv, case.vendor_env or self.vendor_env, "vendor-sandbox")
        if run.passed:
            self._advance(case, Step.NOT_REPRODUCED)
            await self.say(case.room_id, case.thread_id, card.not_reproduced(case.id, run))
            self._advance(case, Step.ENV_REQUESTED)
            await self._hop_out(case, Step.ENV_REQUESTED, config.AGENT_CUSTOMER,
                                f"Please share these environment facts for case #{case.id}, with your owner's consent.",
                                keys=list(ENV_KEYS))
        else:
            self._advance(case, Step.REPRODUCED)
            await self.say(case.room_id, case.thread_id,
                           card.reproduced(case.id, run) + f"\n_@{self.vendor_human}: reply **patch it** in this thread to fix._")

    async def _on_consent(self, case: Case, kv: dict[str, str]) -> None:
        if state.can_advance(case.step, Step.CONSENT_PENDING):
            self._advance(case, Step.CONSENT_PENDING)

    async def _on_env(self, case: Case, kv: dict[str, str]) -> None:
        if kv.get("refused") == "1":
            await self._fail(case, f"@{self.customer_human} declined to share the environment. Nothing was shared.")
            return
        vendor = case.vendor_env or self.vendor_env
        customer = Env(**{k: kv.get(k, getattr(vendor, k)) for k in ENV_KEYS})
        case.customer_env = customer
        self._advance(case, Step.ENV_RECEIVED)
        await self.say(case.room_id, case.thread_id, card.lineup(case.id, vendor, customer))
        self._advance(case, Step.BISECTING)
        await self._hop_out(case, Step.BISECTING, config.AGENT_RUNNER,
                            "Bisect vendor → customer env, one value at a time.",
                            **wire.env_kv("v_", vendor), **wire.env_kv("c_", customer))

    async def _on_bisect(self, case: Case, kv: dict[str, str]) -> None:
        vendor, customer = case.vendor_env, case.customer_env
        culprits = tags.as_list(kv.get("culprits"))
        results = wire.results_from(kv.get("results"))
        failing_env = vendor
        for k in (culprits[0].split("+") if culprits else []):
            failing_env = failing_env.with_value(k, getattr(customer, k))
        failing = wire.run_from("f_", kv, failing_env, "vendor-sandbox")
        case.bisect = _report(vendor, customer, kv, results, culprits, failing)
        if not culprits:
            await self._fail(case, "Bisect isolated no culprit (see the card above).")
            return
        self._advance(case, Step.CULPRIT_FOUND)
        key = culprits[0]
        if "+" in key or key not in ("tz", "lang"):
            self.store.save(case)
            await self.say(case.room_id, case.thread_id,
                           f"Culprit `{key}` can't be flipped on the customer machine; stopping before confirm.")
            return
        self._advance(case, Step.CONFIRM_REQUESTED)
        await self._hop_out(case, Step.CONFIRM_REQUESTED, config.AGENT_CUSTOMER,
                            f"Please run the repro on your machine with {key}={getattr(vendor, key)}, then as-is.",
                            key=key, value=getattr(vendor, key))

    async def _on_confirmed(self, case: Case, kv: dict[str, str]) -> None:
        key, value = kv.get("key", ""), kv.get("value", "")
        customer = case.customer_env
        real = RunResult(customer, tags.as_bool(kv.get("real_pass", "0")), _expected(case), kv.get("real_actual", ""),
                         0, 0.0, "", "customer-machine")
        over = RunResult(customer.with_value(key, value), tags.as_bool(kv.get("override_pass", "0")), _expected(case),
                         kv.get("override_actual", ""), 0, 0.0, "", "customer-machine")
        vendor_fail = _culprit_run(case)
        text = card.confirmed_both(case.id, key, vendor_fail, over, real, value)
        if over.passed and not real.passed and not vendor_fail.passed:
            self._advance(case, Step.CONFIRMED_BOTH)
            await self.say(case.room_id, case.thread_id,
                           text + f"\n\n_@{self.vendor_human}: reply **patch it** in this thread to have fixer write the fix._")
        else:
            await self.say(case.room_id, case.thread_id, text)
            await self._fail(case, "The customer-machine runs did not confirm the sandbox bisect.")

    async def _request_fix(self, case: Case) -> None:
        if case.step not in (Step.CONFIRMED_BOTH, Step.REPRODUCED):
            await self.say(case.room_id, case.thread_id,
                           f"Case #{case.id} is at `{case.step.value}`; I can patch after CONFIRMED BOTH.")
            return
        failing = _culprit_run(case)
        culprit = (case.bisect.culprits[0] if case.bisect and case.bisect.culprits else "tz")
        self._advance(case, Step.FIX_REQUESTED)
        await self._hop_out(case, Step.FIX_REQUESTED, config.AGENT_FIXER,
                            f"Write a failing test and a fix for case #{case.id} (culprit {culprit}).",
                            culprit=culprit, value=getattr(failing.env, culprit.split("+")[0]), **wire.run_kv("f_", failing))

    async def _on_fix(self, case: Case, kv: dict[str, str]) -> None:
        fix = FixResult(branch=kv.get("branch", ""), sha=kv.get("sha", ""), files_changed=tags.as_list(kv.get("files")),
                        test_name=kv.get("test", ""), mode=kv.get("mode", "?"), summary=kv.get("summary", ""))
        case.fix = fix
        self._advance(case, Step.FIX_PUSHED)
        await self.say(case.room_id, case.thread_id, card.fix(case.id, fix))
        await self._hop_out(case, Step.MATRIX_GREEN, config.AGENT_RUNNER,
                            f"Run the env matrix at {fix.sha[:10]}.", ref=fix.sha,
                            **wire.env_kv("v_", case.vendor_env), **wire.env_kv("c_", case.customer_env))

    async def _on_matrix(self, case: Case, kv: dict[str, str]) -> None:
        if kv.get("green") != "1":
            await self._fail(case, f"Matrix not green ({kv.get('ok', '?')}/{kv.get('n', '?')}); not asking the customer to install.")
            return
        self._advance(case, Step.MATRIX_GREEN)
        self._advance(case, Step.VERIFY_REQUESTED)
        await self._hop_out(case, Step.VERIFY_REQUESTED, config.AGENT_CUSTOMER,
                            f"Please install bizclock at {case.fix.sha[:10]} and re-run the original repro.",
                            sha=case.fix.sha)

    async def _on_fixed(self, case: Case, kv: dict[str, str]) -> None:
        if kv.get("passed") == "1":
            self._advance(case, Step.FIXED_ON_CUSTOMER)
        else:
            await self._fail(case, "The customer's machine did not confirm the fix.")

    # ------------------------------------------------------------- helpers

    def _advance(self, case: Case, step: Step) -> None:
        state.advance(case, step)
        self.store.save(case)

    async def _hop_out(self, case: Case, step: Step, to: str, prose: str, **kv: object) -> None:
        try:
            state.record_hop(case, self.name, step)
        except GuardError as e:
            self.store.save(case)
            await self._fail(case, f"Loop guard: {e}")
            return
        self.store.save(case)
        statuses = await self.send(case.room_id, case.thread_id, to, case.id, step, prose, **kv)
        if statuses is not None and statuses.get(to, "live") != "live":
            await self.say(case.room_id, case.thread_id,
                           f"⚠️ {to} is `{statuses.get(to)}` for `{step.value}`; the case is waiting on it.")

    async def _fail(self, case: Case, why: str) -> None:
        if state.can_advance(case.step, Step.FAILED):
            state.advance(case, Step.FAILED)
        self.store.save(case)
        await self.say(case.room_id, case.thread_id, card.failed(case.id, why))


def _expected(case: Case) -> str:
    return case.bisect.target.expected if case.bisect else ""


def _culprit_run(case: Case) -> RunResult:
    rep = case.bisect
    for s in rep.steps:
        if rep.culprits and s.key == rep.culprits[0]:
            return s.result
    return rep.target


def _report(vendor: Env, customer: Env, kv: dict[str, str], results: dict[str, bool], culprits: list[str],
            failing: RunResult) -> BisectReport:
    def mk(env: Env, passed: bool) -> RunResult:
        return RunResult(env, passed, failing.expected, "", 0, 0.0, "", "vendor-sandbox")

    steps = []
    for key, passed in results.items():
        flipped = vendor
        for k in key.split("+"):
            flipped = flipped.with_value(k, getattr(customer, k))
        res = failing if culprits and key == culprits[0] else mk(flipped, passed)
        steps.append(BisectStep(key, "+".join(getattr(vendor, k) for k in key.split("+")),
                                "+".join(getattr(customer, k) for k in key.split("+")), res))
    return BisectReport(mk(vendor, kv.get("baseline") == "1"), mk(customer, kv.get("target") == "1"), steps, culprits)


def main() -> None:
    from woym.agents.base import run_agent
    from woym.switch import creds

    ids: dict[str, str] = {}
    try:
        for handle, h in creds.load().get("humans", {}).items():
            if h.get("switch_participant_id"):
                ids[handle] = h["switch_participant_id"]
    except FileNotFoundError:
        pass
    run_agent(lambda c: TriageAgent(c, human_ids=ids), config.AGENT_TRIAGE)


if __name__ == "__main__":
    main()
