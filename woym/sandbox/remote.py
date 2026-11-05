"""The bizclock bare remote: var/remotes/bizclock.git.

It stands in for the vendor's GitHub. It's seeded from fixtures/bizclock by
fixtures/build-bizclock-history.sh (fixed authors and dates, so the commit
shas are the same on every machine). @fixer pushes fix/case-<n> branches here.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from woym import config

BUILD_SCRIPT = config.ROOT / "fixtures" / "build-bizclock-history.sh"

# Branch names, tags and shas only. No leading "-" (option injection), no "..",
# no "@{" or other rev syntax.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class RemoteError(RuntimeError):
    pass


def check_ref(ref: str) -> str:
    if not isinstance(ref, str) or not _REF_RE.match(ref) or ".." in ref or ref.endswith((".lock", "/", ".")):
        raise RemoteError(f"refusing unsafe git ref: {ref!r}")
    return ref


def _git(remote: Path, *args: str, binary: bool = False) -> bytes | str:
    proc = subprocess.run(
        ["git", f"--git-dir={remote}", *args],
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RemoteError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode()


def init_remote(remote: Path | None = None, force: bool = False) -> Path:
    """Create the bare remote from the fixture if it isn't there yet.

    With force=True it is deleted and rebuilt, which also drops any fix/case-n
    branches. Without it, an existing remote that has main and v2.3.1 is kept.
    """
    remote = Path(remote or config.REMOTE)
    if remote.exists():
        if not force:
            try:
                _git(remote, "rev-parse", "--verify", "-q", "refs/heads/main")
                _git(remote, "rev-parse", "--verify", "-q", "refs/tags/v2.3.1")
                return remote
            except RemoteError:
                pass  # broken or partial: rebuild
        shutil.rmtree(remote)

    remote.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bizclock-src-") as tmp:
        work = Path(tmp) / "bizclock"
        proc = subprocess.run([str(BUILD_SCRIPT), str(work)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RemoteError(f"{BUILD_SCRIPT.name} failed: {proc.stderr.strip()}")
        proc = subprocess.run(
            ["git", "clone", "-q", "--bare", "--no-local", str(work), str(remote)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RemoteError(f"git clone --bare failed: {proc.stderr.strip()}")
    return remote


def archive(ref: str, remote: Path | None = None) -> bytes:
    """A tar of the tree at `ref` (branch, tag or sha)."""
    remote = Path(remote or config.REMOTE)
    check_ref(ref)
    return _git(remote, "archive", "--format=tar", f"{ref}^{{tree}}", binary=True)  # type: ignore[return-value]


def ls_remote(branch: str, remote: Path | None = None) -> str | None:
    """The sha of refs/heads/<branch> on the remote, or None if it doesn't exist."""
    remote = Path(remote or config.REMOTE)
    check_ref(branch)
    out = _git(remote, "ls-remote", str(remote), f"refs/heads/{branch}")
    for line in str(out).splitlines():
        sha, _, name = line.partition("\t")
        if name == f"refs/heads/{branch}":
            return sha
    return None


def resolve(ref: str, remote: Path | None = None) -> str:
    """The commit sha a branch, tag or sha points at."""
    remote = Path(remote or config.REMOTE)
    check_ref(ref)
    return str(_git(remote, "rev-parse", "--verify", f"{ref}^{{commit}}")).strip()
