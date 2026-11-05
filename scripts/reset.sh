#!/usr/bin/env bash
# Put WOYM back in the pre-demo state, in a few seconds. Idempotent.
#
#   scripts/reset.sh      (make reset)
#
# 1. stops the agent processes started from THIS checkout (pidfiles in
#    var/run/, plus any `python -m woym.agents.*` whose cwd is this repo);
# 2. removes leftover sandbox containers (woym-run-*);
# 3. deletes fix/* branches from the bare remote (creates the remote if missing);
# 4. clears var/cases.json, var/work/ and var/logs/;
# 5. puts the customer laptop back on bizclock v2.3.1 (starts it if it's not running).
#
# It does NOT touch the Switch stack or Mattermost: old demo threads stay in
# the channel; the next demo opens a new thread.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VAR="$ROOT/var"
REMOTE="$VAR/remotes/bizclock.git"
CONTAINER="${WOYM_CUSTOMER_CONTAINER:-acme-laptop}"

ms() { echo $(( $(date +%s%N) / 1000000 )); }
T0=$(ms)
step() { printf '%-34s' "$1"; S=$(ms); }
done_() { local d=$(( $(ms) - S )); printf '%s (%d ms)\n' "${1:-ok}" "$d"; }

# --- 1. agents ---------------------------------------------------------------
step "stop agents"
pids=()
if compgen -G "$VAR/run/*.pid" >/dev/null; then
  for f in "$VAR"/run/*.pid; do
    pid="$(cat "$f" 2>/dev/null || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && pids+=("$pid")
    rm -f "$f"
  done
fi
# Strays without a pidfile: only real `python -m woym.agents.<name>` processes
# (argv checked exactly, so a shell whose command line merely mentions the
# pattern is never matched) running from this checkout.
is_agent() {
  local -a argv
  mapfile -d '' -t argv < "/proc/$1/cmdline" 2>/dev/null || return 1
  [[ "${argv[0]##*/}" =~ ^python[0-9.]*$ ]] && [ "${argv[1]:-}" = "-m" ] \
    && [[ "${argv[2]:-}" =~ ^woym\.agents\.[a-z_]+$ ]] \
    && [ "$(readlink "/proc/$1/cwd" 2>/dev/null)" = "$ROOT" ]
}
for pid in $(pgrep -f 'woym\.agents\.' || true); do
  [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ] && continue
  is_agent "$pid" && pids+=("$pid")
done
if [ ${#pids[@]} -gt 0 ]; then
  kill -TERM "${pids[@]}" 2>/dev/null || true
  for _ in $(seq 50); do
    alive=0; for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null && alive=1; done
    [ $alive = 0 ] && break; sleep 0.1
  done
  for p in "${pids[@]}"; do kill -KILL "$p" 2>/dev/null || true; done
  done_ "stopped ${#pids[@]}"
else
  done_ "none running"
fi

# --- 2. sandbox leftovers ----------------------------------------------------
step "remove leftover sandbox runs"
left="$(docker ps -aq --filter 'name=^woym-run-' 2>/dev/null || true)"
if [ -n "$left" ]; then docker rm -f $left >/dev/null; done_ "removed $(echo "$left" | wc -l)"; else done_ "none"; fi

# --- 3. bare remote ----------------------------------------------------------
step "fix/* branches on bare remote"
if [ ! -d "$REMOTE" ]; then
  uv run --quiet python -c "from woym.sandbox.remote import init_remote; init_remote()"
  done_ "created remote"
else
  n=0
  while read -r ref; do
    [ -n "$ref" ] || continue
    git --git-dir="$REMOTE" update-ref -d "$ref"; n=$((n + 1))
  done < <(git --git-dir="$REMOTE" for-each-ref --format='%(refname)' refs/heads/fix/)
  done_ "deleted $n"
fi

# --- 4. runtime state --------------------------------------------------------
step "clear cases, work clones, logs"
rm -f "$VAR/cases.json" "$VAR"/.cases.*.json
rm -rf "$VAR/work" "$VAR/logs"
done_

# --- 5. customer laptop ------------------------------------------------------
step "customer laptop on v2.3.1"
state="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo missing)"
if [ "$state" = "true" ]; then
  out="$(uv run --quiet python -m woym.sandbox.customer install v2.3.1)"
else
  out="$(uv run --quiet python -m woym.sandbox.customer up)"
fi
case "$out" in
  *'"bizclock": "v2.3.1"'*) done_ "ok ($([ "$state" = true ] && echo reinstalled || echo started))" ;;
  *) done_ "FAILED"; echo "$out" >&2; exit 1 ;;
esac

echo "reset done in $(( $(ms) - T0 )) ms"
