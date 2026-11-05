#!/usr/bin/env bash
# Starts Switch 0.31.0 + Mattermost (collab) + Gateway (gateway) and waits for health.
set -euo pipefail
cd "$(dirname "$0")"
./gen-env.sh
docker compose --env-file .env -f standalone-docker-compose.yml up -d
echo -n "waiting for switch api on :8000"
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then echo " ok"; break; fi
  echo -n "."; sleep 2
done
echo -n "waiting for mattermost on :8065"
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8065/api/v4/system/ping >/dev/null 2>&1; then echo " ok"; break; fi
  echo -n "."; sleep 2
done
docker compose --env-file .env -f standalone-docker-compose.yml ps
