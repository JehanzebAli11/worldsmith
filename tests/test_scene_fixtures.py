"""WS-02: the F1 fixtures are complete per SPEC section 5, and F1-A/fix differ only in
bridge_1.length (SPEC section 6).

These are design checks on hand-authored fixtures, not the WS-06 validator, and not results.
"""

import pytest

from _scenes import F1A, F1A_FIX, F1E, FIXTURES, entity, raw
from worldsmith.grammar import scene as sc

_IDS = lambda p: f"{p.parent.name}/{p.name}"  # noqa: E731


def _diff(a, b, path=()):
    """Paths whose values differ between two JSON trees (structure must match)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(path + (k,))
            else:
                out.extend(_diff(a[k], b[k], path + (k,)))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [path]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(_diff(x, y, path + (i,)))
        return out
    return [] if a == b and type(a) is type(b) else [path]


def test_fix_differs_from_f1a_only_in_bridge_length():
    a, fix = raw(F1A), raw(F1A_FIX)
    diffs = _diff(a, fix)
    assert len(diffs) == 1, diffs
    (path,) = diffs
    assert path[0] == "entities"
    assert a["entities"][path[1]]["id"] == "bridge_1"
    assert path[2:] == ("length",)


def test_fix_canonical_diff_is_also_only_bridge_length():
    a = sc.normalize_scene(raw(F1A))
    fix = sc.normalize_scene(raw(F1A_FIX))
    diffs = _diff(a, fix)
    assert len(diffs) == 1
    assert a["entities"][diffs[0][1]]["id"] == "bridge_1" and diffs[0][2:] == ("length",)
    assert sc.canonical_hash(a) != sc.canonical_hash(fix)


REQUIRED_IDS = {
    "spawn": "Spawn", "plat_a": "Platform", "corridor": "Platform", "ledge_1": "Platform",
    "lever_1": "Lever", "gate_1": "Gate", "gap_1": "Gap", "bridge_1": "Bridge",
    "plat_b": "Platform", "goal": "Goal", "kill_floor": "KillFloor",
    "trig_bridge": "Trigger", "trig_goal": "Trigger",
}


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_fixture_has_every_spec_section_5_part(path):
    scene = raw(path)
    types = {e["id"]: e["type"] for e in scene["entities"]}
    for eid, t in REQUIRED_IDS.items():
        assert types.get(eid) == t, eid
    assert "bounds" in scene


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_fixture_wiring(path):
    s = raw(path)
    assert entity(s, "spawn")["on"] == "plat_a"
    assert entity(s, "goal")["on"] == "plat_b"
    assert entity(s, "lever_1")["on"] == "ledge_1"
    assert entity(s, "lever_1")["controls"] == "gate_1"
    assert entity(s, "gate_1")["opens_on"] == "lever_1"
    assert entity(s, "bridge_1")["spans"] == "gap_1"
    tb, tg = entity(s, "trig_bridge"), entity(s, "trig_goal")
    assert (tb["event"], tb["on"], tb["region"]) == ("BRIDGE_CROSSED", "bridge_1", "far_half")
    assert (tg["event"], tg["on"]) == ("GOAL_TOUCHED", "goal")
    assert s["constraints"]["required_events"] == ["BRIDGE_CROSSED", "GOAL_TOUCHED"]
    assert s["constraints"]["player_physics"] == "locked"


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_protected_set(path):
    s = raw(path)
    must = {"spawn", "goal", "kill_floor", "bounds", "plat_a", "plat_b", "gap_1",
            "corridor", "ledge_1", "trig_bridge", "trig_goal"}
    assert must <= set(s["protected"])
    for movable in ("bridge_1", "lever_1", "gate_1"):
        assert movable not in s["protected"]


def _z_extent(e):
    return e["pos"][2] - e["size"][2] / 2, e["pos"][2] + e["size"][2] / 2


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_gap_width_matches_platform_edges(path):
    s = raw(path)
    gap = entity(s, "gap_1")
    assert gap["axis"] == "+Z"
    near = _z_extent(entity(s, gap["from"]))[1]
    far = _z_extent(entity(s, gap["to"]))[0]
    assert far - near == pytest.approx(gap["width"])
    assert gap["width"] >= s["constraints"]["min_gap"]


def test_f1a_bridge_is_too_short_for_its_gap():
    s = raw(F1A)
    assert entity(s, "bridge_1")["length"] < entity(s, "gap_1")["width"]


@pytest.mark.parametrize("path", [F1A_FIX, F1E], ids=_IDS)
def test_control_and_fix_bridges_reach_the_far_platform(path):
    s = raw(path)
    assert entity(s, "bridge_1")["length"] >= entity(s, "gap_1")["width"]


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_lever_sits_on_its_ledge_off_the_corridor(path):
    s = raw(path)
    lever, ledge, corridor = entity(s, "lever_1"), entity(s, "ledge_1"), entity(s, "corridor")
    for axis in (0, 2):
        lo = ledge["pos"][axis] - ledge["size"][axis] / 2
        hi = ledge["pos"][axis] + ledge["size"][axis] / 2
        assert lo <= lever["pos"][axis] <= hi
    # Lateral offset of the lever from the corridor centreline. This is only a fixture
    # design check; WS-06 owns the real detour rule.
    lateral = abs(lever["pos"][0] - corridor["pos"][0])
    assert lateral >= s["constraints"]["min_lever_detour"]
    corridor_half = corridor["size"][0] / 2
    assert lateral > corridor_half


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_kill_floor_is_below_the_gap(path):
    s = raw(path)
    kf, gap = entity(s, "kill_floor"), entity(s, "gap_1")
    near = _z_extent(entity(s, gap["from"]))[1]
    far = _z_extent(entity(s, gap["to"]))[0]
    kz = _z_extent(kf)
    assert kz[0] <= near and far <= kz[1]
    assert kf["pos"][1] + kf["size"][1] / 2 < entity(s, "plat_a")["pos"][1]


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_entity_centres_are_inside_room_bounds(path):
    s = raw(path)
    lo, hi = s["bounds"]["min"], s["bounds"]["max"]
    for e in s["entities"]:
        if "pos" in e:
            assert all(lo[i] <= e["pos"][i] <= hi[i] for i in range(3)), e["id"]


def test_f1e_is_a_distinct_scene():
    assert raw(F1E)["scene_id"] != raw(F1A)["scene_id"]
    assert sc.canonical_hash(raw(F1E)) != sc.canonical_hash(raw(F1A_FIX))


@pytest.mark.parametrize("folder", ["F1-A", "F1-E"])
def test_each_fixture_folder_discloses_what_it_is(folder):
    from _scenes import SCENES_ROOT
    readme = (SCENES_ROOT / folder / "README.md").read_text(encoding="utf-8").lower()
    assert "not a result" in readme
    if folder == "F1-A":
        assert "known defect" in readme and "by construction" in readme
