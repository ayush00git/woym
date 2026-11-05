"""Wire tag between agents (docs/contracts.md §2).

Every agent-to-agent message ends with one line like:

    `woym case=42 step=env node=20 tz=Asia/Kolkata`

Values are percent-encoded so they never contain spaces. Booleans are 1/0 and
lists are comma-joined. The tag is always the LAST non-empty line.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from woym.contracts import Step

SAFE = "/:._-+,"
PREFIX = "woym"
_KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_RESERVED = {"case", "step"}

Parsed = tuple[int, Step, dict[str, str]]


def _encode(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
        if any("," in v for v in items):
            raise ValueError(f"list items may not contain commas: {items!r}")
        return quote(",".join(items), safe=SAFE)
    return quote(str(value), safe=SAFE)


def tag(case_id: int, step: Step | str, **kv: object) -> str:
    """Build the backticked tag line. None values are dropped."""
    step = Step(step)
    parts = [PREFIX, f"case={int(case_id)}", f"step={step.value}"]
    for key, value in kv.items():
        if not _KEY_RE.match(key) or key in _RESERVED:
            raise ValueError(f"bad tag key: {key!r}")
        if value is None:
            continue
        parts.append(f"{key}={_encode(value)}")
    return "`" + " ".join(parts) + "`"


def message(prose: str, case_id: int, step: Step | str, **kv: object) -> str:
    """Human-readable sentence(s), then the tag as the last line."""
    return f"{prose.rstrip()}\n{tag(case_id, step, **kv)}"


def parse(text: str) -> Parsed | None:
    """Return (case_id, step, payload) if the last non-empty line is a tag."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    if not (len(last) >= 2 and last.startswith("`") and last.endswith("`")):
        return None
    tokens = last[1:-1].split()
    if not tokens or tokens[0] != PREFIX:
        return None
    fields: dict[str, str] = {}
    for tok in tokens[1:]:
        key, sep, value = tok.partition("=")
        if not sep or not _KEY_RE.match(key) or key in fields:
            return None
        fields[key] = unquote(value)
    try:
        case_id = int(fields.pop("case"))
        step = Step(fields.pop("step"))
    except (KeyError, ValueError):
        return None
    if case_id < 0:
        return None
    return case_id, step, fields


def as_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value in ("1", "0"):
        return value == "1"
    raise ValueError(f"not a wire bool: {value!r}")


def as_list(value: str | None) -> list[str]:
    if not value:
        return []
    return value.split(",")


def strip_tag(text: str) -> str:
    """The prose part of a tagged message (whole text if untagged)."""
    if parse(text) is None:
        return text
    lines = text.rstrip().splitlines()
    return "\n".join(lines[:-1]).rstrip()
