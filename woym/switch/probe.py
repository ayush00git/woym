"""Manual check of the real client: connect as an agent and print what it sees.

  uv run python -m woym.switch.probe triage              # print events (Ctrl-C to stop)
  uv run python -m woym.switch.probe triage --ops        # list ops and exit
  uv run python -m woym.switch.probe triage --post "hi"  # post once, keep listening
  uv run python -m woym.switch.probe triage --to acme-env --post "hi"   # targeted

Credentials come from var/switch/credentials.json (scripts/setup.sh).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from woym.contracts import MessageEvent
from woym.switch.client import WsSwitchClient


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent")
    ap.add_argument("--ops", action="store_true")
    ap.add_argument("--post")
    ap.add_argument("--to", action="append", default=[])
    ap.add_argument("--thread")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    c = WsSwitchClient.from_credentials(a.agent)
    if a.ops:
        print(json.dumps(sorted(await c.list_ops()), indent=1))
        return

    async def show(ev: MessageEvent) -> None:
        print(json.dumps(ev.__dict__, indent=1), flush=True)

    runner = asyncio.create_task(c.run(show))
    if a.post:
        if a.to:
            print("statuses:", await c.send_targeted(c.room_id, a.to, a.post, a.thread))
        else:
            print("posted:", await c.post(c.room_id, a.post, a.thread))
        for p in await c.participants(c.room_id):
            print("participant:", p)
    await runner


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
