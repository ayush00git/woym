"""WsSwitchClient against a FAKE local Switch (a websockets server plus an httpx
MockTransport). The frame shapes follow the 0.31.0 source (docs/switch-notes.md).
This does not prove the live server behaves the same; the kill test does that."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import websockets

from woym.switch.client import WsSwitchClient, entry_to_event, to_event

ROOM = "room-1"
ME = "@switch-agent-me:localhost"
MEERA = "@switch-user-meera:localhost"
ACME = "@switch-agent-acme:localhost"


def msg_frame(seq: int, sender: str, name: str, body: str, thread=None, addressed=False, mid=None):
    return {
        "event": "message",
        "id": seq,
        "data": {
            "type": "message", "room_id": ROOM, "bridge_id": None, "channel_type": "channel_public",
            "sequence": seq,
            "payload": {"addressed": addressed, "sender": sender, "sender_name": name,
                        "message_id": mid or f"m{seq}", "body": body, "timestamp": 1,
                        "thread_id": thread, "attachments": []},
        },
    }


def ops_transport(calls: list):
    def handle(req: httpx.Request) -> httpx.Response:
        op = req.url.path.rsplit("/", 1)[-1]
        args = json.loads(req.content or b"{}")
        calls.append((op, args, req.headers.get("x-switch-connection-id")))
        if op == "list_participants":
            res = [{"id": ME, "name": "triage", "type": "agent"},
                   {"id": MEERA, "name": "meera", "type": "user"},
                   {"id": ACME, "name": "acme-env", "type": "agent"}]
        elif op == "send_targeted_message":
            res = {"event_id": "e-t", "target_statuses": {n: "live" for n in args["target_names"]}}
        elif op == "post_message":
            res = {"event_id": "e-p"}
        elif op == "read_context":
            res = {"threads": [{"root": {"id": "r1", "kind": "message", "sender": MEERA, "sender_name": "meera", "body": "bug"},
                                "replies": [{"id": "r2", "kind": "message", "sender": ACME, "sender_name": "acme-env", "body": "ok"}]}]}
        else:
            res = {}
        return httpx.Response(200, json={"result": res})
    return httpx.MockTransport(handle)


def test_to_event_maps_fields():
    ev = to_event(msg_frame(5, MEERA, "meera", "yes", thread="r1", addressed=True)["data"], {"meera": "human"})
    assert (ev.room_id, ev.message_id, ev.thread_id, ev.sender_name, ev.sender_kind, ev.text, ev.addressed) == (
        ROOM, "m5", "r1", "meera", "human", "yes", True)


def test_switch_admin_voice_is_system():
    # live: Switch's own notices come from @switch-admin with sender_kind null
    d = msg_frame(1, "@switch-admin:localhost", "admin", "Following the conversation isn't switched on")["data"]
    d["payload"]["sender_kind"] = None
    assert to_event(d, {}).sender_kind == "system"


def test_platform_sender_is_system():
    d = msg_frame(1, "@switch-admin:localhost", "Switch", "x")["data"]
    d["payload"]["sender_kind"] = "platform"
    assert to_event(d, {}).sender_kind == "system"


def test_entry_to_event_thread_root():
    root = entry_to_event(ROOM, "r1", {"id": "r1", "sender": MEERA, "body": "a"}, {})
    reply = entry_to_event(ROOM, "r1", {"id": "r2", "sender": ACME, "sender_name": "acme-env", "body": "b"}, {"acme-env": "agent"})
    assert root.thread_id is None and reply.thread_id == "r1" and reply.sender_kind == "agent"


async def test_run_pongs_skips_self_and_maps_events():
    pongs: list[dict] = []
    seen = []
    calls: list = []
    done = asyncio.Event()

    async def server(ws):
        await ws.send(json.dumps({"event": "connection_state", "data": {"rooms": [ROOM], "cursor": 0}}))
        await asyncio.sleep(0.3)  # let the client claim the room
        await ws.send(json.dumps({"event": "ping", "data": {}}))
        await ws.send(json.dumps(msg_frame(7, ME, "triage", "my own message")))
        await ws.send(json.dumps(msg_frame(8, MEERA, "meera", "@triage it breaks", addressed=True)))
        await ws.send(json.dumps(msg_frame(9, ACME, "acme-env", "env ready", thread="m8")))
        await ws.send(json.dumps({"event": "ping", "data": {}}))
        async for raw in ws:
            pongs.append(json.loads(raw))
            if len(pongs) == 2:
                done.set()
                await asyncio.sleep(0.5)
                return

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        c = WsSwitchClient("triage", "agent-1", "k", ROOM, base_url=f"http://127.0.0.1:{port}",
                           transport=ops_transport(calls))

        async def handler(ev):
            seen.append(ev)

        task = asyncio.create_task(c.run(handler))
        await asyncio.wait_for(done.wait(), 5)
        await asyncio.sleep(0.2)
        st = await c.send_targeted(ROOM, ["@acme-env"], "go", thread_id="m8")
        th = await c.thread(ROOM, "r2")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert pongs[0]["type"] == "pong" and pongs[-1]["cursor"] == 9
    assert [e.text for e in seen] == ["@triage it breaks", "env ready"]  # own message skipped
    assert seen[0].sender_kind == "human" and seen[0].addressed
    assert seen[1].sender_kind == "agent" and seen[1].thread_id == "m8"
    assert st == {"acme-env": "live"}
    assert ("send_targeted_message", {"body": "go", "target_names": ["acme-env"], "thread_id": "m8"}, c.connection_id) in calls
    assert calls[0][0] == "connect_to_room"
    assert [e.message_id for e in th] == ["r1", "r2"] and th[1].thread_id == "r1"


async def test_fakeswitch_roundtrip():
    from tests.switch.fakeswitch import FakeSwitch

    async with FakeSwitch(not_permitted={"acme-env": {"runner"}}) as sw:
        tri, acme, run = sw.client("triage"), sw.client("acme-env"), sw.client("runner")
        seen: dict[str, list] = {"triage": [], "acme-env": []}

        async def mk(n):
            async def h(ev):
                seen[n].append(ev)
            return h

        tasks = [asyncio.create_task(tri.run(await mk("triage"))), asyncio.create_task(acme.run(await mk("acme-env"))),
                 asyncio.create_task(run.run(await mk("triage")))]
        await sw.wait_connected("triage", "acme-env", "runner")
        await asyncio.sleep(0.3)
        root = await sw.human_post("meera", "@triage it breaks")
        await asyncio.sleep(0.2)
        st = await tri.send_targeted(tri.room_id, ["acme-env"], "env please", thread_id=root)
        st2 = await run.send_targeted(run.room_id, ["acme-env"], "sneaky")
        await sw.human_post("meera", "yes", thread_id=root)
        await asyncio.sleep(0.3)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert st == {"acme-env": "live"} and st2 == {"acme-env": "not_permitted"}
    a = {e.text: e for e in seen["acme-env"]}
    assert a["@acme-env env please"].addressed and a["@acme-env env please"].thread_id == root
    assert not a["@acme-env sneaky"].addressed
    assert a["yes"].thread_id == root and a["yes"].sender_kind == "human"


async def test_duplicate_message_ids_are_dropped():
    c = WsSwitchClient("triage", "a", "k", ROOM, base_url="http://127.0.0.1:1")
    q: asyncio.Queue = asyncio.Queue()
    f = msg_frame(3, MEERA, "meera", "hi", mid="sw_dup")["data"]
    await c._on_message(f, q)
    await c._on_message(f, q)
    assert q.qsize() == 1
