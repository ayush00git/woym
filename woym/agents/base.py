"""Shared agent plumbing: guarded sends, tag dispatch, process entry point.

Agents never let an LLM pick recipients or steps: every hop is a fixed
(step, recipient) pair in code, sent with send_targeted and checked by the
per-agent SendGuard (each step once per case, hop cap, 2 s between sends).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from woym import config
from woym.case import tags
from woym.case.state import GuardError, SendGuard, should_ignore
from woym.contracts import MessageEvent, Step, SwitchClient

log = logging.getLogger("woym.agents")

Sleep = Callable[[float], Awaitable[None]]
IST = timezone(timedelta(hours=5, minutes=30))


class Agent:
    name: str = "agent"

    def __init__(self, client: SwitchClient, guard: SendGuard | None = None, sleep: Sleep = asyncio.sleep) -> None:
        self.client = client
        self.guard = guard or SendGuard(self.name)
        self.sleep = sleep
        # A reconnect resumes from a cursor; if that replays an event, handle it once.
        self._seen: OrderedDict[str, None] = OrderedDict()

    # ---------------------------------------------------------------- sends

    async def _pace(self) -> None:
        wait = self.guard.wait_s()
        if wait > 0:
            await self.sleep(wait)

    async def send(self, room_id: str, thread_id: str | None, to: str, case_id: int, step: Step,
                   prose: str, **kv: object) -> dict[str, str] | None:
        """Targeted, tagged hop to one agent. Returns statuses, or None if the
        loop guard refused it (logged, not raised)."""
        reason = self.guard.check(case_id, step)
        if reason:
            log.warning("%s: loop guard: %s", self.name, reason)
            return None
        await self._pace()
        try:
            self.guard.record(case_id, step)
        except GuardError as e:
            log.warning("%s: loop guard: %s", self.name, e)
            return None
        text = tags.message(prose, case_id, step, **kv)
        statuses = await self.client.send_targeted(room_id, [to], text, thread_id)
        if statuses.get(to, "live") != "live":
            log.warning("%s -> %s %s: %s", self.name, to, step.value, statuses)
        return statuses

    async def say(self, room_id: str, thread_id: str | None, text: str) -> str:
        """Untagged (or self-tagged) post to the room, e.g. a card."""
        await self._pace()
        self.guard.record_untagged()
        return await self.client.post(room_id, text, thread_id)

    async def announce(self, room_id: str, thread_id: str | None, case_id: int, step: Step,
                       text: str, **kv: object) -> str | None:
        """A tagged post to the room (no target), still once per (case, step)."""
        reason = self.guard.check(case_id, step)
        if reason:
            log.warning("%s: loop guard: %s", self.name, reason)
            return None
        await self._pace()
        self.guard.record(case_id, step)
        return await self.client.post(room_id, tags.message(text, case_id, step, **kv), thread_id)

    # ------------------------------------------------------------- dispatch

    async def handle(self, event: MessageEvent) -> None:
        if should_ignore(event, self.name):
            return
        if event.message_id:
            if event.message_id in self._seen:
                log.info("%s: skipping redelivered %s", self.name, event.message_id)
                return
            self._seen[event.message_id] = None
            if len(self._seen) > 2000:
                self._seen.popitem(last=False)
        parsed = tags.parse(event.text)
        try:
            if parsed:
                await self.on_tag(event, *parsed)
            else:
                await self.on_chat(event)
        except Exception:
            log.exception("%s failed handling %s", self.name, event.message_id)

    async def on_tag(self, event: MessageEvent, case_id: int, step: Step, kv: dict[str, str]) -> None:
        pass

    async def on_chat(self, event: MessageEvent) -> None:
        pass


def from_agent(event: MessageEvent, name: str) -> bool:
    return event.sender_kind == "agent" and event.sender_name == name


def append_issue(title: str, lines: list[str], path: Path | None = None, who: str = "agents (runtime)") -> None:
    """Runtime fallbacks are logged to ISSUES.md (append only), per §7."""
    path = path or (config.ROOT / "ISSUES.md")
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    body = "\n".join(f"- {ln}" for ln in lines)
    with open(path, "a") as f:
        f.write(f"\n## [{stamp}] {who} | {title}\n{body}\n")


async def to_thread(fn, *args):
    return await asyncio.to_thread(fn, *args)


def run_agent(make: Callable[[SwitchClient], Agent], name: str, room: str | None = None) -> None:
    """`python -m woym.agents.<name>` entry point using A's real client. `room` is a
    room NAME from credentials.json `rooms` (default: the active room)."""
    from woym.switch import creds
    from woym.switch.client import WsSwitchClient

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {name} %(levelname)s %(message)s")
    if room is None:
        client = WsSwitchClient.from_credentials(name)
    else:
        data = creds.load()
        rooms = data.get("rooms", {})
        if room not in rooms:
            raise SystemExit(f"room {room!r} not in credentials.json rooms ({sorted(rooms)}); run setup for it")
        a = data["agents"][name]
        client = WsSwitchClient(name, a["id"], a["api_key"], rooms[room]["id"])
    agent = make(client)
    log.info("%s starting (room %s)", name, getattr(client, "room_id", "?"))

    async def main() -> None:
        try:
            await client.run(agent.handle)
        finally:
            await client.aclose()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


