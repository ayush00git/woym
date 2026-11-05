"""Post as a scripted human (dev or meera) into the WOYM room via the Mattermost API.

  uv run python scripts/humans.py meera "@triage payouts slip to Monday on my machine"
  uv run python scripts/humans.py meera "yes" --thread <root post id or 'last'>
  uv run python scripts/humans.py dev "@triage patch it" --thread last

These are SCRIPTED lines (demo and QA), not a live person typing; the demo labels them.
Prints the new post id. `--thread last` replies in the newest top-level thread.
Tokens and channel come from var/switch/credentials.json (scripts/setup.sh).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from woym import config  # noqa: E402
from woym.switch import creds  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("who", choices=sorted({config.VENDOR_HUMAN, config.CUSTOMER_HUMAN}
                                          | {h for h, _a, _c in config.CUSTOMERS.values()}))
    ap.add_argument("--room", help="room key in credentials rooms.<key> (e.g. acme-nepal); default: the active room")
    ap.add_argument("text")
    ap.add_argument("--thread", help="root post id to reply under, or 'last'")
    a = ap.parse_args()
    data = creds.load()
    if a.room:
        room = data["rooms"][a.room]
    else:
        # a customer who isn't the active room's customer posts in their own room (e.g. kiran -> acme-nepal)
        own = [k for k, (h, _a, _c) in config.CUSTOMERS.items() if h == a.who and k in (data.get("rooms") or {})]
        active_customer = data["room"].get("customer", config.CUSTOMER_HUMAN)
        room = data["rooms"][own[0]] if own and a.who not in (config.VENDOR_HUMAN, active_customer) else data["room"]
    chan = room["channel_id"]
    c = httpx.Client(base_url=f"{config.MATTERMOST_URL.rstrip('/')}/api/v4", timeout=30,
                     headers={"Authorization": f"Bearer {data['humans'][a.who]['mm_token']}"})
    root = a.thread
    if root == "last":
        posts = c.get(f"/channels/{chan}/posts", params={"per_page": 60}).json()
        roots = [p for pid in posts["order"] if not (p := posts["posts"][pid]).get("root_id")]
        root = roots[0]["id"] if roots else None
    body = {"channel_id": chan, "message": a.text}
    if root:
        body["root_id"] = root
    r = c.post("/posts", json=body)
    if r.status_code != 201:
        print(f"post failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
        return 1
    print(r.json()["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
