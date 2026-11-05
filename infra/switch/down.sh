#!/usr/bin/env bash
# Stops the stack. Pass -v to also delete volumes (all users, keys, history).
set -euo pipefail
cd "$(dirname "$0")"
docker compose --env-file .env -f standalone-docker-compose.yml down "$@"
