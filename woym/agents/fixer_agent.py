"""@fixer (owner: dev). Takes fix_req only from @triage and runs the Fixer engine.

If ClaudeFixer fails, it falls back to the stored fixture patch ONLY with an
explicit chat line saying so, plus an ISSUES.md entry (docs/contracts.md §7).
"""

from __future__ import annotations

from pathlib import Path

from woym import config
from woym.agents import wire
from woym.agents.base import Agent, append_issue, from_agent, run_agent, to_thread
from woym.contracts import Fixer, MessageEvent, Step, SwitchClient


class FixerAgent(Agent):
    name = config.AGENT_FIXER

    def __init__(self, client: SwitchClient, fixer: Fixer, fallback: Fixer | None = None,
                 issues_path: Path | None = None, **kw) -> None:
        super().__init__(client, **kw)
        self.fixer = fixer
        self.fallback = fallback
        self.issues_path = issues_path

    async def on_tag(self, event: MessageEvent, case_id: int, step: Step, kv: dict[str, str]) -> None:
        if step != Step.FIX_REQUESTED or not from_agent(event, config.AGENT_TRIAGE):
            return
        room, thread = event.room_id, event.thread_id
        culprit = kv.get("culprit", "")
        env = wire.env_from("c_", kv)
        failing = wire.run_from("f_", kv, env, "vendor-sandbox")
        mode = getattr(self.fixer, "mode", "?")
        who = "Claude Code (`claude -p`)" if mode == "claude" else "the stored FIXTURE patch (replayed, not generated)"
        await self.say(room, thread, f"🛠️ Working on case #{case_id} with {who} in `var/work/case-{case_id}`…")

        try:
            result = await to_thread(self.fixer.fix, case_id, culprit, failing)
        except Exception as e:
            why = " ".join(f"{type(e).__name__}: {e}".split())[:300]
            if self.fallback is None:
                await self.send(room, thread, config.AGENT_TRIAGE, case_id, Step.FIX_PUSHED,
                                "The fixer failed; no branch was pushed.", error=why)
                return
            await self.say(room, thread,
                           f"⚠️ **Claude fixer failed** ({why}). **Falling back to the stored FIXTURE patch** "
                           "(replayed, not generated). This is logged in ISSUES.md.")
            append_issue(f"Case {case_id}: ClaudeFixer failed at runtime, fell back to fixture", [
                "**Status:** worked-around",
                f"**What happened:** {why}",
                f"**Evidence:** {'; '.join(getattr(self.fixer, 'last_log', []) or ['no fixer log'])}",
                "**Impact:** this case's fix is the stored reference patch, labelled FIXTURE in chat.",
                "**Workaround / next step:** check claude -p on this machine; see var/logs/fixer.log.",
            ], self.issues_path)
            try:
                result = await to_thread(self.fallback.fix, case_id, culprit, failing)
            except Exception as e2:
                why2 = " ".join(f"{type(e2).__name__}: {e2}".split())[:300]
                await self.send(room, thread, config.AGENT_TRIAGE, case_id, Step.FIX_PUSHED,
                                "The fixture fallback failed too; no branch was pushed.", error=why2)
                return

        await self.send(
            room, thread, config.AGENT_TRIAGE, case_id, Step.FIX_PUSHED,
            f"Pushed {result.branch} @ {result.sha[:10]} ({result.mode}).",
            branch=result.branch, sha=result.sha, files=result.files_changed, test=result.test_name,
            mode=result.mode, summary=result.summary,
        )


def build(client: SwitchClient, mode: str | None = None) -> FixerAgent:
    from woym.fixer import FixtureFixer, make_fixer

    fixer = make_fixer(mode)
    fallback = FixtureFixer() if fixer.mode == "claude" else None
    return FixerAgent(client, fixer, fallback)


def main() -> None:
    run_agent(build, config.AGENT_FIXER)


if __name__ == "__main__":
    main()
