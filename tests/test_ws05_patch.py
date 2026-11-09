"""WS-05: automated patch apply, M0 stage (bridge_1.length only), base-hash check.

SPEC section 6 (patch DSL), section 15 (WS-05: a stale base is rejected; any other op is
rejected in M0), section 16 (abuse cases that belong to the patch layer). The full validator
(protection, difficulty, structure) is WS-06 and is not tested here.
"""

import copy
import json
import os
import re
import subprocess
import sys

import jsonschema
import pytest

from _repo import REPO_ROOT
from _scenes import F1A, F1A_FIX, F1E, entity, leaf_paths, get_at, raw
from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt

PATCH_SCHEMA_PATH = REPO_ROOT / "grammar" / "patch_schema.json"
M0_FIX_PATCH = REPO_ROOT / "scenes" / "F1-A" / "patches" / "m0_fix.json"

F1A_HASH = "92f866098bfb850cf4c464179887a862f305c143c8e0b38efdc7978493cddb1d"
F1A_FIX_HASH = "49371fa767666577d2f1c30571231069af8eeaeb2eace76e6e832f38c281a396"
# F1-A after the M0 patch: fix.json's content with version 2 (ADR 0006).
F1A_PATCHED_HASH = "297b23c884e196a80f9a19aa32d006768e1ace84cfbd4bd1e932981fdabf0535"
# SHA-256 of the canonical JSON of the M0 fix patch.
M0_FIX_PATCH_HASH = "d89a74ca6a76c777f23c9939c619630487b919321e22c3f3b025dcf3eb3686c5"


def _op(id="bridge_1", field="length", value=16, op="set"):
    return {"op": op, "id": id, "field": field, "value": value}


def _patch(*ops, base=F1A_HASH):
    return {"schema": "worldsmith.patch/0.1", "base_hash": base,
            "ops": list(ops) if ops else [_op()]}


def _f1a():
    return raw(F1A)


def _rejected(patch, scene=None, stage="M0", layer=None):
    scene = _f1a() if scene is None else scene
    with pytest.raises(pt.PatchError) as exc:
        pt.apply_patch(scene, patch, stage=stage)
    errs = exc.value.errors
    assert errs, "a PatchError must list its errors"
    if layer:
        assert any(e.startswith(layer + ":") for e in errs), errs
    return exc.value


# --- constants and data files -------------------------------------------------------

def test_patch_schema_id_and_op_cap():
    assert pt.PATCH_SCHEMA_ID == "worldsmith.patch/0.1"
    assert pt.MAX_OPS == 3


def test_m0_stage_allows_exactly_bridge_1_length():
    assert set(pt.STAGES) == {"M0"}
    rules = pt.STAGES["M0"]
    assert len(rules) == 1
    rule = rules[0]
    assert (rule.op, rule.type, rule.id, rule.field) == ("set", "Bridge", "bridge_1", "length")


def test_patch_schema_file_is_valid_draft_2020_12_and_closed():
    schema = json.loads(PATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["title"] == "worldsmith.patch/0.1"
    assert schema["properties"]["ops"]["maxItems"] == 3
    assert schema["properties"]["ops"]["minItems"] == 1

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    found = list(objects(schema))
    assert len(found) >= 2  # the patch and the op
    for obj in found:
        assert obj.get("additionalProperties") is False, obj
    assert pt.load_patch_schema() == schema


def test_patch_schema_ships_as_package_data():
    import importlib.resources
    assert importlib.resources.files("worldsmith.grammar").joinpath("patch_schema.json").is_file()
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("[tool.setuptools.package-data]", 1)[1]
    assert re.search(r'"worldsmith\.grammar"\s*=\s*\[[^\]]*"patch_schema\.json"', block)


def test_adr_0006_records_the_versioning_decision():
    adr = (REPO_ROOT / "docs" / "ADR" / "0006-patch-versioning.md").read_text(encoding="utf-8")
    low = adr.lower()
    for needle in ["version", "by 1", "base_hash", "stale", F1A_PATCHED_HASH, "m0"]:
        assert needle in low, needle


# --- the M0 fix, reproduced ---------------------------------------------------------

def test_committed_m0_fix_patch_is_the_one_op_patch_on_f1a():
    patch = pt.load_patch(M0_FIX_PATCH)
    assert patch == _patch(_op())
    assert patch["base_hash"] == sc.canonical_hash(_f1a()) == F1A_HASH
    assert pt.patch_hash(patch) == M0_FIX_PATCH_HASH


def test_m0_fix_patch_reproduces_fix_json_except_version():
    base = _f1a()
    out = pt.apply_patch(base, pt.load_patch(M0_FIX_PATCH), stage="M0")
    fix = raw(F1A_FIX)
    assert fix["version"] == 1 and out.scene["version"] == 2
    # The precise difference: every leaf equal except `version` (1 -> 2).
    diffs = [p for p in leaf_paths(fix) if get_at(fix, p) != get_at(out.scene, p)]
    assert diffs == [("version",)]
    assert leaf_paths(fix) == leaf_paths(out.scene)
    assert {**out.scene, "version": 1} == fix
    assert out.scene == {**fix, "version": 2}


def test_m0_fix_hashes_are_recorded():
    out = pt.apply_patch(_f1a(), _patch(), stage="M0")
    assert out.base_hash == F1A_HASH
    assert out.scene_hash == sc.canonical_hash(out.scene) == F1A_PATCHED_HASH
    assert out.scene_hash != F1A_FIX_HASH  # the version is part of the hash (ADR 0002)
    assert sc.canonical_hash({**out.scene, "version": 1}) == F1A_FIX_HASH
    assert out.version == 2
    assert out.patch_hash == M0_FIX_PATCH_HASH
    assert out.stage == "M0"


def test_patched_scene_is_a_valid_scene():
    out = pt.apply_patch(_f1a(), _patch(), stage="M0")
    assert sc.validate_scene(out.scene) == []
    assert entity(out.scene, "bridge_1")["length"] == 16


def test_patch_applies_to_f1e_with_its_own_base():
    base = raw(F1E)
    out = pt.apply_patch(base, _patch(_op(value=12), base=sc.canonical_hash(base)), stage="M0")
    assert entity(out.scene, "bridge_1")["length"] == 12
    assert out.scene["version"] == base["version"] + 1


@pytest.mark.parametrize("value", [1, 64, 1.0, 63.999, 12.5])
def test_values_inside_bounds_are_accepted(value):
    out = pt.apply_patch(_f1a(), _patch(_op(value=value)), stage="M0")
    assert entity(out.scene, "bridge_1")["length"] == value


def test_value_is_stored_at_canonical_precision():
    out = pt.apply_patch(_f1a(), _patch(_op(value=12.3456)), stage="M0")
    assert entity(out.scene, "bridge_1")["length"] == 12.346
    out2 = pt.apply_patch(_f1a(), _patch(_op(value=16.0004)), stage="M0")
    assert entity(out2.scene, "bridge_1")["length"] == 16
    assert out2.scene_hash == F1A_PATCHED_HASH


# --- stale base -------------------------------------------------------------------

@pytest.mark.parametrize("base", [
    F1A_FIX_HASH,
    "f8a8e654274354e33b340da77235f6b27ea0aa01fcdd55235fd2c98d45df0db3",  # F1-E
    "0" * 64,
    F1A_PATCHED_HASH,
])
def test_stale_base_is_rejected(base):
    exc = _rejected(_patch(_op(), base=base), layer="stale")
    assert isinstance(exc, pt.StalePatch)
    assert exc.expected == base and exc.actual == F1A_HASH


def test_stale_patch_is_a_patch_error():
    assert issubclass(pt.StalePatch, pt.PatchError)
    assert issubclass(pt.PatchError, ValueError)


def test_any_scene_change_makes_the_patch_stale():
    scene = _f1a()
    entity(scene, "decor_1")["pos"][0] = 7
    assert isinstance(_rejected(_patch(), scene=scene), pt.StalePatch)


def test_version_change_makes_the_patch_stale():
    scene = _f1a()
    scene["version"] = 2
    assert isinstance(_rejected(_patch(), scene=scene), pt.StalePatch)


def test_order_of_entities_does_not_make_the_patch_stale():
    scene = _f1a()
    scene["entities"].reverse()
    scene["protected"].reverse()
    out = pt.apply_patch(scene, _patch(), stage="M0")
    assert out.scene_hash == F1A_PATCHED_HASH


def test_a_patch_cannot_be_applied_twice():
    first = pt.apply_patch(_f1a(), _patch(), stage="M0")
    with pytest.raises(pt.StalePatch):
        pt.apply_patch(first.scene, _patch(), stage="M0")


# --- M0 stage: any other op is rejected --------------------------------------------

@pytest.mark.parametrize("id,field,value", [
    ("bridge_1", "width", 8),
    ("bridge_1", "thickness", 2),
    ("bridge_1", "lateral_offset", 1),
    ("gap_1", "width", 6),
    ("gap_1", "length", 16),
    ("goal", "size", 4),
    ("gate_1", "size", 1),
    ("lever_1", "pos", 3),
    ("kill_floor", "pos", 0),
    ("plat_b", "size", 40),
    ("spawn", "on", 1),
    ("trig_bridge", "height", 1),
    ("decor_1", "size", 1),
    ("bridge_2", "length", 16),
    ("bridge_1", "spans", 1),
    ("bridge_1", "id", 1),
    ("bridge_1", "type", 1),
    ("scene", "version", 2),
    ("constraints", "min_gap", 4),
    ("bounds", "max", 100),
])
def test_non_bridge_length_op_is_rejected_in_m0(id, field, value):
    exc = _rejected(_patch(_op(id=id, field=field, value=value)), layer="stage")
    assert not isinstance(exc, pt.StalePatch)


@pytest.mark.parametrize("op", ["move", "add", "remove", "delete", "replace", "SET", "set "])
def test_unknown_op_is_rejected(op):
    _rejected(_patch(_op(op=op)), layer="schema")


def test_bridge_1_must_be_a_bridge_in_the_scene():
    scene = _f1a()
    entity(scene, "bridge_1")["id"] = "bridge_2"
    errs = _rejected(_patch(base=sc.canonical_hash(scene)), scene=scene, layer="apply").errors
    assert any("bridge_1" in e for e in errs)


def test_unknown_stage_is_a_programming_error():
    for stage in ["M1", "m0", "", None]:
        with pytest.raises(ValueError) as exc:
            pt.apply_patch(_f1a(), _patch(), stage=stage)
        assert not isinstance(exc.value, pt.PatchError)


def test_stage_is_required():
    with pytest.raises(TypeError):
        pt.apply_patch(_f1a(), _patch())  # noqa


# --- op count -------------------------------------------------------------------

@pytest.mark.parametrize("n", [4, 5, 10])
def test_more_than_three_ops_are_rejected(n):
    _rejected(_patch(*[_op() for _ in range(n)]), layer="schema")


def test_zero_ops_are_rejected():
    p = _patch()
    p["ops"] = []
    _rejected(p, layer="schema")


@pytest.mark.parametrize("n", [2, 3])
def test_two_ops_on_one_target_are_rejected(n):
    _rejected(_patch(*[_op(value=10 + i) for i in range(n)]), layer="conflict")


def test_ops_must_be_a_list():
    p = _patch()
    p["ops"] = _op()
    _rejected(p, layer="schema")


# --- unknown and missing keys ---------------------------------------------------------

@pytest.mark.parametrize("key,value", [
    ("comment", "longer bridge"), ("stage", "M1"), ("version", 2), ("note", 1),
    ("scene", {}), ("lua", "print(1)"),
])
def test_unknown_top_level_key_is_rejected(key, value):
    p = _patch()
    p[key] = value
    _rejected(p, layer="schema")


@pytest.mark.parametrize("key,value", [
    ("comment", "x"), ("reason", "bridge too short"), ("script", "x"), ("type", "Bridge"),
    ("unit", "stud"), ("value2", 1),
])
def test_unknown_op_key_is_rejected(key, value):
    op = _op()
    op[key] = value
    _rejected(_patch(op), layer="schema")


@pytest.mark.parametrize("missing", ["schema", "base_hash", "ops"])
def test_missing_top_level_key_is_rejected(missing):
    p = _patch()
    del p[missing]
    _rejected(p, layer="schema")


@pytest.mark.parametrize("missing", ["op", "id", "field", "value"])
def test_missing_op_key_is_rejected(missing):
    op = _op()
    del op[missing]
    _rejected(_patch(op), layer="schema")


@pytest.mark.parametrize("field", ["length2", "Length", "len", "length_m", "lengths"])
def test_unknown_field_is_rejected(field):
    _rejected(_patch(_op(field=field)), layer="stage")


@pytest.mark.parametrize("schema", ["worldsmith.patch/0.2", "worldsmith.scene/0.1", "", 1])
def test_wrong_schema_is_rejected(schema):
    p = _patch()
    p["schema"] = schema
    _rejected(p, layer="schema")


# --- bounds -----------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 0.5, 0.9996, -16, 64.001, 65, 1e6, 10 ** 400, -(10 ** 400)])
def test_out_of_bounds_value_is_rejected(value):
    exc = _rejected(_patch(_op(value=value)), layer="bounds")
    assert not isinstance(exc, pt.StalePatch)


def test_bounds_come_from_bounds_yaml():
    b = sc.load_bounds()["Bridge.length"]
    pt.apply_patch(_f1a(), _patch(_op(value=b["min"])), stage="M0")
    pt.apply_patch(_f1a(), _patch(_op(value=b["max"])), stage="M0")
    _rejected(_patch(_op(value=b["max"] + 0.01)), layer="bounds")


def test_version_past_its_bound_is_rejected():
    scene = _f1a()
    scene["version"] = sc.load_bounds()["scene.version"]["max"]
    _rejected(_patch(base=sc.canonical_hash(scene)), scene=scene, layer="scene")


# --- code or free-text payloads -------------------------------------------------------

@pytest.mark.parametrize("value", [
    "16", "game:GetService('Workspace')", "require(123)", "os.exit()", "http://x.y/z",
    "", [16], {"x": 16}, None, True, False,
])
def test_non_number_value_is_rejected(value):
    _rejected(_patch(_op(value=value)), layer="schema")


@pytest.mark.parametrize("id", [
    "bridge_1\n", "bridge_1; os.exit()", "Bridge_1", "game:GetService", "http://x",
    "require(1)", "bridge 1", "", "1bridge", "b" * 33, 1, None, ["bridge_1"],
])
def test_bad_id_is_rejected(id):
    exc = _rejected(_patch(_op(id=id)))
    assert any(e.split(":", 1)[0] in {"schema", "string"} for e in exc.errors), exc.errors


@pytest.mark.parametrize("field", ["length\n", "length;", "length()", "Length\n", "", 7, None])
def test_bad_field_is_rejected(field):
    exc = _rejected(_patch(_op(field=field)))
    assert any(e.split(":", 1)[0] in {"schema", "string"} for e in exc.errors), exc.errors


@pytest.mark.parametrize("base", [
    F1A_HASH.upper(), F1A_HASH + "\n", F1A_HASH[:-1], F1A_HASH + "0", "sha256:" + F1A_HASH,
    "", None, 1,
])
def test_bad_base_hash_is_rejected(base):
    exc = _rejected(_patch(base=base))
    assert any(e.split(":", 1)[0] in {"schema", "string"} for e in exc.errors), exc.errors
    assert not isinstance(exc, pt.StalePatch)


@pytest.mark.parametrize("patch", [[], "patch", 16, None])
def test_patch_must_be_an_object(patch):
    _rejected(patch, layer="schema")


# --- strict JSON: NaN, Infinity, duplicate keys -------------------------------------------

def _text(value_literal="16", base=F1A_HASH):
    return ('{"schema":"worldsmith.patch/0.1","base_hash":"%s",'
            '"ops":[{"op":"set","id":"bridge_1","field":"length","value":%s}]}'
            % (base, value_literal))


def test_parse_patch_text_accepts_the_plain_patch():
    assert pt.parse_patch_text(_text()) == _patch()


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_non_finite_number_in_text_is_rejected(literal):
    with pytest.raises(pt.PatchError) as exc:
        pt.parse_patch_text(_text(literal))
    assert all(e.startswith("json:") for e in exc.value.errors)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_number_in_object_is_rejected(value):
    _rejected(_patch(_op(value=value)))


@pytest.mark.parametrize("text", [
    '{"schema":"worldsmith.patch/0.1","schema":"worldsmith.patch/0.1",'
    '"base_hash":"%s","ops":[{"op":"set","id":"bridge_1","field":"length","value":16}]}'
    % F1A_HASH,
    '{"schema":"worldsmith.patch/0.1","base_hash":"%s",'
    '"ops":[{"op":"set","id":"bridge_1","field":"length","value":6,"value":16}]}' % F1A_HASH,
    '{"schema":"worldsmith.patch/0.1","base_hash":"%s",'
    '"ops":[{"op":"set","id":"gap_1","id":"bridge_1","field":"length","value":16}]}' % F1A_HASH,
])
def test_duplicate_keys_are_rejected(text):
    with pytest.raises(pt.PatchError) as exc:
        pt.parse_patch_text(text)
    assert any("duplicate key" in e for e in exc.value.errors)


@pytest.mark.parametrize("text", ["[]", "16", '"patch"', "null", "", "{", "{} {}"])
def test_parse_patch_text_rejects_non_objects_and_bad_json(text):
    with pytest.raises(pt.PatchError):
        pt.parse_patch_text(text)


def test_load_patch_validates_the_shape(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(_text('"16"'), encoding="utf-8")
    with pytest.raises(pt.PatchError):
        pt.load_patch(bad)
    dup = tmp_path / "dup.json"
    dup.write_text('{"ops":[],"ops":[]}', encoding="utf-8")
    with pytest.raises(pt.PatchError):
        pt.load_patch(dup)


# --- the base scene must itself be valid -----------------------------------------------

def test_invalid_base_scene_is_rejected():
    scene = _f1a()
    scene["note"] = "x"
    _rejected(_patch(), scene=scene, layer="base")


def test_check_patch_needs_no_scene_and_lists_every_problem():
    p = _patch(_op(id="gap_1", field="width", value=6), _op(value=100))
    errs = pt.check_patch(p, stage="M0")
    assert any(e.startswith("stage:") for e in errs)
    assert any(e.startswith("bounds:") for e in errs)
    assert pt.check_patch(_patch(), stage="M0") == []


# --- purity, idempotence, determinism ------------------------------------------------

def test_apply_is_pure():
    scene, patch = _f1a(), _patch()
    scene_before, patch_before = copy.deepcopy(scene), copy.deepcopy(patch)
    out = pt.apply_patch(scene, patch, stage="M0")
    assert scene == scene_before and patch == patch_before
    assert out.scene is not scene
    # No shared mutable structure: editing the result leaves the input alone.
    entity(out.scene, "bridge_1")["width"] = 99
    out.scene["entities"].append({"id": "x"})
    out.scene["protected"].append("x")
    assert scene == scene_before


def test_rejected_apply_is_pure_too():
    scene, patch = _f1a(), _patch(_op(value=1000))
    scene_before, patch_before = copy.deepcopy(scene), copy.deepcopy(patch)
    with pytest.raises(pt.PatchError):
        pt.apply_patch(scene, patch, stage="M0")
    assert scene == scene_before and patch == patch_before


def test_applied_patch_result_is_frozen():
    out = pt.apply_patch(_f1a(), _patch(), stage="M0")
    with pytest.raises(Exception):
        out.scene_hash = "x"


def test_same_inputs_give_the_same_result():
    a = pt.apply_patch(_f1a(), _patch(), stage="M0")
    b = pt.apply_patch(_f1a(), _patch(), stage="M0")
    assert a == b
    assert json.dumps(a.scene, sort_keys=True) == json.dumps(b.scene, sort_keys=True)


def test_set_is_idempotent_on_content():
    first = pt.apply_patch(_f1a(), _patch(), stage="M0")
    again = pt.apply_patch(first.scene, _patch(base=first.scene_hash), stage="M0")
    assert again.scene == {**first.scene, "version": 3}
    assert entity(again.scene, "bridge_1")["length"] == 16
    assert again.scene_hash != first.scene_hash  # a new version is a new scene


def test_patch_hash_ignores_key_order_and_number_spelling():
    p = _patch()
    q = {"ops": [{"value": 16.0, "field": "length", "id": "bridge_1", "op": "set"}],
         "base_hash": F1A_HASH, "schema": "worldsmith.patch/0.1"}
    assert pt.patch_hash(p) == pt.patch_hash(q) == M0_FIX_PATCH_HASH


def test_patch_hash_depends_on_op_order():
    a = _patch(_op(value=10), _op(id="bridge_1", field="length", value=12))
    b = _patch(_op(id="bridge_1", field="length", value=12), _op(value=10))
    assert pt.patch_hash(a) != pt.patch_hash(b)


_PROBE = r"""
import json, sys
from worldsmith.validator import patch as pt
scene = json.load(open(sys.argv[1], encoding="utf-8"))
p = pt.load_patch(sys.argv[2])
out = pt.apply_patch(scene, p, stage="M0")
print(out.scene_hash, out.patch_hash, json.dumps(out.scene, sort_keys=True))
"""


def test_apply_is_deterministic_across_processes():
    outs = set()
    for seed in ["0", "1", "12345"]:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        res = subprocess.run([sys.executable, "-c", _PROBE, str(F1A), str(M0_FIX_PATCH)],
                             capture_output=True, text=True, env=env, check=True)
        outs.add(res.stdout)
    assert len(outs) == 1
    scene_hash, patch_hash, _ = outs.pop().split(" ", 2)
    assert scene_hash == F1A_PATCHED_HASH and patch_hash == M0_FIX_PATCH_HASH
