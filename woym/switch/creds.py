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
from pathlib import Path
from typing import Any

from woym.config import VAR

CREDS_FILE = VAR / "switch" / "credentials.json"


def load(path: Path = CREDS_FILE) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run `make setup` (scripts/setup.sh) first")
    return json.loads(path.read_text())


def save(data: dict[str, Any], path: Path = CREDS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def agent(name: str, path: Path = CREDS_FILE) -> dict[str, str]:
    data = load(path)
    try:
        return data["agents"][name]
    except KeyError:
        raise KeyError(f"agent {name!r} not in {path}; known: {sorted(data.get('agents', {}))}") from None
