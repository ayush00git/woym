"""A FAKE Switch for tests: one in-process room that real WsSwitchClient instances
connect to. Frame shapes match what the live 0.31.0 server sent in the kill test
(docs/switch-notes.md, tagged [live]). It is a fake: no addressing policy, no
Mattermost, no persistence. Use it for wiring tests, not as proof Switch behaves.

    async with FakeSwitch() as sw:
        tri = sw.client("triage")            # WsSwitchClient wired to the fake
        acme = sw.client("acme-env")
        await sw.human_post("meera", "@triage it breaks")   # returns message id
        sw.posts                              # every message, in order
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import httpx
import websockets

from woym.switch.client import WsSwitchClient

ROOM = "room-fake"


def agent_sid(name: str) -> str:
    return f"@switch-agent-{name}:localhost"


def human_sid(name: str) -> str:
    return f"@switch-mattermost-fakebridge-{name}:localhost"


class FakeSwitch:
    def __init__(self, humans: tuple[str, ...] = ("dev", "meera"), not_permitted: dict[str, set[str]] | None = None):
        self.humans = humans
        self.agents: dict[str, str] = {}  # agent_id -> name
        self.sockets: dict[str, Any] = {}  # agent_id -> ws
        self.posts: list[dict[str, Any]] = []
        self.seq = 0
        # target agent name -> sender names refused (status not_permitted, addressed False)
        self.not_permitted = not_permitted or {}
        self._srv = None
        self.port = 0

    async def __aenter__(self) -> "FakeSwitch":
        self._srv = await websockets.serve(self._ws, "127.0.0.1", 0)
        self.port = self._srv.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._srv.close()
        await self._srv.wait_closed()

    # --------------------------------------------------------------- setup

    def client(self, name: str) -> WsSwitchClient:
        aid = f"id-{name}"
        self.agents[aid] = name
        return WsSwitchClient(name, aid, "fake-key", ROOM, base_url=f"http://127.0.0.1:{self.port}",
                              transport=httpx.MockTransport(self._ops))

    async def wait_connected(self, *names: str, timeout: float = 5) -> None:
        async def until() -> None:
            while not all(f"id-{n}" in self.sockets for n in names):
                await asyncio.sleep(0.05)
        await asyncio.wait_for(until(), timeout)

    # ------------------------------------------------------------- posting

    async def human_post(self, who: str, text: str, thread_id: str | None = None) -> str:
        targets = re.findall(r"@([a-z0-9][a-z0-9._-]*)", text)
        return await self._emit(human_sid(who), who, text, thread_id, addressed_to=set(targets))

    async def _emit(self, sender: str, name: str, body: str, thread_id: str | None, addressed_to: set[str]) -> str:
        self.seq += 1
        mid = f"sw_{uuid.uuid4().hex}"
        if thread_id:  # normalised to the root, like the server
            root = next((p for p in self.posts if p["message_id"] == thread_id), None)
            thread_id = (root or {}).get("thread_id") or thread_id
        post = {"message_id": mid, "sender": sender, "sender_name": name, "body": body,
                "thread_id": thread_id, "seq": self.seq, "addressed_to": addressed_to}
        self.posts.append(post)
        for aid, ws in list(self.sockets.items()):
            agent = self.agents[aid]
            refused = name in self.not_permitted.get(agent, set())
            frame = {"event": "message", "id": self.seq, "data": {
                "type": "message", "room_id": ROOM, "bridge_id": "fakebridge", "channel_type": "channel_public",
                "sequence": self.seq, "payload": {
                    "addressed": agent in addressed_to and not refused, "sender": sender, "sender_name": name,
                    "sender_kind": None, "on_behalf_of": None, "message_id": mid, "body": body,
                    "timestamp": self.seq, "thread_id": thread_id, "attachments": []}}}
            try:
                await ws.send(json.dumps(frame))
            except websockets.ConnectionClosed:
                pass
        return mid

    # ------------------------------------------------------------ transport

    async def _ws(self, ws: Any) -> None:
        aid = ws.request.path.split("/")[2]
        self.sockets[aid] = ws
        await ws.send(json.dumps({"event": "connection_state", "data": {"rooms": [ROOM], "cursor": self.seq}}))
        try:
            async for _ in ws:  # pongs
                pass
        finally:
            self.sockets.pop(aid, None)

    async def _ops(self, req: httpx.Request) -> httpx.Response:
        aid = req.url.path.split("/")[2]
        name = self.agents[aid]
        op = req.url.path.rsplit("/", 1)[-1]
        a = json.loads(req.content or b"{}")
        if op == "post_message":
            mid = await self._emit(agent_sid(name), name, a["body"], a.get("thread_id"),
                                   set(re.findall(r"@([a-z0-9][a-z0-9._-]*)", a["body"])))
            res: Any = {"event_id": mid}
        elif op == "send_targeted_message":
            names = a.get("target_names") or []
            body = " ".join(f"@{n}" for n in names) + " " + a["body"]  # server prepends @name
            mid = await self._emit(agent_sid(name), name, body, a.get("thread_id"), set(names))
            statuses = {}
            for n in names:
                if n in self.humans:
                    continue
                if name in self.not_permitted.get(n, set()):
                    statuses[n] = "not_permitted"
                else:
                    statuses[n] = "live" if f"id-{n}" in self.sockets else "no_session"
            res = {"event_id": mid, "target_statuses": statuses}
        elif op == "list_participants":
            res = [{"id": i, "name": n, "type": "agent", "status": "live", "alias": None} for i, n in self.agents.items()]
            res += [{"id": f"u-{h}", "name": h, "type": "user", "status": None, "alias": None} for h in self.humans]
        elif op == "read_context":
            threads: dict[str, dict] = {}
            for p in self.posts:
                e = {"id": p["message_id"], "kind": "message", "sender": p["sender"], "sender_name": p["sender_name"],
                     "body": p["body"], "timestamp": p["seq"], "attachments": []}
                if p["thread_id"] and p["thread_id"] in threads:
                    threads[p["thread_id"]]["replies"].append(e)
                else:
                    threads[p["message_id"]] = {"root": e, "replies": []}
            res = {"threads": list(threads.values()), "truncated": False, "oldest_timestamp": 1}
        else:
            res = {}
        return httpx.Response(200, json={"result": res})
