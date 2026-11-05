"""The fixture library itself: tests pass in UTC, the repro fails in IST,
and the stored fix patch turns its own regression test from red to green."""

import os
import subprocess

import pytest
from sandboxfx import needs_node

from woym import config
from woym.sandbox import remote

pytestmark = needs_node


def node(cwd, *args, tz):
    env = {**os.environ, "TZ": tz}
    return subprocess.run(["node", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture()
def clone(tmp_path):
    bare = remote.init_remote(tmp_path / "bizclock.git")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    return work


def test_suite_passes_in_utc():
    assert node(config.BIZCLOCK_SRC, "--test", tz="UTC").returncode == 0


@pytest.mark.parametrize(
    "tz, line, code",
    [
        ("UTC", "cutoff=Fri 17:30 payout=Fri", 0),
        ("Europe/London", "cutoff=Fri 17:30 payout=Fri", 0),
        ("Asia/Kolkata", "cutoff=Mon 17:00 payout=Mon", 1),
        ("America/St_Johns", "cutoff=Mon 17:00 payout=Mon", 1),
    ],
)
def test_repro(tz, line, code):
    p = node(config.BIZCLOCK_SRC, "examples/payout.js", tz=tz)
    assert (p.stdout.strip(), p.returncode) == (line, code)


def test_fix_patch_regression_test_red_then_green(clone):
    patch = config.BIZCLOCK_FIX_PATCH
    # Take only the new test first: it must fail against the buggy code.
    subprocess.run(["git", "apply", "--include=test/*", str(patch)], cwd=clone, check=True)
    assert node(clone, "--test", "test/cutoff-tz.test.js", tz="Asia/Kolkata").returncode != 0
    assert node(clone, "--test", "test/cutoff-tz.test.js", tz="UTC").returncode != 0  # the test sets TZ itself
    # Then the fix: everything passes, including the repro in IST.
    subprocess.run(["git", "apply", "--exclude=test/*", str(patch)], cwd=clone, check=True)
    for tz in ("UTC", "Asia/Kolkata", "Asia/Kathmandu"):
        assert node(clone, "--test", tz=tz).returncode == 0, tz
    p = node(clone, "examples/payout.js", tz="Asia/Kolkata")
    assert (p.stdout.strip(), p.returncode) == ("cutoff=Fri 17:30 payout=Fri", 0)


def test_fix_patch_applies_to_release_tag(clone):
    subprocess.run(["git", "checkout", "-q", "v2.3.1"], cwd=clone, check=True)
    subprocess.run(["git", "apply", "--check", str(config.BIZCLOCK_FIX_PATCH)], cwd=clone, check=True)
