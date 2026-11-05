"""FAKE Switch server for integration tests: speaks the frames and ops that
A's WsSwitchClient uses (docs/switch-notes.md §4–5), so the REAL client can be
driven end to end without the live stack.

- WS  /agents/{id}/connection/ws : connection_state, ping, message frames.
- HTTP POST /agents/{id}/ops/{op}: connect_to_room, list_participants,
  post_message, send_targeted_message, read_context (via httpx.MockTransport).

Every message is broadcast to every connected agent (filter=all), including
the sender (the client must skip its own). addressed=True only for targets.
Human messages are injected with `human()`; their sender ids look like
Switch user participants and list_participants reports them as "user".
This is a fake: it does not enforce addressing policies.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from dataclasses import dataclass, field

import httpx
import websockets


# Shapes follow live 0.31.0 (A's notes): agent senders are "@switch-agent-<8 hex>",
# humans come through the Mattermost bridge, ids are "sw_<32 hex>", and
# list_participants ids are UUIDs (the client maps kinds by name).
BRIDGE = "0b1d6e7a-0000-4000-8000-000000000001"


def agent_pid(name: str) -> str:
    return f"@switch-agent-{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex[:8]}:localhost"


def human_pid(name: str) -> str:
    return f"@switch-mattermost-{BRIDGE}-{name}:localhost"


ADMIN = "@switch-admin:localhost"


@dataclass
class Msg:
    id: str
    sender: str
    sender_name: str
    body: str
    thread_id: str | None
    targets: list[str] = field(default_factory=list)


class FakeSwitch:
    def __init__(self, room_id: str, agents: list[str], humans: list[str], ping_s: float = 2.0):
        self.room_id = room_id
        self.agents = {f"id-{n}": n for n in agents}  # agent uuid -> name
        self.humans = humans
        self.ping_s = ping_s
        self.queues: dict[str, asyncio.Queue] = {}
        self.log: list[Msg] = []
        self.ops: list[tuple[str, str, dict]] = []  # (agent name, op, args)
        self.pongs: dict[str, int] = {}
        self._seq = itertools.count(9_007_199_254_000_000)  # live sequence ids are ~9e15
        self._server = None
        self.port = 0

    # ------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> "FakeSwitch":
        self._server = await websockets.serve(self._ws, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._op)

    # ---------------------------------------------------------------- frames

    def _root(self, thread_id: str | None) -> str | None:
        if not thread_id:
            return None
        for m in self.log:
            if m.id == thread_id:
                return m.thread_id or m.id
        return thread_id

    def _emit(self, sender: str, name: str, body: str, thread_id: str | None, targets=()) -> Msg:
        seq = next(self._seq)
        m = Msg(f"sw_{uuid.uuid4().hex}", sender, name, body, self._root(thread_id), list(targets))
        self.log.append(m)
        for aid, q in self.queues.items():
            frame = {"event": "message", "id": seq, "data": {
                "type": "message", "room_id": self.room_id, "bridge_id": None, "channel_type": "channel_public",
                "sequence": seq,
                "payload": {"addressed": self.agents[aid] in m.targets, "sender": sender, "sender_name": name,
                            "sender_kind": None, "on_behalf_of": None, "opening_message": None,
                            "sender_role": None, "message_id": m.id, "body": body, "timestamp": seq,
                            "thread_id": m.thread_id, "attachments": []}}}
            q.put_nowait(frame)
        return m

    def human(self, name: str, body: str, thread_id: str | None = None) -> str:
        return self._emit(human_pid(name), name, body, thread_id).id

    def as_agent(self, name: str, body: str, thread_id: str | None = None) -> str:
        """An untagged post FROM an agent's sender that our code didn't write:
        a join greeting, or Switch's refusal auto-reply on the agent's behalf."""
        return self._emit(agent_pid(name), name, body, thread_id).id

    def admin(self, body: str, thread_id: str | None = None) -> str:
        return self._emit(ADMIN, "admin", body, thread_id).id

    # -------------------------------------------------------------------- ws

    async def _ws(self, ws) -> None:
        path = ws.request.path.split("?")[0]
        parts = path.strip("/").split("/")  # agents/{id}/connection/ws
        aid = parts[1] if len(parts) >= 4 and parts[0] == "agents" else ""
        if aid not in self.agents:
            await ws.send(json.dumps({"event": "refused", "data": {"status": 404, "detail": "unknown agent"}}))
            return
        q: asyncio.Queue = asyncio.Queue()
        self.queues[aid] = q
        await ws.send(json.dumps({"event": "connection_state", "data": {
            "agent_id": aid, "rooms": [self.room_id], "cursor": 0, "heartbeat_interval_seconds": self.ping_s}}))

        async def pump():
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=self.ping_s)
                except asyncio.TimeoutError:
                    frame = {"event": "ping", "data": {}}
                await ws.send(json.dumps(frame))

        async def read():
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "pong":
                    self.pongs[aid] = self.pongs.get(aid, 0) + 1

        tasks = [asyncio.create_task(pump()), asyncio.create_task(read())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            self.queues.pop(aid, None)

    # ------------------------------------------------------------------- ops

    async def _op(self, req: httpx.Request) -> httpx.Response:
        parts = req.url.path.strip("/").split("/")  # agents/{id}/ops/{op}
        aid, op = parts[1], parts[-1]
        name = self.agents.get(aid)
        if name is None:
            return httpx.Response(404, json={"detail": "unknown agent"})
        args = json.loads(req.content or b"{}")
        self.ops.append((name, op, args))
        if op in ("post_message", "send_targeted_message"):
            from fakes import assert_no_agent_mention
            assert_no_agent_mention(name, args["body"])
        if op == "connect_to_room":
            res = {"room_id": args.get("room_id")}
        elif op == "list_participants":
            # live: UUID ids; the message sender ids above differ, and the client maps by name
            res = [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, n)), "name": n, "type": "agent", "status": "live"}
                   for n in self.agents.values()]
            res += [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, h)), "name": h, "type": "user", "status": None}
                    for h in self.humans]
        elif op == "post_message":
            res = {"event_id": self._emit(agent_pid(name), name, args["body"], args.get("thread_id")).id}
        elif op == "send_targeted_message":
            targets = args["target_names"]
            body = " ".join(f"@{t}" for t in targets) + " " + args["body"]  # live server prepends targets
            m = self._emit(agent_pid(name), name, body, args.get("thread_id"), targets)
            res = {"event_id": m.id, "target_statuses": {t: "live" for t in targets if t in self.agents.values()}}
        elif op == "read_context":
            res = {"threads": []}
        else:
            return httpx.Response(404, json={"detail": f"unknown op {op}"})
        return httpx.Response(200, json={"result": res})
