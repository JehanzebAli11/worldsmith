"""Load, validate and hash `worldsmith.scene/0.1` files (WS-02).

What this module checks, and what it leaves to others (docs/ADR/0002-scene-schema.md):

- schema: `grammar/schema.json` (JSON Schema draft 2020-12). Closed: unknown keys and
  entity types are rejected; every string is an enum, a const or an ID.
- strings: a second, anchored check on every string (JSON Schema `$` accepts a trailing
  newline).
- bounds: `grammar/bounds.yaml`. Every number in [min, max] for its field; a numeric field
  with no bound is rejected (fail closed).
- references: IDs unique, `bounds` reserved, every reference exists and has the right type,
  lever and gate point at each other, protected names exist.

Protection, the locked gap, difficulty, structure (penetration, inside-the-room) and the
navigation approximation belong to the WS-06/WS-07 validator, not here.

Canonical form: numbers rounded to FLOAT_DECIMALS (3) decimal places and written without
trailing zeros (7, 7.0 and 7.0000001 all become `7`; -0 becomes `0`); keys sorted; no
whitespace; `entities` sorted by id and `protected` sorted (their order carries no meaning).
`canonical_hash` is the SHA-256 hex digest of the canonical JSON in UTF-8.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

import jsonschema
import yaml

SCHEMA_ID = "worldsmith.scene/0.1"
ENTITY_TYPES = (
    "Spawn", "Platform", "Gap", "Bridge", "Ramp", "Wall",
    "Lever", "Gate", "Goal", "KillFloor", "Trigger", "Decor",
)
ID_PATTERN = "^[a-z][a-z0-9_]{0,31}$"
RESERVED_NAMES = frozenset({"bounds"})  # `protected` may name the room box; no entity may
FLOAT_DECIMALS = 3

# Every string in a valid scene is an enum value, a const or an ID; all of them fit this.
_STRING_RE = re.compile(r"[A-Za-z0-9_./+-]{1,32}")

# Reference fields per entity type, and the entity types each may point at.
REFERENCES: dict[str, dict[str, tuple[str, ...]]] = {
    "Spawn": {"on": ("Platform",)},
    "Goal": {"on": ("Platform",)},
    "Lever": {"on": ("Platform",), "controls": ("Gate",)},
    "Gate": {"opens_on": ("Lever",)},
    "Gap": {"from": ("Platform",), "to": ("Platform",)},
    "Bridge": {"spans": ("Gap",)},
    "Trigger": {"on": ("Bridge", "Goal")},
}

_PACKAGE = "worldsmith.grammar"


class SceneError(ValueError):
    """A scene failed to parse or validate. `errors` holds every message."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# --- grammar data -------------------------------------------------------------

@lru_cache(maxsize=None)
def _data_text(name: str) -> str:
    return resources.files(_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def load_schema() -> dict:
    """The scene JSON Schema (a fresh copy)."""
    return json.loads(_data_text("schema.json"))


@lru_cache(maxsize=None)
def _validator() -> jsonschema.Draft202012Validator:
    schema = load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@lru_cache(maxsize=None)
def _bounds() -> dict:
    data = yaml.safe_load(_data_text("bounds.yaml"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_ID:
        raise ValueError(f"bounds.yaml must declare schema: {SCHEMA_ID}")
    out = {}
    for key, b in data.get("bounds", {}).items():
        if not isinstance(b, dict) or set(b) != {"min", "max"}:
            raise ValueError(f"bounds.yaml: {key} must be {{min, max}}")
        lo, hi = b["min"], b["max"]
        for v in (lo, hi):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"bounds.yaml: {key} has a non-numeric bound")
        if not lo < hi:
            raise ValueError(f"bounds.yaml: {key} has min >= max")
        out[key] = {"min": lo, "max": hi}
    return out


def load_bounds() -> dict:
    """`{field_key: {"min": .., "max": ..}}` from bounds.yaml (a fresh copy)."""
    return copy.deepcopy(_bounds())


# --- parsing --------------------------------------------------------------------

def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise SceneError([f"json: duplicate key {key!r}"])
        out[key] = value
    return out


def _reject_constant(name):
    raise SceneError([f"json: {name} is not allowed"])


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise SceneError([f"json: number {text} is not finite"])
    return value


def parse_scene_text(text: str) -> dict:
    """Strict JSON: no duplicate keys, no NaN/Infinity, top level must be an object.
    Does not validate; see validate_scene."""
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                         parse_constant=_reject_constant, parse_float=_finite_float)
    except json.JSONDecodeError as exc:
        raise SceneError([f"json: {exc}"]) from None
    if not isinstance(obj, dict):
        raise SceneError(["json: a scene must be a JSON object"])
    return obj


def load_scene(path: str | Path) -> dict:
    """Parse and validate a scene file. Raises SceneError listing every problem."""
    scene = parse_scene_text(Path(path).read_text(encoding="utf-8"))
    errors = validate_scene(scene)
    if errors:
        raise SceneError(errors)
    return scene


# --- validation -------------------------------------------------------------------

def _where(path: Iterable[Any]) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "<root>"


def validate_scene(scene: Any) -> list[str]:
    """Every problem found, each prefixed by its layer (`schema:`, `string:`, `bounds:`,
    `ref:`). Empty means valid. Later layers run only when the schema passes."""
    schema_errors = sorted({
        f"schema: {_where(e.absolute_path)}: {e.message}"
        for e in _validator().iter_errors(scene)})
    if schema_errors:
        return schema_errors
    string_errors = check_strings(scene)
    if string_errors:
        return string_errors
    return check_bounds(scene) + check_references(scene)


def check_strings(scene: Any) -> list[str]:
    errors = []

    def walk(node, path):
        if isinstance(node, str):
            if not _STRING_RE.fullmatch(node):
                errors.append(f"string: {_where(path)}: {node!r} is not an enum value or ID")
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + (i,))

    walk(scene, ())
    return errors


def check_bounds(scene: dict, bounds: dict | None = None) -> list[str]:
    """Numbers against bounds.yaml. A vector's bound applies to each component."""
    bounds = load_bounds() if bounds is None else bounds
    errors: list[str] = []

    def check(key, where, value):
        b = bounds.get(key)
        if b is None:
            errors.append(f"bounds: {where}: no bound declared for {key} (fail closed)")
        elif not (b["min"] <= value <= b["max"]):
            errors.append(f"bounds: {where}: {value!r} is outside [{b['min']}, {b['max']}]"
                          f" for {key}")

    def walk(node, key, where):
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            check(key, where, node)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, key, f"{where}[{i}]")
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{key}.{k}", f"{where}.{k}")

    for name, value in scene.items():
        if name == "entities" and isinstance(value, list):
            for i, ent in enumerate(value):
                if not isinstance(ent, dict):
                    continue
                for field, v in ent.items():
                    walk(v, f"{ent.get('type')}.{field}", f"entities[{i}]({ent.get('id')}).{field}")
        else:
            walk(value, f"scene.{name}", name)

    room = scene.get("bounds")
    if isinstance(room, dict) and isinstance(room.get("min"), list) and isinstance(room.get("max"), list):
        for axis, (lo, hi) in enumerate(zip(room["min"], room["max"])):
            if not lo < hi:
                errors.append(f"bounds: bounds: min[{axis}] ({lo}) must be below max[{axis}] ({hi})")
    return errors


def check_references(scene: dict) -> list[str]:
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    for i, ent in enumerate(scene["entities"]):
        eid = ent["id"]
        if eid in RESERVED_NAMES:
            errors.append(f"ref: entities[{i}]: id {eid!r} is reserved")
        if eid in by_id:
            errors.append(f"ref: entities[{i}]: duplicate id {eid!r}")
        else:
            by_id[eid] = ent

    for ent in scene["entities"]:
        eid, etype = ent["id"], ent["type"]
        for field, allowed in REFERENCES.get(etype, {}).items():
            target = ent[field]
            hit = by_id.get(target)
            if hit is None:
                errors.append(f"ref: {eid}.{field}: dangling reference {target!r}")
            elif hit["type"] not in allowed:
                errors.append(f"ref: {eid}.{field}: {target!r} is a {hit['type']},"
                              f" expected {' or '.join(allowed)}")
        if etype == "Gap" and ent["from"] == ent["to"]:
            errors.append(f"ref: {eid}: from and to are the same platform {ent['from']!r}")
        if etype == "Lever":
            gate = by_id.get(ent["controls"])
            if gate is not None and gate["type"] == "Gate" and gate["opens_on"] != eid:
                errors.append(f"ref: {eid}.controls: gate {gate['id']!r} opens on"
                              f" {gate['opens_on']!r}, not {eid!r}")
        if etype == "Gate":
            lever = by_id.get(ent["opens_on"])
            if lever is not None and lever["type"] == "Lever" and lever["controls"] != eid:
                errors.append(f"ref: {eid}.opens_on: lever {lever['id']!r} controls"
                              f" {lever['controls']!r}, not {eid!r}")

    for name in scene["protected"]:
        if name not in by_id and name not in RESERVED_NAMES:
            errors.append(f"ref: protected: {name!r} names no entity")
    return errors


# --- canonical form and hash -------------------------------------------------------

def _format_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError(f"non-finite number {value!r} has no canonical form")
    text = f"{value:.{FLOAT_DECIMALS}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _quantize(node):
    if isinstance(node, bool) or node is None or isinstance(node, str):
        return node
    if isinstance(node, (int, float)):
        return json.loads(_format_number(node))
    if isinstance(node, list):
        return [_quantize(v) for v in node]
    if isinstance(node, dict):
        return {k: _quantize(v) for k, v in node.items()}
    raise TypeError(f"not a JSON value: {type(node).__name__}")


def _emit(node) -> str:
    if node is None:
        return "null"
    if isinstance(node, bool):
        return "true" if node else "false"
    if isinstance(node, (int, float)):
        return _format_number(node)
    if isinstance(node, str):
        return json.dumps(node, ensure_ascii=True)
    if isinstance(node, list):
        return "[" + ",".join(_emit(v) for v in node) + "]"
    if isinstance(node, dict):
        if not all(isinstance(k, str) for k in node):
            raise TypeError("JSON object keys must be strings")
        return "{" + ",".join(json.dumps(k, ensure_ascii=True) + ":" + _emit(node[k])
                              for k in sorted(node)) + "}"
    raise TypeError(f"not a JSON value: {type(node).__name__}")


def normalize_scene(scene: dict) -> dict:
    """The scene as its canonical JSON parses back: numbers rounded, `entities` sorted by
    id and `protected` sorted. Does not validate."""
    out = _quantize(scene)
    ents = out.get("entities")
    if isinstance(ents, list):
        out["entities"] = sorted(
            ents, key=lambda e: (str(e.get("id")) if isinstance(e, dict) else "", _emit(e)))
    prot = out.get("protected")
    if isinstance(prot, list) and all(isinstance(p, str) for p in prot):
        out["protected"] = sorted(prot)
    return out


def canonical_json(scene: dict) -> str:
    """Sorted keys, no whitespace, numbers at FLOAT_DECIMALS, ASCII. See module docstring."""
    return _emit(normalize_scene(scene))


def canonical_hash(scene: dict) -> str:
    """SHA-256 hex digest of canonical_json(scene) in UTF-8."""
    return hashlib.sha256(canonical_json(scene).encode("utf-8")).hexdigest()


# --- fixture hash table --------------------------------------------------------------

def fixture_paths(scenes_root: str | Path) -> list[Path]:
    """Every `<family-fixture>/<name>.json` under scenes_root, sorted."""
    return sorted(Path(scenes_root).glob("*/*.json"), key=lambda p: p.as_posix())


def render_hashes_md(scenes_root: str | Path) -> str:
    """The text of scenes/HASHES.md (each fixture is loaded and validated first)."""
    root = Path(scenes_root)
    lines = [
        "# Scene hashes",
        "",
        "Generated by `scripts/scene_hashes.py`; do not edit by hand. After changing a fixture,",
        "run `python scripts/scene_hashes.py` (`--check` only verifies; the tests run it).",
        "",
        "`canonical_hash` is the SHA-256 of the scene's canonical JSON (3-decimal numbers,",
        "sorted keys, entities sorted by id; see `docs/ADR/0002-scene-schema.md`). These",
        "identify hand-authored fixtures. They are not results.",
        "",
        "| File | scene_id | version | canonical_hash |",
        "| --- | --- | --- | --- |",
    ]
    for path in fixture_paths(root):
        scene = load_scene(path)
        rel = path.relative_to(root).as_posix()
        lines.append(f"| `{rel}` | {scene['scene_id']} | {scene['version']} |"
                     f" `{canonical_hash(scene)}` |")
    return "\n".join(lines) + "\n"
