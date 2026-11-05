"""Kill test 2: can ONE agent process act in TWO rooms without losing its main room?

Uses a throwaway agent `kt2` (owner dev) and two throwaway rooms kt2-main / kt2-side,
never the WOYM agents or the recording room. Archives both rooms at the end.
Variants:
  V1  two connections (one per room), same agent
  V2  one connection, scope=single, rooms=main,side; connect_to_room(side) to post there
  V3  one connection, scope=all; connect_to_room per post
For each: does a human message in MAIN still reach the agent after it posted in SIDE,
and is anything evicted? Evidence -> var/killtest/two_rooms.json
"""
from __future__ import annotations

import asyncio, json, secrets, time, uuid
from pathlib import Path

import httpx, websockets

from woym.switch import creds
from woym.switch.setup import env_file, register_agent

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var" / "killtest" / "two_rooms.json"
SW, MM = "http://127.0.0.1:8000", "http://127.0.0.1:8065"
R: dict = {}


class Conn:
    def __init__(self, a, scope, rooms):
        self.a, self.cid, self.scope, self.rooms = a, str(uuid.uuid4()), scope, rooms
        self.frames: list = []
        self.http = httpx.AsyncClient(base_url=SW, timeout=30, headers={"Authorization": f"Bearer {a['api_key']}"})

    async def run(self):
        url = (f"ws://127.0.0.1:8000/agents/{self.a['id']}/connection/ws?connection_id={self.cid}"
               f"&scope={self.scope}&filter=all&rooms={','.join(self.rooms)}")
        async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {self.a['api_key']}"}) as ws:
            async for raw in ws:
                m = json.loads(raw)
                if m.get("event") == "ping":
                    await ws.send(json.dumps({"type": "pong", "cursor": 0}))
                    continue
                m["_t"] = time.time()
                self.frames.append(m)
                if m.get("event") in ("evicted", "refused"):
                    return

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def op(self, name, args=None):
        r = await self.http.post(f"/agents/{self.a['id']}/ops/{name}", json=args or {},
                                 headers={"X-Switch-Connection-Id": self.cid})
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)

    def saw(self, text, since):
        return [f for f in self.frames if f.get("event") == "message" and f["_t"] >= since
                and text in f["data"]["payload"]["body"]]

    def control(self):
        return [{k: f.get(k) for k in ("event", "data")} for f in self.frames if f.get("event") not in ("message",)]


async def main():
    if creds.live_lock_holder():
        raise SystemExit("live run in progress; refusing")
    env, d = env_file(), creds.load()
    dev = d["humans"]["dev"]
    gw = httpx.AsyncClient(base_url=f"{SW}/gateway", timeout=30)
    await gw.post("/auth/login", json={"email": dev["email"], "password": dev["switch_password"]})
    a = await register_agent(dev["reg_key"], "kt2", "WOYM kill test 2 probe (two rooms)")
    bridge = d["room"]["bridge_id"]
    rooms = {}
    for n in ("main", "side"):
        r = await gw.post("/rooms", json={"name": f"kt2-{n}-{secrets.token_hex(2)}", "description": "kill test 2",
                                          "bridge_id": bridge, "agent_names": ["kt2"], "user_names": ["dev"],
                                          "channel_type": "channel_public", "internal_only": False})
        rooms[n] = r.json()
    M, S = rooms["main"]["id"], rooms["side"]["id"]
    mm = httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30, headers={"Authorization": f"Bearer {dev['mm_token']}"})

    async def human(room, text):
        await mm.post("/posts", json={"channel_id": rooms[room]["external_channel_id"], "message": text})

    async def check(label, main_conn, side_conn, connect_side_per_post):
        res = {}
        await asyncio.sleep(2)
        if connect_side_per_post:
            res["connect_side"] = (await side_conn.op("connect_to_room", {"room_id": S, "include_general_instructions": False}))[0]
        tag = secrets.token_hex(2)
        res["post_side"] = (await side_conn.op("post_message", {"body": f"{label} agent posts in side {tag}"}))
        await asyncio.sleep(1)
        if connect_side_per_post:
            res["connect_main_back"] = (await main_conn.op("connect_to_room", {"room_id": M, "include_general_instructions": False}))[0]
        t = time.time()
        await human("main", f"{label} human in MAIN {tag}")
        await human("side", f"{label} human in SIDE {tag}")
        await asyncio.sleep(4)
        res["main_msg_received"] = bool(main_conn.saw(f"human in MAIN {tag}", t) or side_conn.saw(f"human in MAIN {tag}", t))
        res["side_msg_received"] = bool(main_conn.saw(f"human in SIDE {tag}", t) or side_conn.saw(f"human in SIDE {tag}", t))
        res["post_main"] = (await main_conn.op("post_message", {"body": f"{label} agent posts in main {tag}"}))[0]
        posts = {}
        for n in ("main", "side"):
            pp = (await mm.get(f"/channels/{rooms[n]['external_channel_id']}/posts", params={"per_page": 50})).json()
            posts[n] = [pp["posts"][i]["message"] for i in pp["order"] if tag in pp["posts"][i]["message"]]
        res["landed"] = posts
        res["control_frames"] = main_conn.control() + ([] if side_conn is main_conn else side_conn.control())
        res["evicted"] = any(f["event"] == "evicted" for f in res["control_frames"])
        R[label] = res
        print(label, json.dumps({k: v for k, v in res.items() if k != "control_frames"}, default=str))

    # V1 two connections
    c1, c2 = Conn(a, "single", [M]), Conn(a, "single", [S])
    c1.start(); c2.start()
    await check("V1", c1, c2, False)
    await c1.stop(); await c2.stop()
    # V2 one single-scope connection with both rooms
    c = Conn(a, "single", [M, S]); c.start()
    await asyncio.sleep(1)
    R["V2_state"] = next((f["data"] for f in c.frames if f.get("event") == "connection_state"), None)
    await check("V2", c, c, True)
    await c.stop()
    # V3 scope=all
    c = Conn(a, "all", [M, S]); c.start()
    await asyncio.sleep(1)
    R["V3_state"] = next((f["data"] for f in c.frames if f.get("event") == "connection_state"), None)
    await check("V3", c, c, True)
    await c.stop()

    admin = httpx.AsyncClient(base_url=f"{SW}/gateway", timeout=30)
    await admin.post("/auth/login", json={"email": env["GATEWAY_ADMIN_EMAIL"], "password": env["GATEWAY_ADMIN_PASSWORD"]})
    mma = httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30)
    mma.headers["Authorization"] = "Bearer " + (await mma.post("/users/login", json={"login_id": env["MATTERMOST_ADMIN_USER"], "password": env["MATTERMOST_ADMIN_PASSWORD"]})).headers["Token"]
    for n in rooms.values():
        await admin.post(f"/rooms/{n['id']}/archive")
        await mma.delete(f"/channels/{n['external_channel_id']}")
    OUT.write_text(json.dumps(R, indent=2, default=str))
    print("evidence ->", OUT)


asyncio.run(main())
