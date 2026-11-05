"""@runner (owner: dev). Runs repros in the vendor sandbox; takes work only from @triage.

- sandbox_run: run the vendor env, reply with the result.
- bisect: bisect vendor -> customer env, post the bisect card, reply with culprits.
- matrix_green: run the env matrix at the fix ref, post the matrix card, reply green or not.

A SandboxError (docker failure, timeout, a repro that printed no result line)
is replied as `error=...`, NEVER as a failing run: a crash must not look like
the bug.
"""

from __future__ import annotations

from woym import config
from woym.agents import wire
from woym.agents.base import Agent, from_agent, run_agent, to_thread
from woym.case import card
from woym.contracts import Env, MessageEvent, Sandbox, Step, SwitchClient


def matrix_envs(vendor: Env, customer: Env) -> list[Env]:
    """Vendor CI, vendor with the customer's zone, the customer env, plus a
    second non-whole-hour zone (Nepal +05:45). Duplicates removed."""
    envs = [vendor, vendor.with_value("tz", customer.tz), customer, customer.with_value("tz", "Asia/Kathmandu")]
    out: list[Env] = []
    for e in envs:
        if e not in out:
            out.append(e)
    return out


class RunnerAgent(Agent):
    name = config.AGENT_RUNNER

    def __init__(self, client: SwitchClient, sandbox: Sandbox, **kw) -> None:
        super().__init__(client, **kw)
        self.sandbox = sandbox

    async def on_tag(self, event: MessageEvent, case_id: int, step: Step, kv: dict[str, str]) -> None:
        if not from_agent(event, config.AGENT_TRIAGE):
            return
        room, thread = event.room_id, event.thread_id
        reply = lambda st, prose, **extra: self.send(room, thread, config.AGENT_TRIAGE, case_id, st, prose, **extra)  # noqa: E731

        if step == Step.SANDBOX_RUN:
            env = wire.env_from("v_", kv)
            try:
                r = await to_thread(self.sandbox.run, env)
            except Exception as e:  # SandboxError and anything unexpected
                await reply(step, "Sandbox error: the run did not complete.", error=_err(e))
                return
            verdict = "passed" if r.passed else "FAILED"
            await reply(step, f"Repro {verdict} in the vendor sandbox: {r.actual}.", **wire.run_kv("", r, dur=True))

        elif step == Step.BISECTING:
            vendor, customer = wire.env_from("v_", kv), wire.env_from("c_", kv)
            try:
                rep = await to_thread(self.sandbox.bisect, vendor, customer)
            except Exception as e:
                await reply(step, "Sandbox error during bisect.", error=_err(e))
                return
            await self.say(room, thread, card.bisect(case_id, rep))
            results = {s.key: s.result.passed for s in rep.steps}
            culprit_run = next((s.result for s in rep.steps if rep.culprits and s.key == rep.culprits[0]), rep.target)
            prose = (f"Bisect done: culprit {', '.join(rep.culprits)}." if rep.culprits
                     else "Bisect done: no culprit isolated.")
            await reply(
                step, prose,
                culprits=rep.culprits, results=wire.results_kv(results),
                baseline=rep.baseline.passed, target=rep.target.passed,
                **wire.run_kv("f_", culprit_run),
            )

        elif step == Step.MATRIX_GREEN:
            ref = kv.get("ref", "")
            envs = matrix_envs(wire.env_from("v_", kv), wire.env_from("c_", kv))
            try:
                results = await to_thread(self.sandbox.matrix, envs, ref)
            except Exception as e:
                await reply(step, "Sandbox error during the matrix run.", error=_err(e))
                return
            await self.say(room, thread, card.matrix(case_id, ref, results))
            ok = sum(r.passed for r in results)
            green = bool(results) and ok == len(results)
            await reply(step, f"Matrix at {ref[:10]}: {ok}/{len(results)} green.",
                        green=green, ok=ok, n=len(results))


def _err(e: Exception) -> str:
    return " ".join(f"{type(e).__name__}: {e}".split())[:300]


def main() -> None:
    from woym.sandbox.runner import DockerSandbox

    run_agent(lambda c: RunnerAgent(c, DockerSandbox()), config.AGENT_RUNNER)


if __name__ == "__main__":
    main()
