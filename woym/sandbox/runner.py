"""Vendor sandbox: run the customer's repro in throwaway Docker containers.

Every run:
- streams `git archive <ref>` from the bare remote over stdin into a tmpfs
  /work (never a bind mount);
- has no network, a read-only root, all capabilities dropped,
  no-new-privileges, 128 pids, 512 MB of memory, uid 1000 and a 90 s timeout;
- sets TZ and LANG from the Env, picks node:<major>-alpine or
  node:<major>-bookworm-slim, and runs `node examples/payout.js`.

The repro prints `cutoff=<Day HH:MM> payout=<Day>` and exits 0 only when the
payout is Friday, so `passed` is exit code 0 plus a parsed line.

Infrastructure failures (Docker missing, image pull fails, timeout) and runs
that print no result line (the repro crashed) raise SandboxError. They are never reported as a failing run, because that would
look like a reproduced bug.
"""

from __future__ import annotations

import itertools
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from woym import config
from woym.contracts import ENV_KEYS, BisectReport, BisectStep, Env, RunResult
from woym.sandbox import remote

EXPECTED = "cutoff=Fri 17:30 payout=Fri"
REPRO_SCRIPT = "examples/payout.js"
MAX_BISECT_RUNS = 10  # including the baseline and target runs

_LINE_RE = re.compile(r"^cutoff=.+ payout=[A-Za-z]{3}$")
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9._/+:-]{1,64}$")
_OS_IMAGE = {"alpine": "alpine", "bookworm": "bookworm-slim"}


class SandboxError(RuntimeError):
    """The sandbox itself failed; nothing is known about the repro."""


def image_for(env: Env) -> str:
    if not env.node.isdigit():
        raise SandboxError(f"node must be a major version like '22', got {env.node!r}")
    try:
        flavour = _OS_IMAGE[env.os]
    except KeyError:
        raise SandboxError(f"unsupported os {env.os!r}; expected one of {sorted(_OS_IMAGE)}") from None
    return f"node:{env.node}-{flavour}"


def parse_output(output: str) -> str:
    """The last `cutoff=... payout=...` line, or "" if there is none."""
    for line in reversed(output.strip().splitlines()):
        if _LINE_RE.match(line.strip()):
            return line.strip()
    return ""


def tail(output: str, n: int = 20) -> str:
    return "\n".join(output.rstrip().splitlines()[-n:])


def docker_command(env: Env, name: str) -> list[str]:
    for key in ("tz", "lang"):
        if not _SAFE_VALUE_RE.match(getattr(env, key)):
            raise SandboxError(f"refusing unsafe {key} value {getattr(env, key)!r}")
    return [
        "docker", "run", "-i", "--rm", "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/work:rw,size=64m,mode=1777",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "128",
        "--memory", "512m",
        "--user", "1000:1000",
        "--workdir", "/work",
        "-e", f"TZ={env.tz}",
        "-e", f"LANG={env.lang}",
        image_for(env),
        "sh", "-c", f"tar -x -f - && exec node {REPRO_SCRIPT}",
    ]  # fmt: skip


class DockerSandbox:
    """Implements woym.contracts.Sandbox."""

    def __init__(self, remote_path: Path | None = None, timeout_s: float = config.RUN_TIMEOUT_S):
        self.remote_path = Path(remote_path or config.REMOTE)
        self.timeout_s = timeout_s
        remote.init_remote(self.remote_path)

    def run(self, env: Env, ref: str | None = None) -> RunResult:
        if ref is not None:
            env = env.with_value("bizclock", ref)  # the result records what actually ran
        started = time.perf_counter()
        code = remote.archive(env.bizclock, self.remote_path)
        name = f"woym-run-{uuid.uuid4().hex[:12]}"
        cmd = docker_command(env, name)
        try:
            proc = subprocess.run(cmd, input=code, capture_output=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            raise SandboxError(f"run timed out after {self.timeout_s:.0f}s in {image_for(env)}") from None
        except FileNotFoundError:
            raise SandboxError("docker CLI not found") from None
        duration = time.perf_counter() - started

        output = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
        # 125: docker itself failed; 126/127: command not runnable in the image.
        if proc.returncode in (125, 126, 127):
            raise SandboxError(f"docker run failed (exit {proc.returncode}): {tail(output, 5)}")

        actual = parse_output(proc.stdout.decode(errors="replace"))
        if not actual:
            # The repro crashed (or isn't ours). Not evidence for or against the bug.
            raise SandboxError(
                f"repro printed no result line (exit {proc.returncode}) in {image_for(env)}: {tail(output, 5)}"
            )
        return RunResult(
            env=env,
            passed=proc.returncode == 0 and actual == EXPECTED,
            expected=EXPECTED,
            actual=actual,
            exit_code=proc.returncode,
            duration_s=round(duration, 3),
            log_tail=tail(output),
            where="vendor-sandbox",
        )

    def bisect(self, vendor: Env, customer: Env) -> BisectReport:
        return bisect(self.run, vendor, customer)

    def matrix(self, envs: list[Env], ref: str) -> list[RunResult]:
        return [self.run(env, ref) for env in envs]


def bisect(
    run: Callable[[Env], RunResult], vendor: Env, customer: Env, max_runs: int = MAX_BISECT_RUNS
) -> BisectReport:
    """Flip one differing key at a time from the vendor value to the customer's.

    Culprits are keys that alone turn the passing baseline into a failure. If
    the baseline passes and the full customer env fails but no single key
    does, pairs are tried (key "a+b", values "x / y") until the run cap.
    Nothing is bisected when the baseline already fails or the target passes:
    there is no pass -> fail flip to explain.
    """
    baseline = run(vendor)
    target = run(customer)
    runs = 2
    steps: list[BisectStep] = []
    culprits: list[str] = []
    if not baseline.passed or target.passed:
        return BisectReport(baseline=baseline, target=target, steps=steps, culprits=culprits)

    keys = vendor.diff(customer)  # ENV_KEYS order
    for key in keys:
        if runs >= max_runs:
            break
        result = run(vendor.with_value(key, getattr(customer, key)))
        runs += 1
        steps.append(BisectStep(key, getattr(vendor, key), getattr(customer, key), result))
        if not result.passed:
            culprits.append(key)

    if not culprits:
        for a, b in itertools.combinations(keys, 2):
            if runs >= max_runs:
                break
            env = vendor.with_value(a, getattr(customer, a)).with_value(b, getattr(customer, b))
            result = run(env)
            runs += 1
            steps.append(
                BisectStep(
                    f"{a}+{b}",
                    f"{getattr(vendor, a)} / {getattr(vendor, b)}",
                    f"{getattr(customer, a)} / {getattr(customer, b)}",
                    result,
                )
            )
            if not result.passed:
                culprits.append(f"{a}+{b}")
    return BisectReport(baseline=baseline, target=target, steps=steps, culprits=culprits)


__all__ = ["DockerSandbox", "SandboxError", "bisect", "EXPECTED", "ENV_KEYS"]
