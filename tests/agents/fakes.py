"""FAKES for agent tests: an in-memory Switch room and fake engines.

FakeBus delivers every message to every other connected agent (like
filter=all), with addressed=True only for send_targeted targets.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from woym.case.state import SendGuard
from woym.contracts import BisectReport, BisectStep, Env, FixResult, MessageEvent, Participant, RunResult

EXPECTED = "cutoff=Fri 17:30 payout=Fri"
# Live Switch: an "@name" in ANY agent post addresses that participant, and an
# agent whose policy refuses the sender posts a visible refusal. Agents may only
# @-mention humans (deliberate pings), never agents.
AGENT_MENTION = re.compile(r"@(triage|runner|fixer|acme-env)\b")


def assert_no_agent_mention(sender, text):
    m = AGENT_MENTION.search(text)
    assert m is None, f"{sender} posted an @agent mention {m.group(0)!r}:\n{text}"
BAD = "cutoff=Mon 17:00 payout=Mon"
FIX_SHA = "f1" * 20


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


@dataclass
class Posted:
    id: str
    sender: str
    kind: str
    text: str
    thread_id: str | None
    targets: list[str] = field(default_factory=list)


class FakeBus:
    def __init__(self, room="room-1"):
        self.room = room
        self.clients: dict[str, "FakeClient"] = {}
        self.handlers = {}
        self.log: list[Posted] = []
        self.queue: list[tuple[Posted, str]] = []
        self.statuses: dict[str, str] = {}  # override per target, e.g. {"acme-env": "not_permitted"}
        self._ids = itertools.count(1)
        self.clock = Clock()

    def client(self, name):
        c = FakeClient(self, name)
        self.clients[name] = c
        return c

    def attach(self, agent):
        self.handlers[agent.client.name] = agent.handle

    def guard(self, name):
        return SendGuard(name, clock=self.clock)

    def _emit(self, sender, kind, text, thread_id, targets=()):
        p = Posted(f"m{next(self._ids)}", sender, kind, text, thread_id, list(targets))
        self.log.append(p)
        for name in self.handlers:
            if name != sender:
                self.queue.append((p, name))
        return p

    def human(self, name, text, thread_id=None, sender_id=None):
        p = self._emit(name, "human", text, thread_id)
        p.sender_id = sender_id or f"u-{name}"
        return p.id

    async def settle(self, limit=500):
        n = 0
        while self.queue:
            p, name = self.queue.pop(0)
            ev = MessageEvent(
                room_id=self.room, message_id=p.id, thread_id=p.thread_id,
                sender_id=getattr(p, "sender_id", f"a-{p.sender}"), sender_name=p.sender,
                sender_kind=p.kind, text=p.text, addressed=name in p.targets,
            )
            await self.handlers[name](ev)
            n += 1
            assert n < limit, "message storm"

    def by(self, sender):
        return [p for p in self.log if p.sender == sender]

    def texts(self):
        return "\n".join(p.text for p in self.log)


class FakeClient:
    def __init__(self, bus, name):
        self.bus, self.name = bus, name

    async def run(self, handler):
        raise NotImplementedError

    async def post(self, room_id, text, thread_id=None):
        assert_no_agent_mention(self.name, text)
        return self.bus._emit(self.name, "agent", text, thread_id).id

    async def send_targeted(self, room_id, targets, text, thread_id=None):
        assert_no_agent_mention(self.name, text)
        self.bus._emit(self.name, "agent", text, thread_id, targets)
        return {t: self.bus.statuses.get(t, "live") for t in targets}

    async def participants(self, room_id):
        return [Participant(id=f"a-{n}", name=n, kind="agent") for n in self.bus.clients]

    async def thread(self, room_id, thread_id):
        return []


# ---------------------------------------------------------------- engines

def _result(env: Env, ref: str | None, where: str) -> RunResult:
    fixed = ref == FIX_SHA
    passed = fixed or env.tz in ("UTC", "Etc/UTC")
    return RunResult(env, passed, EXPECTED, EXPECTED if passed else BAD, 0 if passed else 1, 0.5, "", where)


class FakeSandbox:
    def __init__(self, error_on=None):
        self.calls = []
        self.error_on = error_on

    def _maybe_fail(self, what):
        if self.error_on == what:
            raise RuntimeError(f"fake SandboxError in {what}")

    def run(self, env, ref=None):
        self.calls.append(("run", env, ref))
        self._maybe_fail("run")
        return _result(env, ref, "vendor-sandbox")

    def bisect(self, vendor, customer):
        self.calls.append(("bisect", vendor, customer))
        self._maybe_fail("bisect")
        steps = [BisectStep(k, getattr(vendor, k), getattr(customer, k),
                            _result(vendor.with_value(k, getattr(customer, k)), None, "vendor-sandbox"))
                 for k in vendor.diff(customer)]
        culprits = [s.key for s in steps if not s.result.passed]
        return BisectReport(_result(vendor, None, "vendor-sandbox"), _result(customer, None, "vendor-sandbox"),
                            steps, culprits)

    def matrix(self, envs, ref):
        self.calls.append(("matrix", envs, ref))
        self._maybe_fail("matrix")
        return [_result(e, ref, "vendor-sandbox") for e in envs]


class FakeMachine:
    def __init__(self):
        self.env = Env(node="20", lang="en_IN.UTF-8", os="bookworm", bizclock="v2.3.1", tz="Asia/Kolkata")
        self.calls = []
        self.installed = None

    def probe(self):
        self.calls.append(("probe",))
        return self.env

    def run_repro(self, overrides=None):
        self.calls.append(("run_repro", overrides))
        env = self.env
        for k, v in (overrides or {}).items():
            env = env.with_value(k, v)
        return _result(env, self.installed, "customer-machine")

    def install(self, sha):
        self.calls.append(("install", sha))
        self.installed = sha


class FakeFixer:
    mode = "claude"

    def __init__(self, fail=False, mode="claude"):
        self.fail, self.mode, self.calls = fail, mode, []
        self.last_log = ["fake log line"]

    def fix(self, case_id, culprit, failing):
        self.calls.append((case_id, culprit, failing))
        if self.fail:
            raise RuntimeError("fake claude failure")
        return FixResult(f"fix/case-{case_id}", FIX_SHA, ["src/index.js", "test/cutoff-tz.test.js"],
                         "Friday 17:10 is before the cutoff in Asia/Kolkata", self.mode, "Use exact local time.")
