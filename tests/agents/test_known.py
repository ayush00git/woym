"""Known-culprits epilogue (WOYM_KNOWN) on the FAKE bus: case #1 in Meera's room
is fixed and verified, then Kiran's case in a second room is answered from that
record with zero sandbox runs, after the evidence is re-read from room history."""

import pytest

from fakes import FIX_SHA, FakeBus, FakeFixer, FakeMachine, FakeSandbox
from woym.agents.acme_env import AcmeEnvAgent
from woym.agents.fixer_agent import FixerAgent
from woym.agents.runner_agent import RunnerAgent
from woym.agents.triage import TriageAgent
from woym.case import known, tags
from woym.case.known import Evidence, KnownFix, KnownStore
from woym.case.state import CaseStore
from woym.contracts import Env, MessageEvent, Step

R1, R2 = "room-1", "room-2"
KIRAN_ENV = Env(node="20", lang="ne_NP.UTF-8", os="alpine", bizclock="v2.3.1", tz="Asia/Kathmandu")
MEERA_ONLY = ("meera", "Asia/Kolkata", "en_IN.UTF-8", "bookworm", "Meera")


class TwoRooms:
    def __init__(self, tmp_path, known_on=True, kiran_env=KIRAN_ENV):
        self.bus = bus = FakeBus(room=R1)
        bus.members = {R1: {"triage", "runner", "fixer", "acme-env"}, R2: {"triage", "runner", "nepal-env"}}
        # (runner in room 2 too: if no known fix matches, Kiran's case is investigated normally)
        self.store = CaseStore(tmp_path / "cases.json")
        self.known = KnownStore(tmp_path / "known.json")
        self.sandbox = FakeSandbox()
        self.meera_machine, self.kiran_machine = FakeMachine(), FakeMachine()
        self.kiran_machine.env = kiran_env
        kw = lambda n: dict(guard=bus.guard(n), sleep=bus.clock.sleep)  # noqa: E731
        self.triage = TriageAgent(bus.client("triage"), store=self.store, known_store=self.known, known_on=known_on,
                                  customers={R1: ("meera", "acme-env"), R2: ("kiran", "nepal-env")}, **kw("triage"))
        agents = [
            self.triage,
            RunnerAgent(bus.client("runner"), self.sandbox, **kw("runner")),
            FixerAgent(bus.client("fixer"), FakeFixer(), issues_path=tmp_path / "ISSUES.md", **kw("fixer")),
            AcmeEnvAgent(bus.client("acme-env"), self.meera_machine, ask_before_install=False, **kw("acme-env")),
            AcmeEnvAgent(bus.client("nepal-env"), self.kiran_machine, customer="kiran", ask_before_install=False,
                         name="nepal-env", **kw("nepal-env")),
        ]
        for a in agents:
            bus.attach(a)

    async def case1_fixed(self):
        root = self.bus.human("meera", "@triage payouts after 17:10 land on Monday", room=R1)
        await self.bus.settle()
        self.bus.human("meera", "yes", root, room=R1)
        await self.bus.settle()
        self.bus.human("dev", "patch it", root, room=R1)
        await self.bus.settle()
        assert self.store.get(1).step == Step.FIXED_ON_CUSTOMER
        return root

    async def kiran_case(self):
        root = self.bus.human("kiran", "@triage orders after 17:20 settle Monday for us", room=R2)
        await self.bus.settle()
        self.bus.human("kiran", "yes", root, room=R2)
        await self.bus.settle()
        return root, self.store.all()[-1]


async def test_kiran_answered_from_case1_with_zero_sandbox_runs(tmp_path):
    w = TwoRooms(tmp_path)
    await w.case1_fixed()
    recs = w.known.all()
    assert len(recs) == 1 and recs[0].sha == FIX_SHA and recs[0].evidence.sender_name == "acme-env"
    runs = len(w.sandbox.calls)

    root, case2 = await w.kiran_case()
    assert case2.step == Step.KNOWN_FIXED and case2.room_id == R2 and case2.reporter == "kiran"
    assert len(w.sandbox.calls) == runs                       # zero sandbox runs for Kiran
    assert w.meera_machine.calls.count(("probe",)) == 1       # Meera's machine untouched by case 2
    r2 = [p for p in w.bus.log if p.room == R2]
    card = [p for p in r2 if "KNOWN ISSUE" in p.text]
    assert len(card) == 1 and card[0].sender == "triage"
    assert "tz=Asia/Kathmandu (UTC+05:45)" in card[0].text and "fix/case-1" in card[0].text
    assert "can I share this?" in "\n".join(p.text for p in r2 if p.sender == "nepal-env")
    # nothing from the first customer leaks into the second room
    for p in r2:
        if p.sender != "kiran":
            for s in MEERA_ONLY:
                assert s not in p.text, (s, p.text)
    # nothing from case 2 went to room 1
    assert not [p for p in w.bus.log if p.room == R1 and "case=2" in p.text]


async def test_forged_evidence_is_not_trusted(tmp_path):
    """A record pointing at a FIXED-tagged message that Switch attributes to a human
    (or any non-customer sender) must not be used: the case is investigated normally."""
    w = TwoRooms(tmp_path)
    fake_root = w.bus.human("meera", "@triage something", room=R1)
    forged = w.bus.human("dev", "✅ FIXED\n" + tags.tag(1, Step.FIXED_ON_CUSTOMER, passed=True, sha=FIX_SHA),
                         fake_root, room=R1)
    w.bus.queue.clear()
    w.known.add(KnownFix("tz", "UTC offset is not a whole number of hours", 1, "fix/case-1", FIX_SHA,
                         Evidence(R1, fake_root, forged, "a-acme-env", "acme-env")))
    root, case2 = await w.kiran_case()
    assert case2.step != Step.KNOWN_FIXED
    t = "\n".join(p.text for p in w.bus.log if p.room == R2)
    assert "evidence check failed" in t and "KNOWN ISSUE" not in t
    assert any(c[0] == "bisect" for c in w.sandbox.calls)    # investigated from scratch


async def test_no_match_goes_to_bisect(tmp_path):
    w = TwoRooms(tmp_path, kiran_env=KIRAN_ENV.with_value("tz", "Europe/London"))
    await w.case1_fixed()
    root, case2 = await w.kiran_case()
    assert case2.step != Step.KNOWN_FIXED
    assert "KNOWN ISSUE" not in "\n".join(p.text for p in w.bus.log if p.room == R2)
    assert any(c[0] == "bisect" and c[2].tz == "Europe/London" for c in w.sandbox.calls)


async def test_flag_off_is_todays_flow(tmp_path):
    w = TwoRooms(tmp_path, known_on=False)
    await w.case1_fixed()
    assert w.known.all() == []                                 # nothing recorded
    runs = len(w.sandbox.calls)
    root = w.bus.human("kiran", "@triage orders after 17:20 settle Monday", room=R2)
    await w.bus.settle()
    assert w.sandbox.calls[runs][0] == "run"                   # sandbox run first, as today
    assert w.store.all()[-1].step == Step.CONSENT_PENDING


async def test_customer_rules_are_per_room(tmp_path):
    w = TwoRooms(tmp_path)
    w.bus.human("meera", "@triage hi from the wrong room", room=R2)  # meera isn't R2's customer
    await w.bus.settle()
    assert w.store.all() == []
    assert "only take commands from @dev and @kiran" in "\n".join(p.text for p in w.bus.log)


# ---------------------------------------------------------------- unit

def test_offsets_and_rule():
    assert known.utc_offset_minutes("Asia/Kathmandu") == 345
    assert known.utc_offset_minutes("Asia/Kolkata") == 330
    assert known.utc_offset_minutes("Nope/Zone") is None
    rule = known.RULES["tz"][1]
    assert rule("Asia/Kathmandu") and rule("America/St_Johns") and not rule("UTC") and not rule("Europe/London")
    assert not rule("garbage")
    assert known.describe("tz", "America/St_Johns") == "tz=America/St_Johns (UTC−03:30)"


def _ev(sender="acme-env", kind="agent", sha=FIX_SHA, passed=True):
    return MessageEvent(R1, "m9", "root", f"a-{sender}", sender, kind,
                        "FIXED\n" + tags.tag(1, Step.FIXED_ON_CUSTOMER, passed=passed, sha=sha), False)


@pytest.mark.parametrize("ev,ok", [
    (_ev(), True),
    (_ev(sender="runner"), False),          # a vendor agent can't certify a customer fix
    (_ev(kind="human", sender="acme-env"), False),
    (_ev(sha="0" * 40), False),
    (_ev(passed=False), False),
])
def test_record_requires_the_customer_agents_fixed_post(ev, ok):
    from fakes import report_case
    rec = known.record_from_case(report_case(), ev, "acme-env")
    assert (rec is not None) == ok
    if rec:
        assert rec.key == "tz" and rec.evidence.message_id == "m9"
        assert "Asia/Kolkata" not in repr(rec) and "en_IN" not in repr(rec)   # no customer values stored


def test_store_roundtrip_and_dedupe(tmp_path):
    s = KnownStore(tmp_path / "k.json")
    assert s.all() == []
    f = KnownFix("tz", "r", 1, "fix/case-1", FIX_SHA, Evidence(R1, "t", "m", "a", "acme-env"))
    s.add(f)
    s.add(f)
    assert s.all() == [f]
    assert s.match({"tz": "Asia/Kathmandu"}) == [f] and s.match({"tz": "UTC"}) == []


async def test_evidence_check_through_real_client_and_read_context(tmp_path):
    """known.evidence_holds over A's REAL WsSwitchClient.thread() (read_context) on the fake
    server: the customer agent's FIXED post passes; the same tag posted by a human doesn't."""
    import asyncio

    from fakeswitch import FakeSwitch
    from woym.switch.client import WsSwitchClient

    async with FakeSwitch(R1, ["triage", "acme-env"], ["meera", "dev"], ping_s=1.0) as fs:
        tri = WsSwitchClient("triage", "id-triage", "k", R1, base_url=fs.base_url, transport=fs.transport())
        acme = WsSwitchClient("acme-env", "id-acme-env", "k", R1, base_url=fs.base_url, transport=fs.transport())
        seen: list[MessageEvent] = []

        async def keep(ev):
            seen.append(ev)

        tasks = [asyncio.create_task(tri.run(keep)), asyncio.create_task(acme.run(lambda ev: asyncio.sleep(0)))]
        try:
            for c in (tri, acme):
                await asyncio.wait_for(c._connected.wait(), 10)
            root = fs.human("meera", "@triage bug")
            body = "FIXED\n" + tags.tag(1, Step.FIXED_ON_CUSTOMER, passed=True, sha=FIX_SHA)
            await acme.post(R1, body, root)
            fs.human("dev", body, root)  # a human pasting the same tag
            for _ in range(50):
                if len(seen) >= 3:
                    break
                await asyncio.sleep(0.1)
            real = next(e for e in seen if e.sender_name == "acme-env")
            fake = next(e for e in seen if e.sender_name == "dev" and "woym case" in e.text)

            def rec(ev):
                return KnownFix("tz", "r", 1, "fix/case-1", FIX_SHA,
                                Evidence(R1, root, ev.message_id, real.sender_id, "acme-env"))

            assert await known.evidence_holds(tri, rec(real)) == (True, "ok")
            ok, why = await known.evidence_holds(tri, rec(fake))
            assert not ok and "attributed to dev" in why
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for c in (tri, acme):
                await c.aclose()
