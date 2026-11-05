"""Idempotent bootstrap after `make up` (PLAN.md A5). Run via scripts/setup.sh.

Creates or reuses:
  - Gateway users dev and meera, each with a personal registration key;
  - agents triage/runner/fixer (owned by dev) and acme-env (owned by meera);
  - Mattermost users dev and meera with personal access tokens (for humans.py);
  - the room `acme-support` (Mattermost channel) with all four agents and both humans;
  - addressing: triage open to anyone; acme-env = meera + her agents + triage only;
  - Meera's and Dev's Mattermost accounts linked to their Switch users;
  - `!follow-ups off` posted in the room.
Writes everything to var/switch/credentials.json. Re-running keeps existing keys.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx

from woym import config
from woym.switch import creds

ENV_FILE = config.ROOT / "infra" / "switch" / ".env"
SWITCH = config.SWITCH_URL.rstrip("/")
MM = config.MATTERMOST_URL.rstrip("/")

DEV, MEERA = config.VENDOR_HUMAN, config.CUSTOMER_HUMAN
AGENTS = {
    config.AGENT_TRIAGE: (DEV, "WOYM case lead: opens cases, posts cards, routes work"),
    config.AGENT_RUNNER: (DEV, "WOYM vendor sandbox: runs repros in throwaway containers, bisects"),
    config.AGENT_FIXER: (DEV, "WOYM fixer: writes a failing test and the fix, pushes a branch"),
    config.AGENT_CUSTOMER: (MEERA, "Acme's environment agent: shares facts only with consent, runs repros on Meera's machine"),
}
MANUAL: list[str] = []


def env_file(path: Path = ENV_FILE) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def say(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def check(r: httpx.Response, what: str, ok=(200, 201)) -> Any:
    if r.status_code not in ok:
        raise SystemExit(f"[setup] FAILED {what}: {r.status_code} {r.text[:400]}")
    try:
        return r.json()
    except ValueError:
        return None


async def gw_login(email: str, password: str) -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=f"{SWITCH}/gateway", timeout=30)
    check(await c.post("/auth/login", json={"email": email, "password": password}), f"gateway login {email}")
    return c


def new_password(prefix: str) -> str:
    """Random, but always satisfies Mattermost's and the Gateway's rules (upper, lower, digit, symbol, 8+)."""
    return f"{prefix}-{secrets.token_urlsafe(12)}-Aa1!"


async def stack_fingerprint(admin: httpx.AsyncClient) -> dict[str, Any]:
    """What identifies THIS stack's database: the Mattermost bridge id and the Gateway
    user ids. All cached ids, keys and tokens are only valid for one fingerprint."""
    # Right after `make up`, Switch's own seeder (the `setup` container) may still be
    # creating the Mattermost bridge; wait for it instead of failing.
    bid = None
    for i in range(60):
        bridges = check(await admin.get("/collaborations"), "list bridges (admin)")
        bid = next((b.get("id") or b.get("bridge_id") for b in bridges if b.get("bridge_type") == "mattermost"), None)
        if bid:
            break
        if i == 0:
            say("waiting for Switch's seeder to create the Mattermost bridge…")
        await asyncio.sleep(2)
    if not bid:
        raise SystemExit("[setup] no Mattermost bridge after 120 s; is the collab profile up? (docker logs woym-switch-setup-1)")
    users = check(await admin.get("/users"), "list gateway users")
    by_email = {u["email"]: u["id"] for u in users}
    return {"bridge_id": bid, "switch_user_ids": {h: by_email.get(f"{h}@woym.local") for h in (DEV, MEERA)}}


def stale(data: dict[str, Any], now: dict[str, Any]) -> str | None:
    old = data.get("fingerprint")
    if not old:
        # credentials from before fingerprints: rebuild one from what they recorded
        old = {"bridge_id": (data.get("room") or {}).get("bridge_id"),
               "switch_user_ids": {h: hu.get("switch_user_id") for h, hu in (data.get("humans") or {}).items()}}
        if not old["bridge_id"]:
            return None
    if old.get("bridge_id") != now["bridge_id"]:
        return f"bridge {old.get('bridge_id')} -> {now['bridge_id']}"
    for h, uid in (old.get("switch_user_ids") or {}).items():
        if uid and now["switch_user_ids"].get(h) != uid:
            return f"gateway user {h} {uid} -> {now['switch_user_ids'].get(h)}"
    return None


async def register_agent(reg_key: str, name: str, description: str) -> dict[str, str]:
    async with httpx.AsyncClient(base_url=SWITCH, timeout=30) as c:
        r = await c.post("/agents", headers={"Authorization": f"Bearer {reg_key}"}, json={
            "name": name, "description": description, "connector_type": "custom",
            "integration_profile": {"connection_model": "always_on", "message_exchange": True,
                                    "pre_invocation_mediation": [], "post_invocation_mediation": [],
                                    "event_reporting": []},
            "overwrite": True,
        })
        if r.status_code == 401:
            raise PermissionError(r.text[:200])
        d = check(r, f"register {name}")
        return {"id": d["id"], "api_key": d["api_key"]}


async def main(fresh_room: str | None = None, activate: bool = False, customer: str | None = None,
               camera_also: list[str] | None = None) -> int:
    creds.require_no_live_run()
    env = env_file()
    # Which customer this room serves (config.CUSTOMERS: room key -> (human, agent, container)).
    cust_key = customer or config.ROOM_NAME
    if cust_key not in config.CUSTOMERS:
        raise SystemExit(f"[setup] unknown customer {cust_key!r}; known: {sorted(config.CUSTOMERS)}")
    cust_human, cust_agent, _container = config.CUSTOMERS[cust_key]
    if customer and not fresh_room:
        fresh_room = customer  # C's code looks the room up as rooms[<customer key>]
    humans = [DEV, MEERA] + ([cust_human] if cust_human not in (DEV, MEERA) else [])
    agents = dict(AGENTS)
    if cust_agent not in agents:
        agents[cust_agent] = (cust_human, f"{cust_agent}: the customer's own environment agent; shares facts only with consent")
    try:
        data = creds.load()
    except FileNotFoundError:
        data = {}
    admin = await gw_login(env["GATEWAY_ADMIN_EMAIL"], env["GATEWAY_ADMIN_PASSWORD"])

    # A wiped stack (make down -v) invalidates every cached id, key and token.
    why = stale(data, await stack_fingerprint(admin))
    if why:
        say(f"stack changed ({why}): dropping cached agents, keys, tokens, rooms and participant ids")
        data = {}
    data.setdefault("agents", {})
    data.setdefault("humans", {})
    # Customers set up earlier (e.g. --customer acme-nepal) stay maintained by a plain `make setup`.
    for _key, (h, ag, _c) in config.CUSTOMERS.items():
        if h in data["humans"] and h not in humans:
            humans.append(h)
        if ag in data["agents"] and ag not in agents:
            agents[ag] = (h, f"{ag}: the customer's own environment agent; shares facts only with consent")

    # Gateway users + registration keys
    gw: dict[str, httpx.AsyncClient] = {}
    for h in humans:
        hu = data["humans"].setdefault(h, {})
        hu.setdefault("email", f"{h}@woym.local")
        hu.setdefault("switch_password", new_password(h))
        r = await admin.post("/users", json={"name": h, "email": hu["email"], "password": hu["switch_password"], "role": "user"})
        check(r, f"create gateway user {h}", ok=(200, 201, 409))
        gw[h] = await gw_login(hu["email"], hu["switch_password"])
        hu["switch_user_id"] = check(await gw[h].get("/auth/me"), "auth/me").get("id")
        if not hu.get("reg_key"):
            hu["reg_key"] = check(await gw[h].post("/api-keys", json={"label": f"woym-{h}"}), f"api key {h}")["key"]
        say(f"gateway user {h} ok ({hu['switch_user_id']})")
    creds.save(data)

    # Agents
    for name, (owner, desc) in agents.items():
        a = data["agents"].get(name)
        if a:
            r = await httpx.AsyncClient(base_url=SWITCH, timeout=30).get(
                f"/agents/{a['id']}/ops", headers={"Authorization": f"Bearer {a['api_key']}"})
            if r.status_code == 200:
                say(f"agent {name} ok (kept)")
                continue
        try:
            a = await register_agent(data["humans"][owner]["reg_key"], name, desc)
        except PermissionError:
            say(f"{owner}'s registration key was rejected; minting a new one")
            hu = data["humans"][owner]
            hu["reg_key"] = check(await gw[owner].post("/api-keys", json={"label": f"woym-{owner}"}), f"api key {owner}")["key"]
            a = await register_agent(hu["reg_key"], name, desc)
        a["owner"] = owner
        data["agents"][name] = a
        say(f"agent {name} registered, owner {owner}")
    creds.save(data)

    # Mattermost users + PATs
    mm = httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30)
    r = await mm.post("/users/login", json={"login_id": env["MATTERMOST_ADMIN_USER"], "password": env["MATTERMOST_ADMIN_PASSWORD"]})
    check(r, "mattermost admin login")
    mm.headers["Authorization"] = f"Bearer {r.headers['Token']}"
    check(await mm.put("/config/patch", json={"ServiceSettings": {"EnableUserAccessTokens": True}}), "enable PATs")
    for i in range(60):
        r = await mm.get(f"/teams/name/{env['MATTERMOST_TEAM_NAME']}")
        if r.status_code == 200:
            break
        if i == 0:
            say("waiting for the Mattermost team…")
        await asyncio.sleep(2)
    team = check(r, "team")
    for h in humans:
        hu = data["humans"][h]
        pw = new_password(h.capitalize())
        r = await mm.post("/users", json={"username": h, "email": hu["email"], "password": pw})
        if r.status_code == 201:
            uid = r.json()["id"]
            hu["mm_password"] = pw
        else:
            uid = check(await mm.get(f"/users/username/{h}"), f"mm user {h}")["id"]
            hu.setdefault("mm_password", f"Woym-{h}-Pass-1!")  # created by an older setup.py
        hu["mm_user_id"] = uid
        await mm.post(f"/teams/{team['id']}/members", json={"team_id": team["id"], "user_id": uid})
        await mm.put(f"/users/{uid}/roles", json={"roles": "system_user system_user_access_token"})
        if hu.get("mm_token"):
            ok = await httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=15).get(
                "/users/me", headers={"Authorization": f"Bearer {hu['mm_token']}"})
            if ok.status_code != 200:
                say(f"{h}'s Mattermost token was rejected ({ok.status_code}); minting a new one")
                hu.pop("mm_token")
        if not hu.get("mm_token"):
            hu["mm_token"] = check(await mm.post(f"/users/{uid}/tokens", json={"description": "woym humans.py"}), f"PAT {h}")["token"]
        say(f"mattermost user {h} ok")
    creds.save(data)

    # Room
    bridges = check(await gw[DEV].get("/collaborations"), "list bridges")
    mmb = [b for b in bridges if b.get("bridge_type") == "mattermost"]
    if not mmb:
        raise SystemExit("[setup] no mattermost bridge; is the collab profile up?")
    bridge_id = mmb[0].get("id") or mmb[0].get("bridge_id")
    room = data.get("room")
    new_room = False
    if customer and (data.get("rooms") or {}).get(fresh_room):
        room = data["rooms"][fresh_room]  # a customer room is reused, not recreated
        if (await gw[DEV].get(f"/rooms/{room['id']}")).status_code != 200:
            room = None
    elif fresh_room:
        room = None  # a new room; the active room stays as it is unless --activate
    if room and (await gw[DEV].get(f"/rooms/{room['id']}")).status_code == 200:
        say(f"room {room['name']} ok (kept{'' if room is data.get('room') else ', not active'})")
    else:
        new_room = True
        room_name = fresh_room or config.ROOM_NAME
        d = check(await gw[DEV].post("/rooms", json={
            "name": room_name, "description": "Acme support: cases that work on the vendor's machine but not Acme's",
            "bridge_id": bridge_id, "agent_names": [config.AGENT_TRIAGE, config.AGENT_RUNNER, config.AGENT_FIXER],
            "user_names": [DEV, cust_human], "channel_type": "channel_public", "internal_only": False,
        }), "create room")
        room = {"id": d["id"], "name": room_name, "channel_id": d.get("external_channel_id"), "bridge_id": bridge_id,
                "customer": cust_human, "customer_agent": cust_agent}
        data.setdefault("rooms", {})[room_name] = room
        if not fresh_room or activate or "room" not in data:
            data["room"] = room
            say(f"room {room_name} created ({room['id']}), ACTIVE")
        else:
            say(f"room {room_name} created ({room['id']}), stored as rooms.{room_name}; "
                f"active room unchanged ({data['room']['name']}). Pass --activate to switch")
    creds.save(data)
    cust_id = data["agents"][cust_agent]["id"]
    for who, client in ((cust_human, gw[cust_human]), ("dev", gw[DEV]), ("admin", admin)):
        r = await client.post(f"/rooms/{room['id']}/agents", json={"agent_ids": [cust_id]})
        if r.status_code == 200:
            say(f"{cust_agent} added to room {room['name']} by {who}")
            break
        say(f"adding {cust_agent} as {who} -> {r.status_code} {r.text[:200]}")
    else:
        MANUAL.append(f"{cust_agent} could not be added to the room; see ISSUES.md")

    # Identity links (Mattermost account -> Switch user), so `owner` rules match
    for h in humans:
        r = await gw[h].post(f"/collaborations/{bridge_id}/identities", json={
            "external_user_id": data["humans"][h]["mm_user_id"], "username": h, "user_id": None})
        say(f"link {h}'s mattermost account -> {r.status_code}")
        if r.status_code not in (200, 201, 409):
            MANUAL.append(f"link {h}'s Mattermost account to Switch user {h} (API said {r.status_code}: {r.text[:120]})")

    # Addressing
    triage_id = data["agents"][config.AGENT_TRIAGE]["id"]
    check(await gw[DEV].put(f"/agents/{triage_id}/addressing-policy", json={"policy": None}), "triage open")
    say("triage addressing: anyone")
    rule = {"policy": {"rules": [
        {"owner": True, "owner_agents": True, "users": [], "agents": []},
        {"users": [], "agents": [triage_id]},
    ]}}
    for ca, (ch, _) in ((a, o) for a, o in agents.items() if a not in AGENTS or a == config.AGENT_CUSTOMER):
        if ca not in data["agents"]:
            continue
        check(await gw[ch].put(f"/agents/{data['agents'][ca]['id']}/addressing-policy", json=rule), f"{ca} custom rule")
        say(f"{ca} addressing: {ch} + {ch}'s agents + triage (set by {ch})")

    # Follow-ups: already off server-wide on 0.31.0 [live]: posting "!follow-ups off"
    # only makes Switch reply "isn't switched on", so we no longer post it.

    creds.save(data)

    # Human participant ids: the exact `sender` on message events, e.g.
    # "@switch-mattermost-<bridge>-meera:localhost" [live]. Read from room history
    # (joins and messages), never built from parts. Agents check senders by this id.
    await asyncio.sleep(2)
    entries = await room_entries(data, room["id"])
    room_humans = [DEV, cust_human]
    missing = [h for h in room_humans if not data["humans"][h].get("switch_participant_id")
               and not _sender_ids(entries, h)]
    if missing:
        # Cold stack: a new room may hold no line from a human yet. Each one says
        # hello once, so Switch has a message carrying their sender id.
        for h in missing:
            hello = {DEV: "👋 dev here, vendor support. Agents triage/runner/fixer work for me."}.get(
                h, f"👋 {h} here. {cust_agent} is my agent.")
            r = await httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30, headers={
                "Authorization": f"Bearer {data['humans'][h]['mm_token']}"}).post(
                "/posts", json={"channel_id": room["channel_id"], "message": hello})
            say(f"{h} said hello in the room to reveal their sender id -> {r.status_code}")
        for _ in range(10):
            await asyncio.sleep(1.5)
            entries = await room_entries(data, room["id"])
            if all(_sender_ids(entries, h) for h in missing):
                break
    for h in room_humans:
        if data["humans"][h].get("switch_participant_id"):
            continue  # same id in every room on this bridge [live]
        ids = _sender_ids(entries, h)
        if len(ids) == 1:
            data["humans"][h]["switch_participant_id"] = ids.pop()
            say(f"{h} participant id {data['humans'][h]['switch_participant_id']}")
        else:
            MANUAL.append(f"no unique participant id for {h} in room history (found {ids}); "
                          f"post one message as {h} and re-run setup")
    creds.save(data)

    fp = await stack_fingerprint(admin)
    data["fingerprint"] = fp
    await brand_agents(data, gw, env, cust_human)
    if new_room:
        await wait_for_greetings(data, room["id"], cust_agent)
    if camera_also:
        cam = data.setdefault("camera", {})
        cam["extra_rooms"] = sorted(set(cam.get("extra_rooms", [])) | set(camera_also))
    await ensure_camera(data, env, team["id"])
    creds.save(data)

    say(f"credentials -> {creds.CREDS_FILE}")
    if MANUAL:
        print("\nMANUAL STEPS LEFT:")
        for m in MANUAL:
            print(" -", m)
        return 2
    say("done, no manual steps")
    return 0


async def room_entries(data: dict[str, Any], room_id: str) -> list[dict[str, Any]]:
    tri = data["agents"][config.AGENT_TRIAGE]
    async with httpx.AsyncClient(base_url=SWITCH, timeout=30, headers={"Authorization": f"Bearer {tri['api_key']}"}) as c:
        ctx = check(await c.post(f"/agents/{tri['id']}/ops/read_context", json={"limit": 200, "room_id": room_id}), "read_context")
    out: list[dict[str, Any]] = []
    for t in (ctx or {}).get("result", {}).get("threads", []):
        out += [t.get("root") or {}] + list(t.get("replies") or [])
    return out


def _sender_ids(entries: list[dict[str, Any]], h: str) -> set[str]:
    return {e.get("sender") for e in entries
            if e.get("sender_name") == h and (e.get("sender") or "").startswith("@switch-mattermost-")}


CAMERA = "camera"


async def ensure_camera(data: dict[str, Any], env: dict[str, str], team_id: str) -> None:
    """A read-only Mattermost viewer for scripts/record.py: a member of the ACTIVE
    room's channel only (default team channels left where Mattermost allows it).
    It never posts. It is not linked to any Switch user and is in no addressing rule."""
    mm = httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30)
    r = await mm.post("/users/login", json={"login_id": env["MATTERMOST_ADMIN_USER"], "password": env["MATTERMOST_ADMIN_PASSWORD"]})
    mm.headers["Authorization"] = f"Bearer {r.headers['Token']}"
    pw = new_password("Camera")
    r = await mm.post("/users", json={"username": CAMERA, "email": "camera@woym.local", "password": pw,
                                      "first_name": "camera", "last_name": "(recording)"})
    if r.status_code == 201:
        uid = r.json()["id"]
    else:
        uid = check(await mm.get(f"/users/username/{CAMERA}"), "camera user")["id"]
        pw = (data.get("camera") or {}).get("password") or "Woym-camera-Pass-1!"  # older setup.py
    await mm.post(f"/teams/{team_id}/members", json={"team_id": team_id, "user_id": uid})
    extra = [data["rooms"][k]["channel_id"] for k in (data.get("camera") or {}).get("extra_rooms", [])
             if k in (data.get("rooms") or {})]
    want = data["room"]["channel_id"]
    for ch_id in [want, *extra]:
        await mm.post(f"/channels/{ch_id}/members", json={"user_id": uid})
    left = []
    for ch in (await mm.get(f"/users/{uid}/teams/{team_id}/channels")).json():
        if ch["id"] not in [want, *extra] and ch.get("type") in ("O", "P"):
            rr = await mm.delete(f"/channels/{ch['id']}/members/{uid}")
            left.append(f"{ch['name']}:{rr.status_code}")
    extra_rooms = (data.get("camera") or {}).get("extra_rooms", [])
    data["camera"] = {"username": CAMERA, "mm_user_id": uid, "password": pw, "channel_id": want,
                      "extra_rooms": extra_rooms}
    also = f" + {', '.join(extra_rooms)}" if extra_rooms else ""
    say(f"camera viewer in {data['room']['name']}{also} only (left: {', '.join(left) or 'none'})")


LABELS = {
    config.AGENT_TRIAGE: "triage · owner: dev (vendor)",
    config.AGENT_RUNNER: "runner · owner: dev (vendor)",
    config.AGENT_FIXER: "fixer · owner: dev (vendor)",
    config.AGENT_CUSTOMER: "acme-env · owner: meera (Acme)",
    "nepal-env": "nepal-env · owner: kiran (Acme Nepal)",
}
VENDOR_RGB, CUSTOMER_RGB = (37, 99, 235), (217, 119, 6)  # blue = vendor, amber = customer


def _png(rgb: tuple[int, int, int], n: int = 128) -> bytes:
    """A solid-colour PNG, no dependencies."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * n for _ in range(n))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def brand_agents(data: dict[str, Any], gw: dict[str, httpx.AsyncClient], env: dict[str, str],
                       cust_human: str = MEERA) -> None:
    """Show each agent's owner in Mattermost: the display name is set by the owner
    through Switch (PUT /gateway/agents/{id}/name, which renames the MM bot) [live].
    Avatars are uploaded straight to Mattermost as admin, because Switch's icon_url
    did not reach the bot [live]. Re-run setup after a stack restart in case they reset."""
    for name, label in LABELS.items():
        a = data["agents"].get(name)
        if not a:
            continue  # e.g. nepal-env before its customer room was set up
        r = await gw[a["owner"]].put(f"/agents/{a['id']}/name", json={"name": name, "display_name": label})
        say(f"display name {label!r} -> {r.status_code}")
        if r.status_code != 200:
            MANUAL.append(f"display name for {name}: {r.status_code} {r.text[:120]}")
    mm = httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30)
    r = await mm.post("/users/login", json={"login_id": env["MATTERMOST_ADMIN_USER"], "password": env["MATTERMOST_ADMIN_PASSWORD"]})
    mm.headers["Authorization"] = f"Bearer {r.headers['Token']}"
    cfg = (await mm.get("/config")).json().get("TeamSettings", {}).get("TeammateNameDisplay")
    if cfg not in ("full_name", "nickname_full_name"):
        await mm.put("/config/patch", json={"TeamSettings": {"TeammateNameDisplay": "full_name"}})
        say(f"mattermost TeammateNameDisplay {cfg} -> full_name")
    for name in LABELS:
        if name not in data["agents"]:
            continue
        u = (await mm.get(f"/users/username/{name}")).json()
        rgb = VENDOR_RGB if data["agents"][name]["owner"] == DEV else CUSTOMER_RGB
        r = await mm.post(f"/users/{u['id']}/image", files={"image": (f"{name}.png", _png(rgb), "image/png")})
        if r.status_code != 200:
            MANUAL.append(f"avatar for {name}: {r.status_code}")
    say("avatars: vendor agents blue, acme-env amber")


async def wait_for_greetings(data: dict[str, Any], room_id: str, cust_agent: str = config.AGENT_CUSTOMER,
                             timeout: float = 45) -> None:
    """Switch posts a join greeting for each agent when it is added to a room
    (wording varies: "Hey! I'm on standby — @triage to get started.") [live],
    within the same second, whether or not the agent is connected. Wait until all
    four have a message in the room, so they are at the top before any demo."""
    tri = data["agents"][config.AGENT_TRIAGE]
    want = {config.AGENT_TRIAGE, config.AGENT_RUNNER, config.AGENT_FIXER, cust_agent}
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    async with httpx.AsyncClient(base_url=SWITCH, timeout=30, headers={"Authorization": f"Bearer {tri['api_key']}"}) as c:
        while loop.time() < end:
            r = await c.post(f"/agents/{tri['id']}/ops/read_context", json={"limit": 100, "room_id": room_id})
            seen = {e.get("sender_name") for t in (r.json().get("result") or {}).get("threads", [])
                    for e in [t.get("root") or {}] + list(t.get("replies") or [])
                    if e.get("kind", "message") == "message" and e.get("sender_name") in want}
            if want <= seen:
                say("join greetings posted for all 4 agents")
                return
            await asyncio.sleep(1.5)
    MANUAL.append(f"join greetings missing for {sorted(want - seen)} after {timeout:.0f}s; wait before recording")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh-room", metavar="NAME", help="create a new room (same agents, rule, links) as rooms.NAME")
    ap.add_argument("--activate", action="store_true", help="with --fresh-room: also make it the active room")
    ap.add_argument("--customer", metavar="KEY", help="customer from config.CUSTOMERS (e.g. acme-nepal): its human, "
                    "agent and rule; the room is stored as rooms.KEY unless --fresh-room names it")
    ap.add_argument("--camera-also", action="append", metavar="KEY",
                    help="also keep the camera viewer in rooms.KEY (e.g. acme-nepal, for an epilogue take); remembered")
    a = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(a.fresh_room, a.activate, a.customer, a.camera_also)))
    except creds.LiveRunLocked as e:
        sys.exit(f"[setup] REFUSED: {e}")
