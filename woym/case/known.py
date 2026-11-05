"""Known culprits: answer a new customer from a fix already verified on another
customer's machine (flag WOYM_KNOWN, default off).

The record lives in var/known.json, but the source of truth is Switch room
history: each record points at the FIXED message posted by the customer's own
agent (e.g. acme-env, owned by meera). Before triage answers from a record, it
re-reads that message with read_context and checks that Switch still
attributes it to that agent, and that its tag says the fix passed at this sha.
The vendor can't forge that message, because Switch sets the sender.

Matching is code, not a model: a rule written from the root cause (for tz:
"the UTC offset isn't a whole number of hours").

A record never carries the first customer's environment values. Only the
culprit key, the rule, the branch and sha, and a pointer to the evidence.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from woym import config
from woym.case import tags
from woym.contracts import Case, MessageEvent, Step, SwitchClient

# The date the repro's order is placed (examples/payout.js: Fri 6 Nov 2026).
ORDER_AT = datetime(2026, 11, 6, 17, 10)


def utc_offset_minutes(tz: str, at: datetime = ORDER_AT) -> int | None:
    try:
        off = at.replace(tzinfo=ZoneInfo(tz)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return int(off.total_seconds() // 60) if off is not None else None


def _tz_not_whole_hour(value: str) -> bool:
    off = utc_offset_minutes(value)
    return off is not None and off % 60 != 0


# culprit key -> (human-readable rule, predicate on the customer's value)
RULES: dict[str, tuple[str, Callable[[str], bool]]] = {
    "tz": ("UTC offset is not a whole number of hours", _tz_not_whole_hour),
}


def describe(key: str, value: str) -> str:
    """E.g. 'tz=Asia/Kathmandu (UTC+05:45)'."""
    if key == "tz":
        off = utc_offset_minutes(value)
        if off is not None:
            sign = "+" if off >= 0 else "−"
            h, m = divmod(abs(off), 60)
            return f"tz={value} (UTC{sign}{h:02d}:{m:02d})"
    return f"{key}={value}"


@dataclass(frozen=True)
class Evidence:
    room_id: str
    thread_id: str
    message_id: str  # the customer agent's FIXED post
    sender_id: str  # as Switch attributed it
    sender_name: str  # the customer agent, e.g. "acme-env"


@dataclass(frozen=True)
class KnownFix:
    key: str
    rule: str
    case_id: int
    branch: str
    sha: str
    evidence: Evidence

    def matches(self, values: dict[str, str]) -> bool:
        rule = RULES.get(self.key)
        value = values.get(self.key)
        return bool(rule and value and rule[1](value))


def record_from_case(case: Case, fixed: MessageEvent, customer_agent: str) -> KnownFix | None:
    """A record from a case that just reached FIXED, or None if it doesn't
    qualify: one culprit key that has a rule, the rule holds for that
    customer's own value, and the FIXED post is the customer agent's, saying
    passed=1 at the case's fix sha."""
    if case.fix is None or case.bisect is None or case.customer_env is None:
        return None
    if len(case.bisect.culprits) != 1 or case.bisect.culprits[0] not in RULES:
        return None
    key = case.bisect.culprits[0]
    rule_text, holds = RULES[key]
    if not holds(getattr(case.customer_env, key)):
        return None  # the rule wouldn't even explain this customer
    if not _is_fixed_post(fixed, customer_agent, case.fix.sha):
        return None
    return KnownFix(
        key=key, rule=rule_text, case_id=case.id, branch=case.fix.branch, sha=case.fix.sha,
        evidence=Evidence(fixed.room_id, fixed.thread_id or "", fixed.message_id, fixed.sender_id,
                          fixed.sender_name),
    )


def _is_fixed_post(ev: MessageEvent, customer_agent: str, sha: str) -> bool:
    if ev.sender_kind != "agent" or ev.sender_name != customer_agent:
        return False
    parsed = tags.parse(ev.text)
    if parsed is None:
        return False
    _, step, kv = parsed
    return step == Step.FIXED_ON_CUSTOMER and kv.get("passed") == "1" and kv.get("sha") == sha


async def evidence_holds(client: SwitchClient, fix: KnownFix) -> tuple[bool, str]:
    """Re-read the evidence from Switch room history. (ok, reason)."""
    e = fix.evidence
    try:
        msgs = await client.thread(e.room_id, e.thread_id or e.message_id)
    except Exception as exc:  # history unavailable: never answer from an unchecked record
        return False, f"couldn't read room history: {type(exc).__name__}"
    msg = next((m for m in msgs if m.message_id == e.message_id), None)
    if msg is None:
        return False, "the FIXED message is no longer in room history"
    if msg.sender_id != e.sender_id or msg.sender_name != e.sender_name:
        return False, f"the FIXED message is attributed to {msg.sender_name}, not {e.sender_name}"
    if not _is_fixed_post(msg, e.sender_name, fix.sha):
        return False, "the FIXED message doesn't say passed at this sha"
    return True, "ok"


class KnownStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or config.KNOWN_DB)

    def all(self) -> list[KnownFix]:
        if not self.path.exists():
            return []
        out = []
        for d in json.loads(self.path.read_text()):
            out.append(KnownFix(**{**d, "evidence": Evidence(**d["evidence"])}))
        return out

    def add(self, fix: KnownFix) -> None:
        items = [f for f in self.all() if (f.key, f.sha) != (fix.key, fix.sha)] + [fix]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".known.", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump([asdict(x) for x in items], f, indent=2)
        os.replace(tmp, self.path)

    def match(self, values: dict[str, str]) -> list[KnownFix]:
        return [f for f in self.all() if f.matches(values)]


def enabled() -> bool:
    return bool(config.KNOWN_ENABLED)
