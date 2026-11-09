"""Parse Worldsmith event lines from Studio console output (SPEC sections 8 and 16).

The event templates print exactly:

    WS|<nonce>|<EVENT>|id=<entity id>|pos=<x>,<y>,<z>|t=<server time>

with `pos` the character's HumanoidRootPart in the scene frame (2 decimals) or `na`, and `t`
from `workspace:GetServerTimeNow()` (3 decimals). Console lines have no type and include other
plugins' output, so a line counts only if the tag starts the line (after an optional Studio
timestamp), every field is well formed, the event is one of EVENTS, and the nonce is this
run's. Everything else is ignored. Allowed line prefixes: `[OUTPUT] ` (the Studio MCP's
form for print() lines) and a Studio timestamp.
"""

from __future__ import annotations

import re
import secrets
from typing import NamedTuple

EVENTS = ("LEVER_ACTIVATED", "GATE_OPENED", "BRIDGE_CROSSED", "KILL_FLOOR_TOUCHED", "GOAL_TOUCHED")
NONCE_RE = re.compile(r"[0-9a-f]{8,32}")

_NUM2 = r"-?\d+\.\d{2}"
_LINE_RE = re.compile(
    r"^\s*(?:\[OUTPUT\]\s+)?(?:\d{1,2}:\d{2}:\d{2}\.\d+\s+)?"
    r"WS\|(?P<nonce>[0-9a-f]{8,32}|-)\|(?P<event>[A-Z_]+)\|id=(?P<id>[a-z][a-z0-9_]{0,31})"
    rf"\|pos=(?P<pos>na|{_NUM2},{_NUM2},{_NUM2})\|t=(?P<t>\d+\.\d{{3}})"
    r"(?:\s+-\s+(?:Server|Client)(?:\s+-\s+[\w./:]+)?)?\s*$"
)


class Event(NamedTuple):
    nonce: str
    event: str
    id: str
    pos: tuple[float, float, float] | None
    t: float


def new_nonce() -> str:
    """A fresh 16-hex-character run nonce."""
    return secrets.token_hex(8)


def parse_events(text: str, nonce: str) -> list[Event]:
    """The well-formed events carrying `nonce`, in console order."""
    if not isinstance(nonce, str) or not NONCE_RE.fullmatch(nonce):
        raise ValueError("expected nonce must be 8-32 lowercase hex characters")
    out = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m or m.group("nonce") != nonce or m.group("event") not in EVENTS:
            continue
        pos = None if m.group("pos") == "na" else tuple(float(v) for v in m.group("pos").split(","))
        out.append(Event(nonce, m.group("event"), m.group("id"), pos, float(m.group("t"))))
    return out
