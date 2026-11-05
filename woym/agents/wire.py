"""Encode contract objects into tag key/values and back (agent payloads).

Env keys get a prefix: v_ for the vendor env, c_ for the customer env, so
`v_node=22 v_tz=UTC c_node=20 c_tz=Asia/Kolkata`. A run adds `passed`,
`actual`, `expected`, `exit`, `dur` under its own prefix.
"""

from __future__ import annotations

from woym.case import tags
from woym.contracts import ENV_KEYS, Env, RunResult
from woym.sandbox.runner import EXPECTED  # the repro's correct answer, same for every env


def env_kv(prefix: str, env: Env) -> dict[str, str]:
    return {f"{prefix}{k}": getattr(env, k) for k in ENV_KEYS}


def env_from(prefix: str, kv: dict[str, str]) -> Env:
    try:
        return Env(**{k: kv[f"{prefix}{k}"] for k in ENV_KEYS})
    except KeyError as e:
        raise ValueError(f"missing env field {e.args[0]!r}") from None


def run_kv(prefix: str, run: RunResult, dur: bool = False) -> dict[str, object]:
    """Only what isn't implied: `expected` is dropped when it is the standard
    repro answer, `exit` when it is the usual 0/1 for pass/fail."""
    kv: dict[str, object] = {f"{prefix}passed": run.passed, f"{prefix}actual": run.actual}
    if run.expected != EXPECTED:
        kv[f"{prefix}expected"] = run.expected
    if run.exit_code != (0 if run.passed else 1):
        kv[f"{prefix}exit"] = run.exit_code
    if dur:
        kv[f"{prefix}dur"] = f"{run.duration_s:.1f}"
    return kv


def run_from(prefix: str, kv: dict[str, str], env: Env, where: str) -> RunResult:
    try:
        passed = bool(tags.as_bool(kv[f"{prefix}passed"]))
    except KeyError as e:
        raise ValueError(f"missing run field {e.args[0]!r}") from None
    return RunResult(
        env=env,
        passed=passed,
        expected=kv.get(f"{prefix}expected", EXPECTED),
        actual=kv.get(f"{prefix}actual", ""),
        exit_code=int(kv.get(f"{prefix}exit", "0" if passed else "1")),
        duration_s=float(kv.get(f"{prefix}dur", "0")),
        log_tail="",
        where=where,
    )


def results_kv(results: dict[str, bool]) -> str:
    """{"node": True, "lang+tz": False} -> "node:1,lang+tz:0"."""
    return ",".join(f"{k}:{'1' if v else '0'}" for k, v in results.items())


def results_from(value: str | None) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for item in tags.as_list(value):
        key, _, v = item.rpartition(":")
        out[key] = v == "1"
    return out
