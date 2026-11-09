"""WS-02: the F1 scene schema is closed, and the fixtures validate against it."""

import copy
import json

import pytest

from _repo import REPO_ROOT
from _scenes import (
    ENTITY_TYPES, F1A, FIXTURES, entity, get_at, object_paths, raw, with_value,
)
from worldsmith.grammar import scene as sc

SCHEMA_PATH = REPO_ROOT / "grammar" / "schema.json"
TOP_LEVEL_REQUIRED = {
    "schema", "scene_id", "family", "version", "units", "up",
    "protected", "constraints", "entities",
}


def _errors(scene):
    return sc.validate_scene(scene)


def _assert_rejected(scene, layer=None):
    errs = _errors(scene)
    assert errs, "expected the scene to be rejected"
    if layer:
        assert any(e.startswith(layer + ":") for e in errs), errs
    return errs


# --- the schema file itself -------------------------------------------------

def test_schema_is_draft_2020_12_for_scene_0_1():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == "worldsmith.scene/0.1"
    assert TOP_LEVEL_REQUIRED <= set(schema["required"])


def test_schema_is_itself_a_valid_draft_2020_12_schema():
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(sc.load_schema())


def test_entity_type_set_is_exactly_the_spec_closed_set():
    schema = sc.load_schema()
    assert set(schema["$defs"]["entity_type"]["enum"]) == ENTITY_TYPES
    for t in ENTITY_TYPES:
        d = schema["$defs"][t]
        assert d["properties"]["type"] == {"const": t}
    assert set(sc.ENTITY_TYPES) == ENTITY_TYPES


def _walk(node, path=()):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, path + (i,))


def test_every_object_schema_is_closed():
    for path, node in _walk(sc.load_schema()):
        if node.get("type") == "object":
            closed = node.get("additionalProperties") is False or node.get(
                "unevaluatedProperties") is False
            assert closed, f"object schema at {path} is open"


def test_every_string_schema_is_an_enum_const_or_strict_pattern():
    allowed_patterns = {sc.ID_PATTERN}
    for path, node in _walk(sc.load_schema()):
        if node.get("type") == "string":
            ok = "enum" in node or "const" in node or node.get("pattern") in allowed_patterns
            assert ok, f"free-text string schema at {path}"


def test_id_pattern_is_strict():
    assert sc.ID_PATTERN == "^[a-z][a-z0-9_]{0,31}$"


# --- the fixtures validate --------------------------------------------------

@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_fixture_validates(path):
    assert _errors(raw(path)) == []
    loaded = sc.load_scene(path)
    assert loaded["schema"] == "worldsmith.scene/0.1"
    assert loaded["family"] == "F1"


def test_load_scene_raises_scene_error_with_all_messages(tmp_path):
    bad = raw(F1A)
    bad["entities"].append({"id": "x", "type": "Teleporter"})
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(sc.SceneError) as exc:
        sc.load_scene(p)
    assert exc.value.errors


# --- unknown keys, unknown types --------------------------------------------

@pytest.mark.parametrize(
    "obj_path", object_paths(raw(F1A)), ids=lambda p: "/".join(map(str, p)) or "<root>")
def test_unknown_key_rejected_at_every_level(obj_path):
    scene = raw(F1A)
    get_at(scene, obj_path)["zz_unknown"] = 1
    _assert_rejected(scene, "schema")


@pytest.mark.parametrize("key", ["script", "luau", "source", "url", "note", "description"])
def test_code_or_text_fields_are_unknown_keys(key):
    scene = raw(F1A)
    entity(scene, "bridge_1")[key] = "x"
    _assert_rejected(scene, "schema")


@pytest.mark.parametrize("bad_type", ["Teleporter", "Script", "bridge", "", "Floor"])
def test_unknown_entity_type_rejected(bad_type):
    scene = raw(F1A)
    scene["entities"].append({"id": "extra_1", "type": bad_type, "pos": [0, 0, 0]})
    _assert_rejected(scene, "schema")


@pytest.mark.parametrize("field", sorted(TOP_LEVEL_REQUIRED) + ["bounds"])
def test_missing_top_level_field_rejected(field):
    scene = raw(F1A)
    del scene[field]
    _assert_rejected(scene, "schema")


def test_missing_entity_field_rejected():
    scene = raw(F1A)
    del entity(scene, "bridge_1")["length"]
    _assert_rejected(scene, "schema")


@pytest.mark.parametrize("field,value", [
    ("schema", "worldsmith.scene/0.2"),
    ("family", "F2"),
    ("units", "meter"),
    ("up", "+Z"),
])
def test_top_level_enums_are_closed(field, value):
    _assert_rejected(with_value(raw(F1A), (field,), value), "schema")


def test_player_physics_must_be_locked():
    scene = raw(F1A)
    scene["constraints"]["player_physics"] = "free"
    _assert_rejected(scene, "schema")


def test_required_events_are_a_closed_enum():
    scene = raw(F1A)
    scene["constraints"]["required_events"].append("TELEPORTED")
    _assert_rejected(scene, "schema")


@pytest.mark.parametrize("value", [True, None, "7", [7], {"v": 7}])
def test_numbers_must_be_numbers(value):
    scene = raw(F1A)
    entity(scene, "bridge_1")["length"] = value
    _assert_rejected(scene, "schema")


@pytest.mark.parametrize("vec", [[1, 2], [1, 2, 3, 4], []])
def test_vectors_have_exactly_three_components(vec):
    scene = raw(F1A)
    entity(scene, "plat_a")["pos"] = vec
    _assert_rejected(scene, "schema")


def test_version_must_be_an_integer():
    _assert_rejected(with_value(raw(F1A), ("version",), 1.5), "schema")


# --- free text and code-looking strings --------------------------------------

CODE_LOOKING = [
    "game:GetService",
    'game:GetService("Players")',
    "https://example.com/x",
    "http://evil",
    "require(12345)",
    "Workspace.Part",
    "plat_a; drop",
    "Plat_A",
    "1plat",
    "plat a",
    "plat_a\n",
    "a" * 33,
    "",
]


@pytest.mark.parametrize("value", CODE_LOOKING)
def test_code_looking_entity_id_rejected(value):
    scene = raw(F1A)
    entity(scene, "decor_1")["id"] = value
    _assert_rejected(scene)


# A well-formed id that names nothing is still rejected, as a dangling reference.
@pytest.mark.parametrize("value", CODE_LOOKING + ["loadstring"])
def test_code_looking_reference_rejected(value):
    scene = raw(F1A)
    entity(scene, "gate_1")["opens_on"] = value
    _assert_rejected(scene)


@pytest.mark.parametrize("value", ["game:GetService", "https://x.y", "anything goes", "Pillar"])
def test_decor_palette_is_an_enum_not_free_text(value):
    scene = raw(F1A)
    entity(scene, "decor_1")["palette"] = value
    _assert_rejected(scene, "schema")


@pytest.mark.parametrize("value", ["https://x.y", "game:GetService", "room 0", "Room0"])
def test_scene_id_is_not_free_text(value):
    _assert_rejected(with_value(raw(F1A), ("scene_id",), value), "schema")


def test_trailing_newline_in_id_is_caught_even_though_json_schema_dollar_allows_it():
    scene = raw(F1A)
    entity(scene, "decor_1")["id"] = "decor_1\n"
    errs = _assert_rejected(scene)
    assert any(e.startswith("schema:") or e.startswith("string:") for e in errs)


# --- referential checks ------------------------------------------------------

def test_duplicate_ids_rejected():
    scene = raw(F1A)
    dup = copy.deepcopy(entity(scene, "decor_1"))
    scene["entities"].append(dup)
    errs = _assert_rejected(scene, "ref")
    assert any("duplicate" in e for e in errs)


def test_bounds_is_a_reserved_name_not_an_entity_id():
    scene = raw(F1A)
    entity(scene, "decor_1")["id"] = "bounds"
    _assert_rejected(scene, "ref")


@pytest.mark.parametrize("eid,field", [
    ("spawn", "on"), ("goal", "on"), ("lever_1", "on"), ("lever_1", "controls"),
    ("gate_1", "opens_on"), ("gap_1", "from"), ("gap_1", "to"), ("bridge_1", "spans"),
    ("trig_bridge", "on"), ("trig_goal", "on"),
])
def test_dangling_reference_rejected(eid, field):
    scene = raw(F1A)
    entity(scene, eid)[field] = "missing_thing"
    errs = _assert_rejected(scene, "ref")
    assert any("missing_thing" in e for e in errs)


@pytest.mark.parametrize("eid,field,target", [
    ("bridge_1", "spans", "plat_a"),     # must be a Gap
    ("gate_1", "opens_on", "goal"),      # must be a Lever
    ("lever_1", "controls", "bridge_1"), # must be a Gate
    ("spawn", "on", "gap_1"),            # must be a Platform
    ("gap_1", "to", "bridge_1"),         # must be a Platform
])
def test_reference_to_wrong_type_rejected(eid, field, target):
    scene = raw(F1A)
    entity(scene, eid)[field] = target
    _assert_rejected(scene, "ref")


def test_gap_must_join_two_different_platforms():
    scene = raw(F1A)
    g = entity(scene, "gap_1")
    g["to"] = g["from"]
    _assert_rejected(scene, "ref")


def test_lever_and_gate_must_point_at_each_other():
    scene = raw(F1A)
    scene["entities"].append({"id": "lever_2", "type": "Lever", "on": "ledge_1",
                              "pos": [-12, 0, 24], "controls": "gate_1"})
    _assert_rejected(scene, "ref")


@pytest.mark.parametrize("missing", ["ghost", "trig_ghost"])
def test_protected_id_must_exist(missing):
    scene = raw(F1A)
    scene["protected"].append(missing)
    errs = _assert_rejected(scene, "ref")
    assert any(missing in e for e in errs)


def test_protected_may_name_the_room_bounds():
    scene = raw(F1A)
    assert "bounds" in scene["protected"]
    assert _errors(scene) == []


def test_duplicate_protected_entries_rejected():
    scene = raw(F1A)
    scene["protected"].append(scene["protected"][0])
    _assert_rejected(scene, "schema")


# --- strict JSON parsing -----------------------------------------------------

def test_duplicate_json_keys_rejected():
    text = F1A.read_text(encoding="utf-8")
    bad = text.replace('"version": 1,', '"version": 1, "version": 2,', 1)
    assert bad != text
    with pytest.raises(sc.SceneError):
        sc.parse_scene_text(bad)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_rejected(token):
    text = F1A.read_text(encoding="utf-8")
    bad = text.replace('"version": 1,', f'"version": {token},', 1)
    assert bad != text
    with pytest.raises(sc.SceneError):
        sc.parse_scene_text(bad)


def test_top_level_must_be_an_object():
    with pytest.raises(sc.SceneError):
        sc.parse_scene_text("[1, 2, 3]")
