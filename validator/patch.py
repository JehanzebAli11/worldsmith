"""Apply a bounded scene patch (`worldsmith.patch/0.1`), with a base-hash check (WS-05).

A patch is `{"schema": "worldsmith.patch/0.1", "base_hash": <64 hex>, "ops": [...]}` with 1 to
MAX_OPS (3) ops (SPEC section 6). Release 0.1 has one op, `set`:
`{"op": "set", "id": <entity id>, "field": <field>, "value": <number>}`.

Checks, in order, each reported with its layer prefix (every problem in a layer is listed):

- `json:`     strict parse (no duplicate keys, no NaN/Infinity, an object at the top);
- `schema:`   `grammar/patch_schema.json`: closed objects, `set` only, 1..3 ops, number values;
- `string:`   an anchored re-check of `base_hash`, `id` and `field` (JSON Schema `$` accepts a
              trailing newline);
- `number:`   values must be finite;
- `stage:`    the caller declares the stage; each op must match one of its rules (`STAGES`).
              M0 allows only `set bridge_1.length` (SPEC section 6);
- `conflict:` one op per (id, field);
- `bounds:`   the value is inside `grammar/bounds.yaml` for `<Type>.<field>`;
- `base:`     the current scene must be a valid scene (`grammar.scene.validate_scene`);
- `stale:`    `base_hash` must equal `canonical_hash(current scene)`, else StalePatch;
- `apply:`    the target entity exists, has the stage's type, and the field holds a number;
- `scene:`    the patched scene must be a valid scene again (e.g. `version` within bounds).

`apply_patch` is pure: it never mutates its arguments and returns a new scene. It stores each
value at canonical precision (3 decimals), bumps `version` by 1 and recomputes the canonical
hash (docs/ADR/0006-patch-versioning.md). Protection, difficulty, structure and navigation are
the WS-06/WS-07 validator's job, not this module's.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, NamedTuple

import jsonschema

from worldsmith.grammar import scene as sc

PATCH_SCHEMA_ID = "worldsmith.patch/0.1"
MAX_OPS = 3

_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_PACKAGE = "worldsmith.grammar"


class OpRule(NamedTuple):
    """One op a stage allows: `op` on entity `id` (which must be a `type`), field `field`."""
    op: str
    type: str
    id: str
    field: str


# Widening a stage (M1+) is a recorded ADR (SPEC section 6).
STAGES = MappingProxyType({
    "M0": (OpRule(op="set", type="Bridge", id="bridge_1", field="length"),),
})


class PatchError(ValueError):
    """A patch was rejected. `errors` holds every message, each with its layer prefix."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class StalePatch(PatchError):
    """The patch's base_hash is not the current scene's canonical hash."""

    def __init__(self, expected: str, actual: str):
        self.expected = expected
        self.actual = actual
        super().__init__([f"stale: base_hash {expected} is not the current scene's"
                          f" canonical hash {actual}"])


@dataclass(frozen=True)
class AppliedPatch:
    """The result of apply_patch. `scene` is a new object; the inputs are untouched."""
    scene: dict
    stage: str
    base_hash: str
    scene_hash: str
    patch_hash: str
    version: int


# --- schema and parsing -------------------------------------------------------------

@lru_cache(maxsize=None)
def _schema_text() -> str:
    return resources.files(_PACKAGE).joinpath("patch_schema.json").read_text(encoding="utf-8")


def load_patch_schema() -> dict:
    """The patch JSON Schema (a fresh copy)."""
    return json.loads(_schema_text())


@lru_cache(maxsize=None)
def _validator() -> jsonschema.Draft202012Validator:
    schema = load_patch_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise PatchError([f"json: duplicate key {key!r}"])
        out[key] = value
    return out


def _reject_constant(name):
    raise PatchError([f"json: {name} is not allowed"])


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise PatchError([f"json: number {_short(text)} is not finite"])
    return value


def parse_patch_text(text: str) -> dict:
    """Strict JSON: no duplicate keys, no NaN/Infinity, top level must be an object.
    Does not check the patch; see check_shape / check_patch."""
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                         parse_constant=_reject_constant, parse_float=_finite_float)
    except PatchError:
        raise
    except ValueError as exc:  # JSONDecodeError, or an int literal past Python's digit limit
        raise PatchError([f"json: {_short(str(exc), 120)}"]) from None
    if not isinstance(obj, dict):
        raise PatchError(["json: a patch must be a JSON object"])
    return obj


def load_patch(path: str | Path) -> dict:
    """Parse a patch file and check its shape (stage-independent). Raises PatchError."""
    patch = parse_patch_text(Path(path).read_text(encoding="utf-8"))
    errors = check_shape(patch)
    if errors:
        raise PatchError(errors)
    return patch


# --- checks ---------------------------------------------------------------------

def _short(value: Any, limit: int = 40) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _where(path: Iterable[Any]) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "<root>"


def _stage_rules(stage: Any) -> tuple[OpRule, ...]:
    if not isinstance(stage, str) or stage not in STAGES:
        # A caller bug, not a bad patch: plain ValueError, never PatchError.
        raise ValueError(f"unknown patch stage {stage!r}; known: {sorted(STAGES)}")
    return STAGES[stage]


def _match(rules: tuple[OpRule, ...], op: dict) -> OpRule | None:
    for rule in rules:
        if (op["op"], op["id"], op["field"]) == (rule.op, rule.id, rule.field):
            return rule
    return None


def check_shape(patch: Any) -> list[str]:
    """Stage-independent checks: schema, anchored strings, finite numbers."""
    errors = sorted({f"schema: {_where(e.absolute_path)}: {_short(e.message, 160)}"
                     for e in _validator().iter_errors(patch)})
    if errors:
        return errors
    if not _HASH_RE.fullmatch(patch["base_hash"]):
        errors.append(f"string: base_hash: {_short(patch['base_hash'], 80)!r} is not 64"
                      " lower-case hex digits")
    for i, op in enumerate(patch["ops"]):
        for key in ("id", "field"):
            if not _ID_RE.fullmatch(op[key]):
                errors.append(f"string: ops[{i}].{key}: {_short(op[key])!r} is not an ID")
        value = op["value"]
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"number: ops[{i}].value: {value!r} is not finite")
    return errors


def check_patch(patch: Any, *, stage: str) -> list[str]:
    """Every problem with the patch that needs no scene: shape, stage rules, one op per
    target, bounds. Empty means the patch may be applied to a scene whose hash matches."""
    rules = _stage_rules(stage)
    errors = check_shape(patch)
    if errors:
        return errors
    bounds = sc.load_bounds()
    seen: dict[tuple[str, str], int] = {}
    for i, op in enumerate(patch["ops"]):
        rule = _match(rules, op)
        if rule is None:
            allowed = ", ".join(f"{r.op} {r.id}.{r.field}" for r in rules)
            errors.append(f"stage: ops[{i}]: {op['op']} {_short(op['id'])!r}.{_short(op['field'])!r}"
                          f" is not allowed in stage {stage} (allowed: {allowed})")
        else:
            key = f"{rule.type}.{rule.field}"
            b = bounds.get(key)
            value = op["value"]
            if b is None:
                errors.append(f"bounds: ops[{i}]: no bound declared for {key} (fail closed)")
            elif not (b["min"] <= value <= b["max"]):
                errors.append(f"bounds: ops[{i}].value: {_short(value)} is outside"
                              f" [{b['min']}, {b['max']}] for {key}")
        target = (op["id"], op["field"])
        if target in seen:
            errors.append(f"conflict: ops[{i}]: {_short(op['id'])!r}.{_short(op['field'])!r}"
                          f" is already set by ops[{seen[target]}]")
        else:
            seen[target] = i
    return errors


# --- hashing and apply ------------------------------------------------------------

def patch_hash(patch: dict) -> str:
    """SHA-256 hex of the patch's canonical JSON (same rules as scenes: sorted keys,
    3-decimal numbers; the order of `ops` is kept)."""
    return hashlib.sha256(sc.canonical_json(patch).encode("utf-8")).hexdigest()


def _canonical_number(value: int | float) -> int | float:
    return json.loads(sc.canonical_json({"v": value}))["v"]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def apply_patch(scene: Any, patch: Any, *, stage: str) -> AppliedPatch:
    """Check `patch` for `stage`, check it is based on `scene`, and return the patched scene.

    Pure: neither argument is mutated, and the returned scene shares no structure with
    `scene`. Raises PatchError (StalePatch for a base-hash mismatch) listing every problem
    in the first layer that fails; raises ValueError for an unknown stage.
    """
    errors = check_patch(patch, stage=stage)
    if errors:
        raise PatchError(errors)
    base_errors = sc.validate_scene(scene)
    if base_errors:
        raise PatchError([f"base: {e}" for e in base_errors])
    actual = sc.canonical_hash(scene)
    if patch["base_hash"] != actual:
        raise StalePatch(expected=patch["base_hash"], actual=actual)

    rules = _stage_rules(stage)
    new = copy.deepcopy(scene)
    by_id = {ent["id"]: ent for ent in new["entities"]}
    errors = []
    for i, op in enumerate(patch["ops"]):
        rule = _match(rules, op)
        ent = by_id.get(op["id"])
        if ent is None:
            errors.append(f"apply: ops[{i}]: no entity {op['id']!r} in the scene")
        elif ent["type"] != rule.type:
            errors.append(f"apply: ops[{i}]: {op['id']!r} is a {ent['type']}, not a {rule.type}")
        elif not _is_number(ent.get(op["field"])):
            errors.append(f"apply: ops[{i}]: {op['id']}.{op['field']} is not a number field")
        else:
            ent[op["field"]] = _canonical_number(op["value"])
    if errors:
        raise PatchError(errors)

    new["version"] = scene["version"] + 1
    post = sc.validate_scene(new)
    if post:
        raise PatchError([f"scene: {e}" for e in post])
    return AppliedPatch(scene=new, stage=stage, base_hash=actual,
                        scene_hash=sc.canonical_hash(new), patch_hash=patch_hash(patch),
                        version=new["version"])
