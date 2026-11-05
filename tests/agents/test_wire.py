import pytest

from fakes import FakeMachine
from woym.agents import wire
from woym.agents.runner_agent import matrix_envs
from woym.case import tags
from woym.config import VENDOR_ENV
from woym.contracts import RunResult, Step


def test_env_roundtrip_through_tag():
    c = FakeMachine().env
    t = tags.tag(1, Step.BISECTING, **wire.env_kv("v_", VENDOR_ENV), **wire.env_kv("c_", c))
    _, _, kv = tags.parse(t)
    assert wire.env_from("v_", kv) == VENDOR_ENV and wire.env_from("c_", kv) == c


def test_run_roundtrip_through_tag():
    r = RunResult(VENDOR_ENV, False, "cutoff=Fri 17:30 payout=Fri", "cutoff=Mon 17:00 payout=Mon", 1, 1.234, "x", "vendor-sandbox")
    _, _, kv = tags.parse(tags.tag(1, Step.SANDBOX_RUN, **wire.run_kv("f_", r)))
    back = wire.run_from("f_", kv, VENDOR_ENV, "vendor-sandbox")
    assert (back.passed, back.actual, back.expected, back.exit_code, back.duration_s) == \
        (False, r.actual, r.expected, 1, 1.23)


def test_missing_fields_raise():
    with pytest.raises(ValueError):
        wire.env_from("v_", {"v_node": "22"})
    with pytest.raises(ValueError):
        wire.run_from("", {}, VENDOR_ENV, "x")


def test_results_roundtrip_with_pairs():
    s = wire.results_kv({"node": True, "lang+tz": False})
    _, _, kv = tags.parse(tags.tag(1, Step.BISECTING, results=s))
    assert wire.results_from(kv["results"]) == {"node": True, "lang+tz": False}
    assert wire.results_from(None) == {}


def test_matrix_envs_dedupes():
    c = FakeMachine().env
    envs = matrix_envs(VENDOR_ENV, c)
    assert len(envs) == 4 and envs[0] == VENDOR_ENV and envs[2] == c
    assert len(matrix_envs(VENDOR_ENV, VENDOR_ENV)) == 2
