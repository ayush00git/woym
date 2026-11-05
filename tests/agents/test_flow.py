"""End-to-end case flow on the FAKE bus with FAKE engines (no Switch, no Docker, no claude)."""

import pytest

from fakes import FIX_SHA, FakeBus, FakeFixer, FakeMachine, FakeSandbox
from woym.agents.acme_env import AcmeEnvAgent
from woym.agents.fixer_agent import FixerAgent
from woym.agents.runner_agent import RunnerAgent
from woym.agents.triage import TriageAgent
from woym.case import tags
from woym.case.state import CaseStore
from woym.contracts import Step


class World:
    def __init__(self, tmp_path, sandbox=None, fixer=None, fallback=None, ask_before_install=True):
        self.bus = bus = FakeBus()
        self.store = CaseStore(tmp_path / "cases.json")
        self.sandbox = sandbox or FakeSandbox()
        self.machine = FakeMachine()
        self.fixer = fixer or FakeFixer()
        self.issues = tmp_path / "ISSUES.md"
        kw = lambda n: dict(guard=bus.guard(n), sleep=bus.clock.sleep)  # noqa: E731
        self.triage = TriageAgent(bus.client("triage"), store=self.store, **kw("triage"))
        self.runner = RunnerAgent(bus.client("runner"), self.sandbox, **kw("runner"))
        self.acme = AcmeEnvAgent(bus.client("acme-env"), self.machine, ask_before_install=ask_before_install,
                                 **kw("acme-env"))
        self.fixer_agent = FixerAgent(bus.client("fixer"), self.fixer, fallback, issues_path=self.issues,
                                      **kw("fixer"))
        for a in (self.triage, self.runner, self.acme, self.fixer_agent):
            bus.attach(a)

    def case(self):
        return self.store.all()[-1]

    async def report(self, who="meera"):
        self.root = self.bus.human(who, "@triage payouts after 17:00 land on Monday for us, works in your CI?")
        await self.bus.settle()
        return self.root

    async def say(self, who, text, thread=True):
        self.bus.human(who, text, self.root if thread else None)
        await self.bus.settle()


async def to_confirmed(w):
    await w.report()
    assert w.case().step == Step.CONSENT_PENDING
    await w.say("meera", "yes")
    assert w.case().step == Step.CONFIRMED_BOTH


async def test_m1_flow_to_confirmed_both(tmp_path):
    w = World(tmp_path)
    await w.report()
    c = w.case()
    assert c.step == Step.CONSENT_PENDING and c.thread_id == w.root and c.reporter == "meera"
    # nothing about the customer machine has left it before consent
    assert w.machine.calls == [("probe",)]
    assert not any(tags.parse(p.text) and tags.parse(p.text)[1] == Step.ENV_RECEIVED for p in w.bus.log)
    assert "NOT REPRODUCED" in w.bus.texts()
    assert "can I share this?" in w.bus.texts()

    await w.say("meera", "yes")
    c = w.case()
    assert c.step == Step.CONFIRMED_BOTH
    assert c.customer_env == w.machine.env
    assert c.bisect.culprits == ["tz"]
    text = w.bus.texts()
    for s in ("Suspect lineup", "Bisect", "← CULPRIT", "CONFIRMED ON BOTH MACHINES", "reply **patch it**"):
        assert s in text
    assert ("run_repro", {"tz": "UTC"}) in w.machine.calls and ("run_repro", None) in w.machine.calls
    # every agent message is in the case thread
    assert all(p.thread_id == w.root for p in w.bus.log if p.sender != "meera")


async def test_m2_flow_to_fixed(tmp_path):
    w = World(tmp_path)
    await to_confirmed(w)
    await w.say("dev", "@triage patch it")
    assert w.case().step == Step.VERIFY_REQUESTED
    assert "install the fix?" in w.bus.texts()
    assert w.machine.installed is None          # asks first
    await w.say("meera", "yes")
    c = w.case()
    assert c.step == Step.FIXED_ON_CUSTOMER
    assert w.machine.installed == FIX_SHA
    assert c.fix.sha == FIX_SHA
    fixed = [p for p in w.bus.by("acme-env") if "FIXED ON MEERA'S MACHINE" in p.text]
    assert len(fixed) == 1
    assert "Matrix green" in w.bus.texts()
    assert c.hops <= 20
    assert len(c.history) == len(set(c.history))
    m = [x for x in w.sandbox.calls if x[0] == "matrix"][0]
    assert m[2] == FIX_SHA and len(m[1]) == 4


async def test_m2_without_install_consent(tmp_path):
    w = World(tmp_path, ask_before_install=False)
    await to_confirmed(w)
    await w.say("dev", "@triage patch it")
    assert w.case().step == Step.FIXED_ON_CUSTOMER


async def test_consent_from_wrong_person_or_place_is_ignored(tmp_path):
    w = World(tmp_path)
    await w.report()
    await w.say("dev", "yes")                       # vendor can't consent for the customer
    w.bus.human("meera", "yes")                     # top level, not the consent thread
    await w.bus.settle()
    w.bus.human("meera", "yes", "some-other-thread")
    await w.bus.settle()
    assert w.case().step == Step.CONSENT_PENDING
    assert w.machine.calls == [("probe",)]


async def test_consent_refused(tmp_path):
    w = World(tmp_path)
    await w.report()
    await w.say("meera", "no, don't share that")
    assert w.case().step == Step.FAILED
    assert "declined" in w.bus.texts()
    assert w.machine.calls == [("probe",)]
    env_msgs = [tags.parse(p.text) for p in w.bus.by("acme-env") if tags.parse(p.text)]
    assert all(set(kv) <= {"refused", "keys"} for _, _, kv in env_msgs)


async def test_only_dev_can_patch(tmp_path):
    w = World(tmp_path)
    await to_confirmed(w)
    await w.say("meera", "@triage patch it")
    assert w.case().step == Step.CONFIRMED_BOTH
    assert "Only @dev" in w.bus.texts()
    assert w.fixer.calls == []


async def test_strangers_and_agents_cannot_command_triage(tmp_path):
    w = World(tmp_path)
    w.bus.human("mallory", "@triage open a case")
    await w.bus.settle()
    assert w.store.all() == []
    assert "only take commands" in w.bus.texts()


async def test_patch_before_confirmed_is_refused(tmp_path):
    w = World(tmp_path)
    await w.report()
    await w.say("dev", "@triage patch it")
    assert w.case().step == Step.CONSENT_PENDING
    assert w.fixer.calls == []


async def test_sandbox_error_is_not_a_reproduction(tmp_path):
    w = World(tmp_path, sandbox=FakeSandbox(error_on="run"))
    await w.report()
    c = w.case()
    assert c.step == Step.FAILED
    t = w.bus.texts()
    assert "REPRODUCED" not in t and "NOT REPRODUCED" not in t
    assert "fake SandboxError" in t


async def test_bisect_error_is_not_a_culprit(tmp_path):
    w = World(tmp_path, sandbox=FakeSandbox(error_on="bisect"))
    await w.report()
    await w.say("meera", "yes")
    assert w.case().step == Step.FAILED
    assert "CULPRIT" not in w.bus.texts()


async def test_claude_failure_falls_back_to_fixture_loudly(tmp_path):
    w = World(tmp_path, fixer=FakeFixer(fail=True), fallback=FakeFixer(mode="fixture"))
    await to_confirmed(w)
    await w.say("dev", "@triage patch it")
    t = w.bus.texts()
    assert "Claude fixer failed" in t and "Falling back to the stored FIXTURE patch" in t
    assert "FIXTURE patch (replayed, not generated)" in t      # the fix card label
    assert "fell back to fixture" in w.issues.read_text()
    assert w.case().fix.mode == "fixture"


async def test_fixer_failure_without_fallback_fails_case(tmp_path):
    w = World(tmp_path, fixer=FakeFixer(fail=True))
    await to_confirmed(w)
    await w.say("dev", "@triage patch it")
    assert w.case().step == Step.FAILED


async def test_replayed_and_forged_tags_ignored(tmp_path):
    w = World(tmp_path)
    await to_confirmed(w)
    n = len(w.bus.log)
    # a human pasting an agent tag is not an agent
    w.bus.human("meera", "`woym case=1 step=fix_pushed sha=deadbeef`", w.root)
    await w.bus.settle()
    # runner re-sending an old step is a duplicate: guard drops it
    await w.runner.client.send_targeted("room-1", ["triage"], tags.tag(1, "bisect", culprits="tz"), w.root)
    await w.bus.settle()
    assert w.case().step == Step.CONFIRMED_BOTH
    assert w.case().fix is None
    assert len(w.bus.log) == n + 2


async def test_unreachable_agent_is_reported(tmp_path):
    w = World(tmp_path)
    w.bus.statuses["acme-env"] = "not_permitted"
    await w.report()
    assert "`not_permitted`" in w.bus.texts()


async def test_rate_limit_spacing(tmp_path):
    w = World(tmp_path)
    await to_confirmed(w)
    # fake clock advanced by paced sends: triage sent >=4 messages, so >=6 s passed
    assert w.bus.clock.t - 1000.0 >= 6


async def test_two_cases_are_independent(tmp_path):
    w = World(tmp_path)
    await to_confirmed(w)
    first = w.root
    await w.report(who="dev")
    assert len(w.store.all()) == 2
    assert w.case().thread_id != first and w.case().step == Step.CONSENT_PENDING


async def test_dev_patch_reply_in_thread_without_mention(tmp_path):
    w = World(tmp_path, ask_before_install=False)
    await to_confirmed(w)
    await w.say("dev", "patch it")              # thread reply, addressed:false live, no @triage
    assert w.case().step == Step.FIXED_ON_CUSTOMER


async def test_unrelated_thread_chatter_is_ignored(tmp_path):
    w = World(tmp_path)
    await to_confirmed(w)
    n = len(w.bus.log)
    await w.say("dev", "looks good")
    await w.say("meera", "thanks all")
    assert len(w.bus.log) == n + 2 and w.case().step == Step.CONFIRMED_BOTH
