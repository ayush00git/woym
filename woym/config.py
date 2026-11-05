"""Paths and settings shared by every component. Owned by the lead."""

from __future__ import annotations

import os
from pathlib import Path

from woym.contracts import Env

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, no overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

# Runtime state (gitignored)
VAR = ROOT / "var"
REMOTE = VAR / "remotes" / "bizclock.git"  # stands in for the vendor's GitHub
WORK = VAR / "work"  # throwaway clones (fixer)
CASES_DB = VAR / "cases.json"
LOGS = VAR / "logs"

# Fixtures
BIZCLOCK_SRC = ROOT / "fixtures" / "bizclock"  # the vendor library with the planted bug
BIZCLOCK_FIX_PATCH = ROOT / "fixtures" / "bizclock-fix.patch"  # FIXER=fixture mode

# Switch / chat
SWITCH_URL = os.environ.get("SWITCH_URL", "http://localhost:8000")
MATTERMOST_URL = os.environ.get("MATTERMOST_URL", "http://localhost:8065")
ROOM_NAME = os.environ.get("WOYM_ROOM", "acme-support")

# Who is who (chat handles, without "@")
VENDOR_HUMAN = os.environ.get("WOYM_VENDOR_HUMAN", "dev")
CUSTOMER_HUMAN = os.environ.get("WOYM_CUSTOMER_HUMAN", "meera")
AGENT_TRIAGE = "triage"
AGENT_RUNNER = "runner"
AGENT_FIXER = "fixer"
AGENT_CUSTOMER = "acme-env"

# Customer machine (a long-running container owned by the customer side)
CUSTOMER_CONTAINER = os.environ.get("WOYM_CUSTOMER_CONTAINER", "acme-laptop")

# Vendor CI environment: what "works for me" means
VENDOR_ENV = Env(node="22", lang="en_US.UTF-8", os="alpine", bizclock="main", tz="UTC")

# Modes
FIXER_MODE = os.environ.get("FIXER", "claude")  # "claude" | "fixture"
TRIAGE_LLM = os.environ.get("TRIAGE_LLM", "off")  # "off" | "claude"
MAX_HOPS_PER_CASE = 20  # 16 hops in the full flow (docs/contracts.md §3) + slack
MIN_SECONDS_BETWEEN_SENDS = 2.0
RUN_TIMEOUT_S = 90
