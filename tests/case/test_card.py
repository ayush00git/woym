from casefx import CUSTOMER, VENDOR, report, run
from woym.case import card
from woym.contracts import ENV_KEYS, FixResult


def rows(text):
    return [ln for ln in text.splitlines() if ln.startswith("| ") and "---" not in ln and ":--" not in ln]


def test_not_reproduced():
    t = card.not_reproduced(42, run(VENDOR, True))
    assert "NOT REPRODUCED" in t and "case #42" in t
    assert "TZ=UTC" in t and "payout=Fri" in t
    assert len(t.splitlines()) <= 5


def test_lineup_has_five_rows_in_order_all_pending():
    t = card.lineup(42, VENDOR, CUSTOMER)
    body = rows(t)[1:]
    assert [r.split("|")[2].strip() for r in body] == list(ENV_KEYS)
    assert all(card.PENDING in r for r in body)
    assert "5 of 5 differ" in t
    assert "`UTC` → `Asia/Kolkata`" in t
    assert "CULPRIT" not in t


def test_bisect_rows_flip_and_culprit_marked():
    t = card.bisect(42, report())
    body = rows(t)[1:]
    marks = [card.PASS if card.PASS in r else card.FAIL for r in body]
    assert marks == [card.PASS] * 4 + [card.FAIL]
    assert "CULPRIT" in body[-1] and "**tz**" in body[-1]
    assert sum("CULPRIT" in r for r in body) == 1
    assert "culprit: `tz`" in t.splitlines()[0]
    assert "7 sandbox runs" in t


def test_suspect_table_same_values():
    t = card.suspect_table(VENDOR, VENDOR.with_value("tz", "Asia/Kolkata"))
    assert t.count("(same)") == 4


def test_bisect_no_culprit():
    r = report()
    r.culprits.clear()
    assert "no single culprit" in card.bisect(1, r)
    assert "CULPRIT" not in card.bisect(1, r)


def test_confirmed_both():
    t = card.confirmed_both(
        42, "tz",
        vendor_fail=run(CUSTOMER, False),
        override_run=run(CUSTOMER.with_value("tz", "UTC"), True, "customer-machine"),
        real_run=run(CUSTOMER, False, "customer-machine"),
        override_value="UTC",
    )
    assert "CONFIRMED ON BOTH MACHINES" in t
    assert "`tz=Asia/Kolkata`" in t
    assert len(rows(t)) == 4


def test_not_confirmed_when_customer_override_also_fails():
    t = card.confirmed_both(1, "tz", run(CUSTOMER, False), run(CUSTOMER, False), run(CUSTOMER, False), "UTC")
    assert "NOT CONFIRMED" in t and "CONFIRMED ON BOTH" not in t


def test_fix_card_labels_fixture_mode():
    f = FixResult("fix/case-42", "0123456789abcdef", ["src/cutoff.js", "test/cutoff.test.js"],
                  "cutoff honours half-hour zones", "fixture", "Use minute offsets")
    t = card.fix(42, f)
    assert "FIXTURE" in t and "`0123456789`" in t and "fix/case-42" in t
    assert "FIXTURE" not in card.fix(42, FixResult(**{**f.__dict__, "mode": "claude"}))


def test_matrix_green_and_red():
    envs = [VENDOR, CUSTOMER, VENDOR.with_value("tz", "Asia/Kathmandu")]
    green = card.matrix(42, "abcdef1234567", [run(e, True) for e in envs])
    assert "Matrix green" in green and "3/3" in green and len(rows(green)) == 4
    red = card.matrix(42, "abc", [run(envs[0], True), run(envs[1], False)])
    assert "Matrix red" in red and "1/2" in red
    assert "Matrix red" in card.matrix(1, "x", [])


def test_fixed_card():
    t = card.fixed(42, "0123456789abcdef", run(CUSTOMER, True, "customer-machine"), "meera")
    assert t.startswith("### ✅ FIXED ON MEERA'S MACHINE")


def test_consent_prompt_lists_exact_values():
    t = card.consent_prompt(42, "meera", CUSTOMER.as_dict(), "triage")
    for v in CUSTOMER.as_dict().values():
        assert f"`{v}`" in t
    assert "@meera" in t and "yes" in t


def test_cells_escape_pipes_and_backticks():
    bad = run(VENDOR, True)
    bad = bad.__class__(**{**bad.__dict__, "actual": "a|b`c"})
    t = card.not_reproduced(1, bad)
    assert "`a\\|b'c`" in t


def _all_cards():
    return [
        card.lineup(1, VENDOR, CUSTOMER),
        card.bisect(1, report()),
        card.confirmed_both(1, "tz", run(CUSTOMER, False), run(CUSTOMER, True), run(CUSTOMER, False), "UTC"),
        card.matrix(1, "abc", [run(VENDOR, True)]),
        card.consent_prompt(1, "meera", CUSTOMER.as_dict(), "triage"),
    ]


def test_tables_are_separated_by_blank_lines():
    """GFM/Mattermost would swallow a following text line as a table row."""
    for t in _all_cards():
        lines = t.splitlines()
        for i, ln in enumerate(lines):
            is_row = ln.startswith("|")
            if is_row and i > 0 and not lines[i - 1].startswith("|"):
                assert lines[i - 1] == "", t
            if is_row and i + 1 < len(lines) and not lines[i + 1].startswith("|"):
                assert lines[i + 1] == "", t
