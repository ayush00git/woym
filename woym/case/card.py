"""Mattermost-markdown cards: the visuals of the case thread.

Pure functions of contract types; no I/O. Cards are compact: a one-line
header, at most one table, and a one-line footer.
"""

from __future__ import annotations

from woym.contracts import ENV_KEYS, BisectReport, Env, FixResult, RunResult

PASS = "✅"
FAIL = "❌"
PENDING = "⏳"
SAME = "➖"

KEY_LABEL = {"node": "node", "lang": "lang", "os": "os", "bizclock": "bizclock", "tz": "tz"}


def _code(v: str) -> str:
    v = str(v).replace("`", "'").replace("|", "\\|")
    return f"`{v}`"


def _cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _mark(passed: bool | None) -> str:
    return PENDING if passed is None else (PASS if passed else FAIL)


def _header(emoji: str, title: str, case_id: int, extra: str = "") -> str:
    tail = f" · {extra}" if extra else ""
    return f"#### {emoji} {title} · case #{case_id}{tail}"


# --------------------------------------------------------------------------
# 4. NOT REPRODUCED
# --------------------------------------------------------------------------


def not_reproduced(case_id: int, run: RunResult) -> str:
    e = run.env
    return "\n".join(
        [
            _header(PASS, "NOT REPRODUCED in vendor sandbox", case_id),
            f"Repro **passed** on {_code(f'node {e.node} · {e.os} · {e.lang} · TZ={e.tz} · bizclock {e.bizclock}')} "
            f"in {run.duration_s:.1f}s.",
            f"Got {_code(run.actual)} · expected {_code(run.expected)}",
            "_Works on our machine. Asking the customer's agent for their environment._",
        ]
    )


def reproduced(case_id: int, run: RunResult) -> str:
    return "\n".join(
        [
            _header(FAIL, "REPRODUCED in vendor sandbox", case_id),
            f"Got {_code(run.actual)} · expected {_code(run.expected)} · exit {run.exit_code}",
        ]
    )


# --------------------------------------------------------------------------
# 9 + 11. Suspect lineup, then bisect (same table, rows flip)
# --------------------------------------------------------------------------


def suspect_table(
    vendor: Env,
    customer: Env,
    results: dict[str, bool | None] | None = None,
    culprits: list[str] | tuple[str, ...] = (),
) -> str:
    """Rows node/lang/os/bizclock/tz: vendor → customer, then the result of
    running the vendor env with ONLY that key flipped to the customer value."""
    results = results or {}
    # pair culprits come as "a+b": mark each member row
    culprit_keys = {k for c in culprits for k in c.split("+")}
    paired = {k for c in culprits if "+" in c for k in c.split("+")}
    rows = ["| | key | vendor → customer | alone |", "|:-:|:--|:--|:-:|"]
    for key in ENV_KEYS:
        v, c = getattr(vendor, key), getattr(customer, key)
        if v == c:
            rows.append(f"| {SAME} | {key} | {_code(v)} (same) | {SAME} |")
            continue
        passed = results.get(key)
        culprit = key in culprit_keys
        label = f"**{key}**" if culprit else key
        change = f"{_code(v)} → {_code(c)}"
        tag = " **← CULPRIT (pair)**" if key in paired else " **← CULPRIT**"
        result = _mark(passed) + (tag if culprit else "")
        icon = "🎯" if culprit else "🔸"
        rows.append(f"| {icon} | {label} | {change} | {result} |")
    return "\n".join(rows)


def lineup(case_id: int, vendor: Env, customer: Env) -> str:
    n = len(vendor.diff(customer))
    return "\n".join(
        [
            _header("🧑‍⚖️", "Suspect lineup", case_id, f"{n} of {len(ENV_KEYS)} differ"),
            "",
            suspect_table(vendor, customer),
            "",
            "_Customer shared these values with consent. Bisecting in the vendor sandbox: one value at a time._",
        ]
    )


def bisect(case_id: int, report: BisectReport) -> str:
    vendor, customer = report.baseline.env, report.target.env
    results = {s.key: s.result.passed for s in report.steps}
    if report.culprits:
        extra = "culprit: " + ", ".join(f"`{k}`" for k in report.culprits)
    elif not report.steps:
        extra = "skipped"
    else:
        extra = "no culprit found"
    runs = 2 + len(report.steps)
    footer = (
        f"Vendor env {_mark(report.baseline.passed)} · full customer env in our sandbox "
        f"{_mark(report.target.passed)} · {runs} sandbox runs"
    )
    if not report.steps:
        why = ("the vendor env already fails" if not report.baseline.passed
               else "the customer env passes in our sandbox too")
        footer += f"\n_Bisect skipped: {why}, so there is nothing to isolate._"
    pairs = [s for s in report.steps if "+" in s.key]
    if pairs:
        footer += "\nPairs tried: " + ", ".join(f"`{s.key}` {_mark(s.result.passed)}" for s in pairs)
    return "\n".join(
        [
            _header("🔎", "Bisect", case_id, extra),
            "",
            suspect_table(vendor, customer, results, report.culprits),
            "",
            footer,
        ]
    )


# --------------------------------------------------------------------------
# 13. CONFIRMED ON BOTH MACHINES
# --------------------------------------------------------------------------


def confirmed_both(
    case_id: int,
    key: str,
    vendor_fail: RunResult,
    override_run: RunResult,
    real_run: RunResult,
    override_value: str,
) -> str:
    real_value = getattr(real_run.env, key)
    rows = [
        "| machine | " + key + " | result |",
        "|:--|:--|:-:|",
        f"| vendor sandbox | {_code(real_value)} | {_mark(vendor_fail.passed)} {_cell(vendor_fail.actual)} |",
        f"| customer machine | {_code(override_value)} | {_mark(override_run.passed)} {_cell(override_run.actual)} |",
        f"| customer machine | {_code(real_value)} | {_mark(real_run.passed)} {_cell(real_run.actual)} |",
    ]
    ok = (not vendor_fail.passed) and override_run.passed and (not real_run.passed)
    title = "CONFIRMED ON BOTH MACHINES" if ok else "NOT CONFIRMED"
    return "\n".join(
        [
            _header("🎯" if ok else "⚠️", title, case_id, f"`{key}={real_value}`"),
            "",
            *rows,
            "",
            f"_Flipping only `{key}` flips the result, on our sandbox and on the customer's own machine._"
            if ok
            else "_The customer-machine runs did not match the sandbox bisect._",
        ]
    )


# --------------------------------------------------------------------------
# 16. Fix, 18. matrix, 20. FIXED
# --------------------------------------------------------------------------


def fix(case_id: int, result: FixResult) -> str:
    mode = "Claude Code" if result.mode == "claude" else "⚠️ FIXTURE patch (replayed, not generated)"
    files = ", ".join(_code(f) for f in result.files_changed) or "_none_"
    return "\n".join(
        [
            _header("🛠️", "Fix pushed", case_id, mode),
            f"{_code(result.branch)} at {_code(result.sha[:10])} · test {_code(result.test_name)}",
            f"Files: {files}",
            f"> {_cell(result.summary)}",
        ]
    )


def matrix(case_id: int, ref: str, results: list[RunResult]) -> str:
    rows = ["| node | os | lang | tz | result |", "|:--|:--|:--|:--|:-:|"]
    for r in results:
        e = r.env
        rows.append(
            f"| {e.node} | {e.os} | {_code(e.lang)} | {_code(e.tz)} | {_mark(r.passed)} {_cell(r.actual)} |"
        )
    green = bool(results) and all(r.passed for r in results)
    n_ok = sum(r.passed for r in results)
    return "\n".join(
        [
            _header(PASS if green else FAIL, "Matrix " + ("green" if green else "red"), case_id,
                    f"{n_ok}/{len(results)} at `{ref[:10]}`"),
            "",
            *rows,
        ]
    )


def fixed(case_id: int, sha: str, run: RunResult, customer: str) -> str:
    return "\n".join(
        [
            f"### {PASS} FIXED ON {customer.upper()}'S MACHINE · case #{case_id}",
            f"Installed bizclock {_code(sha[:10])} and re-ran the original repro: "
            f"{_code(run.actual)} · exit {run.exit_code}",
            f"_Posted by the customer's own agent, from the customer's machine._",
        ]
    )


def failed(case_id: int, why: str) -> str:
    return f"#### ⚠️ Case #{case_id} stopped\n{_cell(why)}"


# --------------------------------------------------------------------------
# 6. Consent prompt (asked by @acme-env, to its owner)
# --------------------------------------------------------------------------


def consent_prompt(case_id: int, customer: str, values: dict[str, str], requester: str, runs: int = 2,
                   runs_note: str | None = None) -> str:
    """`runs_note` replaces the repro-runs sentence (known-fix path: runs only if needed)."""
    rows = ["| fact | value |", "|:--|:--|"]
    for key in ENV_KEYS:
        if key in values:
            rows.append(f"| {key} | {_code(values[key])} |")
    return "\n".join(
        [
            f"#### 🔐 @{customer}, can I share this? · case #{case_id}",
            f"{requester} (vendor agent) asked for {len(values)} environment facts. Exactly these values would be sent:",
            "",
            *rows,
            "",
            (f"{runs_note} Nothing else leaves it." if runs_note else
             f"I'd also run the repro **{runs}×** on this machine (with `tz=UTC`, then as-is). Nothing else leaves it."),
            "**Reply `yes` in this thread** to allow, or `no` to refuse.",
        ]
    )


# --------------------------------------------------------------------------
# Known culprit (WOYM_KNOWN): answered from another case's verified fix
# --------------------------------------------------------------------------


def known(case_id: int, matched: str, rule: str, source_case: int, branch: str, sha: str) -> str:
    """`matched` is THIS customer's own value, e.g. "tz=Asia/Kathmandu (UTC+05:45)".
    Takes no Env from the earlier case: nothing about that customer leaks here."""
    return "\n".join(
        [
            _header("🧾", "KNOWN ISSUE · already fixed", case_id, f"matches case #{source_case}"),
            f"Your {_code(matched)}: the {rule[:1].lower() + rule[1:]}, the root cause found in case #{source_case}.",
            f"Fixed in {_code(branch)} at {_code(sha[:10])}, **verified on a customer machine by that customer's "
            "own agent**, with their consent. Switch attributes that FIXED message to their agent, and we re-checked it in room history.",
            "_0 sandbox runs, no vendor human. Matched by a rule written from case "
            f"#{source_case}'s root cause, not by a model._",
        ]
    )
