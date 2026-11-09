"""WS-07: the navigation approximation (SPEC section 7 item 5), ADR 0008.

The walk graph is an APPROXIMATION, never a physical proof: actual runs decide (SPEC section 7).
These tests pin the model's behaviour on the fixtures and on synthetic rooms; they say nothing
about what Roblox physics would do. Jump mode uses a reach derived from the measured character
(M0 measured one jump, n = 1, and nothing else about jumping), so for F1-A its verdict is only
checked for determinism and its "derived" label, never asserted as the truth.
"""

import copy
import json
import math
import os
import subprocess
import sys

import pytest

from _repo import REPO_ROOT
from _scenes import F1A, F1A_FIX, F1E, entity, raw
from worldsmith.grammar import scene as sc
from worldsmith.validator import nav
from worldsmith.validator import validate as vd

MEASURE = REPO_ROOT / "bundles" / "m0" / "character" / "measure.json"
ADR_0008 = REPO_ROOT / "docs" / "ADR" / "0008-navigation-approximation.md"
VALIDATOR_MD = REPO_ROOT / "docs" / "VALIDATOR.md"
M0_FIX_PATCH = REPO_ROOT / "scenes" / "F1-A" / "patches" / "m0_fix.json"
RESULTS = {"CONNECTED", "DISCONNECTED"}


def _ceil2(x):
    return math.ceil(round(x * 100, 6)) / 100


def _with_bridge(scene, length):
    s = copy.deepcopy(scene)
    entity(s, "bridge_1")["length"] = length
    return s


def _with_entity(scene, ent):
    s = copy.deepcopy(scene)
    s["entities"].append(ent)
    return s


# A Wall across the corridor before the ledge opening (z 16-17): nothing gets past it.
CORRIDOR_WALL = {"id": "wall_x", "type": "Wall", "pos": [0, 4, 16.5], "size": [8, 8, 1]}
# A Wall filling the ledge opening (x -5..-4, z 20..28): lever_1 cannot be reached.
LEDGE_SEAL = {"id": "wall_x", "type": "Wall", "pos": [-4.5, 4, 24], "size": [1, 8, 8]}


# --- the config block ---------------------------------------------------------------------

def test_every_parameter_is_declared_with_its_source():
    assert set(nav.PARAMS) >= {"cell", "body_height", "body_radius", "step_up", "walk_speed",
                               "jump_height", "gravity", "jump_rise", "jump_reach_flat"}
    for name, p in nav.PARAMS.items():
        assert set(p) == {"value", "unit", "basis", "source"}, name
        assert isinstance(p["value"], (int, float)) and p["value"] > 0, name
        assert p["basis"] in {"measured", "derived", "assumed", "design"}, name
        assert p["unit"] and p["source"] and p["source"] == p["source"].strip(), name


def test_measured_parameters_match_the_m0_measurement_file():
    m = json.loads(MEASURE.read_text(encoding="utf-8"))
    P = {k: v["value"] for k, v in nav.PARAMS.items()}
    assert P["body_height"] == _ceil2(m["bbox"][1])                       # 5.19
    assert P["body_radius"] == _ceil2(math.hypot(m["bbox"][0], m["bbox"][2]) / 2)  # 2.09
    assert P["walk_speed"] == pytest.approx(m["walk_speed"], abs=1e-6)
    assert P["jump_height"] == pytest.approx(m["jump_height"], abs=1e-6)
    assert P["gravity"] == pytest.approx(m["gravity"], abs=1e-4)
    for name in ("body_height", "walk_speed", "jump_height", "gravity"):
        assert nav.PARAMS[name]["basis"] == "measured", name
        assert "measure.json" in nav.PARAMS[name]["source"], name


def test_jump_parameters_are_derived_not_measured():
    P = {k: v["value"] for k, v in nav.PARAMS.items()}
    for name in ("body_radius", "jump_rise", "jump_reach_flat"):
        assert nav.PARAMS[name]["basis"] == "derived", name
    assert nav.PARAMS["step_up"]["basis"] == "assumed"   # M0 never measured a step
    assert P["jump_rise"] == P["jump_height"]
    flat = P["walk_speed"] * 2 * math.sqrt(2 * P["jump_height"] / P["gravity"])
    assert P["jump_reach_flat"] == pytest.approx(flat, abs=1e-3)
    assert nav.reach(0.0) == pytest.approx(flat, abs=1e-9)
    assert nav.reach(P["jump_rise"]) < nav.reach(0.0) < nav.reach(-5.0)
    assert nav.reach(P["jump_rise"] + 0.01) is None   # above the rise: no jump
    # Consistency with the one measured jump (n = 1): documented, not a calibration.
    m = json.loads(MEASURE.read_text(encoding="utf-8"))
    assert abs(flat - m["jump"]["horizontal"]) / m["jump"]["horizontal"] < 0.02


# --- fixtures ----------------------------------------------------------------------------

def test_f1a_walk_is_disconnected_by_the_10_stud_hole():
    g = nav.navigate(raw(F1A), mode="walk")
    assert g["result"] == "DISCONNECTED"
    assert g["path"] is None
    assert g["gates_opened"] == ["gate_1"]          # the lever is reachable; the hole is not
    assert g["closest"]["on"] == "bridge_1"         # stops on the 6-stud bridge (ends z 42)
    assert 41 < g["closest"]["pos"][2] < 42
    assert "plat_b" not in g["reached_on"] and "kill_floor" not in g["reached_on"]


@pytest.mark.parametrize("path", [F1A_FIX, F1E], ids=["F1-A-fix", "F1-E"])
def test_fix_and_control_walk_are_connected(path):
    scene = raw(path)
    g = nav.navigate(scene, mode="walk")
    assert g["result"] == "CONNECTED", g
    assert g["gates_opened"] == ["gate_1"]
    p = g["path"]
    assert p[0]["via"] == "start" and p[0]["on"] == "spawn"
    assert all(n["via"] in ("walk", "drop") for n in p[1:])
    on = [n["on"] for n in p]
    assert "bridge_1" in on and "kill_floor" not in on
    assert on.index("corridor") < on.index("bridge_1") < len(on) - 1
    _assert_steps_are_neighbours(p)


def test_path_ends_touching_the_goal_trigger():
    g = nav.navigate(raw(F1A_FIX), mode="walk")
    end = g["path"][-1]["pos"]
    x0, x1, z0, z1 = -2, 2, 58, 62               # goal pad footprint on plat_b (geometry)
    dx = max(x0 - end[0], 0, end[0] - x1)
    dz = max(z0 - end[2], 0, end[2] - z1)
    assert math.hypot(dx, dz) < nav.PARAMS["body_radius"]["value"]


def _assert_steps_are_neighbours(path):
    cell = nav.PARAMS["cell"]["value"]
    for a, b in zip(path, path[1:]):
        d = math.hypot(b["pos"][0] - a["pos"][0], b["pos"][2] - a["pos"][2])
        if b["via"] in ("walk", "drop"):
            assert d == pytest.approx(cell), (a, b)
            if b["via"] == "walk":
                assert b["pos"][1] - a["pos"][1] <= nav.PARAMS["step_up"]["value"] + 1e-9
        else:
            assert b["via"] == "jump"
            assert d <= nav.reach(b["pos"][1] - a["pos"][1]) + 1e-9


def test_f1a_jump_is_reported_deterministic_and_labelled_derived():
    a = nav.navigate(raw(F1A), mode="jump")
    b = nav.navigate(raw(F1A), mode="jump")
    assert a == b
    assert a["result"] in RESULTS          # the value itself is NOT asserted: M0 measured one jump
    assert a["basis"] == "derived"
    assert nav.navigate(raw(F1A), mode="walk")["basis"] == "approximation"


def test_jump_mode_never_loses_what_walk_mode_reaches():
    for path in (F1A, F1A_FIX, F1E):
        w = nav.navigate(raw(path), mode="walk")
        j = nav.navigate(raw(path), mode="jump")
        assert j["nodes"] >= w["nodes"]
        if w["result"] == "CONNECTED":
            assert j["result"] == "CONNECTED"


# --- synthetic rooms (model behaviour, not physics) -------------------------------------

def test_wall_across_the_corridor_disconnects():
    s = _with_entity(raw(F1E), CORRIDOR_WALL)
    w = nav.navigate(s, mode="walk")
    assert w["result"] == "DISCONNECTED"
    assert w["gates_opened"] == []                  # the wall stands before the ledge opening
    assert w["closest"]["pos"][2] < 16
    assert nav.navigate(s, mode="jump")["result"] == "DISCONNECTED"   # 8 studs > the 7.2 rise


def test_bridge_with_the_one_stud_join_connects():
    s = _with_bridge(raw(F1A), 17)                  # 1 stud into plat_b (ADR 0007 JOIN_MAX)
    assert nav.navigate(s, mode="walk")["result"] == "CONNECTED"


def test_gate_blocks_until_its_lever_is_reachable():
    sealed = _with_entity(raw(F1E), LEDGE_SEAL)
    g = nav.navigate(sealed, mode="walk")
    assert g["result"] == "DISCONNECTED"
    assert g["gates_opened"] == [] and g["gates_closed"] == ["gate_1"]
    assert 30 < g["closest"]["pos"][2] < 33          # stopped in front of the closed gate
    assert nav.navigate(sealed, mode="jump")["result"] == "DISCONNECTED"
    # Same room with the gate lifted out of its slot (geometry only; the validator rejects
    # that as GATE_BYPASSED): the corridor is open, so the graph connects.
    lifted = copy.deepcopy(sealed)
    entity(lifted, "gate_1")["pos"] = [0, 20, 33.5]
    assert nav.navigate(lifted, mode="walk")["result"] == "CONNECTED"


def test_jump_edges_cross_a_hole_inside_the_derived_reach():
    s = _with_bridge(raw(F1A), 12)                  # a 4-stud hole, under the derived 8.67
    assert nav.navigate(s, mode="walk")["result"] == "DISCONNECTED"
    j = nav.navigate(s, mode="jump")
    assert j["result"] == "CONNECTED" and j["basis"] == "derived"
    assert any(n["via"] == "jump" for n in j["path"])
    _assert_steps_are_neighbours(j["path"])


def test_jump_edges_stop_beyond_the_derived_reach():
    s = _with_bridge(raw(F1A), 4)                   # a 12-stud hole
    assert nav.navigate(s, mode="jump")["result"] == "DISCONNECTED"


def test_decor_neither_blocks_nor_supports():
    # Decor is built CanCollide off (compiler); the graph models what is built.
    s = _with_entity(raw(F1E), {"id": "decor_2", "type": "Decor", "palette": "crate",
                                "pos": [0, 4, 16.5], "size": [8, 8, 1]})
    assert nav.navigate(s, mode="walk")["result"] == "CONNECTED"
    hole = _with_entity(raw(F1A), {"id": "decor_2", "type": "Decor", "palette": "crate",
                                   "pos": [0, -0.5, 47], "size": [6, 1, 10]})
    assert nav.navigate(hole, mode="walk")["result"] == "DISCONNECTED"


def test_unknown_mode_is_a_caller_error():
    with pytest.raises(ValueError):
        nav.navigate(raw(F1A), mode="fly")


def test_inputs_are_not_mutated_and_entity_order_does_not_matter():
    scene = raw(F1A_FIX)
    before = copy.deepcopy(scene)
    g1 = nav.graph(scene)
    assert scene == before
    rev = copy.deepcopy(scene)
    rev["entities"].reverse()
    assert nav.graph(rev) == g1


def test_graph_is_identical_across_processes():
    code = ("import json,sys;sys.path.insert(0,'tests');from _scenes import F1A,F1A_FIX,raw;"
            "from worldsmith.validator import nav;"
            "print(json.dumps([nav.graph(raw(F1A)),nav.graph(raw(F1A_FIX))],sort_keys=True))")
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                           capture_output=True, text=True, check=True)
        outs.add(r.stdout)
    assert len(outs) == 1


def test_graph_shape():
    g = nav.graph(raw(F1A))
    assert set(g) == {"model", "note", "params", "walk", "jump"}
    assert "never a proof" in g["note"] and "ADR 0008" in g["note"]
    assert g["params"] == {k: v["value"] for k, v in nav.PARAMS.items()}
    for mode in ("walk", "jump"):
        m = g[mode]
        assert m["result"] in RESULTS
        assert {"result", "basis", "path", "gates_opened", "gates_closed", "reached_on",
                "nodes", "closest"} <= set(m)
    json.dumps(g)


# --- the validator's `graph` field --------------------------------------------------------

def test_validator_fills_graph_for_the_m0_fix():
    res = vd.validate(raw(F1A), raw(M0_FIX_PATCH), stage="M0")
    assert res.ok
    assert res.graph["walk"]["result"] == "CONNECTED"
    assert "GRAPH_DISCONNECTED_WALK" not in {w.code for w in res.warnings}
    assert res.to_dict()["graph"] == res.graph


def test_disconnected_walk_is_a_warning_never_a_rejection():
    base = raw(F1A)
    patch = {"schema": "worldsmith.patch/0.1", "base_hash": sc.canonical_hash(base),
             "ops": [{"op": "set", "id": "bridge_1", "field": "length", "value": 7}]}
    res = vd.validate(base, patch, stage="M0")
    assert res.ok is True and res.violations == ()
    assert {w.code for w in res.warnings} == {"BRIDGE_SHORT", "GRAPH_DISCONNECTED_WALK"}
    assert res.graph["walk"]["result"] == "DISCONNECTED"
    w = [w for w in res.warnings if w.code == "GRAPH_DISCONNECTED_WALK"][0]
    assert w.ref == "graph.walk" and "approximation" in w.message


def test_graph_never_decides_ok(monkeypatch):
    """Even a graph that says DISCONNECTED in both modes leaves `ok` alone."""
    real_graph = nav.graph

    def fake(scene):
        out = copy.deepcopy(real_graph(scene))
        for mode in ("walk", "jump"):
            out[mode]["result"] = "DISCONNECTED"
        return out
    real = vd.validate(raw(F1A), raw(M0_FIX_PATCH), stage="M0")
    monkeypatch.setattr(vd.nav, "graph", fake)
    res = vd.validate(raw(F1A), raw(M0_FIX_PATCH), stage="M0")
    assert res.ok is True and res.violations == real.violations
    assert "GRAPH_DISCONNECTED_WALK" in {w.code for w in res.warnings}
    assert "GRAPH_DISCONNECTED_WALK" not in vd.CODES and "GRAPH_DISCONNECTED_WALK" in vd.WARNING_CODES


def test_scene_input_also_gets_a_graph():
    res = vd.validate(raw(F1A), raw(F1A_FIX), stage="M0")
    assert res.ok and res.graph["walk"]["result"] == "CONNECTED"


def test_graph_is_none_when_the_candidate_is_not_judged_geometrically():
    res = vd.validate(raw(F1A), "{not json", stage="M0")
    assert res.ok is False and res.graph is None
    bad = raw(F1A_FIX)
    entity(bad, "bridge_1")["length"] = 1000          # out of bounds: geometry does not run
    res = vd.validate(raw(F1A), bad, stage="M0")
    assert "OUT_OF_BOUNDS" in res.codes and res.graph is None


# --- documentation ------------------------------------------------------------------------

def test_adr_0008_documents_the_approximation_and_every_parameter():
    text = ADR_0008.read_text(encoding="utf-8")
    assert "never a physical proof" in text
    assert "Actual runs decide" in text
    for name, p in nav.PARAMS.items():
        assert f"`{name}`" in text, name
        assert p["basis"] in text
    assert "n = 1" in text


def test_validator_md_documents_the_graph_and_its_warning():
    text = VALIDATOR_MD.read_text(encoding="utf-8")
    assert "`GRAPH_DISCONNECTED_WALK`" in text
    assert "ADR 0008" in text and "never a proof" in text
