"""WS-06: the validator (SPEC section 7 items 1-4) and the SPEC section 16 abuse suite.

Every case is a file: `tests/abuse/reject/*.json` must be rejected with (at least) the expected
codes, `tests/abuse/accept/*.json` must pass. A case gives either a concrete patch (`patch`,
`patch_text` or `patch_file`) or a whole patched scene (`scene_file`, or `scene_edits` applied to
the base), the base fixture and the stage. Codes are documented in docs/VALIDATOR.md; the
geometric rules are in docs/ADR/0007-validator-and-m1-stage.md.
"""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _repo import REPO_ROOT
from _scenes import F1A, F1A_FIX, F1E, entity, raw
from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt
from worldsmith.validator import validate as vd

ABUSE = REPO_ROOT / "tests" / "abuse"
REJECT = sorted((ABUSE / "reject").glob("*.json"))
ACCEPT = sorted((ABUSE / "accept").glob("*.json"))
BASES = {"F1-A": F1A, "F1-E": F1E}
VALIDATOR_MD = REPO_ROOT / "docs" / "VALIDATOR.md"
ADR_0007 = REPO_ROOT / "docs" / "ADR" / "0007-validator-and-m1-stage.md"

# SPEC section 16 "Abuse suite" bullets, plus the WS-06 ticket's extra cases.
SPEC16 = [
    "move_goal_or_spawn", "change_gap_or_platforms", "add_ramp_floor_teleport",
    "delete_or_open_gate", "player_physics", "code_payload", "out_of_bounds", "too_many_ops",
    "remove_protected", "non_bridge_op_m0", "success_without_bridge_crossed",
]
TICKET_EXTRAS = [
    "goal_behind_spawn", "shrink_gap", "platform_or_ramp_in_gap", "wall_blocks_corridor",
    "gate_opens_elsewhere", "lever_in_corridor", "decor_as_floor", "bridge_out_of_room",
    "bridge_overlaps_platform", "noop",
]
# Codes the ticket names; all must exist.
TICKET_CODES = [
    "PROTECTED_CHANGED", "GAP_CHANGED", "PHYSICS_CHANGED", "NEW_FLOOR", "TELEPORT",
    "GATE_LEVER_UNLINKED", "DETOUR_TOO_SHORT", "PENETRATION", "DANGLING_REF", "OUT_OF_BOUNDS",
    "NOOP_PATCH", "STAGE_OP_FORBIDDEN", "TOO_MANY_OPS", "CODE_PAYLOAD",
]


def _case(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _edit_scene(base, edits):
    scene = copy.deepcopy(base)
    for e in edits:
        path, value = e["path"], e["value"]
        if path[0] == "entity":
            node = entity(scene, path[1])
            rest = path[2:]
        else:
            node, rest = scene, path
        for p in rest[:-1]:
            node = node[p]
        node[rest[-1]] = copy.deepcopy(value)
    return scene


def _candidate(case, base):
    if "patch" in case:
        return copy.deepcopy(case["patch"])
    if "patch_text" in case:
        return case["patch_text"]
    if "patch_file" in case:
        return raw(REPO_ROOT / case["patch_file"])
    if "scene_file" in case:
        return raw(REPO_ROOT / case["scene_file"])
    return _edit_scene(base, case["scene_edits"])


def _run(case):
    base = raw(BASES[case["base"]])
    cand = _candidate(case, base)
    return base, cand, vd.validate(base, cand, stage=case["stage"])


# --- the abuse suite ------------------------------------------------------------------

@pytest.mark.parametrize("path", REJECT, ids=lambda p: p.stem)
def test_abuse_case_is_rejected_with_expected_codes(path):
    case = _case(path)
    _, _, res = _run(case)
    assert res.ok is False
    assert res.violations, "a rejection must list its violations"
    expected = set(case["expect"])
    assert expected <= res.codes, (sorted(res.codes), res.to_dict())
    if case.get("exact"):
        assert res.codes == expected, res.to_dict()


@pytest.mark.parametrize("path", ACCEPT, ids=lambda p: p.stem)
def test_legit_case_passes(path):
    case = _case(path)
    _, _, res = _run(case)
    assert res.ok is True, res.to_dict()
    assert res.violations == ()
    assert sorted({w.code for w in res.warnings}) == sorted(case["warnings"]), res.to_dict()


def test_abuse_suite_covers_every_spec16_bullet_and_ticket_case():
    covered = {k for p in REJECT for k in _case(p)["spec16"]}
    missing = [k for k in SPEC16 + TICKET_EXTRAS if k not in covered]
    assert not missing, missing
    assert len(REJECT) >= len(SPEC16) + len(TICKET_EXTRAS)


def test_case_files_are_well_formed():
    names = set()
    for path in REJECT + ACCEPT:
        case = _case(path)
        assert case["case"] == path.stem
        assert case["case"] not in names
        names.add(case["case"])
        assert case["base"] in BASES and case["stage"] in pt.STAGES
        assert case["why"]
        kinds = [k for k in ("patch", "patch_text", "patch_file", "scene_file", "scene_edits") if k in case]
        assert len(kinds) == 1, (path.name, kinds)
        if path in REJECT:
            assert case["expect"] and set(case["expect"]) <= set(vd.CODES), path.name
        if "patch" in case and case["base"] == "F1-A" and case["case"] != "stale_base":
            assert case["patch"]["base_hash"] == sc.canonical_hash(raw(F1A))


def test_both_input_kinds_are_exercised():
    kinds = {k for p in REJECT for k in ("patch", "patch_text", "scene_edits") if k in _case(p)}
    assert kinds == {"patch", "patch_text", "scene_edits"}
    stages = {_case(p)["stage"] for p in REJECT}
    assert stages == {"M0", "M1"}


# --- agreement with the patch engine (WS-05) -------------------------------------------

@pytest.mark.parametrize("path", REJECT + ACCEPT, ids=lambda p: p.stem)
def test_validator_is_at_least_as_strict_as_apply_patch(path):
    """Whatever apply_patch rejects, the validator rejects; whatever the validator accepts,
    apply_patch applies, to the same scene."""
    case = _case(path)
    base, cand, res = _run(case)
    if not isinstance(cand, dict) or cand.get("schema", "").startswith("worldsmith.scene/"):
        pytest.skip("not a patch object")
    try:
        applied = pt.apply_patch(base, cand, stage=case["stage"])
    except pt.PatchError:
        assert res.ok is False
        return
    if res.ok:
        assert res.scene is not None
        assert sc.canonical_hash(res.scene) == applied.scene_hash


def test_m0_fix_through_validator_gives_the_ws05_hash():
    base = raw(F1A)
    patch = raw(REPO_ROOT / "scenes" / "F1-A" / "patches" / "m0_fix.json")
    res = vd.validate(base, patch, stage="M0")
    assert res.ok
    assert sc.canonical_hash(res.scene) == \
        "297b23c884e196a80f9a19aa32d006768e1ace84cfbd4bd1e932981fdabf0535"


# --- output shape and codes -------------------------------------------------------------

def test_output_shape_matches_spec_section_7():
    res = vd.validate(raw(F1A), raw(REPO_ROOT / "scenes" / "F1-A" / "patches" / "m0_fix.json"),
                      stage="M0")
    d = res.to_dict()
    assert set(d) == {"ok", "violations", "warnings", "graph"}
    assert d["ok"] is True and d["violations"] == [] and d["graph"] is None  # graph: WS-07
    json.dumps(d)
    bad = vd.validate(raw(F1A), _case(ABUSE / "reject" / "shrink_gap_m0.json")["patch"], stage="M0")
    d = bad.to_dict()
    assert d["ok"] is False
    for v in d["violations"]:
        assert set(v) == {"code", "message", "ref"}
        assert v["code"] in vd.CODES and v["message"]


def test_codes_are_stable_identifiers():
    for code, meaning in vd.CODES.items():
        assert re.fullmatch(r"[A-Z][A-Z0-9_]+", code), code
        assert meaning and meaning == meaning.strip()
    for code in vd.WARNING_CODES:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]+", code) and code not in vd.CODES
    for code in TICKET_CODES:
        assert code in vd.CODES, code


def test_every_code_is_documented_in_validator_md():
    text = VALIDATOR_MD.read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", text, flags=re.M))
    assert documented == set(vd.CODES) | set(vd.WARNING_CODES)


def test_every_code_is_produced_by_some_case():
    produced = set()
    for path in REJECT:
        produced |= _run(_case(path))[2].codes
    assert produced == set(vd.CODES), sorted(set(vd.CODES) - produced)


def test_violations_are_sorted_and_unique():
    case = _case(ABUSE / "reject" / "remove_spawn.json")
    res = _run(case)[2]
    keys = [(v.code, v.ref or "", v.message) for v in res.violations]
    assert keys == sorted(set(keys))


# --- determinism and purity ----------------------------------------------------------------

def test_validation_is_pure():
    base = raw(F1A)
    patch = _case(ABUSE / "reject" / "four_ops.json")["patch"]
    b0, p0 = copy.deepcopy(base), copy.deepcopy(patch)
    vd.validate(base, patch, stage="M1")
    assert base == b0 and patch == p0
    scene = _edit_scene(base, [{"path": ["entity", "bridge_1", "length"], "value": 16}])
    s0 = copy.deepcopy(scene)
    vd.validate(base, scene, stage="M0")
    assert scene == s0 and base == b0


def test_entity_order_does_not_change_the_result():
    case = _case(ABUSE / "reject" / "remove_gate.json")
    base, cand, res = _run(case)
    shuffled = copy.deepcopy(base)
    shuffled["entities"].reverse()
    patch = copy.deepcopy(cand)
    assert sc.canonical_hash(shuffled) == patch["base_hash"]
    assert vd.validate(shuffled, patch, stage="M1").to_dict() == res.to_dict()


def test_validation_is_deterministic_across_processes():
    code = (
        "import json,sys\n"
        "from worldsmith.validator import validate as vd\n"
        "base=json.load(open(sys.argv[1]))\n"
        "out=[]\n"
        "for p in sys.argv[2:]:\n"
        "    c=json.load(open(p))\n"
        "    out.append(vd.validate(base,c['patch'],stage=c['stage']).to_dict())\n"
        "print(json.dumps(out,sort_keys=True))\n")
    files = [str(p) for p in REJECT if "patch" in _case(p) and _case(p)["base"] == "F1-A"][:12]
    outs = set()
    for seed in ("0", "1", "4242"):
        r = subprocess.run([sys.executable, "-c", code, str(F1A), *files], capture_output=True,
                           text=True, env={"PYTHONHASHSEED": seed, "PATH": ""}, check=True)
        outs.add(r.stdout)
    assert len(outs) == 1


# --- caller errors are not verdicts ------------------------------------------------------------

def test_unknown_stage_is_a_programming_error():
    for stage in ["M9", "m1", "", None]:
        with pytest.raises(ValueError):
            vd.validate(raw(F1A), raw(F1A_FIX), stage=stage)


def test_invalid_base_is_a_programming_error():
    base = raw(F1A)
    base["note"] = "x"
    with pytest.raises(ValueError):
        vd.validate(base, raw(F1A_FIX), stage="M0")


def test_stage_is_required():
    with pytest.raises(TypeError):
        vd.validate(raw(F1A), raw(F1A_FIX))  # noqa


# --- the base fixtures and the geometry the rules use --------------------------------------------

@pytest.mark.parametrize("path", [F1A, F1A_FIX, F1E], ids=lambda p: p.parent.name + "/" + p.stem)
def test_fixtures_have_no_solid_overlaps_and_stay_in_the_room(path):
    scene = raw(path)
    assert vd.solid_overlaps(scene) == []
    assert vd.outside_room(scene) == []


def test_fixture_measures_are_what_adr_0007_states():
    m = vd.measures(raw(F1A))
    assert m["walk_top"] == 0
    assert m["gap_faces"] == (36, 52)
    assert m["lever_detour"] == {"lever_1": 15}
    assert m["min_gap"] == 12 and m["min_lever_detour"] == 10
    assert m["walkway_strip"] == {"lo": [-5, 0, 0], "hi": [5, 6, 68]}


def test_rule_constants():
    assert vd.EPS == 0.001
    assert vd.JOIN_MAX == 1.0
    assert vd.CLEARANCE == 6.0
    assert vd.FLOOR_BAND == 1.0
    assert vd.FLOOR_MIN_SPAN == 1.0


def test_unchanged_base_is_a_noop_not_a_pass():
    res = vd.validate(raw(F1A), raw(F1A), stage="M0")
    assert res.ok is False and res.codes == {"NOOP_PATCH"}


def test_hand_authored_fix_scene_passes_in_m0():
    assert vd.validate(raw(F1A), raw(F1A_FIX), stage="M0").ok


# --- M1 stage (ADR 0007) in the patch engine ---------------------------------------------------

def test_m0_stage_is_unchanged():
    rules = pt.STAGES["M0"]
    assert [(r.op, r.type, r.id, r.field) for r in rules] == [("set", "Bridge", "bridge_1", "length")]
    assert pt.STAGE_SCHEMAS["M0"] == ("worldsmith.patch/0.1",)


def test_m1_stage_is_exactly_the_adr_0007_list():
    got = sorted((r.op, r.type, r.id, r.field) for r in pt.STAGES["M1"])
    assert got == sorted([
        ("set", "Bridge", "bridge_1", "length"),
        ("set", "Bridge", "bridge_1", "width"),
        ("set", "Bridge", "bridge_1", "lateral_offset"),
        ("move", "Lever", "lever_1", "pos"),
        ("move", "Gate", "gate_1", "pos"),
        ("add", "Wall", None, None),
        ("add", "Decor", None, None),
    ])
    assert pt.STAGE_SCHEMAS["M1"] == ("worldsmith.patch/0.1", "worldsmith.patch/0.2")
    assert set(pt.STAGES) == {"M0", "M1"}


def test_patch_schema_0_2_is_valid_and_closed_except_the_added_entity():
    import jsonschema
    schema = pt.load_patch_schema("worldsmith.patch/0.2")
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["title"] == "worldsmith.patch/0.2"
    assert schema["properties"]["ops"]["maxItems"] == 3
    for name in ("set_op", "move_op", "add_op", "remove_op"):
        assert schema["$defs"][name]["additionalProperties"] is False
    assert schema["$defs"]["op"]["unevaluatedProperties"] is False
    # The 0.1 schema is untouched.
    assert pt.load_patch_schema() == pt.load_patch_schema("worldsmith.patch/0.1")
    assert pt.load_patch_schema()["title"] == "worldsmith.patch/0.1"


def test_patch_schema_0_2_ships_as_package_data():
    import importlib.resources
    assert importlib.resources.files("worldsmith.grammar").joinpath("patch_schema_0.2.json").is_file()
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"patch_schema_0.2.json"' in text.split("[tool.setuptools.package-data]", 1)[1]


def test_m0_rejects_patch_0_2_at_the_schema_layer_as_before():
    p = _case(ABUSE / "reject" / "m0_move_lever.json")["patch"]
    with pytest.raises(pt.PatchError) as exc:
        pt.apply_patch(raw(F1A), p, stage="M0")
    assert any(e.startswith("schema:") for e in exc.value.errors)


@pytest.mark.parametrize("name", ["lever_within_ledge", "gate_nudged", "decor_off_route",
                                  "wall_off_route", "three_ops"])
def test_m1_apply_patch_applies_move_and_add(name):
    case = _case(ABUSE / "accept" / f"{name}.json")
    base = raw(F1A)
    applied = pt.apply_patch(base, case["patch"], stage="M1")
    assert applied.version == 2
    assert sc.validate_scene(applied.scene) == []
    for op in case["patch"]["ops"]:
        if op["op"] == "move":
            assert entity(applied.scene, op["id"])["pos"] == op["pos"]
        if op["op"] == "add":
            assert entity(applied.scene, op["entity"]["id"]) == op["entity"]


def test_m1_apply_rejects_add_of_an_existing_id():
    p = {"schema": "worldsmith.patch/0.2", "base_hash": sc.canonical_hash(raw(F1A)),
         "ops": [{"op": "add", "entity": {"id": "decor_1", "type": "Decor", "palette": "crate",
                                          "pos": [-6.5, 1, 2], "size": [2, 2, 2]}}]}
    with pytest.raises(pt.PatchError) as exc:
        pt.apply_patch(raw(F1A), p, stage="M1")
    assert any(e.startswith("apply:") for e in exc.value.errors)
    assert vd.validate(raw(F1A), p, stage="M1").codes >= {"BAD_TARGET"}


def test_m1_apply_rejects_a_string_value_for_a_number_field():
    p = {"schema": "worldsmith.patch/0.2", "base_hash": sc.canonical_hash(raw(F1A)),
         "ops": [{"op": "set", "id": "bridge_1", "field": "length", "value": "plat_b"}]}
    with pytest.raises(pt.PatchError):
        pt.apply_patch(raw(F1A), p, stage="M1")
    assert not vd.validate(raw(F1A), p, stage="M1").ok


def test_m1_add_of_a_non_palette_type_is_forbidden():
    p = {"schema": "worldsmith.patch/0.2", "base_hash": sc.canonical_hash(raw(F1A)),
         "ops": [{"op": "add", "entity": {"id": "lever_2", "type": "Lever", "on": "ledge_1",
                                          "pos": [-12, 0, 22], "controls": "gate_1"}}]}
    errs = pt.check_patch(p, stage="M1")
    assert any(e.startswith("stage:") for e in errs)


# --- docs ------------------------------------------------------------------------------------

def test_adr_0007_records_the_rules():
    text = ADR_0007.read_text(encoding="utf-8").lower()
    for needle in ["m1", "worldsmith.patch/0.2", "detour", "join", "floor-like", "walkway",
                   "gap column", "clearance", "1 stud", "6 studs", "0.001", "decor",
                   "support", "no-op", "touched"]:
        assert needle in text, needle


def test_validator_md_names_the_api_and_output():
    text = VALIDATOR_MD.read_text(encoding="utf-8")
    for needle in ["validate(", "ok", "violations", "warnings", "graph", "stable", "ADR 0007"]:
        assert needle in text, needle
