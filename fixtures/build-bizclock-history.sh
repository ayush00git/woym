#!/usr/bin/env bash
# Build the bizclock fixture's git history into a fresh (non-bare) repo.
#
#   fixtures/build-bizclock-history.sh <out-dir>
#
# fixtures/bizclock/ holds the library as it is on `main` (2.3.2-dev). This
# script replays a small, made-up but plausible history from it with fixed
# authors and dates, so every machine gets the same commit shas:
#
#   v2.3.1 (tag)  the release the customer runs. Has the whole-hour TZ bug.
#   main          2.3.2-dev: adds formatCutoff(). Unrelated; the bug is still there.
#
# The repo is a demo fixture, not a real project. The people named below are
# fictional.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/bizclock" && pwd)"
OUT="${1:?usage: $0 <out-dir>}"

if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  echo "error: $OUT exists and is not empty" >&2
  exit 1
fi
mkdir -p "$OUT"
cd "$OUT"

PRIYA="Priya Raman <priya@bizclock.dev>"
TOM="Tom Becker <tom@bizclock.dev>"

# Deterministic git: no user config, hooks, signing or templates.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
g() { git -c core.hooksPath=/dev/null -c commit.gpgsign=false -c tag.gpgsign=false \
          -c init.defaultBranch=main -c core.autocrlf=false "$@"; }

commit() { # commit "<author>" "<iso date>" "<message>"
  local name="${1% <*}" email="${1##*<}"
  email="${email%>}"
  g add -A
  GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" GIT_AUTHOR_DATE="$2" \
  GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" GIT_COMMITTER_DATE="$2" \
    g commit -q -m "$3"
}

take() { # copy files from the fixture, keeping paths
  for f in "$@"; do mkdir -p "$(dirname "$f")"; cp "$SRC/$f" "$f"; done
}

set_version() { sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"$1\"/" package.json; }

# The 2.3.x index.js has no formatCutoff export.
index_without_format() {
  mkdir -p src
  grep -v "require('./format')" "$SRC/src/index.js" > src/index.js
}

# CHANGELOG without the "Unreleased" section.
changelog_released() {
  awk '/^## Unreleased/{skip=1; next} /^## /{skip=0} !skip' "$SRC/CHANGELOG.md" > CHANGELOG.md
}

g init -q -b main .

# 1. Initial import
take package.json LICENSE .gitignore README.md
index_without_format
set_version 2.3.0
sed -i 's|"test": "node --test"|"test": "echo \\"no tests yet\\""|' package.json
commit "$PRIYA" "2025-02-03T10:12:00+01:00" "Initial import of bizclock 2.3.0

nextCutoff() and isBusinessDay(), split out of the payments service."

# 2. Tests
take package.json test/cutoff.test.js
set_version 2.3.0
commit "$PRIYA" "2025-02-04T16:40:00+01:00" "test: cover weekends, exact cutoff and custom cutoff times"

# 3. CI
take .github/workflows/ci.yml
commit "$TOM" "2025-02-10T09:05:00+00:00" "ci: run tests on node 20 and 22"

# 4. Example
take examples/payout.js
commit "$TOM" "2025-03-18T14:22:00+00:00" "docs: add payout example

Mirrors the support question we get most: when does a late-Friday order
get paid?"

# 5. Release 2.3.1
changelog_released
set_version 2.3.1
commit "$PRIYA" "2025-04-02T11:30:00+02:00" "chore: release 2.3.1"
GIT_COMMITTER_NAME="Priya Raman" GIT_COMMITTER_EMAIL="priya@bizclock.dev" \
GIT_COMMITTER_DATE="2025-04-02T11:31:00+02:00" \
  g tag -a v2.3.1 -m "bizclock 2.3.1"

# 6. Start the next version
set_version 2.3.2-dev
commit "$PRIYA" "2025-04-02T11:35:00+02:00" "chore: start 2.3.2-dev"

# 7. Unrelated feature on main
take src/index.js src/format.js test/format.test.js CHANGELOG.md
commit "$TOM" "2025-06-12T17:48:00+00:00" "feat: add formatCutoff() for dashboard labels"

# Sanity: the working tree must now match the fixture exactly.
if ! diff -r -q --exclude=.git "$SRC" . >/dev/null; then
  echo "error: rebuilt main does not match fixtures/bizclock:" >&2
  diff -r -q --exclude=.git "$SRC" . >&2 || true
  exit 1
fi

echo "built $(g rev-list --count main) commits in $OUT"
echo "main   $(g rev-parse main)"
echo "v2.3.1 $(g rev-parse 'v2.3.1^{commit}')"
