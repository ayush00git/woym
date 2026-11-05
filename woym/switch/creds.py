"""Local credentials written by scripts/setup.sh and read by agents and humans.py.

File: var/switch/credentials.json (gitignored under var/). Shape:

{
  "room":   {"id": "<switch room uuid>", "name": "acme-support", "channel_id": "<mm channel id>"},
  "agents": {"triage": {"id": "<uuid>", "api_key": "...", "owner": "dev"}, ...},
  "humans": {"dev": {"mm_user_id": "...", "mm_token": "...", "switch_user_id": "..."}, ...}
}
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from woym.config import VAR

CREDS_FILE = VAR / "switch" / "credentials.json"
LIVE_LOCK = VAR / "run" / "live.lock"  # docs/contracts.md §8: held by demo.py during a live run


class LiveRunLocked(RuntimeError):
    pass


def live_lock_holder(lock: Path | None = None) -> int | None:
    """pid holding the live-run lock, or None. A stale lock (dead pid) is removed."""
    lock = lock or LIVE_LOCK
    if not lock.exists():
        return None
    text = lock.read_text().strip()
    try:
        pid = int(json.loads(text)["pid"]) if text.startswith("{") else int(text.split()[0])
    except (ValueError, KeyError, IndexError):
        raise LiveRunLocked(f"{lock} exists but has no readable pid ({text[:60]!r}); "
                            "check no demo is running, then delete it") from None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        lock.unlink(missing_ok=True)  # stale
        return None
    except PermissionError:
        pass  # alive, owned by someone else
    return pid


def require_no_live_run(lock: Path | None = None) -> None:
    lock = lock or LIVE_LOCK
    pid = live_lock_holder(lock)
    if pid is not None and pid != os.getpid():
        raise LiveRunLocked(f"a live demo run holds {lock} (pid {pid}); refusing to change "
                            "credentials, rooms or agents. Wait for it to finish.")


def load(path: Path = CREDS_FILE) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run `make setup` (scripts/setup.sh) first")
    return json.loads(path.read_text())


def save(data: dict[str, Any], path: Path = CREDS_FILE) -> None:
    require_no_live_run()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-", suffix=".tmp")  # 0600 from creation
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def agent(name: str, path: Path = CREDS_FILE) -> dict[str, str]:
    data = load(path)
    try:
        return data["agents"][name]
    except KeyError:
        raise KeyError(f"agent {name!r} not in {path}; known: {sorted(data.get('agents', {}))}") from None
