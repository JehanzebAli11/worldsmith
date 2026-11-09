"""Apply a bounded scene patch (`worldsmith.patch/0.1`), with a base-hash check (WS-05).

A patch is `{"schema": "worldsmith.patch/0.1", "base_hash": <64 hex>, "ops": [...]}` with 1 to
MAX_OPS (3) ops (SPEC section 6). Release 0.1 has one op, `set`:
`{"op": "set", "id": <entity id>, "field": <field>, "value": <number>}`.
Release 0.2 (WS-06, ADR 0007) adds `move` (`{"op": "move", "id", "pos": [x, y, z]}`), `add`
(`{"op": "add", "entity": {...}}`) and `remove` (`{"op": "remove", "id"}`), and lets `set` carry an
ID or a vector. What a patch may say is wider than what a stage applies: `STAGES` decides.
M0 accepts only 0.1 patches and exactly `set bridge_1.length`, as WS-05 made it; M1 accepts 0.1
and 0.2 (`STAGE_SCHEMAS`).

Checks, in order, each reported with its layer prefix (every problem in a layer is listed):

- `json:`     strict parse (no duplicate keys, no NaN/Infinity, an object at the top);
- `schema:`   `grammar/patch_schema.json` (0.1) or `patch_schema_0.2.json`: closed objects,
              1..3 ops. A stage checks a patch against the declared schema when the stage
              allows it, else against the stage's first schema (so M0 behaves as in WS-05);
- `string:`   an anchored re-check of `base_hash`, `id` and `field` (JSON Schema `$` accepts a
              trailing newline);
- `number:`   values must be finite;
- `stage:`    the caller declares the stage; each op must match one of its rules (`STAGES`).
              M0 allows only `set bridge_1.length` (SPEC section 6); M1: ADR 0007;
- `conflict:` one op per (id, field);
- `bounds:`   the value is inside `grammar/bounds.yaml` for `<Type>.<field>`;
- `base:`     the current scene must be a valid scene (`grammar.scene.validate_scene`);
- `stale:`    `base_hash` must equal `canonical_hash(current scene)`, else StalePatch;
- `apply:`    the target entity exists, has the stage's type, and the field holds a number
              (`set`) or a position (`move`); an added entity's id is new (`add`);
- `scene:`    the patched scene must be a valid scene again (e.g. `version` within bounds).

`apply_patch` is pure: it never mutates its arguments and returns a new scene. It stores each
value (and an added entity's numbers) at canonical precision (3 decimals), bumps `version` by 1 and recomputes the canonical
hash (docs/ADR/0006-patch-versioning.md). Protection, difficulty and structure are the WS-06
validator's job (`worldsmith.validator.validate`), navigation WS-07's; not this module's.
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
PATCH_SCHEMA_ID_0_2 = "worldsmith.patch/0.2"
PATCH_SCHEMA_IDS = (PATCH_SCHEMA_ID, PATCH_SCHEMA_ID_0_2)
_SCHEMA_FILES = {PATCH_SCHEMA_ID: "patch_schema.json", PATCH_SCHEMA_ID_0_2: "patch_schema_0.2.json"}
MAX_OPS = 3

_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_PACKAGE = "worldsmith.grammar"


class OpRule(NamedTuple):
    """One op a stage allows: `op` on entity `id` (which must be a `type`), field `field`
    (`pos` for a move). An `add` rule has id and field None: any new entity of `type`."""
    op: str
    type: str
    id: str | None
    field: str | None


# Widening a stage (M1+) is a recorded ADR (SPEC section 6). M0: WS-05, ADR 0006. M1: ADR 0007.
STAGES = MappingProxyType({
    "M0": (OpRule(op="set", type="Bridge", id="bridge_1", field="length"),),
    "M1": (
        OpRule(op="set", type="Bridge", id="bridge_1", field="length"),
        OpRule(op="set", type="Bridge", id="bridge_1", field="width"),
        OpRule(op="set", type="Bridge", id="bridge_1", field="lateral_offset"),
        OpRule(op="move", type="Lever", id="lever_1", field="pos"),
        OpRule(op="move", type="Gate", id="gate_1", field="pos"),
        OpRule(op="add", type="Wall", id=None, field=None),
        OpRule(op="add", type="Decor", id=None, field=None),
    ),
})
# The patch schema versions each stage accepts; the first is what a stage checks an
# unaccepted version against (so M0 reports a 0.2 patch exactly as WS-05 did).
STAGE_SCHEMAS = MappingProxyType({
    "M0": (PATCH_SCHEMA_ID,),
    "M1": (PATCH_SCHEMA_ID, PATCH_SCHEMA_ID_0_2),
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
def _schema_text(schema_id: str = PATCH_SCHEMA_ID) -> str:
    return resources.files(_PACKAGE).joinpath(_SCHEMA_FILES[schema_id]).read_text(encoding="utf-8")


def load_patch_schema(schema_id: str = PATCH_SCHEMA_ID) -> dict:
    """The patch JSON Schema for `schema_id` (default 0.1; a fresh copy)."""
    return json.loads(_schema_text(schema_id))


@lru_cache(maxsize=None)
def _validator(schema_id: str = PATCH_SCHEMA_ID) -> jsonschema.Draft202012Validator:
    schema = load_patch_schema(schema_id)
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


def stage_rules(stage: str) -> tuple[OpRule, ...]:
    """The rules of `stage`; ValueError for an unknown stage."""
    return _stage_rules(stage)


def op_target(op: dict) -> tuple[Any, Any, Any]:
    """(op, id, field) of an op: `set` → its field, `move` → "pos", `add` → the added entity's
    id and None, `remove` → None. Tolerant of malformed ops (missing parts are None)."""
    kind = op.get("op")
    if kind == "add":
        ent = op.get("entity")
        return kind, ent.get("id") if isinstance(ent, dict) else None, None
    if kind == "move":
        return kind, op.get("id"), "pos"
    return kind, op.get("id"), op.get("field")


def _match(rules: tuple[OpRule, ...], op: dict) -> OpRule | None:
    kind = op.get("op")
    if kind == "add":
        ent = op.get("entity")
        etype = ent.get("type") if isinstance(ent, dict) else None
        for rule in rules:
            if rule.op == "add" and rule.type == etype:
                return rule
        return None
    _, oid, field = op_target(op)
    for rule in rules:
        if rule.op != "add" and (kind, oid, field) == (rule.op, rule.id, rule.field):
            return rule
    return None


def _describe(op: dict) -> str:
    kind, oid, field = op_target(op)
    if kind == "add":
        ent = op.get("entity")
        etype = ent.get("type") if isinstance(ent, dict) else None
        return f"add {_short(etype)!r}"
    if kind == "remove":
        return f"remove {_short(oid)!r}"
    if kind == "move":
        return f"move {_short(oid)!r}"
    return f"{_short(kind)} {_short(oid)!r}.{_short(field)!r}"


def _describe_rule(rule: OpRule) -> str:
    if rule.op == "add":
        return f"add {rule.type}"
    if rule.op == "move":
        return f"move {rule.id}"
    return f"{rule.op} {rule.id}.{rule.field}"


def _numbers(node):
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield node
    elif isinstance(node, list):
        for v in node:
            yield from _numbers(v)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _numbers(v)


def check_shape(patch: Any, *, schema_id: str | None = None) -> list[str]:
    """Stage-independent checks: schema, anchored strings, finite numbers. `schema_id` picks
    the schema; by default the patch's declared one if known, else 0.1."""
    if schema_id is None:
        declared = patch.get("schema") if isinstance(patch, dict) else None
        schema_id = declared if declared in PATCH_SCHEMA_IDS else PATCH_SCHEMA_ID
    errors = sorted({f"schema: {_where(e.absolute_path)}: {_short(e.message, 160)}"
                     for e in _validator(schema_id).iter_errors(patch)})
    if errors:
        return errors
    if not _HASH_RE.fullmatch(patch["base_hash"]):
        errors.append(f"string: base_hash: {_short(patch['base_hash'], 80)!r} is not 64"
                      " lower-case hex digits")
    for i, op in enumerate(patch["ops"]):
        for key in ("id", "field"):
            if key in op and not _ID_RE.fullmatch(op[key]):
                errors.append(f"string: ops[{i}].{key}: {_short(op[key])!r} is not an ID")
        if isinstance(op.get("value"), str) and not _ID_RE.fullmatch(op["value"]):
            errors.append(f"string: ops[{i}].value: {_short(op['value'])!r} is not an ID")
        for key in ("value", "pos", "entity"):
            for value in _numbers(op.get(key)):
                if isinstance(value, float) and not math.isfinite(value):
                    errors.append(f"number: ops[{i}].{key}: {value!r} is not finite")
    return errors


def check_ops(patch: Any, *, stage: str) -> list[str]:
    """Stage rules, one op per target and bounds, for whatever ops are objects (tolerant of
    a malformed patch, so the WS-06 validator can name what each op tried). Also refuses a
    patch schema version the stage does not accept."""
    rules = _stage_rules(stage)
    errors: list[str] = []
    declared = patch.get("schema") if isinstance(patch, dict) else None
    if declared not in STAGE_SCHEMAS[stage]:
        errors.append(f"stage: patch schema {_short(declared)!r} is not accepted in stage {stage}"
                      f" (accepted: {', '.join(STAGE_SCHEMAS[stage])})")
    ops = patch.get("ops") if isinstance(patch, dict) else None
    if not isinstance(ops, list):
        return errors
    bounds = sc.load_bounds()
    seen: dict[tuple[Any, Any], int] = {}
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        rule = _match(rules, op)
        if rule is None:
            allowed = ", ".join(_describe_rule(r) for r in rules)
            errors.append(f"stage: ops[{i}]: {_describe(op)} is not allowed in stage {stage}"
                          f" (allowed: {allowed})")
        elif rule.op == "set":
            key = f"{rule.type}.{rule.field}"
            b = bounds.get(key)
            value = op.get("value")
            if not _is_number(value):
                errors.append(f"stage: ops[{i}]: {_describe(op)} takes a number in stage {stage}")
            elif b is None:
                errors.append(f"bounds: ops[{i}]: no bound declared for {key} (fail closed)")
            elif not (b["min"] <= value <= b["max"]):
                errors.append(f"bounds: ops[{i}].value: {_short(value)} is outside"
                              f" [{b['min']}, {b['max']}] for {key}")
        elif rule.op == "move":
            key = f"{rule.type}.pos"
            b = bounds.get(key)
            pos = op.get("pos")
            if not (isinstance(pos, list) and len(pos) == 3 and all(_is_number(v) for v in pos)):
                errors.append(f"stage: ops[{i}]: {_describe(op)} takes [x, y, z] in stage {stage}")
            elif b is None:
                errors.append(f"bounds: ops[{i}]: no bound declared for {key} (fail closed)")
            else:
                for k, v in enumerate(pos):
                    if not (b["min"] <= v <= b["max"]):
                        errors.append(f"bounds: ops[{i}].pos[{k}]: {_short(v)} is outside"
                                      f" [{b['min']}, {b['max']}] for {key}")
        kind, oid, field = op_target(op)
        if isinstance(oid, str):
            target = (oid, field if kind in ("set", "move") else "*")
            clash = [j for (tid, tf), j in seen.items()
                     if tid == oid and (tf == target[1] or "*" in (tf, target[1]))]
            if clash:
                what = f"{_short(oid)!r}.{_short(field)!r}" if kind in ("set", "move") else repr(_short(oid))
                errors.append(f"conflict: ops[{i}]: {what} is already set by ops[{clash[0]}]")
            else:
                seen[target] = i
    return errors


def check_patch(patch: Any, *, stage: str) -> list[str]:
    """Every problem with the patch that needs no scene: shape, stage rules, one op per
    target, bounds. Empty means the patch may be applied to a scene whose hash matches."""
    _stage_rules(stage)
    accepted = STAGE_SCHEMAS[stage]
    declared = patch.get("schema") if isinstance(patch, dict) else None
    errors = check_shape(patch, schema_id=declared if declared in accepted else accepted[0])
    if errors:
        return errors
    return check_ops(patch, stage=stage)


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
        if op["op"] == "add":
            added = op["entity"]
            if added.get("id") in by_id:
                errors.append(f"apply: ops[{i}]: an entity {added.get('id')!r} already exists")
            else:
                ent = json.loads(sc.canonical_json({"v": added}))["v"]
                new["entities"].append(ent)
                by_id[ent["id"]] = ent
            continue
        ent = by_id.get(op["id"])
        if ent is None:
            errors.append(f"apply: ops[{i}]: no entity {op['id']!r} in the scene")
        elif ent["type"] != rule.type:
            errors.append(f"apply: ops[{i}]: {op['id']!r} is a {ent['type']}, not a {rule.type}")
        elif op["op"] == "move":
            if not (isinstance(ent.get("pos"), list) and len(ent["pos"]) == 3):
                errors.append(f"apply: ops[{i}]: {op['id']} has no position")
            else:
                ent["pos"] = [_canonical_number(v) for v in op["pos"]]
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
