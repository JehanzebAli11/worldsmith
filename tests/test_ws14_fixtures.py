"""WS-14: three broken F1 fixtures (F1-B, F1-C, F1-D), each with one hand-authored M1 fix patch.

Design checks on hand-authored fixtures (ADR 0013), CPU only: the validator, the ADR 0008 walk
graph (an approximation, never a proof) and plain geometry of the frozen p0.v3 route. Nothing
here is played; only live runs decide what the route does (ADR 0013 states the prediction and the
live gate that tests it).
"""

import copy
import json

import pytest

from _repo import REPO_ROOT
from _scenes import F1A, F1A_FIX, SCENES_ROOT, entity, raw
from worldsmith.compiler import geometry as geo
from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt
from worldsmith.validator import validate as vd

ROUTE = REPO_ROOT / "config" / "p0_route_F1.json"
ROUTE_SHA = "b9514a4b5bba46e9c77e7554486515e7de6e26ff0cfe1d77a62e26e1b525b36a"
ADR = REPO_ROOT / "docs" / "ADR" / "0013-harder-f1-fixtures.md"

NAMES = ("F1-B", "F1-C", "F1-D")
SCENE = {n: SCENES_ROOT / n / "scene.json" for n in NAMES}
FIX = {n: SCENES_ROOT / n / "fix.json" for n in NAMES}
PATCH = {n: SCENES_ROOT / n / "patches" / "m1_fix.json" for n in NAMES}

LEVER_FIX_POS = [-10, 0, 24]
# The hand fix of each fixture, op for op (ADR 0013).
FIX_OPS = {
    "F1-B": [{"op": "set", "id": "bridge_1", "field": "lateral_offset", "value": 0}],
    "F1-C": [{"op": "move", "id": "lever_1", "pos": LEVER_FIX_POS}],
    "F1-D": [{"op": "move", "id": "lever_1", "pos": LEVER_FIX_POS},
             {"op": "set", "id": "bridge_1", "field": "length", "value": 16}],
}
WALL_LEDGE = {"id": "wall_ledge", "type": "Wall", "pos": [-12, 4, 24], "size": [2, 8, 8]}


def _walk(result):
    return result.graph["walk"]["result"] if result.graph else None


def _ids(seq):
    return [p if isinstance(p, str) else f"{p.parent.name}/{p.name}" for p in seq]


def _expected_scene(name):
    """Each broken fixture rebuilt from F1-A by its declared defect (ADR 0013), nothing else."""
    s = raw(F1A)
    s["scene_id"] = name.lower().replace("-", "_")
    b = entity(s, "bridge_1")
    if name == "F1-B":
        b["length"], b["lateral_offset"] = 16, 10
    else:
        b["length"] = 16 if name == "F1-C" else 6
        entity(s, "lever_1")["pos"] = [-16, 0, 24]
        ids = [e["id"] for e in s["entities"]]
        s["entities"].insert(ids.index("ledge_1") + 1, copy.deepcopy(WALL_LEDGE))
        s["protected"].insert(s["protected"].index("ledge_1") + 1, "wall_ledge")
    return s


# --- the fixtures are exactly the declared defects -------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_scene_is_valid_and_is_f1a_plus_its_declared_defect_only(name):
    s = raw(SCENE[name])
    assert sc.validate_scene(s) == []
    assert s == _expected_scene(name)
    assert s["version"] == 1


@pytest.mark.parametrize("name", NAMES)
def test_locked_f1_rules_are_f1as(name):
    a, s = raw(F1A), raw(SCENE[name])
    for key in ("schema", "family", "units", "up", "bounds", "constraints"):
        assert s[key] == a[key], key
    assert set(a["protected"]) <= set(s["protected"])
    for eid in a["protected"]:
        if eid != "bounds":
            assert entity(s, eid) == entity(a, eid), eid       # gap, platforms, spawn, goal, kill floor, walls, triggers
    assert entity(s, "gap_1")["width"] == 16 >= s["constraints"]["min_gap"]
    assert entity(s, "gate_1") == entity(a, "gate_1")


@pytest.mark.parametrize("name", NAMES)
def test_scene_ids_are_distinct(name):
    others = [raw(F1A)["scene_id"], raw(SCENES_ROOT / "F1-E" / "scene.json")["scene_id"]]
    others += [raw(SCENE[n])["scene_id"] for n in NAMES if n != name]
    assert raw(SCENE[name])["scene_id"] not in others


@pytest.mark.parametrize("path", [*SCENE.values(), *FIX.values()], ids=_ids([*SCENE.values(), *FIX.values()]))
def test_no_solid_overlaps_and_inside_the_room(path):
    s = raw(path)
    assert vd.solid_overlaps(s) == []
    assert vd.outside_room(s) == []


# --- the hand fix: one worldsmith.patch/0.2 patch that the M1 stage accepts -------------------

@pytest.mark.parametrize("name", NAMES)
def test_hand_patch_is_the_declared_m1_patch(name):
    p = pt.load_patch(PATCH[name])
    assert p == {"schema": "worldsmith.patch/0.2", "base_hash": sc.canonical_hash(raw(SCENE[name])),
                 "ops": FIX_OPS[name]}
    assert pt.check_patch(p, stage="M1") == []
    assert pt.check_patch(p, stage="M0")                      # never an M0 patch
    assert sorted(f.name for f in PATCH[name].parent.iterdir()) == ["m1_fix.json"]


@pytest.mark.parametrize("name", NAMES)
def test_hand_patch_reproduces_fix_json(name):
    out = pt.apply_patch(raw(SCENE[name]), pt.load_patch(PATCH[name]), stage="M1")
    fix = raw(FIX[name])
    assert out.scene == fix and fix["version"] == 2           # ADR 0006: the patch bumps the version
    assert out.scene_hash == sc.canonical_hash(fix)


@pytest.mark.parametrize("name", NAMES)
def test_broken_scene_is_walk_disconnected_and_hand_fix_accepted_and_connected(name):
    s = raw(SCENE[name])
    base = vd.validate(s, s, stage="M1")                      # the scene judged as itself: graph only
    assert base.graph["walk"]["result"] == "DISCONNECTED"
    res = vd.validate(s, pt.load_patch(PATCH[name]), stage="M1")
    assert res.ok, res.to_dict()["violations"]
    assert _walk(res) == "CONNECTED"
    assert "GRAPH_DISCONNECTED_WALK" not in {w.code for w in res.warnings}


def test_ticket_op_rules():
    not_length = [n for n in NAMES if {"op": "set", "id": "bridge_1", "field": "length", "value": 16}
                  not in FIX_OPS[n] and all(o.get("field") != "length" for o in FIX_OPS[n])]
    assert len(not_length) >= 2                                 # at least two fixes are not bridge_1.length
    assert any(2 <= len(FIX_OPS[n]) <= 3 for n in NAMES)        # at least one needs 2-3 ops


def test_f1d_needs_both_ops():
    s = raw(SCENE["F1-D"])
    for op in FIX_OPS["F1-D"]:
        one = {"schema": "worldsmith.patch/0.2", "base_hash": sc.canonical_hash(s), "ops": [op]}
        res = vd.validate(s, one, stage="M1")
        assert res.ok and _walk(res) == "DISCONNECTED", op     # each op alone is legal but not enough


@pytest.mark.parametrize("name", ["F1-C", "F1-D"])
def test_lever_rules_hold_after_the_move(name):
    out = pt.apply_patch(raw(SCENE[name]), pt.load_patch(PATCH[name]), stage="M1").scene
    m = vd.measures(out)
    assert m["lever_detour"] == {"lever_1": 10} and m["min_lever_detour"] == 10   # exactly the F1 minimum
    assert entity(out, "lever_1")["on"] == "ledge_1"


def test_f1b_fix_is_the_f1a_fix_room():
    """F1-B's fixed room is F1-A's M0 fixed room (bridge 16 x 6 on the centreline), which passed
    3/3 in every recorded live episode; only scene_id and version differ."""
    fix = raw(FIX["F1-B"])
    a = raw(F1A_FIX)
    assert {**fix, "scene_id": a["scene_id"], "version": 1} == a


def test_f1c_and_f1d_fixes_are_the_same_room():
    c, d = raw(FIX["F1-C"]), raw(FIX["F1-D"])
    assert {**d, "scene_id": c["scene_id"]} == c


# --- plain geometry behind the route reasoning (ADR 0013; not physics, not a result) ----------

@pytest.fixture(scope="module")
def route():
    import hashlib
    assert hashlib.sha256(ROUTE.read_bytes()).hexdigest() == ROUTE_SHA       # the frozen p0.v3 route
    wps = {w["name"]: w["pos"] for w in json.loads(ROUTE.read_text())["waypoints"]}
    assert len(wps) == 9
    return wps


def _part(scene, eid, name=None):
    by_id = {e["id"]: e for e in scene["entities"]}
    parts = geo.entity_parts(by_id[eid], by_id)
    return next(p for p in parts if name is None or p["name"] == name)


def _x(box):
    return box["pos"][0] - box["size"][0] / 2, box["pos"][0] + box["size"][0] / 2


def test_f1b_route_line_crosses_the_gap_where_no_bridge_is(route):
    """P0 walks x = 0 from past_gate (z 35.5) through bridge (z 40) to landing (z 55): in F1-B no
    built part lies under that line between the corridor (z 36) and plat_b (z 52); in the fix the
    bridge covers it (+-2 studs either side of the line, the measured half-width)."""
    assert route["past_gate"][0] == route["bridge"][0] == route["landing"][0] == 0
    lo, hi = _x(_part(raw(SCENE["F1-B"]), "bridge_1"))
    assert lo > 2                                                       # 7..13: wholly beside the line
    lo, hi = _x(_part(raw(FIX["F1-B"]), "bridge_1"))
    assert lo <= -2 and hi >= 2


@pytest.mark.parametrize("name", ["F1-C", "F1-D"])
def test_wall_stands_between_ledge_entry_and_the_broken_lever(name, route):
    s = raw(SCENE[name])
    wall = _part(s, "wall_ledge")
    wlo, whi = _x(wall)
    zone = _part(s, "lever_1", "zone")
    zlo, zhi = _x(zone)
    entry, target = route["ledge_entry"], route["lever_zone"]
    assert entry[2] == target[2] == 24                                    # the segment runs along z = 24
    assert target[0] < whi < entry[0]                                     # the wall cuts the segment
    assert zhi <= wlo and whi - zhi >= 2                                  # zone at least 2 studs behind the near face
    ledge = _part(s, "ledge_1")
    assert wall["size"][2] >= ledge["size"][2] and wall["pos"][2] == ledge["pos"][2]  # wall spans the ledge
    assert wall["pos"][1] - wall["size"][1] / 2 == 0 and wall["size"][1] >= 6        # from the walking surface up


@pytest.mark.parametrize("name", ["F1-C", "F1-D"])
def test_fixed_lever_zone_lies_on_the_route_before_the_wall(name, route):
    out = raw(FIX[name])
    zlo, zhi = _x(_part(out, "lever_1", "zone"))
    wlo, whi = _x(_part(out, "wall_ledge"))
    entry = route["ledge_entry"]
    assert zhi > whi                                                     # the zone reaches the near side of the wall
    assert 0 < entry[0] - zhi <= 1                                       # P0 enters it within 1 stud of ledge_entry
    zone = _part(out, "lever_1", "zone")
    assert zone["pos"][2] - zone["size"][2] / 2 <= 24 <= zone["pos"][2] + zone["size"][2] / 2


# --- disclosure ------------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_readme_discloses_the_defect(name):
    text = (SCENES_ROOT / name / "README.md").read_text(encoding="utf-8").lower()
    for phrase in ("not a result", "known defect", "by construction", "adr 0013", "m1_fix.json"):
        assert phrase in text, phrase


def test_adr_0013_records_each_defect_fix_and_expected_tag():
    text = ADR.read_text(encoding="utf-8")
    for name in NAMES:
        assert name in text
    for phrase in ("lateral_offset", "move lever_1", "wall_ledge", "FELL", "STUCK", "LEVER_ACTIVATED",
                   "p0.v3", "approximation", "never a proof", "Dropped or redesigned"):
        assert phrase in text, phrase
