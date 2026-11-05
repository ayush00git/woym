"""Shared fixer pipeline: clone, check the edit, prove the test, commit, push.

Whoever writes the edit (Claude or the stored patch), OUR code decides whether
it is acceptable:
- only files under src/ and test/ may change (never the repro in examples/);
- at least one test file must change, and no existing test may be deleted;
- the full suite passes under the customer's culprit value AND the vendor value;
- the changed tests FAIL on the original src/ under the culprit value (so the
  test really catches the bug);
- then we commit, push fix/case-<n>, and read the sha back with ls-remote.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from woym import config
from woym.sandbox import remote as remote_mod

# Env keys that map to a process env var the tests can be run under.
ENV_VARS = {"tz": "TZ", "lang": "LANG"}
ALLOWED_PREFIXES = ("src/", "test/")
TEST_TIMEOUT_S = 120
_VALUE_RE = re.compile(r"^[A-Za-z0-9_+\-/.:]{1,64}$")
_TEST_NAME_RE = re.compile(r"""^\+.*?\b(?:test|it)\(\s*(['"`])(.+?)\1""", re.M)

AUTHOR = {
    "GIT_AUTHOR_NAME": "WOYM fixer",
    "GIT_AUTHOR_EMAIL": "fixer@woym.invalid",
    "GIT_COMMITTER_NAME": "WOYM fixer",
    "GIT_COMMITTER_EMAIL": "fixer@woym.invalid",
}


class FixerError(RuntimeError):
    pass


def safe_value(value: str) -> str:
    if not _VALUE_RE.match(value or ""):
        raise FixerError(f"refusing unsafe env value: {value!r}")
    return value


def safe_text(text: str, limit: int = 120) -> str:
    """Sandbox output going into a prompt: one line, no backticks, capped."""
    return " ".join(str(text).replace("`", "'").split())[:limit]


def _git(cwd: Path, *args: str, check: bool = True, env: dict | None = None) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, timeout=60,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", **(env or {})},
    )
    if check and proc.returncode != 0:
        raise FixerError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass
class Workspace:
    case_id: int
    path: Path
    remote: Path
    base_sha: str

    @property
    def branch(self) -> str:
        return f"fix/case-{self.case_id}"


def prepare(case_id: int, remote: Path | None = None, work_root: Path | None = None,
            base: str = "main") -> Workspace:
    """Fresh clone of the bare remote into var/work/case-<n> on fix/case-<n>."""
    remote = Path(remote or config.REMOTE)
    work_root = Path(work_root or config.WORK)
    remote_mod.check_ref(base)
    path = work_root / f"case-{int(case_id)}"
    if path.exists():
        shutil.rmtree(path)
    work_root.mkdir(parents=True, exist_ok=True)
    _git(work_root, "clone", "-q", "--no-local", str(remote), str(path))
    _git(path, "checkout", "-q", "-B", f"fix/case-{int(case_id)}", f"origin/{base}")
    base_sha = _git(path, "rev-parse", "HEAD").strip()
    return Workspace(case_id, path, remote, base_sha)


def changed_files(ws: Workspace) -> dict[str, str]:
    """{path: status} vs HEAD, including untracked files. Status is A/M/D."""
    _git(ws.path, "add", "-A", "--intent-to-add")
    out = _git(ws.path, "diff", "--name-status", "HEAD")
    files: dict[str, str] = {}
    for line in out.splitlines():
        status, _, name = line.partition("\t")
        files[name] = status[0]
    return files


def is_test(path: str) -> bool:
    return path.startswith("test/") or path.endswith(".test.js")


def check_changes(ws: Workspace) -> tuple[list[str], list[str]]:
    """Validate the edit. Returns (src_files, test_files)."""
    if _git(ws.path, "rev-parse", "HEAD").strip() != ws.base_sha:
        raise FixerError("HEAD moved: the edit made its own commits")
    files = changed_files(ws)
    if not files:
        raise FixerError("no files changed")
    outside = [f for f in files if not f.startswith(ALLOWED_PREFIXES)]
    if outside:
        raise FixerError(f"edit touched files outside src/ and test/: {outside}")
    deleted_tests = [f for f, s in files.items() if s == "D" and is_test(f)]
    if deleted_tests:
        raise FixerError(f"edit deleted tests: {deleted_tests}")
    tests = sorted(f for f in files if is_test(f))
    src = sorted(f for f in files if not is_test(f))
    if not tests:
        raise FixerError("no regression test was added or changed")
    if not src:
        raise FixerError("no source file was changed")
    return src, tests


def run_tests(ws: Workspace, env_vars: dict[str, str], files: list[str] | None = None) -> tuple[bool, str]:
    env = {**os.environ, **env_vars}
    proc = subprocess.run(
        ["node", "--test", *(files or [])], cwd=ws.path, env=env,
        capture_output=True, text=True, timeout=TEST_TIMEOUT_S,
    )
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, "\n".join(out[-20:])


def prove_regression(ws: Workspace, src: list[str], tests: list[str], env_vars: dict[str, str]) -> None:
    """The changed tests must fail against the ORIGINAL src files."""
    saved: dict[str, bytes | None] = {}
    for f in src:
        p = ws.path / f
        saved[f] = p.read_bytes() if p.exists() else None
    try:
        for f in src:
            p = ws.path / f
            proc = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ws.path, capture_output=True)
            if proc.returncode == 0:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(proc.stdout)
            elif p.exists():
                p.unlink()  # file is new in the edit
        ok, out = run_tests(ws, env_vars, tests)
    finally:
        for f, data in saved.items():
            p = ws.path / f
            if data is None:
                if p.exists():
                    p.unlink()
            else:
                p.write_bytes(data)
    if ok:
        raise FixerError(f"the new test passes on the unfixed code with {env_vars}, so it doesn't catch the bug:\n{out}")


def test_names(ws: Workspace, tests: list[str]) -> list[str]:
    diff = _git(ws.path, "diff", "HEAD", "--", *tests)
    return [m.group(2) for m in _TEST_NAME_RE.finditer(diff)]


def commit_and_push(ws: Workspace, message: str) -> str:
    _git(ws.path, "add", "-A")
    _git(ws.path, "commit", "-q", "-m", message, env=AUTHOR)
    _git(ws.path, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{ws.branch}")
    sha = remote_mod.ls_remote(ws.branch, ws.remote)
    local = _git(ws.path, "rev-parse", "HEAD").strip()
    if sha != local:
        raise FixerError(f"ls-remote says {sha}, local HEAD is {local}")
    return sha


def culprit_env(culprit: str, customer_value: str) -> tuple[dict[str, str], dict[str, str]]:
    """(env vars with the customer's culprit value, env vars with the vendor value)."""
    var = ENV_VARS.get(culprit)
    if not var:
        raise FixerError(f"can't run tests under culprit {culprit!r}: no env var for it")
    vendor_value = getattr(config.VENDOR_ENV, culprit)
    return {var: safe_value(customer_value)}, {var: safe_value(vendor_value)}


@dataclass(frozen=True)
class Verified:
    src: list[str]
    tests: list[str]
    test_name: str


def verify(ws: Workspace, culprit: str, customer_value: str) -> Verified:
    """Run every check on the edit. Raises FixerError with the reason."""
    bad_env, good_env = culprit_env(culprit, customer_value)
    src, tests = check_changes(ws)
    for env in (bad_env, good_env):
        ok, out = run_tests(ws, env)
        if not ok:
            raise FixerError(f"tests fail after the edit with {env}:\n{out}")
    # The test must catch the bug on the original code under the culprit value AND
    # under the vendor's own CI value, so it guards the vendor's CI, not just this host.
    prove_regression(ws, src, tests, bad_env)
    try:
        prove_regression(ws, src, tests, good_env)
    except FixerError as e:
        raise FixerError(
            f"the new test only fails when the host has {bad_env}; under {good_env} (the vendor's CI) it passes "
            "on the unfixed code, so it wouldn't guard CI. The test must set the value itself "
            f"(e.g. process.env.{next(iter(bad_env))} = '{next(iter(bad_env.values()))}')."
        ) from e
    names = test_names(ws, tests)
    return Verified(src, tests, names[0] if names else tests[0])
