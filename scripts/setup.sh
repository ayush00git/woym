#!/usr/bin/env bash
# Idempotent bootstrap after `make up`: users, keys, agents, room, addressing,
# follow-ups off. Writes var/switch/credentials.json. See woym/switch/setup.py.
set -euo pipefail
cd "$(dirname "$0")/.."
curl -fsS http://127.0.0.1:8000/health >/dev/null || { echo "Switch is not up: run make up"; exit 1; }
exec uv run python -m woym.switch.setup "$@"
