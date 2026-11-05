"""@acme-env (owner: meera, the customer). The only agent that touches the customer machine.

- env_req from @triage: probe the machine, then ASK MEERA in the case thread,
  listing the exact values that would be shared. Nothing leaves before a yes.
- meera's "yes" in that thread: send the requested values to @triage.
- confirm_req from @triage: only if consent was granted for this case, run the
  repro with the override and as-is, and report both.
- verify_req from @triage: install the patched build and re-run the repro, then
  post FIXED (or still failing) to the room. By default this asks Meera first,
  because installing code was not covered by the first consent.

Work is accepted only from @triage (sender checked in code); consent only from
the customer human, in the consent thread (woym.case.consent).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from woym import config
from woym.agents.base import Agent, from_agent, run_agent, to_thread
from woym.case import card, tags
from woym.case.consent import ConsentRequest, Verdict, evaluate
from woym.contracts import ENV_KEYS, CustomerMachine, MessageEvent, Step, SwitchClient

ALLOWED_OVERRIDES = {"tz", "lang"}


@dataclass
class _Pending:
    kind: str  # "env" | "install"
    req: ConsentRequest
    sha: str = ""


@dataclass
class _CaseState:
    granted: bool = False
    pending: list[_Pending] = field(default_factory=list)


class AcmeEnvAgent(Agent):
    name = config.AGENT_CUSTOMER

    def __init__(self, client: SwitchClient, machine: CustomerMachine, customer: str = config.CUSTOMER_HUMAN,
                 customer_id: str | None = None, ask_before_install: bool = True, name: str | None = None,
                 **kw) -> None:
        # The same agent code serves every customer (acme-env for meera, nepal-env for kiran).
        self.name = name or self.name
        super().__init__(client, **kw)
        self.machine = machine
        self.customer = customer
        self.customer_id = customer_id
        self.ask_before_install = ask_before_install
        self.cases: dict[int, _CaseState] = {}

    def _case(self, case_id: int) -> _CaseState:
        return self.cases.setdefault(case_id, _CaseState())

    # ------------------------------------------------------------ from triage

    async def on_tag(self, event: MessageEvent, case_id: int, step: Step, kv: dict[str, str]) -> None:
        if not from_agent(event, config.AGENT_TRIAGE):
            return
        room, thread = event.room_id, event.thread_id
        st = self._case(case_id)

        if step == Step.ENV_REQUESTED:
            keys = [k for k in tags.as_list(kv.get("keys")) if k in ENV_KEYS] or list(ENV_KEYS)
            try:
                env = await to_thread(self.machine.probe)
            except Exception as e:
                await self._tell_triage(room, thread, case_id, Step.ENV_RECEIVED, "Could not read this machine.",
                                        error=_err(e))
                return
            values = {k: getattr(env, k) for k in keys}
            req = ConsentRequest(case_id, room, thread or event.message_id, self.customer, values, self.customer_id)
            st.pending.append(_Pending("env", req))
            await self.announce(room, thread, case_id, Step.CONSENT_PENDING,
                                card.consent_prompt(case_id, self.customer, values, config.AGENT_TRIAGE),
                                keys=list(values))

        elif step == Step.CONFIRM_REQUESTED:
            if not st.granted:
                await self.say(room, thread, f"Not running anything: @{self.customer} hasn't approved case #{case_id}.")
                return
            key, value = kv.get("key", ""), kv.get("value", "")
            if key not in ALLOWED_OVERRIDES:
                await self._tell_triage(room, thread, case_id, Step.CONFIRMED_BOTH, f"Refusing override {key!r}.",
                                        error=f"override {key} not allowed")
                return
            try:
                over = await to_thread(self.machine.run_repro, {key: value})
                real = await to_thread(self.machine.run_repro, None)
            except Exception as e:
                await self._tell_triage(room, thread, case_id, Step.CONFIRMED_BOTH,
                                        "Customer machine error during the confirm runs.", error=_err(e))
                return
            await self._tell_triage(
                room, thread, case_id, Step.CONFIRMED_BOTH,
                f"On this machine: {key}={value} → {_pf(over.passed)}, as-is → {_pf(real.passed)}.",
                key=key, value=value, override_pass=over.passed, real_pass=real.passed,
                override_actual=over.actual, real_actual=real.actual,
            )

        elif step == Step.VERIFY_REQUESTED:
            sha = kv.get("sha", "")
            if not st.granted:
                await self.say(room, thread, f"Not installing anything: @{self.customer} hasn't approved case #{case_id}.")
                return
            if self.ask_before_install:
                req = ConsentRequest(case_id, room, thread or event.message_id, self.customer, {"sha": sha},
                                     self.customer_id)
                st.pending.append(_Pending("install", req, sha))
                await self.say(room, thread, "\n".join([
                    f"#### 🔐 @{self.customer}, install the fix? · case #{case_id}",
                    f"{config.AGENT_TRIAGE} (vendor agent) asks me to install bizclock `{sha[:10]}` (a fix branch from the vendor) "
                    "on this machine and re-run the original repro once.",
                    "**Reply `yes` in this thread** to allow, or `no` to refuse.",
                ]))
            else:
                await self._verify(room, thread, case_id, sha)

    # ----------------------------------------------------------- from meera

    async def on_chat(self, event: MessageEvent) -> None:
        for case_id, st in self.cases.items():
            for p in list(st.pending):
                verdict = evaluate(p.req, event)
                if verdict == Verdict.IGNORED:
                    continue
                st.pending.remove(p)
                if p.kind == "env":
                    await self._env_answer(p.req, verdict, event)
                else:
                    await self._install_answer(p, verdict)
                return

    async def _env_answer(self, req: ConsentRequest, verdict: Verdict, event: MessageEvent) -> None:
        st = self._case(req.case_id)
        if verdict == Verdict.GRANTED:
            st.granted = True
            await self._tell_triage(req.room_id, req.thread_id, req.case_id, Step.ENV_RECEIVED,
                                    f"@{self.customer} approved. Sharing exactly the {len(req.values)} values shown above.",
                                    **req.values)
        else:
            await self._tell_triage(req.room_id, req.thread_id, req.case_id, Step.ENV_RECEIVED,
                                    f"@{self.customer} declined. Nothing was shared.", refused=True)

    async def _install_answer(self, p: _Pending, verdict: Verdict) -> None:
        if verdict == Verdict.GRANTED:
            await self._verify(p.req.room_id, p.req.thread_id, p.req.case_id, p.sha)
        else:
            await self.announce(p.req.room_id, p.req.thread_id, p.req.case_id, Step.FIXED_ON_CUSTOMER,
                                f"@{self.customer} declined the install. Nothing was installed.", refused=True)

    async def _verify(self, room: str, thread: str | None, case_id: int, sha: str) -> None:
        try:
            await to_thread(self.machine.install, sha)
            run = await to_thread(self.machine.run_repro, None)
        except Exception as e:
            await self.announce(room, thread, case_id, Step.FIXED_ON_CUSTOMER,
                                f"#### ⚠️ Couldn't verify on {self.customer}'s machine · case #{case_id}\n{_err(e)}",
                                passed=False, error=_err(e), sha=sha)
            return
        if run.passed:
            text = card.fixed(case_id, sha, run, self.customer)
        else:
            text = (f"#### ❌ Still failing on {self.customer}'s machine · case #{case_id}\n"
                    f"bizclock `{sha[:10]}` gives `{run.actual}` (expected `{run.expected}`).")
        await self.announce(room, thread, case_id, Step.FIXED_ON_CUSTOMER, text,
                            passed=run.passed, actual=run.actual, sha=sha)

    async def _tell_triage(self, room: str, thread: str | None, case_id: int, step: Step, prose: str, **kv) -> None:
        await self.send(room, thread, config.AGENT_TRIAGE, case_id, step, prose, **kv)


def _pf(passed: bool) -> str:
    return "pass ✅" if passed else "fail ❌"


def _err(e: Exception) -> str:
    return " ".join(f"{type(e).__name__}: {e}".split())[:300]


def customer_for(agent: str) -> tuple[str, str, str]:
    """(room name, customer human, container) for a customer agent, from config.CUSTOMERS."""
    for room, (human, a, container) in config.CUSTOMERS.items():
        if a == agent:
            return room, human, container
    raise SystemExit(f"{agent!r} is not a customer agent in config.CUSTOMERS")


def main() -> None:
    """`python -m woym.agents.acme_env` runs acme-env. WOYM_AGENT_NAME=nepal-env runs
    the second customer's agent (epilogue) in its own room, on its own machine."""
    from woym.sandbox.customer import DockerCustomerMachine

    ask = os.environ.get("WOYM_INSTALL_CONSENT", "1") != "0"
    name = os.environ.get("WOYM_AGENT_NAME", config.AGENT_CUSTOMER)
    if name == config.AGENT_CUSTOMER:
        run_agent(lambda c: AcmeEnvAgent(c, DockerCustomerMachine(), ask_before_install=ask), name)
        return
    room, human, container = customer_for(name)
    run_agent(lambda c: AcmeEnvAgent(c, DockerCustomerMachine(container=container), customer=human,
                                     ask_before_install=ask, name=name), name, room=room)


if __name__ == "__main__":
    main()
