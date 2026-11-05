"""Real SwitchClient (contracts.SwitchClient) over the Switch 0.31.0 agent protocol.

- Events come down one WebSocket: /agents/{id}/connection/ws. The server sends
  {"event":"ping"} every 2 s; we answer {"type":"pong","cursor":n}, and that is the
  heartbeat (6 s TTL).
- Actions go up as HTTP ops: POST /agents/{id}/ops/{op} with X-Switch-Connection-Id.

Protocol details and what is confirmed live are in docs/switch-notes.md.

The handler runs on a separate worker task, one event at a time in arrival order,
so a slow handler (a 90 s sandbox run) never delays pongs.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import uuid
from typing import Any

import httpx
import websockets

from woym import config
from woym.contracts import Handler, MessageEvent, Participant
from woym.switch import creds

log = logging.getLogger("woym.switch")


class SwitchError(RuntimeError):
    def __init__(self, op: str, status: int, body: Any):
        super().__init__(f"switch op {op} -> {status}: {body}")
        self.op, self.status, self.body = op, status, body


class Refused(RuntimeError):
    """The server refused the connection (bad key, not a room member...). Not retried."""


# Switch's own voice ("Following the conversation isn't switched on...") arrives
# from this sender with sender_kind null [live]. Matched exactly, not parsed.
SYSTEM_SENDER_PREFIX = "@switch-admin:"


def _payload_kind(payload: dict[str, Any]) -> str | None:
    if payload.get("sender_kind") == "platform" or (payload.get("sender") or "").startswith(SYSTEM_SENDER_PREFIX):
        return "system"
    return None


def to_event(data: dict[str, Any], kinds: dict[str, str]) -> MessageEvent:
    """Map a `message` frame's data to a MessageEvent. `kinds` maps participant
    NAME -> "human"|"agent" (from list_participants, whose ids are UUIDs and do
    not match the event's `sender` id [live])."""
    p = data.get("payload") or {}
    sender = p.get("sender") or ""
    kind = _payload_kind(p) or kinds.get(p.get("sender_name") or "") or "human"
    return MessageEvent(
        room_id=data.get("room_id") or "",
        message_id=p.get("message_id") or "",
        thread_id=p.get("thread_id") or None,
        sender_id=sender,
        sender_name=p.get("sender_name") or sender,
        sender_kind=kind,
        text=p.get("body") or "",
        addressed=bool(p.get("addressed")),
    )


def entry_to_event(room_id: str, thread_root: str, e: dict[str, Any], kinds: dict[str, str]) -> MessageEvent:
    """Map a read_context entry to a MessageEvent."""
    sender = e.get("sender") or ""
    kind = _payload_kind(e) or kinds.get(e.get("sender_name") or "", "human")
    mid = e.get("id") or ""
    return MessageEvent(
        room_id=room_id,
        message_id=mid,
        thread_id=None if mid == thread_root else thread_root,
        sender_id=sender,
        sender_name=e.get("sender_name") or sender,
        sender_kind=kind,
        text=e.get("body") or "",
        addressed=False,  # history carries no addressing
    )


def _norm_kind(t: str | None) -> str:
    t = (t or "").lower()
    if "agent" in t:
        return "agent"
    return "human"


class WsSwitchClient:
    """One connected agent. Construct with from_credentials(name) in agents."""

    def __init__(
        self,
        name: str,
        agent_id: str,
        api_key: str,
        room_id: str,
        base_url: str = config.SWITCH_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.name = name
        self.agent_id = agent_id
        self.room_id = room_id
        self._key = api_key
        self._base = base_url.rstrip("/")
        self.connection_id = str(uuid.uuid4())
        self.cursor: int | None = None
        self._http = httpx.AsyncClient(
            base_url=self._base,
            timeout=30,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )
        self._kinds: dict[str, str] = {}
        self._own_msgs: set[str] = set()  # event ids we posted
        # start_from=<cursor> is EXCLUSIVE on reconnect [live 2026-11-05], so replays
        # shouldn't repeat; this is a cheap guard in case a server version differs.
        self._recent: collections.deque[str] = collections.deque(maxlen=1000)
        self._connected = asyncio.Event()
        self._connected_room: str | None = None

    @classmethod
    def from_credentials(cls, name: str, **kw: Any) -> "WsSwitchClient":
        data = creds.load()
        a = data["agents"][name]
        return cls(name, a["id"], a["api_key"], data["room"]["id"], **kw)

    # ---------------------------------------------------------------- ops

    async def op(self, name: str, args: dict[str, Any] | None = None) -> Any:
        r = await self._http.post(
            f"/agents/{self.agent_id}/ops/{name}",
            json=args or {},
            headers={"X-Switch-Connection-Id": self.connection_id},
        )
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code != 200:
            raise SwitchError(name, r.status_code, body)
        return body.get("result") if isinstance(body, dict) else body

    async def list_ops(self) -> dict[str, Any]:
        r = await self._http.get(f"/agents/{self.agent_id}/ops")
        r.raise_for_status()
        return r.json().get("operations", {})

    async def _ensure_room(self, room_id: str) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=30)
        if self._connected_room != room_id:
            await self.op("connect_to_room", {"room_id": room_id, "include_general_instructions": False})
            self._connected_room = room_id

    # ------------------------------------------------------- SwitchClient

    async def post(self, room_id: str, text: str, thread_id: str | None = None) -> str:
        await self._ensure_room(room_id)
        args: dict[str, Any] = {"body": text}
        if thread_id:
            args["thread_id"] = thread_id
        res = await self.op("post_message", args)
        eid = (res or {}).get("event_id", "")
        self._own_msgs.add(eid)
        return eid

    async def send_targeted(
        self, room_id: str, targets: list[str], text: str, thread_id: str | None = None
    ) -> dict[str, str]:
        await self._ensure_room(room_id)
        args: dict[str, Any] = {"body": text, "target_names": [t.lstrip("@") for t in targets]}
        if thread_id:
            args["thread_id"] = thread_id
        res = await self.op("send_targeted_message", args) or {}
        self._own_msgs.add(res.get("event_id", ""))
        statuses = res.get("target_statuses") or {}
        if any(s != "live" for s in statuses.values()):
            log.warning("%s send_targeted %s -> %s", self.name, targets, statuses)
        return statuses

    async def participants(self, room_id: str) -> list[Participant]:
        await self._ensure_room(room_id)
        return await self._refresh_kinds()

    async def _refresh_kinds(self) -> list[Participant]:
        rows = await self.op("list_participants") or []
        out = []
        for r in rows:
            kind = _norm_kind(r.get("type"))
            self._kinds[r.get("name", "")] = kind
            out.append(Participant(id=r.get("id", ""), name=r.get("name", ""), kind=kind))
        return out

    async def thread(self, room_id: str, thread_id: str) -> list[MessageEvent]:
        await self._ensure_room(room_id)
        ctx = await self.op("read_context", {"limit": 200, "room_id": room_id}) or {}
        for t in ctx.get("threads", []):
            root = t.get("root") or {}
            ids = {root.get("id")} | {r.get("id") for r in t.get("replies", [])}
            if thread_id in ids:
                rid = root.get("id") or thread_id
                entries = [root] + list(t.get("replies", []))
                return [entry_to_event(room_id, rid, e, self._kinds) for e in entries if e.get("kind", "message") == "message"]
        return []

    async def run(self, handler: Handler) -> None:
        queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
        worker = asyncio.create_task(self._work(queue, handler))
        backoff = 1.0
        try:
            while True:
                try:
                    await self._session(queue)
                    backoff = 1.0
                except Refused:
                    raise
                except (OSError, websockets.ConnectionClosed, websockets.InvalidStatus, asyncio.TimeoutError) as e:
                    log.warning("%s connection lost (%s); reconnecting in %.0fs", self.name, e, backoff)
                self._connected.clear()
                self._connected_room = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------------------------------------------------------- internals

    def _ws_url(self) -> str:
        q = (
            f"connection_id={self.connection_id}&scope=single&filter=all"
            f"&rooms={self.room_id}&client=woym&client_version=0.1.0"
        )
        if self.cursor is not None:
            q += f"&start_from={self.cursor}"
        return self._base.replace("http", "ws", 1) + f"/agents/{self.agent_id}/connection/ws?{q}"

    async def _session(self, queue: asyncio.Queue[MessageEvent]) -> None:
        async with websockets.connect(
            self._ws_url(), additional_headers={"Authorization": f"Bearer {self._key}"}, open_timeout=15
        ) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                ev = msg.get("event")
                if ev == "ping":
                    await ws.send(json.dumps({"type": "pong", "cursor": self.cursor or 0}))
                    continue
                if isinstance(msg.get("id"), int):
                    self.cursor = max(self.cursor or 0, msg["id"])
                data = msg.get("data") or {}
                if ev == "connection_state":
                    log.info("%s connected: rooms=%s cursor=%s", self.name, data.get("rooms"), data.get("cursor"))
                    if self.cursor is None and isinstance(data.get("cursor"), int):
                        self.cursor = data["cursor"]
                    # claim the room and learn who is who, without blocking pongs
                    asyncio.create_task(self._after_open())
                elif ev == "refused":
                    raise Refused(f"{self.name}: {data}")
                elif ev == "evicted":
                    if data.get("code") == "taken_over":
                        raise Refused(f"{self.name} taken over by another process: {data}")
                    log.warning("%s evicted: %s", self.name, data)
                    return
                elif ev == "gap":
                    log.warning("%s gap: %s", self.name, data)
                elif ev == "message":
                    await self._on_message(data, queue)

    async def _after_open(self) -> None:
        try:
            await self.op("connect_to_room", {"room_id": self.room_id, "include_general_instructions": False})
            self._connected_room = self.room_id
            self._connected.set()
            await self._refresh_kinds()
        except Exception as e:  # logged, the next event will retry participants
            log.error("%s connect_to_room failed: %s", self.name, e)
            self._connected.set()

    async def _on_message(self, data: dict[str, Any], queue: asyncio.Queue[MessageEvent]) -> None:
        p = data.get("payload") or {}
        mid = p.get("message_id")
        if mid:
            if mid in self._recent:
                return
            self._recent.append(mid)
        sender = p.get("sender") or ""
        sname = p.get("sender_name") or ""
        if sname and sname not in self._kinds and _payload_kind(p) is None and self._connected.is_set():
            # bounded, so a slow server can't hold up pongs past the 6 s TTL
            try:
                await asyncio.wait_for(self._refresh_kinds(), timeout=2)
            except Exception as e:
                log.warning("%s participants refresh failed: %s", self.name, e)
        # Own messages: by posted event id, or by our agent name (agent names are
        # unique, and Mattermost reserves the bot's username, so a human can't reuse it).
        if p.get("message_id") in self._own_msgs or sname == self.name:
            if not sender.startswith("@switch-mattermost-"):
                return
        await queue.put(to_event(data, self._kinds))

    async def _work(self, queue: asyncio.Queue[MessageEvent], handler: Handler) -> None:
        while True:
            ev = await queue.get()
            try:
                await handler(ev)
            except Exception:
                log.exception("%s handler failed on %s", self.name, ev.message_id)
