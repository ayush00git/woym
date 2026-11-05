import json

import pytest

from casefx import CUSTOMER, VENDOR, report, run
from woym import config
from woym.case import state
from woym.case.state import CaseStore, GuardError, SendGuard, TransitionError
from woym.contracts import Case, FixResult, MessageEvent, Step

# The full M2 path of §3, as triage sees it.
HAPPY = [
    Step.SANDBOX_RUN, Step.NOT_REPRODUCED, Step.ENV_REQUESTED, Step.CONSENT_PENDING,
    Step.ENV_RECEIVED, Step.BISECTING, Step.CULPRIT_FOUND, Step.CONFIRM_REQUESTED,
    Step.CONFIRMED_BOTH, Step.FIX_REQUESTED, Step.FIX_PUSHED, Step.MATRIX_GREEN,
    Step.VERIFY_REQUESTED, Step.FIXED_ON_CUSTOMER,
]


def new_case() -> Case:
    return Case(id=1, room_id="r", thread_id="t", reporter="meera")


def test_happy_path_transitions():
    c = new_case()
    for s in HAPPY:
        state.advance(c, s)
    assert c.step == Step.FIXED_ON_CUSTOMER


def test_consent_step_is_optional_for_triage():
    c = new_case()
    for s in [Step.SANDBOX_RUN, Step.NOT_REPRODUCED, Step.ENV_REQUESTED, Step.ENV_RECEIVED]:
        state.advance(c, s)


def test_reproduced_directly_goes_to_fix():
    c = new_case()
    state.advance(c, Step.SANDBOX_RUN)
    state.advance(c, Step.REPRODUCED)
    state.advance(c, Step.FIX_REQUESTED)


@pytest.mark.parametrize("frm,to", [
    (Step.REPORTED, Step.BISECTING),
    (Step.NOT_REPRODUCED, Step.FIX_REQUESTED),       # can't patch before confirming
    (Step.ENV_REQUESTED, Step.BISECTING),           # can't bisect without env
    (Step.CULPRIT_FOUND, Step.FIX_REQUESTED),
    (Step.FIX_PUSHED, Step.FIXED_ON_CUSTOMER),       # must go through matrix + verify
    (Step.ENV_RECEIVED, Step.ENV_RECEIVED),          # no self loops
])
def test_illegal_transitions(frm, to):
    c = new_case()
    c.step = frm
    with pytest.raises(TransitionError):
        state.advance(c, to)
    assert c.step == frm


def test_any_nonterminal_can_fail_and_terminals_are_final():
    for s in state.TRANSITIONS:
        assert state.can_advance(s, Step.FAILED) == (s not in state.TERMINAL)
    for t in state.TERMINAL:
        for s in Step:
            assert not state.can_advance(t, s)


def test_transition_table_covers_every_step():
    assert set(state.TRANSITIONS) == set(Step)


# ---- loop guard on the case --------------------------------------------------

def test_record_hop_once_per_agent_step():
    c = new_case()
    state.record_hop(c, "triage", Step.SANDBOX_RUN)
    state.record_hop(c, "runner", Step.SANDBOX_RUN)   # same step, other agent: ok
    with pytest.raises(GuardError):
        state.record_hop(c, "triage", Step.SANDBOX_RUN)
    assert c.history == ["triage:sandbox_run", "runner:sandbox_run"]
    assert c.hops == 2


def test_record_hop_limit():
    limit = config.MAX_HOPS_PER_CASE
    c = new_case()
    for i in range(limit):
        state.record_hop(c, f"agent{i}", Step.SANDBOX_RUN)
    with pytest.raises(GuardError, match="hop limit"):
        state.record_hop(c, "one-more", Step.SANDBOX_RUN)
    assert c.hops == limit


def test_record_hop_limit_override():
    c = new_case()
    for i in range(3):
        state.record_hop(c, f"a{i}", Step.SANDBOX_RUN, max_hops=3)
    with pytest.raises(GuardError, match="hop limit"):
        state.record_hop(c, "a3", Step.SANDBOX_RUN, max_hops=3)


def test_full_flow_completes_under_hop_limit():
    """Every tagged message of §3 (agent->agent plus posts to room/human)
    fits under the configured hop limit, and each is recorded exactly once."""
    hops = [("triage", "sandbox_run"), ("runner", "sandbox_run"), ("triage", "not_reproduced"),
            ("triage", "env_req"), ("acme-env", "consent"), ("acme-env", "env"),
            ("triage", "bisect"), ("runner", "bisect"), ("triage", "confirm_req"),
            ("acme-env", "confirmed"), ("triage", "fix_req"), ("fixer", "fix_pushed"),
            ("triage", "matrix_green"), ("runner", "matrix_green"), ("triage", "verify_req"),
            ("acme-env", "fixed")]
    assert len(hops) == 16 <= config.MAX_HOPS_PER_CASE
    c = new_case()
    for a, s in hops:
        state.record_hop(c, a, s)
    assert c.hops == 16 and len(set(c.history)) == 16


# ---- per-agent send guard ----------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_send_guard_once_per_case_step():
    g = SendGuard("runner", clock=Clock())
    g.record(1, Step.SANDBOX_RUN)
    g.clock.t += 5
    assert g.check(1, Step.SANDBOX_RUN)
    with pytest.raises(GuardError, match="already"):
        g.record(1, Step.SANDBOX_RUN)
    assert g.check(2, Step.SANDBOX_RUN) is None     # other case is fine


def test_send_guard_rate_limit_2s():
    clk = Clock()
    g = SendGuard("triage", clock=clk)
    assert g.wait_s() == 0
    g.record(1, Step.SANDBOX_RUN)
    clk.t += 0.5
    assert g.wait_s() == pytest.approx(1.5)
    with pytest.raises(GuardError, match="too fast"):
        g.record(1, Step.ENV_REQUESTED)
    with pytest.raises(GuardError, match="too fast"):
        g.record_untagged()
    clk.t += 1.5
    assert g.wait_s() == 0
    g.record(1, Step.ENV_REQUESTED)
    clk.t += 2.0
    g.record_untagged()


def test_send_guard_hop_cap():
    clk = Clock()
    g = SendGuard("x", clock=clk, max_hops=3)
    for s in [Step.SANDBOX_RUN, Step.BISECTING, Step.MATRIX_GREEN]:
        g.record(1, s)
        clk.t += 3
    assert "hop limit" in g.check(1, Step.FIXED_ON_CUSTOMER)


def test_send_guard_defaults_from_config():
    g = SendGuard("x")
    assert g.min_gap_s == config.MIN_SECONDS_BETWEEN_SENDS
    assert g.max_hops == config.MAX_HOPS_PER_CASE


def ev(**kw):
    base = dict(room_id="r", message_id="m", thread_id=None, sender_id="u", sender_name="meera",
                sender_kind="human", text="hi", addressed=False)
    base.update(kw)
    return MessageEvent(**base)


def test_should_ignore():
    assert state.should_ignore(ev(sender_kind="system"), "triage")
    assert state.should_ignore(ev(sender_name="triage", sender_kind="agent"), "triage")
    assert not state.should_ignore(ev(), "triage")
    assert not state.should_ignore(ev(sender_name="runner", sender_kind="agent"), "triage")


# ---- store -------------------------------------------------------------------

def test_store_roundtrip_full_case(tmp_path):
    store = CaseStore(tmp_path / "var" / "cases.json")
    c = store.open_case("room", "root1", "meera")
    assert c.id == 1
    c.step = Step.FIX_PUSHED
    c.vendor_env, c.customer_env = VENDOR, CUSTOMER
    c.bisect = report()
    c.fix = FixResult("fix/case-1", "a" * 40, ["src/cutoff.js"], "ist half hour", "claude", "Use minutes")
    state.record_hop(c, "triage", Step.SANDBOX_RUN)
    store.save(c)

    again = CaseStore(tmp_path / "var" / "cases.json").get(1)
    assert again == c
    assert again.bisect.steps[-1].result.env.tz == "Asia/Kolkata"
    assert json.loads((tmp_path / "var" / "cases.json").read_text())["cases"]["1"]["step"] == "fix_pushed"


def test_store_ids_increment_and_lookup(tmp_path):
    store = CaseStore(tmp_path / "cases.json")
    a = store.open_case("room", "t1", "meera")
    b = store.open_case("room", "t2", "meera")
    assert (a.id, b.id) == (1, 2)
    assert store.by_thread("room", "t2").id == 2
    assert store.by_thread("room", "nope") is None
    assert store.by_thread("other", "t1") is None
    assert store.get(99) is None
    assert {c.id for c in store.all()} == {1, 2}


def test_store_empty_and_no_temp_leftovers(tmp_path):
    store = CaseStore(tmp_path / "cases.json")
    assert store.all() == []
    store.open_case("r", None, "meera")
    assert [p.name for p in tmp_path.iterdir()] == ["cases.json"]


def test_run_result_roundtrip_via_store(tmp_path):
    store = CaseStore(tmp_path / "c.json")
    c = store.open_case("r", "t", "meera")
    c.bisect = report()
    store.save(c)
    assert store.get(c.id).bisect.baseline == run(VENDOR, True)
