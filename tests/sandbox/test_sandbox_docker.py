"""End to end against real Docker containers (skipped without Docker).
Images are pulled on first use; scripts/pull-images.sh pre-pulls them."""

import subprocess

import pytest
from sandboxfx import CUSTOMER_ENV, needs_docker

from woym import config
from woym.config import VENDOR_ENV
from woym.contracts import Env
from woym.sandbox import remote
from woym.sandbox.runner import DockerSandbox, SandboxError

pytestmark = needs_docker


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    return DockerSandbox(remote_path=tmp_path_factory.mktemp("r") / "bizclock.git")


def test_vendor_env_passes(sandbox):
    r = sandbox.run(VENDOR_ENV)
    assert r.passed and r.exit_code == 0
    assert r.actual == "cutoff=Fri 17:30 payout=Fri"
    assert r.where == "vendor-sandbox" and r.duration_s > 0


def test_customer_env_fails_in_vendor_sandbox(sandbox):
    r = sandbox.run(CUSTOMER_ENV)
    assert not r.passed and r.exit_code == 1
    assert r.actual == "cutoff=Mon 17:00 payout=Mon"


def test_bisect_finds_tz(sandbox):
    rep = sandbox.bisect(VENDOR_ENV, CUSTOMER_ENV)
    assert [s.result.passed for s in rep.steps] == [True, True, True, True, False]
    assert rep.culprits == ["tz"]


def test_container_has_no_network(sandbox):
    # Run a different script through the same flags: no network interfaces but lo.
    from woym.sandbox import runner
    cmd = runner.docker_command(VENDOR_ENV, "woym-test-net")
    cmd[-1] = "node -e \"console.log(Object.keys(require('os').networkInterfaces()).join(','))\""
    out = subprocess.run(cmd, input=b"", capture_output=True, timeout=60).stdout.decode().strip()
    assert out in ("lo", "")


def test_matrix_after_fix_is_green(sandbox, tmp_path):
    bare = sandbox.remote_path
    work = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "checkout", "-q", "-b", "fix/case-1"], check=True)
    subprocess.run([*git, "am", "-q", str(config.BIZCLOCK_FIX_PATCH)], check=True)
    subprocess.run([*git, "push", "-q", "origin", "fix/case-1"], check=True)
    sha = remote.ls_remote("fix/case-1", bare)
    envs = [VENDOR_ENV, CUSTOMER_ENV, VENDOR_ENV.with_value("tz", "Asia/Kathmandu")]
    results = sandbox.matrix(envs, sha)
    assert [r.passed for r in results] == [True, True, True]
    assert all(r.env.bizclock == sha for r in results)


def test_unknown_image_raises_sandbox_error(sandbox):
    with pytest.raises(SandboxError):
        sandbox.run(Env(node="99", lang="C", os="alpine", bizclock="main", tz="UTC"))


def test_crashing_repro_raises_not_fails(sandbox, monkeypatch):
    from woym.sandbox import runner
    real = runner.docker_command

    def crashing(env, name):
        cmd = real(env, name)
        cmd[-1] = "cat >/dev/null; node -e 'throw new Error(\"boom\")'"
        return cmd

    monkeypatch.setattr(runner, "docker_command", crashing)
    with pytest.raises(SandboxError, match="no result line"):
        sandbox.run(VENDOR_ENV)
