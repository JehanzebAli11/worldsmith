"""Typed slots for the fixed Luau templates in grammar/templates/ (WS-03, SPEC section 8).

A template is reviewed Luau with slots written `{{kind:name}}`. Only these kinds exist, and
none takes free text:

- num:   a finite int or float, written in fixed point with at most 3 decimals (like the
         canonical hash), never an exponent, `-0` as `0`;
- int:   an int in [0, 1_000_000];
- id:    an entity ID (`^[a-z][a-z0-9_]{0,31}$`), written as a double-quoted Luau string;
- enum:  one value of the closed set ENUMS[name], written as a double-quoted Luau string;
- hash:  64 lowercase hex characters (a canonical scene hash), quoted;
- nonce: 8 to 32 lowercase hex characters (a run nonce), quoted.

Every slot must be supplied and every supplied value must be used. Anything else raises
TemplateError. The characters these kinds allow cannot close a Luau string or comment.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from importlib import resources

from worldsmith.grammar.scene import ENTITY_TYPES, FLOAT_DECIMALS

SLOT_KINDS = frozenset({"num", "int", "id", "enum", "hash", "nonce"})
INT_MAX = 1_000_000

# Closed value sets for enum slots (names are slot names).
ENUMS: dict[str, frozenset[str]] = {
    "palette": frozenset({"crate", "pillar", "lamp", "plant", "banner"}),
    "event": frozenset({"BRIDGE_CROSSED", "GOAL_TOUCHED"}),
    "region": frozenset({"landing", "whole"}),
    "axis": frozenset({"+X", "-X", "+Z", "-Z"}),
    "etype": frozenset(ENTITY_TYPES),
}

_SLOT_RE = re.compile(r"\{\{([a-z]+):([a-z][a-z0-9_]*)\}\}")
_ANY_SLOT_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_NONCE_RE = re.compile(r"[0-9a-f]{8,32}")

_PACKAGE = "worldsmith.grammar"


class TemplateError(ValueError):
    """A template slot or value is not allowed."""


@lru_cache(maxsize=None)
def load_template(name: str) -> str:
    """The text of grammar/templates/<name>.luau (`events/<x>` for event scripts)."""
    if not re.fullmatch(r"(events/)?[A-Za-z_][A-Za-z0-9_]*", name):
        raise TemplateError(f"bad template name {name!r}")
    parts = name.split("/")
    node = resources.files(_PACKAGE).joinpath("templates")
    for part in parts[:-1]:
        node = node.joinpath(part)
    text = node.joinpath(parts[-1] + ".luau").read_text(encoding="utf-8")
    if not text.isascii():
        raise TemplateError(f"template {name} is not ASCII")
    return text


def placeholders(text: str) -> list[tuple[str, str]]:
    """Every `{{kind:name}}` slot in order. Raises TemplateError on a malformed `{{...}}`."""
    out = []
    for m in _ANY_SLOT_RE.finditer(text):
        sm = _SLOT_RE.fullmatch(m.group(0))
        if not sm:
            raise TemplateError(f"malformed slot {m.group(0)!r}")
        out.append((sm.group(1), sm.group(2)))
    return out


def format_number(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemplateError(f"not a number: {value!r}")
    if not math.isfinite(value):
        raise TemplateError(f"not finite: {value!r}")
    if isinstance(value, int):
        if abs(value) > 10**6:
            raise TemplateError(f"number out of template range: {value!r}")
        return str(value)
    if abs(value) > 10**6:
        raise TemplateError(f"number out of template range: {value!r}")
    text = f"{value:.{FLOAT_DECIMALS}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def format_slot(kind: str, name: str, value) -> str:
    if kind == "num":
        return format_number(value)
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= INT_MAX:
            raise TemplateError(f"{name}: not an int in [0, {INT_MAX}]: {value!r}")
        return str(value)
    if not isinstance(value, str):
        raise TemplateError(f"{name}: expected a string for {kind}, got {value!r}")
    if kind == "id":
        ok = _ID_RE.fullmatch(value)
    elif kind == "enum":
        if name not in ENUMS:
            raise TemplateError(f"{name}: no closed set for this enum slot")
        ok = value in ENUMS[name]
    elif kind == "hash":
        ok = _HASH_RE.fullmatch(value)
    elif kind == "nonce":
        ok = _NONCE_RE.fullmatch(value)
    else:
        raise TemplateError(f"{name}: unknown slot kind {kind!r}")
    if not ok:
        raise TemplateError(f"{name}: {value!r} is not a valid {kind}")
    return '"' + value + '"'


def render(text: str, values: dict) -> str:
    """Fill every slot of `text` from `values` (strict: all used, none missing)."""
    slots = placeholders(text)
    kinds: dict[str, str] = {}
    for kind, name in slots:
        if kind not in SLOT_KINDS:
            raise TemplateError(f"slot kind {kind!r} is not allowed")
        if kinds.setdefault(name, kind) != kind:
            raise TemplateError(f"slot {name!r} used with two kinds")
    missing = set(kinds) - set(values)
    extra = set(values) - set(kinds)
    if missing or extra:
        raise TemplateError(f"slots missing {sorted(missing)}, unused values {sorted(extra)}")
    rendered = {name: format_slot(kind, name, values[name]) for name, kind in kinds.items()}
    return _SLOT_RE.sub(lambda m: rendered[m.group(2)], text)
