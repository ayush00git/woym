"""Runner and bisect logic, without Docker."""

from dataclasses import replace

import pytest
from sandboxfx import CUSTOMER_ENV

from woym.config import VENDOR_ENV
from woym.contracts import Env, RunResult
from woym.sandbox import runner


def fake_run(failing):
    """A run() that fails when `failing(env)` is true. Records calls."""
    calls = []

    def run(env, ref=None):
        calls.append(env)
        ok = not failing(env)
        return RunResult(env, ok, runner.EXPECTED, runner.EXPECTED if ok else "cutoff=Mon 17:00 payout=Mon",
                         0 if ok else 1, 0.2, "", "vendor-sandbox")

    run.calls = calls
    return run


def test_parse_output():
    assert runner.parse_output("noise\ncutoff=Fri 17:30 payout=Fri\n") == "cutoff=Fri 17:30 payout=Fri"
    assert runner.parse_output("Error: boom\n    at x") == ""


def test_image_for():
    assert runner.image_for(VENDOR_ENV) == "node:22-alpine"
    assert runner.image_for(CUSTOMER_ENV) == "node:20-bookworm-slim"
    with pytest.raises(runner.SandboxError):
        runner.image_for(replace(VENDOR_ENV, os="windows"))
    with pytest.raises(runner.SandboxError):
        runner.image_for(replace(VENDOR_ENV, node="22; rm -rf /"))


def test_docker_command_is_locked_down():
    cmd = runner.docker_command(CUSTOMER_ENV, "woym-run-x")
    joined = " ".join(cmd)
    for flag in ["--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--pids-limit 128", "--memory 512m", "--user 1000:1000", "--rm", "-i"]:
        assert flag in joined
    assert "-v" not in cmd and "--volume" not in cmd and "--mount" not in cmd and "--privileged" not in cmd
    assert "TZ=Asia/Kolkata" in cmd and "LANG=en_IN.UTF-8" in cmd


def test_docker_command_rejects_odd_values():
    with pytest.raises(runner.SandboxError):
        runner.docker_command(replace(CUSTOMER_ENV, tz="UTC -v /:/host"), "x")


def test_bisect_single_culprit():
    run = fake_run(lambda e: e.tz == "Asia/Kolkata")
    rep = runner.bisect(run, VENDOR_ENV, CUSTOMER_ENV)
    assert rep.baseline.passed and not rep.target.passed
    assert [s.key for s in rep.steps] == ["node", "lang", "os", "bizclock", "tz"]
    assert [s.result.passed for s in rep.steps] == [True, True, True, True, False]
    assert rep.steps[-1].vendor_value == "UTC" and rep.steps[-1].customer_value == "Asia/Kolkata"
    assert rep.culprits == ["tz"]
    assert len(run.calls) == 7


def test_bisect_only_differing_keys():
    customer = VENDOR_ENV.with_value("tz", "Asia/Kolkata")
    rep = runner.bisect(fake_run(lambda e: e.tz != "UTC"), VENDOR_ENV, customer)
    assert [s.key for s in rep.steps] == ["tz"] and rep.culprits == ["tz"]


def test_bisect_falls_back_to_pairs_and_respects_cap():
    # Fails only when both lang and tz are the customer's.
    run = fake_run(lambda e: e.lang == "en_IN.UTF-8" and e.tz == "Asia/Kolkata")
    rep = runner.bisect(run, VENDOR_ENV, CUSTOMER_ENV)
    assert len(run.calls) <= runner.MAX_BISECT_RUNS
    singles = rep.steps[:5]
    assert all(s.result.passed for s in singles)
    # 2 + 5 singles leaves 3 pair runs: node+lang, node+os, node+bizclock. lang+tz isn't reached.
    assert [s.key for s in rep.steps[5:]] == ["node+lang", "node+os", "node+bizclock"]
    assert rep.culprits == []


def test_bisect_pairs_find_culprit_within_cap():
    customer = Env(node="22", lang="en_IN.UTF-8", os="alpine", bizclock="main", tz="Asia/Kolkata")
    run = fake_run(lambda e: e.lang == "en_IN.UTF-8" and e.tz == "Asia/Kolkata")
    rep = runner.bisect(run, VENDOR_ENV, customer)
    assert rep.culprits == ["lang+tz"]
    assert rep.steps[-1].vendor_value == "en_US.UTF-8 / UTC"


@pytest.mark.parametrize("failing", [lambda e: True, lambda e: False])
def test_bisect_skips_when_nothing_to_explain(failing):
    run = fake_run(failing)
    rep = runner.bisect(run, VENDOR_ENV, CUSTOMER_ENV)
    assert rep.steps == [] and rep.culprits == [] and len(run.calls) == 2
