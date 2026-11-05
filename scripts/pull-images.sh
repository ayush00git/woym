#!/usr/bin/env bash
# Pre-pull every image the WOYM sandbox and customer machine use, so the demo
# never waits on a download. Safe to re-run.
#
#   scripts/pull-images.sh            # node images + build woym/acme-laptop
#   scripts/pull-images.sh --no-build # node images only
#
# The Switch / Mattermost images are pulled by infra/switch (platform), not here.
set -euo pipefail

ms() { echo $(( $(date +%s%N) / 1000000 )); }
secs() { local d=$(( $(ms) - $1 )); printf '%d.%01ds' $((d / 1000)) $((d % 1000 / 100)); }

IMAGES=(
  node:22-alpine          # vendor CI env (VENDOR_ENV)
  node:20-bookworm-slim   # customer env in the vendor sandbox; base of acme-laptop
  node:20-alpine          # matrix / bisect variants
  node:22-bookworm-slim
)

for img in "${IMAGES[@]}"; do
  start=$(ms)
  docker pull -q "$img" >/dev/null
  printf '%-24s ok  %s\n' "$img" "$(secs "$start")"
done

if [ "${1:-}" != "--no-build" ]; then
  root="$(cd "$(dirname "$0")/.." && pwd)"
  start=$(ms)
  docker build -q -t woym/acme-laptop:latest "$root/infra/acme-laptop" >/dev/null
  printf '%-24s ok  %s\n' "woym/acme-laptop (build)" "$(secs "$start")"
fi
