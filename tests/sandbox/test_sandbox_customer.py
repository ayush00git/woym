"""Customer machine. Uses its own container name and remote, so it never
touches a running demo's acme-laptop."""

import subprocess

import pytest
from sandboxfx import CUSTOMER_ENV, needs_docker

from woym import config
from woym.sandbox import customer, remote
from woym.sandbox.customer import DockerCustomerMachine, _os_family
from woym.sandbox.runner import SandboxError


def test_os_family():
    assert _os_family('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_CODENAME=bookworm\n') == "bookworm"
    assert _os_family("NAME=\"Alpine Linux\"\nID=alpine\nVERSION_ID=3.20.3\n") == "alpine"


def test_probe_allowlist_is_exactly_the_env_keys():
    assert list(customer.PROBES) == ["node", "lang", "os", "bizclock", "tz"]
    assert "Intl" not in " ".join(customer.PROBES["tz"])


@pytest.mark.parametrize("overrides", [{"node": "22"}, {"PATH": "/tmp"}, {"tz": "UTC; rm -rf ~"}, {"tz": "$(id)"}])
def test_bad_overrides_rejected_before_running_anything(overrides):
    m = DockerCustomerMachine(container="does-not-exist")
    with pytest.raises(SandboxError):
        m.run_repro(overrides)


@pytest.fixture(scope="module")
def machine(tmp_path_factory):
    m = DockerCustomerMachine(container="woym-test-acme-laptop",
                              remote_path=tmp_path_factory.mktemp("r") / "bizclock.git")
    m.up("v2.3.1")
    yield m
    m.down()


@needs_docker
def test_probe(machine):
    assert machine.probe() == CUSTOMER_ENV


@needs_docker
def test_repro_fails_with_real_tz_and_passes_with_utc(machine):
    real = machine.run_repro()
    assert (real.passed, real.actual, real.exit_code) == (False, "cutoff=Mon 17:00 payout=Mon", 1)
    assert real.where == "customer-machine" and real.env == CUSTOMER_ENV
    utc = machine.run_repro({"tz": "UTC"})
    assert (utc.passed, utc.actual) == (True, "cutoff=Fri 17:30 payout=Fri")
    assert utc.env.tz == "UTC"
    # the override is per run; the machine's own TZ is untouched
    assert machine.probe().tz == "Asia/Kolkata"


@needs_docker
def test_install_fix_then_verify(machine, tmp_path):
    bare = machine.remote_path
    work = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "checkout", "-q", "-b", "fix/case-9"], check=True)
    subprocess.run([*git, "am", "-q", str(config.BIZCLOCK_FIX_PATCH)], check=True)
    subprocess.run([*git, "push", "-q", "origin", "fix/case-9"], check=True)
    sha = remote.ls_remote("fix/case-9", bare)

    machine.install(sha)
    assert machine.probe().bizclock == sha
    fixed = machine.run_repro()
    assert (fixed.passed, fixed.actual) == (True, "cutoff=Fri 17:30 payout=Fri")
    assert fixed.env.tz == "Asia/Kolkata" and fixed.env.bizclock == sha

    machine.install("v2.3.1")  # and back
    assert machine.probe().bizclock == "v2.3.1"
    assert not machine.run_repro().passed


@needs_docker
def test_install_rejects_unknown_ref_and_keeps_old_install(machine):
    with pytest.raises(remote.RemoteError):
        machine.install("--help")
    with pytest.raises(remote.RemoteError):
        machine.install("no-such-branch")
    assert machine.probe().bizclock == "v2.3.1"
