"""ClaudeFixer and FixtureFixer, both implementing contracts.Fixer.

Claude only edits files in a throwaway clone. Our code runs the tests, proves
the new test catches the bug, commits, pushes fix/case-<n> and reads the sha
with ls-remote. The model's reply is used only as the one-line summary.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from woym import config
from woym.contracts import FixResult, RunResult
from woym.fixer import workspace as ws_mod
from woym.fixer.workspace import FixerError, Workspace

CLAUDE_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(node *),Bash(npm test*)"
CLAUDE_TIMEOUT_S = 300
SUMMARY_MAX = 200

PROMPT = """\
You are fixing a bug in bizclock, a small zero-dependency Node library (the current directory).

Symptom: `node examples/payout.js` prints `{actual}` but should print `{expected}` when {var}={value}.
It passes when {var}={vendor_value}. A bisect isolated the cause to the `{key}` setting.

Do exactly this:
1. Add a regression test under test/ (node:test) that fails with the current code when {var}={value}.
2. Make the smallest fix in src/ so the whole suite passes under both {var}={value} and {var}={vendor_value}.
Your shell already has {var}={value}. Run tests with `npm test` or `node --test`.

Rules: edit only files under src/ and test/. Do not touch examples/, package.json or git. No dependencies.
When done, reply with ONE plain sentence: the root cause and the fix.
"""


@dataclass(frozen=True)
class ClaudeOutput:
    text: str
    ok: bool
    duration_s: float
    cost_usd: float | None = None
    denials: int = 0
    raw: str = ""


ClaudeRunner = Callable[[str, Path, dict[str, str]], ClaudeOutput]


def run_claude_cli(prompt: str, cwd: Path, env_vars: dict[str, str],
                   bin: str = "claude", model: str = "sonnet", timeout: int = CLAUDE_TIMEOUT_S) -> ClaudeOutput:
    """Run `claude -p` non-interactively (flags verified on this machine, see ISSUES.md)."""
    cmd = [bin, "-p", prompt, "--allowedTools", CLAUDE_TOOLS, "--model", model,
           "--output-format", "json", "--strict-mcp-config", "--setting-sources", "project"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env={**os.environ, **env_vars}, capture_output=True,
                              text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise FixerError(f"claude CLI not found: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise FixerError(f"claude -p timed out after {timeout}s") from e
    dt = time.monotonic() - t0
    try:
        j = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ClaudeOutput("", False, dt, raw=(proc.stdout + proc.stderr)[-2000:])
    return ClaudeOutput(
        text=str(j.get("result") or ""),
        ok=proc.returncode == 0 and not j.get("is_error"),
        duration_s=dt,
        cost_usd=j.get("total_cost_usd"),
        denials=len(j.get("permission_denials") or []),
        raw=proc.stdout[-2000:],
    )


def _one_line(text: str) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= SUMMARY_MAX else line[: SUMMARY_MAX - 1] + "…"


class _Base:
    mode = "?"

    def __init__(self, remote: Path | None = None, work_root: Path | None = None, base: str = "main") -> None:
        self.remote = Path(remote or config.REMOTE)
        self.work_root = Path(work_root or config.WORK)
        self.base = base
        self.last_log: list[str] = []  # human-readable trace of the last fix(), for chat/ISSUES

    def _log(self, msg: str) -> None:
        self.last_log.append(msg)

    def _edit(self, ws: Workspace, culprit: str, failing: RunResult, env_vars: dict[str, str]) -> str:
        """Make the edit in the clone; return a one-line summary."""
        raise NotImplementedError

    def fix(self, case_id: int, culprit: str, failing: RunResult) -> FixResult:
        self.last_log = []
        value = getattr(failing.env, culprit, None)
        if value is None:
            raise FixerError(f"unknown culprit {culprit!r}")
        bad_env, _ = ws_mod.culprit_env(culprit, value)
        ws = ws_mod.prepare(case_id, self.remote, self.work_root, self.base)
        self._log(f"cloned {self.base} @ {ws.base_sha[:10]} into {ws.path}")
        summary = self._edit(ws, culprit, failing, bad_env)
        verified = ws_mod.verify(ws, culprit, value)
        self._log(f"verified: tests pass under {culprit}={value} and vendor value; new test fails on old code")
        msg = f"fix: {culprit}={value} regression (case {case_id})\n\n{summary}\n\nWOYM fixer mode: {self.mode}"
        sha = ws_mod.commit_and_push(ws, msg)
        self._log(f"pushed {ws.branch} @ {sha[:10]} (sha from ls-remote)")
        return FixResult(
            branch=ws.branch, sha=sha, files_changed=verified.src + verified.tests,
            test_name=verified.test_name, mode=self.mode, summary=summary,
        )


class ClaudeFixer(_Base):
    mode = "claude"

    def __init__(self, *args, runner: ClaudeRunner | None = None, **kw) -> None:
        super().__init__(*args, **kw)
        self.runner = runner or run_claude_cli
        self.last_output: ClaudeOutput | None = None

    def prompt(self, culprit: str, failing: RunResult, env_vars: dict[str, str]) -> str:
        (var, value), = env_vars.items()
        return PROMPT.format(
            actual=ws_mod.safe_text(failing.actual), expected=ws_mod.safe_text(failing.expected),
            var=var, value=value, vendor_value=getattr(config.VENDOR_ENV, culprit), key=culprit,
        )

    def _edit(self, ws: Workspace, culprit: str, failing: RunResult, env_vars: dict[str, str]) -> str:
        out = self.runner(self.prompt(culprit, failing, env_vars), ws.path, env_vars)
        self.last_output = out
        cost = f", ${out.cost_usd:.2f}" if out.cost_usd is not None else ""
        self._log(f"claude -p finished in {out.duration_s:.0f}s{cost}, {out.denials} tool denials")
        if not out.ok:
            raise FixerError(f"claude -p failed: {out.raw[-500:] or out.text[:500]}")
        return _one_line(out.text) or "Claude Code edited src/ and test/."


class FixtureFixer(_Base):
    """FIXER=fixture: applies the stored reference patch. Labelled as replayed."""

    mode = "fixture"

    def __init__(self, *args, patch: Path | None = None, **kw) -> None:
        super().__init__(*args, **kw)
        self.patch = Path(patch or config.BIZCLOCK_FIX_PATCH)

    def _edit(self, ws: Workspace, culprit: str, failing: RunResult, env_vars: dict[str, str]) -> str:
        if not self.patch.exists():
            raise FixerError(f"fixture patch missing: {self.patch}")
        ws_mod._git(ws.path, "apply", "--whitespace=nowarn", str(self.patch.resolve()))
        self._log(f"applied stored patch {self.patch.name}")
        subject, lines = "", self.patch.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Subject:"):
                parts = [line.removeprefix("Subject:")]
                parts += [ln for ln in lines[i + 1:i + 4] if ln.startswith(" ")][:1]
                subject = " ".join(" ".join(parts).replace("[PATCH]", "").split())
                break
        return _one_line(f"Stored reference fix (fixture mode, not generated): {subject}")


def make_fixer(mode: str | None = None, **kw) -> ClaudeFixer | FixtureFixer:
    mode = (mode or config.FIXER_MODE).lower()
    if mode == "claude":
        return ClaudeFixer(**kw)
    if mode == "fixture":
        return FixtureFixer(**{k: v for k, v in kw.items() if k != "runner"})
    raise ValueError(f"FIXER must be 'claude' or 'fixture', not {mode!r}")
