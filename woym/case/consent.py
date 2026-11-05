"""Consent check for @acme-env (docs/contracts.md §1, §3 steps 6–7).

A reply only counts as consent when ALL of these hold:
- it comes from the customer human (by sender id when known, else by handle),
  and the sender is a human, not an agent or system;
- it is in the case's consent thread;
- it contains a yes-word and no refusal or hedge word.
Anything negative from the right person in the right thread is a refusal.
Everything else is ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from woym.contracts import MessageEvent

YES_WORDS = frozenset(
    {"yes", "y", "yep", "yeah", "yup", "ok", "okay", "sure", "approve", "approved", "allow", "allowed", "go", "👍", "✅"}
)
NO_WORDS = frozenset(
    {"no", "n", "nope", "nah", "don't", "dont", "not", "stop", "deny", "denied", "refuse", "never", "cancel",
     "wait", "hold", "but", "except", "only", "👎", "❌"}
)
_WORD_RE = re.compile(r"[a-z']+|👍|👎|✅|❌")
_MENTION_RE = re.compile(r"@[\w.\-]+")


class Verdict(str, Enum):
    GRANTED = "granted"
    REFUSED = "refused"
    IGNORED = "ignored"


@dataclass(frozen=True)
class ConsentRequest:
    case_id: int
    room_id: str
    thread_id: str  # the case thread the consent prompt was posted in
    customer: str  # handle of the human whose consent counts
    values: dict[str, str] = field(default_factory=dict)  # exactly what will be shared
    customer_id: str | None = None  # sender id, preferred over the handle


def words(text: str) -> list[str]:
    text = _MENTION_RE.sub(" ", (text or "").lower().replace("’", "'"))
    return _WORD_RE.findall(text)


def classify(text: str) -> Verdict:
    """Classify reply text alone (no sender/thread check)."""
    ws = words(text)
    if any(w in NO_WORDS for w in ws):
        return Verdict.REFUSED
    if any(w in YES_WORDS for w in ws):
        return Verdict.GRANTED
    return Verdict.IGNORED


def is_from_customer(req: ConsentRequest, event: MessageEvent) -> bool:
    if event.sender_kind != "human":
        return False
    if req.customer_id:
        return event.sender_id == req.customer_id
    return event.sender_name == req.customer


def evaluate(req: ConsentRequest, event: MessageEvent) -> Verdict:
    if event.room_id != req.room_id or event.thread_id != req.thread_id:
        return Verdict.IGNORED
    if not is_from_customer(req, event):
        return Verdict.IGNORED
    return classify(event.text)
