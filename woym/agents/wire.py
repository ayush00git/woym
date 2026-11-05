"""Encode contract objects into tag key/values and back (agent payloads).

Env keys get a prefix: v_ for the vendor env, c_ for the customer env, so
`v_node=22 v_tz=UTC c_node=20 c_tz=Asia/Kolkata`. A run adds `passed`,
`actual`, `expected`, `exit`, `dur` under its own prefix.
"""

from __future__ import annotations

from woym.case import tags
from woym.contracts import ENV_KEYS, Env, RunResult


def env_kv(prefix: str, env: Env) -> dict[str, str]:
    return {f"{prefix}{k}": getattr(env, k) for k in ENV_KEYS}


def env_from(prefix: str, kv: dict[str, str]) -> Env:
    try:
        return Env(**{k: kv[f"{prefix}{k}"] for k in ENV_KEYS})
    except KeyError as e:
        raise ValueError(f"missing env field {e.args[0]!r}") from None


def run_kv(prefix: str, run: RunResult) -> dict[str, object]:
    return {
        f"{prefix}passed": run.passed,
        f"{prefix}actual": run.actual,
        f"{prefix}expected": run.expected,
        f"{prefix}exit": run.exit_code,
        f"{prefix}dur": f"{run.duration_s:.2f}",
    }


def run_from(prefix: str, kv: dict[str, str], env: Env, where: str) -> RunResult:
    try:
        return RunResult(
            env=env,
            passed=bool(tags.as_bool(kv[f"{prefix}passed"])),
            expected=kv.get(f"{prefix}expected", ""),
            actual=kv.get(f"{prefix}actual", ""),
            exit_code=int(kv.get(f"{prefix}exit", "0")),
            duration_s=float(kv.get(f"{prefix}dur", "0")),
            log_tail="",
            where=where,
        )
    except KeyError as e:
        raise ValueError(f"missing run field {e.args[0]!r}") from None


def results_kv(results: dict[str, bool]) -> str:
    """{"node": True, "lang+tz": False} -> "node:1,lang+tz:0"."""
    return ",".join(f"{k}:{'1' if v else '0'}" for k, v in results.items())


def results_from(value: str | None) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for item in tags.as_list(value):
        key, _, v = item.rpartition(":")
        out[key] = v == "1"
    return out
