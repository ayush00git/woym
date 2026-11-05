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


async def register_agent(reg_key: str, name: str, description: str) -> dict[str, str]:
    async with httpx.AsyncClient(base_url=SWITCH, timeout=30) as c:
        r = await c.post("/agents", headers={"Authorization": f"Bearer {reg_key}"}, json={
            "name": name, "description": description, "connector_type": "custom",
            "integration_profile": {"connection_model": "always_on", "message_exchange": True,
                                    "pre_invocation_mediation": [], "post_invocation_mediation": [],
                                    "event_reporting": []},
            "overwrite": True,
        })
        d = check(r, f"register {name}")
        return {"id": d["id"], "api_key": d["api_key"]}


async def main() -> int:
    env = env_file()
    try:
        data = creds.load()
    except FileNotFoundError:
        data = {}
    data.setdefault("agents", {})
    data.setdefault("humans", {})

    # Gateway users + registration keys
    admin = await gw_login(env["GATEWAY_ADMIN_EMAIL"], env["GATEWAY_ADMIN_PASSWORD"])
    gw: dict[str, httpx.AsyncClient] = {}
    for h in (DEV, MEERA):
        hu = data["humans"].setdefault(h, {})
        hu.setdefault("email", f"{h}@woym.local")
        hu.setdefault("switch_password", f"woym-{h}-pass-1")
        r = await admin.post("/users", json={"name": h, "email": hu["email"], "password": hu["switch_password"], "role": "user"})
        check(r, f"create gateway user {h}", ok=(200, 201, 409))
        gw[h] = await gw_login(hu["email"], hu["switch_password"])
        hu["switch_user_id"] = check(await gw[h].get("/auth/me"), "auth/me").get("id")
        if not hu.get("reg_key"):
            hu["reg_key"] = check(await gw[h].post("/api-keys", json={"label": f"woym-{h}"}), f"api key {h}")["key"]
        say(f"gateway user {h} ok ({hu['switch_user_id']})")
    creds.save(data)

    # Agents
    for name, (owner, desc) in AGENTS.items():
        a = data["agents"].get(name)
        if a:
            r = await httpx.AsyncClient(base_url=SWITCH, timeout=30).get(
                f"/agents/{a['id']}/ops", headers={"Authorization": f"Bearer {a['api_key']}"})
            if r.status_code == 200:
                say(f"agent {name} ok (kept)")
                continue
        a = await register_agent(data["humans"][owner]["reg_key"], name, desc)
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
    team = check(await mm.get(f"/teams/name/{env['MATTERMOST_TEAM_NAME']}"), "team")
    for h in (DEV, MEERA):
        hu = data["humans"][h]
        r = await mm.post("/users", json={"username": h, "email": hu["email"], "password": f"Woym-{h}-Pass-1!"})
        uid = r.json()["id"] if r.status_code == 201 else check(await mm.get(f"/users/username/{h}"), f"mm user {h}")["id"]
        hu["mm_user_id"] = uid
        await mm.post(f"/teams/{team['id']}/members", json={"team_id": team["id"], "user_id": uid})
        await mm.put(f"/users/{uid}/roles", json={"roles": "system_user system_user_access_token"})
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
    if room and (await gw[DEV].get(f"/rooms/{room['id']}")).status_code == 200:
        say(f"room {room['name']} ok (kept)")
    else:
        d = check(await gw[DEV].post("/rooms", json={
            "name": config.ROOM_NAME, "description": "Acme support: cases that work on the vendor's machine but not Acme's",
            "bridge_id": bridge_id, "agent_names": [config.AGENT_TRIAGE, config.AGENT_RUNNER, config.AGENT_FIXER],
            "user_names": [DEV, MEERA], "channel_type": "channel_public", "internal_only": False,
        }), "create room")
        room = data["room"] = {"id": d["id"], "name": config.ROOM_NAME, "channel_id": d.get("external_channel_id"), "bridge_id": bridge_id}
        say(f"room {config.ROOM_NAME} created ({room['id']})")
    creds.save(data)
    acme_id = data["agents"][config.AGENT_CUSTOMER]["id"]
    for who, client in (("meera", gw[MEERA]), ("dev", gw[DEV]), ("admin", admin)):
        r = await client.post(f"/rooms/{room['id']}/agents", json={"agent_ids": [acme_id]})
        if r.status_code == 200:
            say(f"acme-env added to room by {who}")
            break
        say(f"adding acme-env as {who} -> {r.status_code} {r.text[:200]}")
    else:
        MANUAL.append("acme-env could not be added to the room; see ISSUES.md")

    # Identity links (Mattermost account -> Switch user), so `owner` rules match
    for h in (DEV, MEERA):
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
    check(await gw[MEERA].put(f"/agents/{acme_id}/addressing-policy", json=rule), "acme-env custom rule")
    say("acme-env addressing: meera + meera's agents + triage (set by meera)")

    # Follow-ups off
    if room.get("channel_id"):
        r = await httpx.AsyncClient(base_url=f"{MM}/api/v4", timeout=30, headers={
            "Authorization": f"Bearer {data['humans'][DEV]['mm_token']}"}).post(
            "/posts", json={"channel_id": room["channel_id"], "message": "!follow-ups off"})
        say(f"posted '!follow-ups off' as dev -> {r.status_code}")
    creds.save(data)

    say(f"credentials -> {creds.CREDS_FILE}")
    if MANUAL:
        print("\nMANUAL STEPS LEFT:")
        for m in MANUAL:
            print(" -", m)
        return 2
    say("done, no manual steps")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
