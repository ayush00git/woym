"""Shared types and interfaces for every WOYM component.

Owned by the lead. Code against these; if one is wrong or missing, tell the lead
(and log it in ISSUES.md) instead of changing it locally. Details and rationale
are in docs/contracts.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Protocol

# --------------------------------------------------------------------------
# Switch layer. Implemented in woym/switch/ (platform).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Participant:
    id: str
    name: str  # chat handle without "@"
    kind: str  # "human" | "agent"
    owner: str | None = None  # Switch owner id for agents, if known


@dataclass(frozen=True)
class MessageEvent:
    room_id: str
    message_id: str
    thread_id: str | None  # root message id of the thread, None for top level
    sender_id: str
    sender_name: str
    sender_kind: str  # "human" | "agent" | "system"
    text: str
    addressed: bool  # True when this agent was explicitly targeted


Handler = Callable[[MessageEvent], Awaitable[None]]


class SwitchClient(Protocol):
    """One connected agent. All hops between agents MUST use send_targeted:
    live, an "@name" inside post() DOES address that participant, and a refused
    address triggers a visible refusal (kill test, docs/switch-notes.md §0), so
    agent prose never contains "@<agent>"."""

    name: str

    async def run(self, handler: Handler) -> None:
        """Register/connect, answer pings, call handler for every message in
        the agent's rooms (addressed or not). Never returns unless cancelled.
        Must skip the agent's own messages before calling handler."""

    async def post(self, room_id: str, text: str, thread_id: str | None = None) -> str:
        """Post a message. Returns the new message id."""

    async def send_targeted(
        self, room_id: str, targets: list[str], text: str, thread_id: str | None = None
    ) -> dict[str, str]:
        """Post addressed to agent/user names. Returns status per agent target:
        "live" | "no_session" | "disconnected" | "not_permitted" | other."""

    async def participants(self, room_id: str) -> list[Participant]: ...

    async def thread(self, room_id: str, thread_id: str) -> list[MessageEvent]:
        """Root message + replies, oldest first (via read_context)."""


# --------------------------------------------------------------------------
# Sandbox layer. Implemented in woym/sandbox/ (sandbox).
# --------------------------------------------------------------------------

# The only environment facts that ever leave the customer machine, in this order.
ENV_KEYS: tuple[str, ...] = ("node", "lang", "os", "bizclock", "tz")


@dataclass(frozen=True)
class Env:
    node: str  # major version, e.g. "22"
    lang: str  # e.g. "en_US.UTF-8"
    os: str  # image family: "alpine" | "bookworm"
    bizclock: str  # git ref of the library under test, e.g. "main" or "v2.3.1"
    tz: str  # IANA zone, e.g. "UTC" or "Asia/Kolkata"

    def as_dict(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in ENV_KEYS}

    def with_value(self, key: str, value: str) -> "Env":
        d = self.as_dict()
        d[key] = value
        return Env(**d)

    def diff(self, other: "Env") -> list[str]:
        """Keys whose values differ, in ENV_KEYS order."""
        return [k for k in ENV_KEYS if getattr(self, k) != getattr(other, k)]


@dataclass(frozen=True)
class RunResult:
    env: Env
    passed: bool
    expected: str  # e.g. "cutoff=Fri 17:30 IST, payout=Fri"
    actual: str
    exit_code: int
    duration_s: float
    log_tail: str  # last ~20 lines of combined output
    where: str  # "vendor-sandbox" | "customer-machine"


@dataclass(frozen=True)
class BisectStep:
    key: str  # variable flipped from vendor value to customer value
    vendor_value: str
    customer_value: str
    result: RunResult


@dataclass(frozen=True)
class BisectReport:
    baseline: RunResult  # vendor env
    target: RunResult  # full customer env, inside the vendor sandbox
    steps: list[BisectStep]  # one per differing key, ENV_KEYS order
    culprits: list[str]  # keys that alone flip pass -> fail (pairs tried if none)


class Sandbox(Protocol):
    """Vendor-side runner: untrusted repro runs in throwaway containers."""

    def run(self, env: Env, ref: str | None = None) -> RunResult:
        """ref overrides env.bizclock (used for the matrix after a fix)."""

    def bisect(self, vendor: Env, customer: Env) -> BisectReport: ...

    def matrix(self, envs: list[Env], ref: str) -> list[RunResult]: ...


class CustomerMachine(Protocol):
    """Customer side. Used ONLY by @acme-env. Read-only allow-listed probes."""

    def probe(self) -> Env:
        """Read the allow-listed facts from the customer machine."""

    def run_repro(self, overrides: dict[str, str] | None = None) -> RunResult:
        """Run the customer's repro script on their machine, optionally with
        overridden env vars (e.g. {"tz": "UTC"} for the causality check)."""

    def install(self, sha: str) -> None:
        """Install the library at this commit (patched build) on the machine."""


# --------------------------------------------------------------------------
# Fixer layer. Implemented in woym/fixer/ (agents).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FixResult:
    branch: str  # e.g. "fix/case-42"
    sha: str  # read from the bare remote with git ls-remote, never from LLM text
    files_changed: list[str]
    test_name: str  # the failing-then-passing test that was added
    mode: str  # "claude" | "fixture"
    summary: str  # one or two lines for the chat card


class Fixer(Protocol):
    def fix(self, case_id: int, culprit: str, failing: RunResult) -> FixResult: ...


# --------------------------------------------------------------------------
# Case layer. Implemented in woym/case/ (agents).
# --------------------------------------------------------------------------


class Step(str, Enum):
    REPORTED = "reported"
    SANDBOX_RUN = "sandbox_run"
    NOT_REPRODUCED = "not_reproduced"
    REPRODUCED = "reproduced"  # reproduced in the vendor sandbox directly
    ENV_REQUESTED = "env_req"  # @triage -> @acme-env
    CONSENT_PENDING = "consent"  # @acme-env -> customer human
    ENV_RECEIVED = "env"  # @acme-env -> @triage
    BISECTING = "bisect"  # @triage -> @runner
    CULPRIT_FOUND = "culprit"
    CONFIRM_REQUESTED = "confirm_req"  # @triage -> @acme-env
    CONFIRMED_BOTH = "confirmed"
    FIX_REQUESTED = "fix_req"  # vendor human -> @triage -> @fixer
    FIX_PUSHED = "fix_pushed"
    MATRIX_GREEN = "matrix_green"
    VERIFY_REQUESTED = "verify_req"  # @triage -> @acme-env
    FIXED_ON_CUSTOMER = "fixed"
    KNOWN_FIXED = "known"  # epilogue (WOYM_KNOWN): matched a verified record, terminal
    FAILED = "failed"


@dataclass
class Case:
    id: int
    room_id: str
    thread_id: str | None
    reporter: str  # customer human handle
    step: Step = Step.REPORTED
    vendor_env: Env | None = None
    customer_env: Env | None = None
    bisect: BisectReport | None = None
    fix: FixResult | None = None
    hops: int = 0  # loop guard: max config.MAX_HOPS_PER_CASE (20) per case
    history: list[str] = field(default_factory=list)  # steps already fired
