#!/usr/bin/env bash
# Fresh-clone test: does the judge path work from a clean checkout?
#
#   scripts/fresh-clone-test.sh            # make sync test images customer-up
#   scripts/fresh-clone-test.sh --full     # ...then make up setup demo (needs the Switch stack approved)
#   scripts/fresh-clone-test.sh --keep     # keep the clone dir even on success
#
# It clones the COMMITTED state (HEAD) of this repo into a temp dir, so
# uncommitted work isn't tested. Each step is timed; the first failure stops
# the run, prints the log tail and keeps the clone for inspection.
#
# The clone's customer laptop uses its own container name (acme-laptop-fresh),
# so the live demo's acme-laptop isn't touched. It's removed at the end.
# With --full, `make up` uses the same ports as a running stack: run
# `make down` in the main checkout first.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
FULL=0; KEEP=0
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    --keep) KEEP=1 ;;
    *) echo "usage: $0 [--full] [--keep]" >&2; exit 2 ;;
  esac
done

DIR="$(mktemp -d "${TMPDIR:-/tmp}/woym-fresh-XXXXXX")"
LOGS="$DIR/_logs"; mkdir -p "$LOGS"
export WOYM_CUSTOMER_CONTAINER=acme-laptop-fresh
export FIXER="${FIXER:-fixture}"

ms() { echo $(( $(date +%s%N) / 1000000 )); }
fmt() { printf '%d.%01ds' $(($1 / 1000)) $(($1 % 1000 / 100)); }
T0=$(ms)
ROWS=()

cleanup() {
  docker rm -f "$WOYM_CUSTOMER_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

run() { # run <name> <command...>   (in the clone)
  local name="$1"; shift
  local s; s=$(ms)
  printf '%-14s ' "$name"
  if (cd "$DIR/woym" && "$@") >"$LOGS/$name.log" 2>&1; then
    local d=$(( $(ms) - s )); ROWS+=("$(printf '%-14s ok    %s' "$name" "$(fmt $d)")")
    echo "ok    $(fmt $d)"
  else
    local code=$? d=$(( $(ms) - s ))
    echo "FAIL  $(fmt $d) (exit $code)"
    echo "---- last 25 lines of $LOGS/$name.log ----"
    tail -25 "$LOGS/$name.log"
    echo "clone kept at $DIR/woym"
    exit 1
  fi
}

echo "fresh-clone test of $(git -C "$SRC" rev-parse --short HEAD) ($(git -C "$SRC" log -1 --format=%s))"
[ -n "$(git -C "$SRC" status --porcelain)" ] && echo "note: the working tree has uncommitted changes; they are NOT in the clone"
echo "clone: $DIR/woym"

s=$(ms)
git clone -q "$SRC" "$DIR/woym" || { echo "git clone failed"; exit 1; }
ROWS+=("$(printf '%-14s ok    %s' clone "$(fmt $(( $(ms) - s )))")"); echo "clone          ok    $(fmt $(( $(ms) - s )))"

# What a judge needs on PATH before anything else.
for tool in git docker uv make; do
  command -v "$tool" >/dev/null || { echo "missing prerequisite: $tool"; exit 1; }
done

run sync        make sync
run test        make test
run images      make images
# NAME must be passed: the Makefile defaults it to acme-laptop, which is the live demo container
run customer-up make customer-up NAME="$WOYM_CUSTOMER_CONTAINER"
# The customer laptop must report exactly the demo facts.
run probe       sh -c 'out="$(uv run python -m woym.sandbox.customer probe)" && echo "$out" &&
  [ "$out" = '"'"'{"node": "20", "lang": "en_IN.UTF-8", "os": "bookworm", "bizclock": "v2.3.1", "tz": "Asia/Kolkata"}'"'"' ]'
run repro-ist    sh -c 'uv run python -m woym.sandbox.customer repro | tee /dev/stderr | grep -q "^cutoff=Mon 17:00 payout=Mon passed=False"'
run repro-utc    sh -c 'uv run python -m woym.sandbox.customer repro --tz UTC | tee /dev/stderr | grep -q "^cutoff=Fri 17:30 payout=Fri passed=True"'

if [ $FULL = 1 ]; then
  [ -f "$DIR/woym/.env.example" ] && [ ! -f "$DIR/woym/.env" ] && cp "$DIR/woym/.env.example" "$DIR/woym/.env"
  run up      make up
  run setup   make setup
  run demo    make demo
fi

echo
echo "== fresh-clone summary ($(fmt $(( $(ms) - T0 ))) total)"
printf '%s\n' "${ROWS[@]}"
grep -h -E '^[0-9]+ passed|passed' "$LOGS/test.log" | tail -1 | sed 's/^/tests: /'

if [ $KEEP = 1 ]; then echo "clone kept at $DIR/woym"; else rm -rf "$DIR"; fi
