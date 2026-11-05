"""Preflight status for recording: `make status` / `uv run python scripts/status.py [--json]`.

READ-ONLY. It never connects as an agent (a second connection would take over the
room claim from a running agent), never writes credentials, and never posts.
It reads:
  - Switch /health, Mattermost /system/ping;
  - var/switch/credentials.json (active room, agents);
  - Gateway admin GET /gateway/agents/{id}: owner, addressing policy, sessions;
  - var/run/live.lock, var/cases.json (latest case and step).

An agent counts as connected when Switch reports it "live" in the active room
(rooms[].status, or a session with state "live"). Exit 0 when the stack is up and all 4 agents are connected, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from woym import config  # noqa: E402
from woym.switch import creds  # noqa: E402

SWITCH = config.SWITCH_URL.rstrip("/")
MM = config.MATTERMOST_URL.rstrip("/")
ENV_FILE = config.ROOT / "infra" / "switch" / ".env"
AGENTS = (config.AGENT_TRIAGE, config.AGENT_RUNNER, config.AGENT_FIXER, config.AGENT_CUSTOMER)


def env_file() -> dict[str, str]:
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def describe_policy(policy: dict | None, names: dict[str, str]) -> str:
    if not policy or not policy.get("rules"):
        return "anyone (open)"
    parts = []
    for r in policy["rules"]:
        who = []
        if r.get("owner"):
            who.append("owner")
        if r.get("owner_agents"):
            who.append("owner's agents")
        for kind in ("agents", "users"):
            v = r.get(kind)
            if v == "*":
                who.append(f"any {kind[:-1]}")
            elif v:
                who += [names.get(i, i[:8]) for i in v]
        if r.get("platform"):
            who.append("platform")
        scope = "" if r.get("rooms", "*") == "*" else f" in {len(r['rooms'])} room(s)"
        parts.append(" + ".join(who) + scope)
    return " | ".join(parts)


def collect() -> dict[str, Any]:
    s: dict[str, Any] = {"ok": True, "problems": []}

    def bad(msg: str) -> None:
        s["ok"] = False
        s["problems"].append(msg)

    h = httpx.Client(timeout=5)
    for key, url in (("switch", f"{SWITCH}/health"), ("mattermost", f"{MM}/api/v4/system/ping")):
        try:
            s[key] = "up" if h.get(url).status_code == 200 else "unhealthy"
        except httpx.HTTPError:
            s[key] = "down"
        if s[key] != "up":
            bad(f"{key} is {s[key]} (make up)")

    pid = creds.live_lock_holder()
    s["live_run"] = f"running (pid {pid})" if pid else "none"

    try:
        data = creds.load()
    except FileNotFoundError:
        bad("no var/switch/credentials.json (make setup)")
        return s
    room = data.get("room") or {}
    s["room"] = {k: room.get(k) for k in ("name", "id", "channel_id")}
    s["other_rooms"] = sorted(set((data.get("rooms") or {})) - {room.get("name")})

    s["agents"] = {}
    if s.get("switch") == "up":
        env = env_file()
        gw = httpx.Client(base_url=f"{SWITCH}/gateway", timeout=10)
        r = gw.post("/auth/login", json={"email": env["GATEWAY_ADMIN_EMAIL"], "password": env["GATEWAY_ADMIN_PASSWORD"]})
        if r.status_code != 200:
            bad(f"gateway admin login failed ({r.status_code})")
            return s
        names = {a["id"]: n for n, a in data.get("agents", {}).items()}
        for n in AGENTS:
            a = data.get("agents", {}).get(n)
            if not a:
                s["agents"][n] = {"connected": False, "error": "not in credentials"}
                bad(f"{n}: not registered (make setup)")
                continue
            d = gw.get(f"/agents/{a['id']}")
            if d.status_code != 200:
                s["agents"][n] = {"connected": False, "error": f"gateway {d.status_code}"}
                bad(f"{n}: gateway says {d.status_code}")
                continue
            d = d.json()
            live = [x for x in d.get("sessions") or [] if x.get("room_id") == room.get("id") and x.get("state") == "live"]
            here = [x for x in d.get("rooms") or [] if x.get("room_id") == room.get("id")]
            in_room = bool(here)
            room_status = here[0].get("status") if here else None  # "live" | "disconnected" [live]
            s["agents"][n] = {
                "connected": bool(live) or room_status == "live",
                "room_status": room_status,
                "last_seen": live[0].get("last_seen_at") if live else None,
                "owner": d.get("owner_name"),
                "display_name": d.get("display_name"),
                "in_room": in_room,
                "addressing": describe_policy(d.get("addressing_policy"), names),
            }
            if not in_room:
                bad(f"{n}: not a member of room {room.get('name')}")
            if not live:
                bad(f"{n}: not connected (start the agents)")

    try:
        cases = json.loads(config.CASES_DB.read_text()).get("cases", {})
    except (FileNotFoundError, ValueError):
        cases = {}
    if cases:
        c = max(cases.values(), key=lambda c: int(c.get("id", 0)))
        s["latest_case"] = {"id": c.get("id"), "step": c.get("step"), "hops": c.get("hops"),
                            "room_matches": c.get("room_id") == room.get("id"),
                            "last": (c.get("history") or [None])[-1]}
    else:
        s["latest_case"] = None
    return s


def show(s: dict[str, Any]) -> None:
    mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
    print(f"{mark(s.get('switch') == 'up')} switch       {s.get('switch')}")
    print(f"{mark(s.get('mattermost') == 'up')} mattermost   {s.get('mattermost')}")
    print(f"   live run     {s.get('live_run')}")
    r = s.get("room")
    if r:
        print(f"   room         {r['name']}  {r['id']}  (mm channel {r['channel_id']})")
        if s.get("other_rooms"):
            print(f"   other rooms  {', '.join(s['other_rooms'])}")
    for n, a in (s.get("agents") or {}).items():
        if "error" in a:
            print(f"{mark(False)} @{n:9} {a['error']}")
            continue
        conn = "connected" if a["connected"] else "NOT connected"
        print(f"{mark(a['connected'] and a['in_room'])} @{n:9} {conn:13} owner: {a['owner']:6} addressing: {a['addressing']}")
    c = s.get("latest_case")
    if c:
        warn = "" if c["room_matches"] else "  (different room)"
        print(f"   latest case  #{c['id']} step={c['step']} hops={c['hops']} last={c['last']}{warn}")
    else:
        print("   latest case  none")
    print("\nREADY" if s["ok"] else "\nNOT READY:\n - " + "\n - ".join(s["problems"]))


def main() -> int:
    ap = argparse.ArgumentParser(description="WOYM preflight (read-only)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    s = collect()
    if a.json:
        print(json.dumps(s, indent=2))
    else:
        show(s)
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
