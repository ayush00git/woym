"""Known-culprits epilogue (WOYM_KNOWN) on the FAKE bus: case #1 in Meera's room
is fixed and verified, then Kiran's case in a second room is answered from that
record with zero sandbox runs, after the evidence is re-read from room history."""

import pytest

from fakes import FIX_SHA, FakeBus, FakeClient, FakeFixer, FakeMachine, FakeSandbox
from woym.agents.acme_env import KNOWN_RUNS_NOTE, AcmeEnvAgent
from woym.agents.base import MultiRoomClient
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


class RoomBound(FakeClient):
    """A per-room connection: it may only ever post into its own room."""

    def __init__(self, bus, name, room):
        super().__init__(bus, name)
        self.room = room

    async def post(self, room_id, text, thread_id=None):
        assert room_id == self.room, f"{self.name}'s {self.room} connection posted into {room_id}"
        return await super().post(room_id, text, thread_id)

    async def send_targeted(self, room_id, targets, text, thread_id=None):
        assert room_id == self.room, f"{self.name}'s {self.room} connection sent into {room_id}"
        return await super().send_targeted(room_id, targets, text, thread_id)

    async def thread(self, room_id, thread_id):
        assert room_id == self.room
        return await super().thread(room_id, thread_id)


class TwoRooms:
    def __init__(self, tmp_path, known_on=True, kiran_env=KIRAN_ENV, multiroom=False, fix_rooms=None):
        self.bus = bus = FakeBus(room=R1)
        bus.members = {R1: {"triage", "runner", "fixer", "acme-env"}, R2: {"triage", "runner", "nepal-env"}}
        # (runner in room 2 too: if no known fix matches, Kiran's case is investigated normally)
        self.store = CaseStore(tmp_path / "cases.json")
        self.known = KnownStore(tmp_path / "known.json")
        self.sandbox = FakeSandbox()
        self.meera_machine, self.kiran_machine = FakeMachine(), FakeMachine()
        self.kiran_machine.env = kiran_env
        kw = lambda n: dict(guard=bus.guard(n), sleep=bus.clock.sleep)  # noqa: E731
        tri_client = bus.client("triage")
        if multiroom:  # one connection per room, as live (platform's two_rooms kill test)
            self.tri_rooms = {R1: RoomBound(bus, "triage", R1), R2: RoomBound(bus, "triage", R2)}
            tri_client = MultiRoomClient(self.tri_rooms)
        self.triage = TriageAgent(tri_client, store=self.store, known_store=self.known, known_on=known_on,
                                  customers={R1: ("meera", "acme-env"), R2: ("kiran", "nepal-env")},
                                  fix_rooms=fix_rooms, **kw("triage"))
        self.fixer = FakeFixer()
        agents = [
            self.triage,
            RunnerAgent(bus.client("runner"), self.sandbox, **kw("runner")),
            FixerAgent(bus.client("fixer"), self.fixer, issues_path=tmp_path / "ISSUES.md", **kw("fixer")),
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



# ---------------------------------------------------------------- post-freeze: wording, multi-room

async def test_known_path_consent_wording(tmp_path):
    w = TwoRooms(tmp_path)
    r1 = await w.case1_fixed()
    first = next(p.text for p in w.bus.log if p.room == R1 and "can I share this?" in p.text)
    assert "**2×**" in first and KNOWN_RUNS_NOTE not in first           # no records yet: today's wording
    await w.kiran_case()
    second = next(p.text for p in w.bus.log if p.room == R2 and "can I share this?" in p.text)
    assert KNOWN_RUNS_NOTE in second and "2×" not in second
    assert "Asia/Kathmandu" in second                                   # Kiran still sees all 5 values


async def test_flag_off_consent_and_env_req_unchanged(tmp_path):
    w = TwoRooms(tmp_path, known_on=False)
    await w.case1_fixed()
    w.bus.human("kiran", "@triage orders after 17:20 settle Monday", room=R2)
    await w.bus.settle()
    reqs = [p.text for p in w.bus.log if "step=env_req" in p.text]
    assert reqs and all("known=" not in t for t in reqs)
    assert all(KNOWN_RUNS_NOTE not in p.text for p in w.bus.log)


async def test_triage_over_multiroom_client_never_crosses_rooms(tmp_path):
    w = TwoRooms(tmp_path, multiroom=True)
    await w.case1_fixed()
    root, case2 = await w.kiran_case()
    assert case2.step == Step.KNOWN_FIXED
    # each room's triage traffic went through that room's own connection (RoomBound asserts the room)
    tri = [p for p in w.bus.log if p.sender == "triage"]
    assert {p.room for p in tri if "case=1 " in p.text} == {R1}
    assert {p.room for p in tri if "case=2 " in p.text} == {R2}
    assert any("KNOWN ISSUE" in p.text for p in tri if p.room == R2)


class _Rec:
    def __init__(self, name):
        self.name, self.calls = name, []

    async def run(self, handler):
        self.calls.append(("run", handler))

    async def post(self, room_id, text, thread_id=None):
        self.calls.append(("post", room_id))
        return "m"

    async def send_targeted(self, room_id, targets, text, thread_id=None):
        self.calls.append(("send", room_id))
        return {}

    async def participants(self, room_id):
        self.calls.append(("participants", room_id))
        return []

    async def thread(self, room_id, thread_id):
        self.calls.append(("thread", room_id))
        return []

    async def aclose(self):
        self.calls.append(("close",))


async def test_multiroom_client_routes_by_room():
    a, b = _Rec("triage"), _Rec("triage")
    m = MultiRoomClient({R1: a, R2: b})
    assert m.name == "triage"
    await m.post(R2, "x")
    await m.send_targeted(R1, ["runner"], "y")
    await m.thread(R2, "t")
    await m.participants(R1)
    assert a.calls == [("send", R1), ("participants", R1)]
    assert b.calls == [("post", R2), ("thread", R2)]
    with pytest.raises(KeyError, match="not connected to room nope"):
        await m.post("nope", "z")
    handler = object()
    await m.run(handler)
    assert ("run", handler) in a.calls and ("run", handler) in b.calls
    await m.aclose()
    assert ("close",) in a.calls and ("close",) in b.calls
    with pytest.raises(ValueError):
        MultiRoomClient({})


@pytest.mark.parametrize("flag,expected", [(False, []), (True, ["acme-nepal"])])
def test_triage_main_serves_side_room_only_with_flag(monkeypatch, flag, expected):
    from woym import config
    from woym.agents import base, triage
    from woym.switch import creds

    data = {"room": {"id": "rid-main"},
            "rooms": {"acme-support": {"id": "rid-main"}, "acme-nepal": {"id": "rid-np"}},
            "humans": {"kiran": {"switch_participant_id": "@switch-mattermost-b-kiran:localhost"}}}
    monkeypatch.setattr(creds, "load", lambda *a, **k: data)
    monkeypatch.setattr(config, "KNOWN_ENABLED", flag)
    monkeypatch.setitem(config.CUSTOMERS, "acme-support", ("meera", "acme-env", "acme-laptop"))
    got = {}
    monkeypatch.setattr(base, "run_agent", lambda make, name, **kw: got.update(kw, name=name, make=make))
    triage.main()
    assert got["name"] == "triage" and got["extra_rooms"] == expected
    agent = got["make"](_Rec("triage"))
    assert agent.fix_rooms == ({"rid-main"} if flag else None)
    assert agent.customers == {"rid-main": ("meera", "acme-env"), "rid-np": ("kiran", "nepal-env")}
    assert agent.human_ids["kiran"].endswith("-kiran:localhost")



# ---------------------------------------------------------------- runner in side rooms; no fixer there

async def test_no_match_in_side_room_bisects_then_stops_at_confirmed(tmp_path):
    """No known record: Kiran's case is investigated for real in room 2 (runner serves it),
    confirmed on Kiran's machine, then stops with a clear line because fixer serves room 1 only."""
    from woym.agents.triage import NO_FIX_HERE

    w = TwoRooms(tmp_path, fix_rooms={R1})
    root, case2 = await w.kiran_case()
    assert case2.step == Step.CONFIRMED_BOTH
    r2 = "\n".join(p.text for p in w.bus.log if p.room == R2)
    assert "NOT REPRODUCED" in r2 and "← CULPRIT" in r2 and "CONFIRMED ON BOTH MACHINES" in r2
    assert NO_FIX_HERE in r2 and "reply **patch it**" not in r2
    assert ("run_repro", {"tz": "UTC"}) in w.kiran_machine.calls
    w.bus.human("dev", "patch it", root, room=R2)
    await w.bus.settle()
    assert w.store.all()[-1].step == Step.CONFIRMED_BOTH and w.fixer.calls == []
    assert r2.count(NO_FIX_HERE) == 1 and "\n".join(p.text for p in w.bus.log).count(NO_FIX_HERE) == 2


async def test_fix_rooms_default_is_todays_behaviour(tmp_path):
    w = TwoRooms(tmp_path, known_on=False)          # fix_rooms=None
    await w.case1_fixed()                           # patch prompt + fix work in room 1 as before
    assert w.fixer.calls and "reply **patch it**" in "\n".join(p.text for p in w.bus.log)


@pytest.mark.parametrize("flag,expected", [(False, []), (True, ["acme-nepal"])])
def test_runner_main_serves_side_room_only_with_flag(monkeypatch, flag, expected):
    from woym import config
    from woym.agents import base, runner_agent
    from woym.switch import creds

    data = {"room": {"id": "rid-main"}, "rooms": {"acme-support": {"id": "rid-main"}, "acme-nepal": {"id": "rid-np"}}}
    monkeypatch.setattr(creds, "load", lambda *a, **k: data)
    monkeypatch.setattr(config, "KNOWN_ENABLED", flag)
    monkeypatch.setitem(config.CUSTOMERS, "acme-support", ("meera", "acme-env", "acme-laptop"))
    got = {}
    monkeypatch.setattr(base, "run_agent", lambda make, name, **kw: got.update(kw, name=name))
    monkeypatch.setattr(runner_agent, "run_agent", base.run_agent)
    runner_agent.main()
    assert got["name"] == "runner" and got["extra_rooms"] == expected


def test_side_rooms_skips_missing_and_active(monkeypatch):
    from woym import config
    from woym.agents import base
    from woym.switch import creds

    monkeypatch.setattr(config, "KNOWN_ENABLED", True)
    monkeypatch.setitem(config.CUSTOMERS, "acme-support", ("meera", "acme-env", "acme-laptop"))
    monkeypatch.setattr(creds, "load", lambda *a, **k: {"room": {"id": "x"}, "rooms": {"acme-support": {"id": "x"}}})
    assert base.side_rooms() == []                    # acme-nepal not set up yet
