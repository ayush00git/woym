"""Case store, state transitions and the loop guard (docs/contracts.md §3).

The store (var/cases.json) is owned by @triage, the only agent that tracks the
whole case. The other agents are stateless per case apart from their own
`SendGuard`, which enforces the per-agent part of the loop guard.

Loop guard:
- each agent fires each step at most once per case (history entries "agent:step");
- at most MAX_HOPS_PER_CASE tagged hops per case;
- at most one targeted send per agent every MIN_SECONDS_BETWEEN_SENDS;
- agents ignore their own messages and Switch system messages (`should_ignore`).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from woym import config
from woym.contracts import (
    BisectReport,
    BisectStep,
    Case,
    Env,
    FixResult,
    MessageEvent,
    RunResult,
    Step,
)

# --------------------------------------------------------------------------
# Transitions (triage's view of the case)
# --------------------------------------------------------------------------

TERMINAL = frozenset({Step.FIXED_ON_CUSTOMER, Step.FAILED, Step.KNOWN_FIXED})

TRANSITIONS: dict[Step, frozenset[Step]] = {
    # REPORTED -> ENV_REQUESTED and ENV_RECEIVED -> KNOWN_FIXED are only taken with
    # WOYM_KNOWN=1 and a known-fix record (woym/case/known.py).
    Step.REPORTED: frozenset({Step.SANDBOX_RUN, Step.ENV_REQUESTED}),
    Step.SANDBOX_RUN: frozenset({Step.NOT_REPRODUCED, Step.REPRODUCED}),
    Step.NOT_REPRODUCED: frozenset({Step.ENV_REQUESTED}),
    Step.REPRODUCED: frozenset({Step.FIX_REQUESTED}),
    Step.ENV_REQUESTED: frozenset({Step.CONSENT_PENDING, Step.ENV_RECEIVED}),
    Step.CONSENT_PENDING: frozenset({Step.ENV_RECEIVED}),
    Step.ENV_RECEIVED: frozenset({Step.BISECTING, Step.KNOWN_FIXED}),
    Step.BISECTING: frozenset({Step.CULPRIT_FOUND}),
    Step.CULPRIT_FOUND: frozenset({Step.CONFIRM_REQUESTED}),
    Step.CONFIRM_REQUESTED: frozenset({Step.CONFIRMED_BOTH}),
    Step.CONFIRMED_BOTH: frozenset({Step.FIX_REQUESTED}),
    Step.FIX_REQUESTED: frozenset({Step.FIX_PUSHED}),
    Step.FIX_PUSHED: frozenset({Step.MATRIX_GREEN}),
    Step.MATRIX_GREEN: frozenset({Step.VERIFY_REQUESTED}),
    Step.VERIFY_REQUESTED: frozenset({Step.FIXED_ON_CUSTOMER}),
    Step.FIXED_ON_CUSTOMER: frozenset(),
    Step.KNOWN_FIXED: frozenset(),
    Step.FAILED: frozenset(),
}


class TransitionError(ValueError):
    pass


def can_advance(current: Step, new: Step) -> bool:
    if current in TERMINAL:
        return False
    return new == Step.FAILED or new in TRANSITIONS[current]


def advance(case: Case, new: Step) -> Case:
    """Move the case to `new` or raise TransitionError. Any non-terminal step
    may go to FAILED."""
    if not can_advance(case.step, new):
        raise TransitionError(f"case {case.id}: {case.step.value} -> {new.value} not allowed")
    case.step = new
    return case


# --------------------------------------------------------------------------
# Loop guard
# --------------------------------------------------------------------------


class GuardError(RuntimeError):
    pass


def history_key(agent: str, step: Step | str) -> str:
    return f"{agent}:{Step(step).value}"


def record_hop(case: Case, agent: str, step: Step | str, max_hops: int | None = None) -> None:
    """Record that `agent` fired `step` on this case. Raises GuardError if it
    already did, or if the case is out of hops. Mutates the case."""
    max_hops = config.MAX_HOPS_PER_CASE if max_hops is None else max_hops
    key = history_key(agent, step)
    if key in case.history:
        raise GuardError(f"case {case.id}: {key} already fired")
    if case.hops >= max_hops:
        raise GuardError(f"case {case.id}: hop limit {max_hops} reached")
    case.history.append(key)
    case.hops += 1


class SendGuard:
    """Per-agent loop guard: each (case, step) once, a hop cap per case and a
    minimum gap between sends. Pure apart from the injectable clock."""

    def __init__(
        self,
        agent: str,
        min_gap_s: float | None = None,
        max_hops: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.agent = agent
        self.min_gap_s = config.MIN_SECONDS_BETWEEN_SENDS if min_gap_s is None else min_gap_s
        self.max_hops = config.MAX_HOPS_PER_CASE if max_hops is None else max_hops
        self.clock = clock
        self._fired: set[tuple[int, str]] = set()
        self._hops: dict[int, int] = {}
        self._last_send: float | None = None

    def check(self, case_id: int, step: Step | str) -> str | None:
        """None if allowed, else the reason it is refused."""
        step = Step(step)
        if (case_id, step.value) in self._fired:
            return f"{self.agent} already sent {step.value} for case {case_id}"
        if self._hops.get(case_id, 0) >= self.max_hops:
            return f"{self.agent} hit the hop limit ({self.max_hops}) for case {case_id}"
        return None

    def wait_s(self) -> float:
        """Seconds to wait before the next send is allowed (0 if none)."""
        if self._last_send is None:
            return 0.0
        return max(0.0, self.min_gap_s - (self.clock() - self._last_send))

    def record(self, case_id: int, step: Step | str) -> None:
        reason = self.check(case_id, step)
        if reason:
            raise GuardError(reason)
        if self.wait_s() > 0:
            raise GuardError(f"{self.agent} is sending too fast (wait {self.wait_s():.2f}s)")
        self._fired.add((case_id, Step(step).value))
        self._hops[case_id] = self._hops.get(case_id, 0) + 1
        self._last_send = self.clock()

    def record_untagged(self) -> None:
        """Rate-limit a send that carries no case step (e.g. a card post)."""
        if self.wait_s() > 0:
            raise GuardError(f"{self.agent} is sending too fast (wait {self.wait_s():.2f}s)")
        self._last_send = self.clock()


def should_ignore(event: MessageEvent, my_name: str) -> bool:
    """Agents ignore their own messages and Switch system messages."""
    return event.sender_kind == "system" or event.sender_name == my_name


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _env(d: dict | None) -> Env | None:
    return Env(**d) if d else None


def _run(d: dict | None) -> RunResult | None:
    if not d:
        return None
    return RunResult(**{**d, "env": Env(**d["env"])})


def _bisect(d: dict | None) -> BisectReport | None:
    if not d:
        return None
    return BisectReport(
        baseline=_run(d["baseline"]),
        target=_run(d["target"]),
        steps=[BisectStep(**{**s, "result": _run(s["result"])}) for s in d["steps"]],
        culprits=list(d["culprits"]),
    )


def _fix(d: dict | None) -> FixResult | None:
    return FixResult(**{**d, "files_changed": list(d["files_changed"])}) if d else None


def case_to_dict(case: Case) -> dict:
    d = asdict(case)
    d["step"] = case.step.value
    return d


def case_from_dict(d: dict) -> Case:
    return Case(
        id=d["id"],
        room_id=d["room_id"],
        thread_id=d.get("thread_id"),
        reporter=d["reporter"],
        step=Step(d.get("step", Step.REPORTED.value)),
        vendor_env=_env(d.get("vendor_env")),
        customer_env=_env(d.get("customer_env")),
        bisect=_bisect(d.get("bisect")),
        fix=_fix(d.get("fix")),
        hops=d.get("hops", 0),
        history=list(d.get("history", [])),
    )


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


@dataclass
class CaseStore:
    """JSON-file case store. Writes are atomic (temp file + rename)."""

    path: Path = field(default_factory=lambda: config.CASES_DB)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"next_id": 1, "cases": {}}
        return json.loads(self.path.read_text())

    def _dump(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".cases.", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def open_case(self, room_id: str, thread_id: str | None, reporter: str) -> Case:
        with self._lock:
            data = self._load()
            case = Case(id=data["next_id"], room_id=room_id, thread_id=thread_id, reporter=reporter)
            data["next_id"] += 1
            data["cases"][str(case.id)] = case_to_dict(case)
            self._dump(data)
            return case

    def get(self, case_id: int) -> Case | None:
        d = self._load()["cases"].get(str(case_id))
        return case_from_dict(d) if d else None

    def save(self, case: Case) -> None:
        with self._lock:
            data = self._load()
            data["cases"][str(case.id)] = case_to_dict(case)
            data["next_id"] = max(data["next_id"], case.id + 1)
            self._dump(data)

    def by_thread(self, room_id: str, thread_id: str) -> Case | None:
        for d in self._load()["cases"].values():
            if d["room_id"] == room_id and d.get("thread_id") == thread_id:
                return case_from_dict(d)
        return None

    def all(self) -> list[Case]:
        return [case_from_dict(d) for d in self._load()["cases"].values()]
