"""WS-02: every numeric field has hard bounds in grammar/bounds.yaml, enforced on load."""

import pytest
import yaml

from _repo import REPO_ROOT
from _scenes import ENTITY_TYPES, F1A, FIXTURES, bound_key, get_at, leaf_paths, raw, with_value
from worldsmith.grammar import scene as sc

BOUNDS_PATH = REPO_ROOT / "grammar" / "bounds.yaml"


def _numeric_schema_fields(schema):
    """Independent walk of schema.json: dotted names of every numeric field."""
    defs = schema["$defs"]

    def resolve(s):
        while "$ref" in s:
            s = defs[s["$ref"].rsplit("/", 1)[-1]]
        return s

    def is_numeric(s):
        s = resolve(s)
        if s.get("type") in ("number", "integer"):
            return True
        return s.get("type") == "array" and is_numeric(s.get("items", {}))

    out = set()

    def walk(s, prefix):
        s = resolve(s)
        for name, sub in s.get("properties", {}).items():
            if is_numeric(sub):
                out.add(f"{prefix}.{name}")
            elif resolve(sub).get("type") == "object":
                walk(sub, f"{prefix}.{name}")

    walk(schema, "scene")
    for t in ENTITY_TYPES:
        walk(defs[t], t)
    return out


def test_bounds_file_header_is_labelled_estimate():
    head = "\n".join(BOUNDS_PATH.read_text(encoding="utf-8").splitlines()[:6])
    assert "[ESTIMATE] until M0 measures the character" in head
    assert "1 stud = 0.28 m is a units convention, not a character scale" in head


def test_bounds_yaml_parses_and_every_bound_is_a_finite_min_max_pair():
    data = yaml.safe_load(BOUNDS_PATH.read_text(encoding="utf-8"))
    assert data["schema"] == "worldsmith.scene/0.1"
    for key, b in data["bounds"].items():
        assert set(b) == {"min", "max"}, key
        assert isinstance(b["min"], (int, float)) and isinstance(b["max"], (int, float)), key
        assert b["min"] < b["max"], key


def test_every_numeric_schema_field_has_a_bound_and_no_bound_is_stale():
    declared = set(sc.load_bounds())
    in_schema = _numeric_schema_fields(sc.load_schema())
    assert in_schema, "schema has no numeric fields?"
    assert in_schema - declared == set(), "numeric fields without bounds"
    assert declared - in_schema == set(), "bounds for fields the schema does not have"


def test_named_spec_fields_are_bounded():
    b = sc.load_bounds()
    for key in ["Gap.width", "Bridge.length", "Bridge.width", "scene.constraints.min_gap",
                "scene.constraints.min_lever_detour", "Platform.pos", "Platform.size",
                "Lever.pos", "scene.bounds.min", "scene.bounds.max"]:
        assert key in b, key


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_fixture_numbers_are_inside_bounds(path):
    scene = raw(path)
    assert sc.check_bounds(scene) == []


def _numeric_leaves(scene):
    return [p for p in leaf_paths(scene)
            if isinstance(get_at(scene, p), (int, float)) and not isinstance(get_at(scene, p), bool)]


_F1A = raw(F1A)
_NUMERIC = _numeric_leaves(_F1A)


def test_fixture_has_numeric_leaves_to_probe():
    assert len(_NUMERIC) > 20


@pytest.mark.parametrize("side", ["below_min", "above_max"])
@pytest.mark.parametrize("path", _NUMERIC, ids=lambda p: "/".join(map(str, p)))
def test_out_of_bounds_number_rejected(path, side):
    scene = raw(F1A)
    b = sc.load_bounds()[bound_key(scene, path)]
    value = b["min"] - 1 if side == "below_min" else b["max"] + 1
    if path == ("version",):
        value = int(value)
    errs = sc.validate_scene(with_value(scene, path, value))
    assert any(e.startswith("bounds:") for e in errs), errs


@pytest.mark.parametrize("path", [p for p in _NUMERIC if p[0] == "entities"][:8], ids=lambda p: "/".join(map(str, p)))
def test_values_exactly_on_a_bound_are_allowed_by_the_bounds_check(path):
    scene = raw(F1A)
    b = sc.load_bounds()[bound_key(scene, path)]
    for value in (b["min"], b["max"]):
        assert sc.check_bounds(with_value(scene, path, value)) == []


def test_room_bounds_min_must_be_below_max():
    scene = raw(F1A)
    scene["bounds"]["min"][0] = scene["bounds"]["max"][0]
    errs = sc.validate_scene(scene)
    assert any(e.startswith("bounds:") for e in errs), errs


def test_bounds_check_fails_closed_on_an_unbounded_field():
    scene = raw(F1A)
    bounds = dict(sc.load_bounds())
    del bounds["Bridge.length"]
    errs = sc.check_bounds(scene, bounds)
    assert any("Bridge.length" in e and "no bound" in e for e in errs), errs
