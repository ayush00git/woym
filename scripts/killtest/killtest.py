"""WOYM kill test (PLAN.md A2): can agents owned by two different Switch users
work together in one Mattermost room, with Switch enforcing who may address whom?

Runs against the local stack from infra/switch (Switch 0.31.0 + Mattermost + Gateway).
Every step records PASS / FAIL / INFO with raw evidence in var/killtest/:
  results.json   step -> {status, detail, evidence}
  events.jsonl   every WebSocket frame each probe agent received
  state.json     ids, keys and tokens created (secrets; gitignored under var/)

Usage:  uv run python scripts/killtest/killtest.py
It is safe to re-run: users/keys/agents are reused where they already exist.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "infra" / "switch" / ".env"
OUT = ROOT / "var" / "killtest"
SWITCH = "http://127.0.0.1:8000"
MM = "http://127.0.0.1:8065"

RESULTS: dict[str, dict[str, Any]] = {}
STATE: dict[str, Any] = {}


def load_env() -> dict[str, str]:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def record(step: str, status: str, detail: str, evidence: Any = None) -> None:
    RESULTS[step] = {"status": status, "detail": detail, "evidence": evidence}
    print(f"[{status:4}] {step}: {detail}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2, default=str))
    (OUT / "state.json").write_text(json.dumps(STATE, indent=2, default=str))


def short(r: httpx.Response) -> dict[str, Any]:
    try:
        body: Any = r.json()
    except Exception:
        body = r.text[:500]
    return {"status": r.status_code, "url": str(r.request.url), "body": body}


# --------------------------------------------------------------------------
# Gateway (cookie auth) and Mattermost (token auth) helpers
# --------------------------------------------------------------------------


async def gw_login(email: str, password: str) -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=f"{SWITCH}/gateway", timeout=30)
    r = await c.post("/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"gateway login {email}: {short(r)}")
    return c


async def mm_admin(env: dict[str, str]) -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30)
    r = await c.post(
        "/users/login",
        json={"login_id": env["MATTERMOST_ADMIN_USER"], "password": env["MATTERMOST_ADMIN_PASSWORD"]},
    )
    if r.status_code != 200:
        raise RuntimeError(f"mattermost admin login: {short(r)}")
    c.headers["Authorization"] = f"Bearer {r.headers['Token']}"
    return c


# --------------------------------------------------------------------------
# Probe agent: register, WebSocket connection, ops
# --------------------------------------------------------------------------


class Probe:
    def __init__(self, name: str, agent_id: str, api_key: str):
        self.name, self.id, self.key = name, agent_id, api_key
        self.conn_id = str(uuid.uuid4())
        self.cursor = 0
        self.frames: list[dict[str, Any]] = []
        self.pings = 0
        self.evicted: dict[str, Any] | None = None
        self.state_frame: dict[str, Any] | None = None
        self.task: asyncio.Task | None = None
        self.http = httpx.AsyncClient(
            base_url=SWITCH, timeout=30, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def op(self, name: str, args: dict[str, Any] | None = None) -> httpx.Response:
        return await self.http.post(
            f"/agents/{self.id}/ops/{name}",
            json=args or {},
            headers={"X-Switch-Connection-Id": self.conn_id},
        )

    async def run_ws(self, rooms: list[str]) -> None:
        q = f"connection_id={self.conn_id}&scope=single&filter=all&client=woym-killtest&client_version=0.1.0"
        if rooms:
            q += "&rooms=" + ",".join(rooms)
        url = SWITCH.replace("http", "ws", 1) + f"/agents/{self.id}/connection/ws?{q}"
        log = (OUT / "events.jsonl").open("a")
        async with websockets.connect(
            url, additional_headers={"Authorization": f"Bearer {self.key}"}
        ) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                ev = msg.get("event")
                if ev == "ping":
                    self.pings += 1
                    await ws.send(json.dumps({"type": "pong", "cursor": self.cursor}))
                    continue
                if msg.get("id") is not None:
                    try:
                        self.cursor = max(self.cursor, int(msg["id"]))
                    except (TypeError, ValueError):
                        pass
                msg["_t"] = time.time()
                msg["_agent"] = self.name
                self.frames.append(msg)
                log.write(json.dumps(msg, default=str) + "\n")
                log.flush()
                if ev == "connection_state":
                    self.state_frame = msg.get("data")
                if ev in ("evicted", "refused"):
                    self.evicted = msg
                    return

    def start(self, rooms: list[str]) -> None:
        self.task = asyncio.create_task(self.run_ws(rooms))

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def messages(self, since: float) -> list[dict[str, Any]]:
        return [f for f in self.frames if f.get("event") == "message" and f["_t"] >= since]

    async def wait_message(self, since: float, contains: str, timeout: float = 15) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            for f in self.messages(since):
                body = (f.get("data") or {}).get("payload", {}).get("body", "")
                if contains in body:
                    return f
            await asyncio.sleep(0.3)
        return None


async def register(env: dict[str, str], reg_key: str, name: str) -> tuple[str, str]:
    async with httpx.AsyncClient(base_url=SWITCH, timeout=30) as c:
        body = {
            "name": name,
            "description": f"WOYM kill-test probe {name}",
            "connector_type": "custom",
            "integration_profile": {
                "connection_model": "always_on",
                "message_exchange": True,
                "pre_invocation_mediation": [],
                "post_invocation_mediation": [],
                "event_reporting": [],
            },
            "overwrite": True,
        }
        r = await c.post("/agents", json=body, headers={"Authorization": f"Bearer {reg_key}"})
        if r.status_code != 200:
            raise RuntimeError(f"register {name}: {short(r)}")
        d = r.json()
        return d["id"], d["api_key"]


# --------------------------------------------------------------------------
# The test
# --------------------------------------------------------------------------


async def main() -> int:
    env = load_env()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "events.jsonl").write_text("")
    tag = time.strftime("%H%M%S")

    # S0 health
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SWITCH}/health")
        record("S0.health", "PASS" if r.status_code == 200 else "FAIL", f"GET /health -> {r.status_code}", short(r))
        v = await c.get(f"{SWITCH}/version")
        record("S0.version", "INFO", f"GET /version -> {v.status_code}", short(v))

    # S1 Gateway users + registration keys (A2.1)
    admin = await gw_login(env["GATEWAY_ADMIN_EMAIL"], env["GATEWAY_ADMIN_PASSWORD"])
    me = await admin.get("/auth/me")
    STATE["admin"] = me.json()
    users: dict[str, dict[str, str]] = {}
    for handle in ("dev", "meera"):
        email, pw = f"{handle}@woym.local", f"woym-{handle}-pass-1"
        r = await admin.post("/users", json={"name": handle, "email": email, "password": pw, "role": "user"})
        if r.status_code not in (200, 409):
            record(f"S1.create_user.{handle}", "FAIL", "POST /gateway/users", short(r))
            return 1
        record(f"S1.create_user.{handle}", "PASS", f"POST /gateway/users -> {r.status_code}", short(r))
        cl = await gw_login(email, pw)
        who = (await cl.get("/auth/me")).json()
        k = await cl.post("/api-keys", json={"label": f"woym-{handle}-{tag}"})
        ok = k.status_code == 200 and "key" in k.json()
        record(f"S1.reg_key.{handle}", "PASS" if ok else "FAIL", f"POST /gateway/api-keys as {handle} -> {k.status_code}",
               {**short(k), "body": "<redacted>"} if ok else short(k))
        if not ok:
            return 1
        users[handle] = {"email": email, "password": pw, "id": who.get("id"), "reg_key": k.json()["key"]}
        STATE.setdefault("gw_clients", {})
    STATE["users"] = users
    gw = {h: await gw_login(u["email"], u["password"]) for h, u in users.items()}

    # S2 register one probe per owner (A2.2)
    probes: dict[str, Probe] = {}
    for handle in ("dev", "meera"):
        name = f"kt-{handle}"
        aid, key = await register(env, users[handle]["reg_key"], name)
        probes[handle] = Probe(name, aid, key)
        detail = (await admin.get(f"/agents/{aid}")).json()
        owner = detail.get("owner_id") or detail.get("owner", {})
        record(f"S2.register.{name}", "PASS", f"POST /agents with {handle}'s registration key -> id {aid}",
               {"owner_field": owner, "expected_owner": users[handle]["id"], "addressing_policy": detail.get("addressing_policy")})
    STATE["agents"] = {h: {"name": p.name, "id": p.id, "api_key": p.key} for h, p in probes.items()}
    dev, mee = probes["dev"], probes["meera"]

    # S3 Mattermost users + PATs (A2.7)
    mm = await mm_admin(env)
    pr = await mm.put("/config/patch", json={"ServiceSettings": {"EnableUserAccessTokens": True}})
    record("S3.mm_enable_pat", "PASS" if pr.status_code == 200 else "FAIL", f"PUT /config/patch -> {pr.status_code}")
    team = (await mm.get(f"/teams/name/{env['MATTERMOST_TEAM_NAME']}")).json()
    mm_users: dict[str, dict[str, str]] = {}
    for handle in ("dev", "meera"):
        pw = f"Woym-{handle}-Pass-1!"
        r = await mm.post("/users", json={"username": handle, "email": f"{handle}@woym.local", "password": pw})
        if r.status_code == 201:
            uid = r.json()["id"]
        else:
            uid = (await mm.get(f"/users/username/{handle}")).json()["id"]
        await mm.post(f"/teams/{team['id']}/members", json={"team_id": team["id"], "user_id": uid})
        await mm.put(f"/users/{uid}/roles", json={"roles": "system_user system_user_access_token"})
        t = await mm.post(f"/users/{uid}/tokens", json={"description": f"woym humans.py {tag}"})
        ok = t.status_code == 200
        mm_users[handle] = {"id": uid, "password": pw, "token": t.json().get("token") if ok else None}
        record(f"S3.mm_user.{handle}", "PASS" if ok else "FAIL", f"user {uid}, PAT -> {t.status_code}", None if ok else short(t))
    STATE["mm_users"] = mm_users
    STATE["mm_team"] = team["id"]

    # S4 room with both owners' agents (A2.4)
    bridges = (await gw["dev"].get("/collaborations")).json()
    STATE["bridges"] = bridges
    mmb = [b for b in bridges if b.get("bridge_type") == "mattermost"]
    if not mmb:
        record("S4.bridge", "FAIL", "no mattermost bridge listed by /gateway/collaborations", bridges)
        return 1
    bridge_id = mmb[0].get("id") or mmb[0].get("bridge_id")
    record("S4.bridge", "PASS", f"mattermost bridge {bridge_id}")
    room_name = f"kt-{tag}"
    r = await gw["dev"].post("/rooms", json={
        "name": room_name, "description": "WOYM kill test", "bridge_id": bridge_id,
        "agent_names": [dev.name], "user_names": ["dev", "meera"],
        "channel_type": "channel_public", "internal_only": False,
    })
    if r.status_code not in (200, 201):
        record("S4.create_room", "FAIL", "POST /gateway/rooms as dev", short(r))
        return 1
    room = r.json()
    room_id = room["id"]
    STATE["room"] = {"id": room_id, "name": room_name, "external_channel_id": room.get("external_channel_id")}
    record("S4.create_room", "PASS", f"room {room_id}, mm channel {room.get('external_channel_id')}")

    # Cross-owner join, in order of preference
    dev.start([])
    await asyncio.sleep(2)
    inv = await dev.op("invite_agent_to_room", {"room_id": room_id, "agent_name": mee.name})
    record("S4.invite_op_cross_owner_default_policy", "PASS" if inv.status_code == 200 else "INFO",
           f"kt-dev invite_agent_to_room(kt-meera) under kt-meera's default policy -> {inv.status_code}", short(inv))
    in_room = inv.status_code == 200
    if not in_room:
        g = await gw["meera"].post(f"/rooms/{room_id}/agents", json={"agent_ids": [mee.id]})
        record("S4.gateway_add_by_meera", "PASS" if g.status_code == 200 else "INFO",
               f"meera POST /gateway/rooms/{{id}}/agents -> {g.status_code}", short(g))
        in_room = g.status_code == 200
    if not in_room:
        g = await gw["dev"].post(f"/rooms/{room_id}/agents", json={"agent_ids": [mee.id]})
        record("S4.gateway_add_by_dev", "PASS" if g.status_code == 200 else "INFO",
               f"dev (room creator) POST /gateway/rooms/{{id}}/agents -> {g.status_code}", short(g))
        in_room = g.status_code == 200
    if not in_room:
        g = await admin.post(f"/rooms/{room_id}/agents", json={"agent_ids": [mee.id]})
        record("S4.gateway_add_by_admin", "PASS" if g.status_code == 200 else "FAIL",
               f"admin POST /gateway/rooms/{{id}}/agents -> {g.status_code}", short(g))
        in_room = g.status_code == 200
    if not in_room:
        return 1

    # S5 connect both, survive pings, ops list (A2.2)
    await dev.stop()
    dev = probes["dev"] = Probe(dev.name, dev.id, dev.key)
    t0 = time.time()
    dev.start([room_id])
    mee.start([room_id])
    for p in (dev, mee):
        c = await p.op("connect_to_room", {"room_id": room_id, "include_general_instructions": False})
        record(f"S5.connect_to_room.{p.name}", "PASS" if c.status_code == 200 else "FAIL", f"-> {c.status_code}",
               {k: v for k, v in (c.json().get("result") or {}).items() if k in ("participants", "warning", "room_id", "name")} if c.status_code == 200 else short(c))
    await asyncio.sleep(14)
    for p in (dev, mee):
        alive = p.evicted is None and p.task is not None and not p.task.done()
        record(f"S5.survive_pings.{p.name}", "PASS" if alive and p.pings >= 5 else "FAIL",
               f"{p.pings} pings answered in {time.time()-t0:.0f}s, evicted={p.evicted}",
               {"connection_state": p.state_frame})
    ops = await dev.http.get(f"/agents/{dev.id}/ops")
    opnames = sorted((ops.json().get("operations") or {}).keys()) if ops.status_code == 200 else []
    (OUT / "ops.json").write_text(json.dumps(ops.json(), indent=2))
    record("S5.ops_list", "PASS" if opnames else "FAIL", f"{len(opnames)} ops", opnames)

    parts = await dev.op("list_participants")
    record("S5.list_participants", "INFO", f"-> {parts.status_code}", short(parts))

    # S9 message edit? (A2.6)
    edit_like = [o for o in opnames if any(w in o for w in ("edit", "update_message", "redact", "delete_message"))]
    record("S9.edit_own_message", "INFO", f"edit-like ops: {edit_like or 'none'}", edit_like)

    # S6 cross-owner targeted send (A2.3)
    s = time.time()
    body1 = f"kt cross-owner #1 {secrets.token_hex(3)}"
    r1 = await dev.op("send_targeted_message", {"body": body1, "target_names": [mee.name]})
    st1 = (r1.json().get("result") or {}).get("target_statuses") if r1.status_code == 200 else None
    got1 = await mee.wait_message(s, body1, 8)
    record("S6.targeted_default_policy", "PASS" if st1 and st1.get(mee.name) == "not_permitted" else "INFO",
           f"kt-dev -> kt-meera under default policy: statuses={st1}, kt-meera saw it={bool(got1)} addressed={(got1 or {}).get('data',{}).get('payload',{}).get('addressed')}",
           {"send": short(r1), "event": got1})

    # Meera sets a custom rule: her own agents and herself, plus kt-dev by id.
    policy = {"policy": {"rules": [
        {"owner": True, "owner_agents": True, "users": [], "agents": []},
        {"users": [], "agents": [dev.id]},
    ]}}
    pp = await gw["meera"].put(f"/agents/{mee.id}/addressing-policy", json=policy)
    record("S6.meera_sets_custom_rule", "PASS" if pp.status_code == 200 else "FAIL",
           f"meera PUT /gateway/agents/{{kt-meera}}/addressing-policy -> {pp.status_code}",
           (pp.json() or {}).get("addressing_policy") if pp.status_code == 200 else short(pp))
    pd = await gw["dev"].put(f"/agents/{mee.id}/addressing-policy", json={"policy": None})
    record("S6.dev_cannot_change_meeras_policy", "PASS" if pd.status_code == 403 else "FAIL",
           f"dev PUT addressing-policy on kt-meera -> {pd.status_code}", short(pd))

    s = time.time()
    body2 = f"kt cross-owner #2 {secrets.token_hex(3)}"
    r2 = await dev.op("send_targeted_message", {"body": body2, "target_names": [mee.name]})
    st2 = (r2.json().get("result") or {}).get("target_statuses") if r2.status_code == 200 else None
    got2 = await mee.wait_message(s, body2, 8)
    addressed2 = (got2 or {}).get("data", {}).get("payload", {}).get("addressed")
    record("S6.targeted_after_rule", "PASS" if st2 and st2.get(mee.name) == "live" and addressed2 else "FAIL",
           f"statuses={st2}, kt-meera addressed={addressed2}", {"send": short(r2), "event": got2})

    # Reverse direction: kt-meera -> kt-dev (kt-dev keeps the default policy)
    s = time.time()
    body3 = f"kt reverse {secrets.token_hex(3)}"
    r3 = await mee.op("send_targeted_message", {"body": body3, "target_names": [dev.name]})
    st3 = (r3.json().get("result") or {}).get("target_statuses") if r3.status_code == 200 else None
    got3 = await dev.wait_message(s, body3, 8)
    record("S6.reverse_default_policy", "INFO", f"kt-meera -> kt-dev statuses={st3}, addressed={(got3 or {}).get('data',{}).get('payload',{}).get('addressed')}",
           {"send": short(r3), "event": got3})

    # Can an agent change its own addressing? (identity claim)
    sa = await mee.op("update_agent_detail", {"agent_id": mee.id, "addressing": "anyone"})
    record("S6.agent_changes_own_addressing", "INFO", f"kt-meera update_agent_detail(self, addressing=anyone) -> {sa.status_code}", short(sa))
    # restore the custom rule in case that succeeded
    await gw["meera"].put(f"/agents/{mee.id}/addressing-policy", json=policy)

    # post_message containing @name addresses nobody?
    s = time.time()
    body4 = f"@{mee.name} kt plain post {secrets.token_hex(3)}"
    r4 = await dev.op("post_message", {"body": body4})
    got4 = await mee.wait_message(s, body4.split(" ", 1)[1], 8)
    record("S6.post_with_at_name", "INFO", f"post_message('@kt-meera ...') -> addressed={(got4 or {}).get('data',{}).get('payload',{}).get('addressed')}",
           {"send": short(r4), "event": got4})

    # S7 human messages from Mattermost (A2.5)
    chan = STATE["room"]["external_channel_id"]
    mmu = {h: httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30, headers={"Authorization": f"Bearer {mm_users[h]['token']}"}) for h in mm_users}

    async def human_post(h: str, msg: str, root: str | None = None) -> dict:
        r = await mmu[h].post("/posts", json={"channel_id": chan, "message": msg, **({"root_id": root} if root else {})})
        return r.json()

    s = time.time()
    root = await human_post("meera", f"@{mee.name} kt human root {secrets.token_hex(2)}")
    STATE["mm_root_post"] = root.get("id")
    gm = await mee.wait_message(s, "kt human root", 10)
    gd = await dev.wait_message(s, "kt human root", 10)
    record("S7.human_root_mention", "INFO",
           f"meera '@kt-meera' root: kt-meera addressed={(gm or {}).get('data',{}).get('payload',{}).get('addressed')}, kt-dev saw={bool(gd)}",
           {"mm_post": {k: root.get(k) for k in ("id", "root_id", "user_id")}, "kt-meera": gm, "kt-dev": gd})

    s = time.time()
    await human_post("meera", "kt thread reply yes", root.get("id"))
    gm = await mee.wait_message(s, "kt thread reply yes", 10)
    p = (gm or {}).get("data", {}).get("payload", {})
    record("S7.human_thread_reply_followups_default", "INFO",
           f"thread reply: kt-meera saw={bool(gm)} addressed={p.get('addressed')} thread_id={p.get('thread_id')} sender={p.get('sender')} sender_name={p.get('sender_name')}",
           gm)

    s = time.time()
    await human_post("dev", "!follow-ups off")
    await asyncio.sleep(3)
    sysmsgs = [f.get("data", {}).get("payload", {}).get("body") for f in dev.frames if f["_t"] >= s]
    record("S7.followups_off_command", "INFO", "dev posted '!follow-ups off'", {"frames_after": sysmsgs, "cmd_frames": [f for f in dev.frames if f["_t"] >= s and f.get("event") == "command"]})

    s = time.time()
    await human_post("meera", "kt thread reply two", root.get("id"))
    gm = await mee.wait_message(s, "kt thread reply two", 10)
    p = (gm or {}).get("data", {}).get("payload", {})
    record("S7.human_thread_reply_followups_off", "INFO",
           f"after follow-ups off: kt-meera saw={bool(gm)} addressed={p.get('addressed')} thread_id={p.get('thread_id')}", gm)

    # Agent reply into the human thread, using the event's thread_id
    tid = p.get("thread_id") or p.get("message_id")
    if tid:
        rr = await mee.op("post_message", {"body": "kt agent reply in thread", "thread_id": tid})
        posts = (await mmu["meera"].get(f"/posts/{root.get('id')}/thread")).json()
        in_thread = any("kt agent reply in thread" in v.get("message", "") for v in (posts.get("posts") or {}).values())
        record("S7.agent_reply_lands_in_mm_thread", "PASS" if in_thread else "FAIL",
               f"post_message(thread_id={tid}) -> {rr.status_code}; visible in MM thread={in_thread}", short(rr))

    ctx = await mee.op("read_context", {"limit": 20})
    (OUT / "read_context.json").write_text(json.dumps(ctx.json(), indent=2))
    record("S7.read_context", "PASS" if ctx.status_code == 200 else "FAIL", f"-> {ctx.status_code}, saved read_context.json")

    # Identity linking via API (Meera claims her Mattermost account)
    mm_meera_id = mm_users["meera"]["id"]
    li = await gw["meera"].post(f"/collaborations/{bridge_id}/identities",
                                json={"external_user_id": mm_meera_id, "username": "meera", "user_id": None})
    record("S8.link_mm_identity_via_api", "PASS" if li.status_code in (200, 201) else "INFO",
           f"meera POST /gateway/collaborations/{{bridge}}/identities -> {li.status_code}", short(li))
    if li.status_code in (200, 201):
        s = time.time()
        await human_post("meera", f"@{mee.name} kt linked owner {secrets.token_hex(2)}")
        gm = await mee.wait_message(s, "kt linked owner", 10)
        p = (gm or {}).get("data", {}).get("payload", {})
        record("S8.owner_rule_after_link", "PASS" if p.get("addressed") else "INFO",
               f"meera '@kt-meera' after link: addressed={p.get('addressed')}", gm)
        s = time.time()
        await human_post("dev", f"@{mee.name} kt dev pokes meera's agent")
        gm = await mee.wait_message(s, "kt dev pokes", 10)
        await asyncio.sleep(3)
        refusal = [f.get("data", {}).get("payload", {}).get("body") for f in dev.frames if f["_t"] >= s and f.get("event") == "message"]
        record("S8.unlinked_human_refused", "INFO",
               f"dev (not in rule) '@kt-meera': addressed={(gm or {}).get('data',{}).get('payload',{}).get('addressed')}",
               {"event": gm, "room_msgs_after": refusal})

    for p in probes.values():
        await p.stop()
    print("\nresults in", OUT)
    fails = [k for k, v in RESULTS.items() if v["status"] == "FAIL"]
    print("FAILS:", fails or "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
