"""WS-03: BRIDGE_CROSSED is anchored to the far side of gap_1 (owner decision, 9 Nov 2026).

ADR 0003. The trigger sits on the first LANDING_DEPTH (2) studs of the gap's `to` platform
(plat_b), so it fires only after a real crossing, whatever the bridge length. A bridge-length
patch no longer moves it, and the M0 fix still differs from F1-A in one field.
"""

import copy

import pytest

from _scenes import F1A, F1A_FIX, F1E, FIXTURES, entity, raw
from worldsmith.grammar.scene import load_schema, validate_scene


def _ids(paths):
    return [p.parent.name + "/" + p.name for p in paths]


def test_schema_regions_are_landing_and_whole_only():
    trig = load_schema()["$defs"]["Trigger"]
    assert trig["properties"]["region"]["enum"] == ["landing", "whole"]
    assert "far_half" not in str(load_schema())


@pytest.mark.parametrize("path", FIXTURES, ids=_ids(FIXTURES))
def test_bridge_trigger_is_on_the_gap_landing(path):
    s = raw(path)
    tb = entity(s, "trig_bridge")
    assert (tb["event"], tb["on"], tb["region"]) == ("BRIDGE_CROSSED", "gap_1", "landing")
    assert entity(s, "gap_1")["to"] == "plat_b"
    assert validate_scene(s) == []


def test_fix_still_differs_from_f1a_in_bridge_length_only():
    a, f = raw(F1A), raw(F1A_FIX)
    entity(a, "bridge_1")["length"] = entity(f, "bridge_1")["length"]
    assert a == f


@pytest.mark.parametrize("event,on,region", [
    ("BRIDGE_CROSSED", "bridge_1", "landing"),   # the bridge is no longer a valid host
    ("BRIDGE_CROSSED", "gap_1", "whole"),        # a gap trigger must use the landing region
    ("BRIDGE_CROSSED", "goal", "whole"),         # BRIDGE_CROSSED must sit on a Gap
    ("GOAL_TOUCHED", "gap_1", "landing"),        # GOAL_TOUCHED must sit on the Goal
    ("GOAL_TOUCHED", "goal", "landing"),         # the landing region belongs to a Gap
])
def test_event_host_region_pairing_is_enforced(event, on, region):
    s = raw(F1A)
    t = entity(s, "trig_bridge")
    t["event"], t["on"], t["region"] = event, on, region
    errs = validate_scene(s)
    assert errs and all(e.startswith("ref:") for e in errs), errs


def test_far_half_region_is_rejected_by_the_schema():
    s = raw(F1A)
    entity(s, "trig_bridge").update({"on": "bridge_1", "region": "far_half"})
    errs = validate_scene(s)
    assert errs and all(e.startswith("schema:") for e in errs)


def test_valid_pairs_pass():
    s = raw(F1E)
    assert validate_scene(copy.deepcopy(s)) == []
    assert (entity(s, "trig_goal")["on"], entity(s, "trig_goal")["region"]) == ("goal", "whole")
