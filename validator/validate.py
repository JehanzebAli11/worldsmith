"""The Worldsmith validator (WS-06): SPEC section 7 items 1-4, independent of the architect.

    validate(base, candidate, *, stage) -> Result

`base` is the current (committed) scene; it must be valid (else ValueError: a caller bug, not a
verdict). `candidate` is a patch (a dict, or patch text) or a whole patched scene (a dict whose
`schema` is `worldsmith.scene/...`). `stage` is the caller's stage (`M0`, `M1`; see
`worldsmith.validator.patch.STAGES`). The result is `{ok, violations[], warnings[], graph}`
(`Result.to_dict()`); `graph` is reserved for the WS-07 navigation approximation and is None.
A rejected patch counts against the revision budget (SPEC section 7); counting is the caller's.

Layers (codes in `CODES`, documented in docs/VALIDATOR.md; rules in ADR 0007):

1. Patch layer (patch input only): parse (PATCH_MALFORMED), payload scan (CODE_PAYLOAD), op
   count (TOO_MANY_OPS), shape (SCHEMA_INVALID), stage rules / one op per target / bounds
   (STAGE_OP_FORBIDDEN, OP_CONFLICT, OUT_OF_BOUNDS, from `patch.check_ops`), base hash
   (STALE_BASE). A patch that is not well-formed apart from its op count, or is stale, stops
   here. Otherwise every op is simulated on a copy of the base, whatever the stage allows, so
   the scene checks can name what a forbidden op would have done (BAD_TARGET, NOOP_PATCH).
   Scene input instead derives its ops from the diff against the base and checks them against
   the stage the same way.
2. Grammar (SPEC section 7.1): the candidate scene against the WS-02 checks (SCHEMA_INVALID,
   CODE_PAYLOAD, OUT_OF_BOUNDS, DANGLING_REF, REF_INVALID, GATE_LEVER_UNLINKED).
3. Protection (7.2): PROTECTED_CHANGED, CONSTRAINTS_CHANGED, REQUIRED_EVENTS_CHANGED,
   PHYSICS_CHANGED, TELEPORT, NEW_FLOOR.
4. Difficulty (7.3): GAP_CHANGED, GAP_BELOW_MIN, GATE_LEVER_UNLINKED, GATE_BYPASSED,
   DETOUR_TOO_SHORT, PATH_BLOCKED.
5. Structure (7.4): PENETRATION, OUTSIDE_ROOM, UNSUPPORTED, LAYOUT_BROKEN.

Geometry (layers 3-5) runs only on a candidate inside the grammar (schema, strings and bounds
pass); it uses the compiler's own part geometry (`compiler/geometry.py`), so the validator judges
what would be built. Pure and deterministic: inputs are never mutated; violations are sorted.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable

from worldsmith.compiler import geometry as geo
from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt

# --- rule constants (ADR 0007) ---------------------------------------------------------------
EPS = 0.001            # geometric tolerance: the canonical precision (ADR 0002)
JOIN_MAX = 1.0         # a bridge may sink this far into its gap's far platform, along the gap axis
CLEARANCE = 6.0        # walkway height above a walking surface (M0 character: 5.19 studs tall)
FLOOR_BAND = 1.0       # a box whose top is within this of the walking surface is floor-like ...
FLOOR_MIN_SPAN = 1.0   # ... if its top face is at least this wide on both horizontal axes

# Stable codes. Changing a meaning or removing a code is an ADR; the "does the architect
# cheat?" layer (ROADMAP section 4.4) counts them.
CODES = MappingProxyType({
    "PATCH_MALFORMED": "the patch is not a strict JSON object",
    "SCHEMA_INVALID": "the patch or the patched scene breaks its JSON Schema or the anchored ID check",
    "CODE_PAYLOAD": "a key or string holds characters no ID, enum or hash uses (code, URL, free text)",
    "OUT_OF_BOUNDS": "a number is outside its grammar/bounds.yaml range",
    "TOO_MANY_OPS": "more than 3 ops (or, for a patched scene, more than 3 changes)",
    "STAGE_OP_FORBIDDEN": "an op (or a change) the stage does not allow",
    "OP_CONFLICT": "two ops on one target",
    "STALE_BASE": "base_hash is not the current scene's canonical hash",
    "BAD_TARGET": "an op names an entity or field that does not exist, or adds an existing id",
    "NOOP_PATCH": "an op sets something to the value it already has, or the scene is unchanged",
    "PROTECTED_CHANGED": "a protected entity, the room box, the protected list or the scene identity changed",
    "CONSTRAINTS_CHANGED": "min_gap or min_lever_detour changed",
    "REQUIRED_EVENTS_CHANGED": "required_events or the event triggers changed",
    "PHYSICS_CHANGED": "player physics touched (player_physics unlocked, or a physics field named)",
    "TELEPORT": "a spawn point or teleport added",
    "NEW_FLOOR": "a Platform, Ramp or Bridge added, a solid added over the gap, or a floor-like box off the base floors",
    "GAP_CHANGED": "the gap entity or the distance between its platforms changed",
    "GAP_BELOW_MIN": "the gap is narrower than the base min_gap",
    "GATE_LEVER_UNLINKED": "a gate no longer exists, or it and its lever no longer point at each other",
    "GATE_BYPASSED": "a gate no longer fills its slot in the corridor",
    "DETOUR_TOO_SHORT": "a lever is closer to the path centreline than min_lever_detour",
    "PATH_BLOCKED": "an added or changed Wall or Decor intrudes into the walkway",
    "PENETRATION": "solids overlap, beyond the allowed bridge join",
    "DANGLING_REF": "a reference names no entity",
    "REF_INVALID": "a reference names the wrong type, an id is duplicated or reserved, or a trigger is mis-hosted",
    "OUTSIDE_ROOM": "a built part of an added or changed entity leaves the room box",
    "UNSUPPORTED": "a lever does not stand on its platform",
    "LAYOUT_BROKEN": "not exactly one spawn, goal and gap, or spawn and goal not on opposite sides of the gap",
})
WARNING_CODES = MappingProxyType({
    "BRIDGE_SHORT": "a bridge ends before its gap's far platform (allowed; F1-A is built that way)",
})

# Exact names (lower case, underscores removed) of player physics properties; only used to
# name what a rejected op or key tried. The grammar has no such field, so they are rejected anyway.
PHYSICS_NAMES = frozenset({
    "walkspeed", "jumppower", "jumpheight", "usejumppower", "gravity", "hipheight",
    "maxslopeangle", "autojumpenabled", "playerphysics",
})
TELEPORT_TYPES = frozenset({"teleport", "teleporter", "teleportpad", "portal", "spawnlocation"})
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9_./+-]*")
_FLOORS = ("Platform", "Ramp", "Bridge")
_IDENTITY = ("schema", "scene_id", "family", "units", "up")


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    ref: str | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "ref": self.ref}


@dataclass(frozen=True)
class Result:
    ok: bool
    violations: tuple[Violation, ...]
    warnings: tuple[Violation, ...]
    graph: str | None = None
    scene: dict | None = field(default=None, compare=False, repr=False)  # the judged candidate

    @property
    def codes(self) -> set[str]:
        return {v.code for v in self.violations}

    def to_dict(self) -> dict:
        return {"ok": self.ok, "violations": [v.to_dict() for v in self.violations],
                "warnings": [w.to_dict() for w in self.warnings], "graph": self.graph}


class _Out:
    def __init__(self):
        self.v: set[tuple] = set()
        self.w: set[tuple] = set()

    def add(self, code: str, message: str, ref: str | None = None):
        assert code in CODES, code
        self.v.add((code, ref or "", message, ref))

    def warn(self, code: str, message: str, ref: str | None = None):
        assert code in WARNING_CODES, code
        self.w.add((code, ref or "", message, ref))

    def result(self, scene=None) -> Result:
        v = tuple(Violation(c, m, r) for c, _, m, r in sorted(self.v, key=lambda t: t[:3]))
        w = tuple(Violation(c, m, r) for c, _, m, r in sorted(self.w, key=lambda t: t[:3]))
        return Result(ok=not v, violations=v, warnings=w, graph=None, scene=scene)


# --- entry points ------------------------------------------------------------------------------

def validate(base: dict, candidate: Any, *, stage: str) -> Result:
    """Judge `candidate` (a patch, patch text, or a whole patched scene) against `base`."""
    if isinstance(candidate, dict) and isinstance(candidate.get("schema"), str) \
            and candidate["schema"].startswith("worldsmith.scene/"):
        return validate_scene_change(base, candidate, stage=stage)
    return validate_patch(base, candidate, stage=stage)


def _check_inputs(base: Any, stage: Any):
    pt.stage_rules(stage)  # ValueError for an unknown stage
    errors = sc.validate_scene(base)
    if errors:
        raise ValueError(f"the base scene is invalid: {'; '.join(errors[:5])}")


def validate_patch(base: dict, patch: Any, *, stage: str) -> Result:
    _check_inputs(base, stage)
    out = _Out()
    if isinstance(patch, (bytes, bytearray)):
        patch = patch.decode("utf-8", errors="replace")
    if isinstance(patch, str):
        try:
            patch = pt.parse_patch_text(patch)
        except pt.PatchError as exc:
            out.add("PATCH_MALFORMED", "; ".join(exc.errors))
            return out.result()
    if not isinstance(patch, dict):
        out.add("PATCH_MALFORMED", f"a patch must be a JSON object, not {type(patch).__name__}")
        return out.result()

    _scan_payload(patch, out, "patch")
    ops_raw = patch.get("ops")
    ops = [op for op in ops_raw if isinstance(op, dict)] if isinstance(ops_raw, list) else []
    _name_checks(ops, out)
    if isinstance(ops_raw, list) and len(ops_raw) > pt.MAX_OPS:
        out.add("TOO_MANY_OPS", f"{len(ops_raw)} ops; at most {pt.MAX_OPS}", "ops")

    # Shape, against the declared schema when it is a known version (so a 0.2 patch in M0 is
    # named for what it does; the stage then refuses the version). A long op list is checked in
    # chunks of MAX_OPS so the count is reported once, as TOO_MANY_OPS.
    declared = patch.get("schema")
    schema_id = declared if declared in pt.PATCH_SCHEMA_IDS else pt.STAGE_SCHEMAS[stage][-1]
    if isinstance(ops_raw, list) and len(ops_raw) > pt.MAX_OPS:
        probes = [dict(patch, ops=ops_raw[i:i + pt.MAX_OPS]) for i in range(0, len(ops_raw), pt.MAX_OPS)]
    else:
        probes = [patch]
    shape = sorted({e for p in probes for e in pt.check_shape(p, schema_id=schema_id)})
    for e in shape:
        out.add("SCHEMA_INVALID", e)
    for e in pt.check_ops(patch, stage=stage):
        layer = e.split(":", 1)[0]
        out.add({"stage": "STAGE_OP_FORBIDDEN", "conflict": "OP_CONFLICT",
                 "bounds": "OUT_OF_BOUNDS"}[layer], e, _op_ref(e))
    if shape:
        return out.result()

    actual = sc.canonical_hash(base)
    if patch["base_hash"] != actual:
        out.add("STALE_BASE", f"base_hash {patch['base_hash']} is not the current scene's"
                              f" canonical hash {actual}", "base_hash")
        return out.result()

    cand = _simulate(base, ops, out)
    _judge(base, cand, out)
    return out.result(cand)


def validate_scene_change(base: dict, scene: Any, *, stage: str) -> Result:
    _check_inputs(base, stage)
    out = _Out()
    if not isinstance(scene, dict):
        out.add("SCHEMA_INVALID", "a scene must be a JSON object")
        return out.result()
    _scan_payload(scene, out, "scene")
    ops = _derive_ops(base, scene)
    if not ops:
        out.add("NOOP_PATCH", "the scene is the base scene (version aside)")
    if len(ops) > pt.MAX_OPS:
        out.add("TOO_MANY_OPS", f"{len(ops)} changes; at most {pt.MAX_OPS}")
    _name_checks(ops, out)
    for e in pt.check_ops({"schema": pt.STAGE_SCHEMAS[stage][0], "ops": ops}, stage=stage):
        if e.startswith("stage:"):
            out.add("STAGE_OP_FORBIDDEN", "change " + e.split(": ", 1)[1], _op_ref(e))
    _judge(base, scene, out)
    return out.result(scene)


# --- patch layer helpers ------------------------------------------------------------------------

def _op_ref(message: str) -> str | None:
    m = re.match(r"\w+: (ops\[\d+\])", message)
    return m.group(1) if m else None


def _scan_payload(node: Any, out: _Out, where: str):
    """CODE_PAYLOAD: any key or string with a character outside [A-Za-z0-9_./+-]."""
    def walk(n, path):
        if isinstance(n, str):
            if not _TOKEN_CHARS.fullmatch(n):
                out.add("CODE_PAYLOAD", f"{path}: {_short(n)!r} is not an ID, enum value or hash", path)
        elif isinstance(n, dict):
            for k, v in n.items():
                if isinstance(k, str) and not _TOKEN_CHARS.fullmatch(k):
                    out.add("CODE_PAYLOAD", f"{path}: key {_short(k)!r} is not an ID", path)
                walk(v, f"{path}.{k}")
        elif isinstance(n, list):
            for i, v in enumerate(n):
                walk(v, f"{path}[{i}]")
    walk(node, where)


def _norm(name: Any) -> str:
    return name.lower().replace("_", "") if isinstance(name, str) else ""


def _name_checks(ops: list, out: _Out):
    """PHYSICS_CHANGED / TELEPORT from what an op names (exact lists, see PHYSICS_NAMES)."""
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        if _norm(op.get("field")) in PHYSICS_NAMES:
            out.add("PHYSICS_CHANGED", f"ops[{i}] sets {_short(op.get('field'))!r}", f"ops[{i}]")
        ent = op.get("entity") if op.get("op") == "add" else None
        if isinstance(ent, dict):
            for k in ent:
                if _norm(k) in PHYSICS_NAMES:
                    out.add("PHYSICS_CHANGED", f"ops[{i}] adds an entity with {_short(k)!r}", f"ops[{i}]")
            if _norm(ent.get("type")) in TELEPORT_TYPES or ent.get("type") == "Spawn":
                out.add("TELEPORT", f"ops[{i}] adds a {_short(ent.get('type'))!r}", f"ops[{i}]")


def _canon(value: Any) -> str | None:
    try:
        return sc.canonical_json({"v": value})
    except (TypeError, ValueError):
        return None


def _same(a: Any, b: Any) -> bool:
    ca, cb = _canon(a), _canon(b)
    return ca is not None and ca == cb


def _simulate(base: dict, ops: list, out: _Out) -> dict:
    """Apply every well-formed op to a copy of base, whatever the stage allows. Bumps version."""
    new = copy.deepcopy(base)
    by_id = {e["id"]: e for e in new["entities"]}
    for i, op in enumerate(ops):
        ref = f"ops[{i}]"
        kind = op["op"]
        if kind == "add":
            ent = op["entity"]
            if ent.get("id") in by_id:
                out.add("BAD_TARGET", f"{ref}: an entity {_short(ent.get('id'))!r} already exists", ref)
                continue
            ent = copy.deepcopy(ent)
            new["entities"].append(ent)
            if isinstance(ent.get("id"), str):
                by_id[ent["id"]] = ent
            continue
        target = by_id.get(op["id"])
        if target is None:
            out.add("BAD_TARGET", f"{ref}: no entity {_short(op['id'])!r}", ref)
            continue
        if kind == "remove":
            new["entities"] = [e for e in new["entities"] if e is not target]
            del by_id[op["id"]]
            continue
        fld = "pos" if kind == "move" else op["field"]
        value = op["pos"] if kind == "move" else op["value"]
        if fld in ("id", "type") or fld not in target:
            out.add("BAD_TARGET", f"{ref}: {op['id']} ({target['type']}) has no settable field {_short(fld)!r}", ref)
            continue
        if _same(target[fld], value):
            out.add("NOOP_PATCH", f"{ref}: {op['id']}.{fld} is already {_canon(value)[5:-1]}", ref)
        target[fld] = copy.deepcopy(value)
    new["version"] = base["version"] + 1
    return new


def _derive_ops(base: dict, scene: dict) -> list[dict]:
    """The changes from base to scene, as synthetic ops (version ignored: ADR 0006 bookkeeping)."""
    ops: list[dict] = []
    for key in sorted(set(base) | set(scene)):
        if key in ("entities", "version"):
            continue
        if not (key in base and key in scene and _same(base[key], scene[key])):
            ops.append({"op": "set", "id": "scene", "field": key, "value": scene.get(key)})
    b_ents = {e["id"]: e for e in base["entities"]}
    s_ents = _entities(scene)
    for eid in sorted(b_ents):
        if eid not in s_ents:
            ops.append({"op": "remove", "id": eid})
            continue
        b, s = b_ents[eid], s_ents[eid]
        for fld in sorted(set(b) | set(s)):
            if fld in b and fld in s and _same(b[fld], s[fld]):
                continue
            if fld == "pos":
                ops.append({"op": "move", "id": eid, "pos": s.get("pos")})
            else:
                ops.append({"op": "set", "id": eid, "field": fld, "value": s.get(fld)})
    for eid in sorted(set(s_ents) - set(b_ents)):
        ops.append({"op": "add", "entity": s_ents[eid]})
    ents = scene.get("entities")
    if isinstance(ents, list):
        for e in ents:
            if not (isinstance(e, dict) and isinstance(e.get("id"), str)):
                ops.append({"op": "add", "entity": e if isinstance(e, dict) else {"value": e}})
    return ops


def _entities(scene: Any) -> dict:
    ents = scene.get("entities") if isinstance(scene, dict) else None
    out: dict = {}
    if isinstance(ents, list):
        for e in ents:
            if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"] not in out:
                out[e["id"]] = e
    return out


def _short(value: Any, limit: int = 40) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


# --- the scene judgement --------------------------------------------------------------------------

def _judge(base: dict, cand: dict, out: _Out):
    errors = sc.validate_scene(cand)
    in_grammar = True
    for e in errors:
        layer = e.split(":", 1)[0]
        if layer in ("schema", "string"):
            in_grammar = False
            out.add("SCHEMA_INVALID", e)
        elif layer == "bounds":
            in_grammar = False
            out.add("OUT_OF_BOUNDS", e)
        else:
            _map_ref(e, out)
    _protection(base, cand, out)
    if in_grammar:
        _geometry(base, cand, out)


def _map_ref(e: str, out: _Out):
    if "dangling reference" in e:
        out.add("DANGLING_REF", e)
    elif re.match(r"ref: \w+\.controls: gate ", e) or re.match(r"ref: \w+\.opens_on: lever ", e):
        out.add("GATE_LEVER_UNLINKED", e)
    elif e.startswith("ref: protected:"):
        out.add("PROTECTED_CHANGED", e)
    else:
        out.add("REF_INVALID", e)


def _protection(base: dict, cand: dict, out: _Out):
    """Raw checks that hold even on a candidate outside the grammar."""
    for key in _IDENTITY:
        if not _same(base.get(key), cand.get(key)):
            out.add("PROTECTED_CHANGED", f"scene {key} changed", f"scene.{key}")
    c_ents = _entities(cand)
    b_ents = {e["id"]: e for e in base["entities"]}
    for name in base["protected"]:
        if name in sc.RESERVED_NAMES:
            if not _same(base.get(name), cand.get(name)):
                out.add("PROTECTED_CHANGED", f"the room box ({name}) changed", f"scene.{name}")
        elif name not in c_ents:
            out.add("PROTECTED_CHANGED", f"protected {name} was removed", f"entity:{name}")
        elif not _same(b_ents[name], c_ents[name]):
            out.add("PROTECTED_CHANGED", f"protected {name} changed", f"entity:{name}")
    prot = cand.get("protected")
    missing = [n for n in base["protected"] if not (isinstance(prot, list) and n in prot)]
    if missing:
        out.add("PROTECTED_CHANGED", f"no longer protected: {', '.join(missing)}", "scene.protected")

    bc, cc = base["constraints"], cand.get("constraints")
    cc = cc if isinstance(cc, dict) else {}
    for key in ("min_gap", "min_lever_detour"):
        if not _same(bc.get(key), cc.get(key)):
            out.add("CONSTRAINTS_CHANGED", f"constraints.{key} changed", f"scene.constraints.{key}")
    if cc.get("player_physics") != "locked":
        out.add("PHYSICS_CHANGED", f"constraints.player_physics is {_short(cc.get('player_physics'))!r},"
                                   " not 'locked'", "scene.constraints.player_physics")
    if not _same(bc.get("required_events"), cc.get("required_events")):
        out.add("REQUIRED_EVENTS_CHANGED", "constraints.required_events changed",
                "scene.constraints.required_events")
    triggers = lambda ents: sorted(_canon(e) or "" for e in ents.values() if e.get("type") == "Trigger")
    if triggers(b_ents) != triggers(c_ents):
        out.add("REQUIRED_EVENTS_CHANGED", "the set of event triggers changed")

    def keys(n, path):
        if isinstance(n, dict):
            for k, v in n.items():
                p = f"{path}.{k}"
                if _norm(k) in PHYSICS_NAMES and p != "scene.constraints.player_physics":
                    out.add("PHYSICS_CHANGED", f"{p} names a player physics property", p)
                keys(v, p)
        elif isinstance(n, list):
            for i, v in enumerate(n):
                keys(v, f"{path}[{i}]")
    keys(cand, "scene")

    for eid, ent in c_ents.items():
        if eid not in b_ents:
            if _norm(ent.get("type")) in TELEPORT_TYPES or ent.get("type") == "Spawn":
                out.add("TELEPORT", f"{eid} is an added {_short(ent.get('type'))!r}", f"entity:{eid}")
            if ent.get("type") in _FLOORS:
                out.add("NEW_FLOOR", f"{eid} is an added {ent['type']} (never allowed in F1)", f"entity:{eid}")


# --- geometry -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Part:
    name: str
    lo: tuple
    hi: tuple
    solid: bool


def _part(p: dict, etype: str) -> _Part:
    size = list(p["size"])
    if p.get("yaw") in (90, -90, 270):
        size[0], size[2] = size[2], size[0]
    lo = tuple(p["pos"][k] - size[k] / 2 for k in range(3))
    hi = tuple(p["pos"][k] + size[k] / 2 for k in range(3))
    # Decor is built CanCollide off, but the validator does not rely on a template detail.
    return _Part(p["name"], lo, hi, bool(p["can_collide"]) or etype == "Decor")


def _parts(scene: dict) -> dict:
    by_id = _entities(scene)
    out = {}
    for eid, ent in sorted(by_id.items()):
        try:
            out[eid] = tuple(_part(p, ent["type"]) for p in geo.entity_parts(ent, by_id))
        except (KeyError, TypeError, IndexError, ValueError):
            continue  # a dangling reference: reported by the grammar layer
    return out


def _overlap(a_lo, a_hi, b_lo, b_hi) -> tuple | None:
    ov = tuple(min(a_hi[k], b_hi[k]) - max(a_lo[k], b_lo[k]) for k in range(len(a_lo)))
    return ov if all(o > EPS for o in ov) else None


def _frame(scene: dict) -> dict | None:
    """F1 frame from a scene: its gap, axis indices, faces, walking surface."""
    ents = _entities(scene)
    gaps = [e for _, e in sorted(ents.items()) if e.get("type") == "Gap"]
    if not gaps:
        return None
    gap = gaps[0]
    src, dst = ents.get(gap["from"]), ents.get(gap["to"])
    if not (src and dst and src.get("type") == "Platform" and dst.get("type") == "Platform"):
        return None
    i = {"X": 0, "Z": 2}[gap["axis"][1]]
    sign = 1 if gap["axis"][0] == "+" else -1
    from_face = src["pos"][i] + sign * src["size"][i] / 2
    to_face = dst["pos"][i] - sign * dst["size"][i] / 2
    return {"gap": gap, "src": src, "dst": dst, "i": i, "j": 2 - i, "sign": sign,
            "from_face": from_face, "to_face": to_face,
            "walk_top": src["pos"][1] + src["size"][1] / 2}


def _footprint(ent_or_part, i, j):
    lo, hi = ent_or_part
    return (lo[i], lo[j]), (hi[i], hi[j])


def _box(ent: dict):
    lo = tuple(ent["pos"][k] - ent["size"][k] / 2 for k in range(3))
    hi = tuple(ent["pos"][k] + ent["size"][k] / 2 for k in range(3))
    return lo, hi


def _num(x: float):
    x = round(x, 3) + 0.0
    return int(x) if x == int(x) else x


def _walkway(base: dict, fr: dict) -> list[tuple]:
    """(name, lo, hi) volumes from the base scene (ADR 0007)."""
    ents = _entities(base)
    i, j, top = fr["i"], fr["j"], fr["walk_top"]
    src = fr["src"]
    s_lo, s_hi = _box(src)
    along = [s_lo[i], s_hi[i], *_box(fr["dst"])[0][i:i + 1], _box(fr["dst"])[1][i]]
    for e in ents.values():
        if e.get("type") in ("Spawn", "Goal") and e.get("on") in ents:
            lo, hi = _box(ents[e["on"]])
            along += [lo[i], hi[i]]
    lo, hi = [0.0] * 3, [0.0] * 3
    lo[i], hi[i] = min(along), max(along)
    lo[j], hi[j] = s_lo[j], s_hi[j]
    lo[1], hi[1] = top, top + CLEARANCE
    vols = [("walkway strip", tuple(lo), tuple(hi))]
    for eid, e in sorted(ents.items()):
        if e.get("type") == "Lever" and e.get("on") in ents:
            p_lo, p_hi = _box(ents[e["on"]])
            vols.append((f"the {eid} platform", (p_lo[0], p_hi[1], p_lo[2]),
                         (p_hi[0], p_hi[1] + CLEARANCE, p_hi[2])))
    return vols


def measures(scene: dict) -> dict:
    """The quantities the rules use, for one scene (tests and docs)."""
    fr = _frame(scene)
    strip = _walkway(scene, fr)[0]
    ents = _entities(scene)
    return {
        "walk_top": _num(fr["walk_top"]),
        "gap_faces": tuple(sorted((_num(fr["from_face"]), _num(fr["to_face"])))),
        "lever_detour": {eid: _num(abs(e["pos"][fr["j"]] - fr["src"]["pos"][fr["j"]]))
                         for eid, e in sorted(ents.items()) if e.get("type") == "Lever"},
        "min_gap": scene["constraints"]["min_gap"],
        "min_lever_detour": scene["constraints"]["min_lever_detour"],
        "walkway_strip": {"lo": [_num(v) for v in strip[1]], "hi": [_num(v) for v in strip[2]]},
    }


def _bridge_join_ok(scene_ents: dict, a: str, b: str, ov: tuple) -> bool:
    for br, pl in ((a, b), (b, a)):
        e = scene_ents.get(br)
        if not (e and e.get("type") == "Bridge"):
            continue
        gap = scene_ents.get(e.get("spans"))
        if not (gap and gap.get("to") == pl):
            continue
        i = {"X": 0, "Z": 2}[gap["axis"][1]]
        if ov[i] <= JOIN_MAX + EPS:
            return True
    return False


def solid_overlaps(scene: dict, only: Iterable[str] | None = None) -> list[tuple[str, str, tuple]]:
    """Pairs of entities whose solid parts overlap (beyond EPS on every axis), the allowed bridge
    join excepted. With `only`, pairs that involve at least one of those ids."""
    parts = _parts(scene)
    ents = _entities(scene)
    only = set(parts) if only is None else set(only)
    found = []
    ids = sorted(parts)
    for x, a in enumerate(ids):
        for b in ids[x + 1:]:
            if a not in only and b not in only:
                continue
            hit = None
            for p in parts[a]:
                for q in parts[b]:
                    if p.solid and q.solid:
                        ov = _overlap(p.lo, p.hi, q.lo, q.hi)
                        if ov and not _bridge_join_ok(ents, a, b, ov):
                            hit = ov
                            break
                if hit:
                    break
            if hit:
                found.append((a, b, tuple(_num(v) for v in hit)))
    return found


def outside_room(scene: dict, only: Iterable[str] | None = None) -> list[tuple[str, str]]:
    """(entity, part) pairs with a built part (solid or not) outside the room box."""
    room = scene["bounds"]
    parts = _parts(scene)
    only = set(parts) if only is None else set(only)
    return [(eid, p.name) for eid in sorted(parts) if eid in only for p in parts[eid]
            if any(p.lo[k] < room["min"][k] - EPS or p.hi[k] > room["max"][k] + EPS for k in range(3))]


def _geometry(base: dict, cand: dict, out: _Out):
    fr_b, fr_c = _frame(base), _frame(cand)
    b_ents, c_ents = _entities(base), _entities(cand)
    b_parts, c_parts = _parts(base), _parts(cand)
    touched = sorted(eid for eid in c_parts if b_parts.get(eid) != c_parts[eid])

    # Structure: penetration and the room box, for what the patch touched.
    for a, b, ov in solid_overlaps(cand, touched):
        out.add("PENETRATION", f"{a} and {b} overlap by {list(ov)} studs", f"pair:{a}+{b}")
    for eid, name in outside_room(cand, touched):
        part = eid if name == eid else f"{eid}/{name}"
        out.add("OUTSIDE_ROOM", f"{part} leaves the room box", f"entity:{eid}")
    _layout(cand, fr_c, out)
    for eid, lever in sorted(c_ents.items()):
        if lever.get("type") != "Lever":
            continue
        plat = c_ents.get(lever.get("on"))
        if not (plat and plat.get("type") == "Platform"):
            continue
        p_lo, p_hi = _box(plat)
        x, y, z = lever["pos"]
        hx, hz = geo.LEVER_BASE[0] / 2, geo.LEVER_BASE[2] / 2
        on_top = abs(y - p_hi[1]) <= EPS
        inside = (p_lo[0] - EPS <= x - hx and x + hx <= p_hi[0] + EPS
                  and p_lo[2] - EPS <= z - hz and z + hz <= p_hi[2] + EPS)
        if not (on_top and inside):
            out.add("UNSUPPORTED", f"{eid} does not stand on {lever['on']}", f"entity:{eid}")
    if fr_b is None:
        return

    # Difficulty: the gap.
    gap_b = fr_b["gap"]
    gap_c = c_ents.get(gap_b["id"])
    min_gap = base["constraints"]["min_gap"]
    if not _same(gap_b, gap_c):
        out.add("GAP_CHANGED", f"{gap_b['id']} changed", f"entity:{gap_b['id']}")
    if fr_c is not None:
        dist = fr_c["sign"] * (fr_c["to_face"] - fr_c["from_face"])
        base_dist = fr_b["sign"] * (fr_b["to_face"] - fr_b["from_face"])
        width = fr_c["gap"]["width"]
        if abs(dist - base_dist) > EPS or abs(dist - width) > EPS:
            out.add("GAP_CHANGED", f"the platforms of {fr_c['gap']['id']} are {_num(dist)} studs apart"
                                   f" (base {_num(base_dist)}; width says {_num(width)})",
                    f"entity:{fr_c['gap']['id']}")
        if min(dist, width) < min_gap - EPS:
            out.add("GAP_BELOW_MIN", f"{fr_c['gap']['id']} is {_num(min(dist, width))} studs;"
                                     f" min_gap is {min_gap}", f"entity:{fr_c['gap']['id']}")
        for eid, e in sorted(c_ents.items()):
            if e.get("type") == "Bridge" and e.get("spans") == fr_c["gap"]["id"] and eid in c_parts:
                p = c_parts[eid][0]
                end = p.hi[fr_c["i"]] if fr_c["sign"] > 0 else -p.lo[fr_c["i"]]
                if end < fr_c["sign"] * fr_c["to_face"] - EPS:
                    out.warn("BRIDGE_SHORT", f"{eid} ends {_num(fr_c['sign'] * fr_c['to_face'] - end)}"
                                             f" studs before {fr_c['gap']['to']}", f"entity:{eid}")

    # Difficulty: gates stay linked and fill their slot; levers keep their detour.
    i, j = fr_b["i"], fr_b["j"]
    for gid, gate in sorted(b_ents.items()):
        if gate.get("type") != "Gate":
            continue
        lid = gate["opens_on"]
        g, lever = c_ents.get(gid), c_ents.get(lid)
        if not (g and g.get("type") == "Gate"):
            out.add("GATE_LEVER_UNLINKED", f"gate {gid} is gone", f"entity:{gid}")
            continue
        if not (lever and lever.get("type") == "Lever" and g.get("opens_on") == lid
                and lever.get("controls") == gid):
            out.add("GATE_LEVER_UNLINKED", f"gate {gid} and lever {lid} no longer point at each other",
                    f"entity:{gid}")
        (blo, bhi), (clo, chi) = _box(gate), _box(g)
        covers = all(clo[k] <= blo[k] + EPS and chi[k] >= bhi[k] - EPS for k in (j, 1))
        along = min(chi[i], bhi[i]) - max(clo[i], blo[i]) > EPS
        if not (covers and along):
            out.add("GATE_BYPASSED", f"gate {gid} no longer fills its slot", f"entity:{gid}")
    need = base["constraints"]["min_lever_detour"]
    for eid, lever in sorted(c_ents.items()):
        if lever.get("type") == "Lever":
            d = abs(lever["pos"][j] - fr_b["src"]["pos"][j])
            if d < need - EPS:
                out.add("DETOUR_TOO_SHORT", f"{eid} is {_num(d)} studs from the path centreline;"
                                            f" min_lever_detour is {need}", f"entity:{eid}")

    # Protection and difficulty for added/changed Walls and Decor: no new floors, no blocking.
    room = cand["bounds"]
    top = fr_b["walk_top"]
    g_lo, g_hi = [0.0] * 3, [0.0] * 3
    g_lo[i], g_hi[i] = sorted((fr_b["from_face"], fr_b["to_face"]))
    g_lo[j], g_hi[j] = room["min"][j], room["max"][j]
    g_lo[1], g_hi[1] = room["min"][1], top + CLEARANCE
    walkable = [(p.lo, p.hi) for eid, ps in b_parts.items() for p in ps
                if b_ents[eid]["type"] in ("Platform", "Bridge")]
    walkway = _walkway(base, fr_b)
    for eid in touched:
        ent = c_ents[eid]
        if ent.get("type") not in ("Wall", "Decor"):
            continue
        for p in c_parts[eid]:
            if _overlap(p.lo, p.hi, g_lo, g_hi):
                out.add("NEW_FLOOR", f"{eid} is a solid over the gap span", f"entity:{eid}")
            spans = (p.hi[0] - p.lo[0], p.hi[2] - p.lo[2])
            if abs(p.hi[1] - top) <= FLOOR_BAND + EPS and min(spans) >= FLOOR_MIN_SPAN - EPS:
                inside = any(w_lo[0] - EPS <= p.lo[0] and p.hi[0] <= w_hi[0] + EPS and
                             w_lo[2] - EPS <= p.lo[2] and p.hi[2] <= w_hi[2] + EPS
                             for w_lo, w_hi in walkable)
                if not inside:
                    out.add("NEW_FLOOR", f"{eid} is floor-like (top at {_num(p.hi[1])}) outside every"
                                         " base floor", f"entity:{eid}")
            for name, v_lo, v_hi in walkway:
                if _overlap(p.lo, p.hi, v_lo, v_hi):
                    out.add("PATH_BLOCKED", f"{eid} intrudes into {name}", f"entity:{eid}")

    # Teleport: more than one spawn point.
    spawns = [eid for eid, e in c_ents.items() if e.get("type") == "Spawn"]
    if len(spawns) > 1:
        out.add("TELEPORT", f"{len(spawns)} spawn points: {', '.join(sorted(spawns))}")


def _layout(cand: dict, fr: dict | None, out: _Out):
    ents = _entities(cand)
    counts = {t: sorted(eid for eid, e in ents.items() if e.get("type") == t) for t in ("Spawn", "Goal", "Gap")}
    for t, ids in counts.items():
        if len(ids) != 1:
            out.add("LAYOUT_BROKEN", f"{len(ids)} {t} entities; F1 has exactly one")
    if fr is None or len(counts["Spawn"]) < 1 or len(counts["Goal"]) < 1:
        return
    goal = ents[counts["Goal"][0]]
    if goal.get("on") != fr["gap"]["to"]:
        out.add("LAYOUT_BROKEN", f"the goal is on {goal.get('on')}, not on {fr['gap']['to']}"
                                 " (the far side of the gap)", f"entity:{goal['id']}")
    for sid in counts["Spawn"]:
        plat = ents.get(ents[sid].get("on"))
        if not (plat and plat.get("type") == "Platform"):
            continue
        lo, hi = _box(plat)
        near = hi[fr["i"]] <= fr["from_face"] + EPS if fr["sign"] > 0 else lo[fr["i"]] >= fr["from_face"] - EPS
        if not near:
            out.add("LAYOUT_BROKEN", f"{sid} is on {plat['id']}, not on the near side of the gap",
                    f"entity:{sid}")
