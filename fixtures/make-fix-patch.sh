#!/usr/bin/env bash
# Regenerate fixtures/bizclock-fix.patch: the reference fix for the
# whole-hour offset bug plus a regression test. Used by FIXER=fixture mode
# (`git apply` or `git am` in a clone of main; it also applies to v2.3.1).
#
#   fixtures/make-fix-patch.sh
#
# It checks that the new test fails before the fix and passes after it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"$HERE/build-bizclock-history.sh" "$TMP/repo" >/dev/null
cd "$TMP/repo"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

cat > test/cutoff-tz.test.js <<'EOF'
'use strict';

// Regression: zones whose UTC offset is not a whole number of hours.
// nextCutoff() used to round the offset to whole hours, so IST (+05:30) was
// treated as +06:00 and a Friday 17:10 order missed the 17:30 cutoff.

const test = require('node:test');
const assert = require('node:assert/strict');
const { nextCutoff } = require('..');

function inZone(tz, fn) {
  const saved = process.env.TZ;
  process.env.TZ = tz;
  try {
    fn();
  } finally {
    if (saved === undefined) delete process.env.TZ;
    else process.env.TZ = saved;
  }
}

const zones = ['Asia/Kolkata', 'Asia/Kathmandu', 'America/St_Johns', 'Australia/Adelaide', 'UTC'];

for (const tz of zones) {
  test(`Friday 17:10 is before the 17:30 cutoff in ${tz}`, () => {
    inZone(tz, () => {
      const order = new Date(2026, 10, 6, 17, 10);
      assert.deepEqual(nextCutoff(order), new Date(2026, 10, 6, 17, 30));
    });
  });

  test(`Friday 17:40 rolls to Monday 17:30 in ${tz}`, () => {
    inZone(tz, () => {
      const order = new Date(2026, 10, 6, 17, 40);
      assert.deepEqual(nextCutoff(order), new Date(2026, 10, 9, 17, 30));
    });
  });
}
EOF

echo "== new test before the fix (must fail)"
if TZ=Asia/Kolkata node --test test/cutoff-tz.test.js >"$TMP/before.log" 2>&1; then
  echo "error: regression test passed before the fix" >&2
  exit 1
fi
grep -E '^ℹ (pass|fail)' "$TMP/before.log"

# The fix: compute in local wall-clock time with local getters/setters
# instead of shifting by a (rounded) offset. setDate() keeps the wall-clock
# time across DST changes too.
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("src/index.js")
s = p.read_text()

s = s.replace('''// Offset of the host zone from UTC at `date`, in hours.
function localOffsetHours(date) {
  return Math.round(-date.getTimezoneOffset() / 60);
}

''', "")

old_body = s[s.index("  // Shift into \"local wall-clock\" time"):s.index("function isWeekendUTC")]
new_body = '''  // Work in local wall-clock time directly. Offsets are not always whole
  // hours (IST is +05:30, Nepal +05:45), so never shift by a rounded offset.
  const candidate = new Date(
    date.getFullYear(), date.getMonth(), date.getDate(), cutoff.hours, cutoff.minutes
  );
  while (candidate < date || !isBusinessDay(candidate)) {
    candidate.setDate(candidate.getDate() + 1);
  }
  return candidate;
}

'''
s = s.replace(old_body, new_body)
s = re.sub(r"function isWeekendUTC\(ms\) \{.*?\n\}\n\n", "", s, flags=re.S)
s = s.replace("const HOUR_MS = 60 * 60 * 1000;\nconst MINUTE_MS = 60 * 1000;\nconst DAY_MS = 24 * HOUR_MS;\n\n", "")
p.write_text(s)
PY

echo "== after the fix, in several zones (must pass)"
for tz in UTC Asia/Kolkata Asia/Kathmandu America/St_Johns America/New_York; do
  TZ=$tz node --test >"$TMP/after.log" 2>&1 || { cat "$TMP/after.log"; echo "error: tests fail in $tz after the fix" >&2; exit 1; }
  echo "$tz: $(grep -E '^ℹ pass' "$TMP/after.log")"
  TZ=$tz node examples/payout.js
done

g() { git -c core.hooksPath=/dev/null -c commit.gpgsign=false "$@"; }
BASE="$(g rev-parse HEAD)"
g add -A
GIT_AUTHOR_NAME="WOYM reference fix" GIT_AUTHOR_EMAIL="fixture@woym.invalid" \
GIT_AUTHOR_DATE="2026-11-05T19:30:00+05:30" \
GIT_COMMITTER_NAME="WOYM reference fix" GIT_COMMITTER_EMAIL="fixture@woym.invalid" \
GIT_COMMITTER_DATE="2026-11-05T19:30:00+05:30" \
  g commit -q -m "fix: use exact local time in nextCutoff(), not a whole-hour offset

nextCutoff() shifted dates by Math.round(-getTimezoneOffset() / 60) hours.
In zones with a fractional offset (Asia/Kolkata +05:30) that is off by 30
minutes, so a Friday 17:10 order was treated as 17:40 and rolled to Monday.

Compute the cutoff with local getters/setters instead, and add a regression
test that runs in several non-whole-hour zones.

This is the stored reference fix used by WOYM's FIXER=fixture mode."

g format-patch -1 --stdout --no-signature > "$HERE/bizclock-fix.patch"

# The patch must apply cleanly to both refs.
# (main has already moved to the fix commit here, so check its parent.)
for ref in "$BASE" v2.3.1; do
  g checkout -q --detach "$ref"
  g apply --check "$HERE/bizclock-fix.patch"
  echo "applies cleanly to $(g describe --tags --always "$ref") ($ref)"
done
echo "wrote $HERE/bizclock-fix.patch"
