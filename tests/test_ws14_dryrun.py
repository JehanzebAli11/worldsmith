"""WS-14: the dry run's stand-in rules and M1 fixtures (owner decision 11 Nov 2026; HANDOFF WS-14
open question 5).

FakeOperator (runner/dryrun.py) decides plays by geometry: the lever's zone must be reachable past
walls, the gate stays shut until then, the route must stay over the bridge deck, the bridge must
reach the far platform. These tests check that the rules are geometry (never scene ids), that they
reproduce the live gate (bundles/experiments/gate_*) attempt for attempt, and that F1-A/F1-E plays
are what the earlier bridge-length-only rule gave, byte for byte. The M1 architect fixtures
(architect/fixtures/m1/) replay as declared. CPU only; simulated; NOT a result.
"""

import copy
import json

import pytest

from _repo import REPO_ROOT
from worldsmith.architect import fixtures as fx
from worldsmith.architect import prompt as pr
from worldsmith.grammar import scene as sc
from worldsmith.runner import dryrun
from worldsmith.runner import experiment as ex

EXPERIMENTS = REPO_ROOT / "config" / "experiments"
GATES = ["gate_f1b", "gate_f1b_fix", "gate_f1c", "gate_f1c_fix", "gate_f1d", "gate_f1d_fix"]
KEYS = ["f1a", "f1b", "f1c", "f1d"]
M1_NAMES = [f"m1/{k}_m1_{kind}" for k in KEYS for kind in ("exhausted", "fix", "reject_then_fix")]


def _observed(ep_dir, version=1):
    rows = [json.loads((ep_dir / f"v{version}" / "attempts" / f"{i}.json").read_text()) for i in (1, 2, 3)]
    return pr.observed_from_classification({"attempts": rows, "episode_tags": []})


@pytest.fixture(scope="module")
def parts():
    return {c["experiment"]: c for c in ex.load_suite(EXPERIMENTS / "m2_hard.yaml", REPO_ROOT)}


# --- the fake operator's stand-in rules: scene geometry, never scene ids (simulated) -------------

def _scene(name, file="scene.json"):
    return copy.deepcopy(sc.load_scene(REPO_ROOT / "scenes" / name / file))


def _ent(scene, eid):
    return next(e for e in scene["entities"] if e["id"] == eid)


def _events(op, scene):
    r = op.play(scene, "0123456789abcdef", 1)
    return [m["message"].split("|")[2] for m in r["logs"] if m["message"].startswith("WS|")]


@pytest.fixture(scope="module")
def op(parts):
    return dryrun.FakeOperator(parts["m2_hard_f1a"])


def test_stand_in_lever_rule_is_geometry(op, parts):
    wp = parts["m2_hard_f1a"]["_params"]["waypoints"]
    a = _scene("F1-A")                                       # F1-A's id, F1-C's wall in front of its lever
    _ent(a, "bridge_1")["length"] = 16
    a["entities"].append(dict(_ent(_scene("F1-C"), "wall_ledge"), id="wall_extra_1"))
    assert dryrun.stand_in_outcome(a, wp) == ("STUCK", None, False)
    assert _events(op, a) == []
    c = _scene("F1-C")                                       # F1-C's id, its wall_ledge taken away
    c["entities"] = [e for e in c["entities"] if e["id"] != "wall_ledge"]
    assert dryrun.stand_in_outcome(c, wp) == ("SUCCESS", None, True)
    g = _scene("F1-C")                                       # lever still walled off, gate moved off the path
    _ent(g, "gate_1")["pos"] = [20, 4, 33.5]
    assert dryrun.stand_in_outcome(g, wp) == ("SUCCESS", None, False)
    assert _events(op, g) == ["BRIDGE_CROSSED", "GOAL_TOUCHED"]
    stop = dryrun.walk(_scene("F1-C"), wp)["end"]           # where the live gate's characters stood
    assert stop[0] == 0 and 32.45 <= stop[1] <= 32.5


def test_stand_in_bridge_rule_is_geometry(op, parts):
    wp = parts["m2_hard_f1a"]["_params"]["waypoints"]
    a = _scene("F1-A")
    br = _ent(a, "bridge_1")
    br["length"] = 16
    for off, want in ((10, ("FELL", "beside", True)), (-10, ("FELL", "beside", True)),
                      (2, ("SUCCESS", None, True)), (0, ("SUCCESS", None, True))):
        br["lateral_offset"] = off
        assert dryrun.stand_in_outcome(a, wp) == want, off
    br["lateral_offset"] = 10
    r = op.play(a, "0123456789abcdef", 2)
    assert dryrun.GATE_F1B_FALLS[1] in "".join(m["message"] for m in r["logs"])
    b = _scene("F1-B")                                       # F1-B's id with the bridge back on the path
    _ent(b, "bridge_1")["lateral_offset"] = 0
    assert dryrun.stand_in_outcome(b, wp) == ("SUCCESS", None, True)


@pytest.mark.parametrize("name", ["F1-A", "F1-E"])
def test_stand_in_rules_keep_f1a_and_f1e_byte_identical(op, parts, name):
    """On rooms with a reachable lever and the bridge under the path, every play report is the
    earlier bridge-length-only report (the forced-outcome path is that code, unchanged)."""
    wp = parts["m2_hard_f1a"]["_params"]["waypoints"]
    s = _scene(name)
    for length in [x / 2 for x in range(2, 129)]:            # every M0 length a patch could set, 0.5 apart
        _ent(s, "bridge_1")["length"] = length
        reaches = dryrun.bridge_reaches(s)
        assert dryrun.stand_in_outcome(s, wp) == (("SUCCESS", None, True) if reaches else ("FELL", "short", True))
        for i in (1, 2, 3):
            assert op.play(s, "0123456789abcdef", i) == op.play(s, "0123456789abcdef", i,
                                                                "SUCCESS" if reaches else "FELL")


@pytest.mark.parametrize("gate", GATES)
def test_dry_run_of_each_gate_scene_matches_the_live_gate(gate, tmp_path):
    """The six live gate experiments, dry-run: the same outcomes and tags per attempt as the
    recorded bundles, and the same architect view (observed) of every attempt. Simulated."""
    cfg = ex.load_experiment(EXPERIMENTS / f"{gate}.yaml", REPO_ROOT)
    dryrun.run_dry(cfg, tmp_path)
    live = REPO_ROOT / "bundles" / "experiments" / gate / "B0" / "ep0"
    dry_ep = tmp_path / gate / "B0" / "ep0"
    ls, ds = (json.loads((x / "summary.json").read_text()) for x in (live, dry_ep))
    assert ls["dry_run"] is False and ds["dry_run"] is True
    assert [(v["outcomes"], v["tags"]) for v in ds["versions"]] == [(v["outcomes"], v["tags"]) for v in ls["versions"]]
    assert ds["final"]["verdict"] == ls["final"]["verdict"]
    assert _observed(dry_ep) == _observed(live)
# --- the M1 architect fixtures (hand-written replies; not model outputs) -------------------------

@pytest.mark.parametrize("name", M1_NAMES)
def test_m1_fixtures_replay_as_declared(name):
    doc = fx.load_fixture(name)
    assert "not a model output" in doc["provenance"] and doc["stage"] == "M1" and doc["max_revisions"] == 3
    res, log = fx.replay(name)
    assert [r.verdict for r in res.revisions] == [t["expect"]["verdict"] for t in doc["turns"]]
    for rev, t in zip(res.revisions, doc["turns"]):
        assert set(t["expect"].get("codes", [])) <= set(rev.codes), (name, rev.codes)
        if "malformed_reason" in t["expect"]:
            assert rev.malformed["reason"] == t["expect"]["malformed_reason"]
    e = doc["expect"]
    assert (res.used, res.rejected, res.exhausted) == (e["revisions_used"], e["rejected"], e["exhausted"])
    assert (res.accepted.index if res.accepted else None) == e["accepted_on"]


def test_m1_fixtures_are_listed_and_hashed():
    names = fx.fixture_names()
    assert [n for n in names if n.startswith("m1/")] == sorted(M1_NAMES)
    for n in names:                                          # the request hashes are current
        assert [t["request_sha"] for t in fx.load_fixture(n)["turns"]] == fx.compute_request_shas(n), n


def test_m1_fixtures_observe_the_live_gate_or_m0():
    """F1-B/C/D fixtures read the live gate's B0 attempts (which the dry run reproduces); F1-A's read
    M0's classification (which the dry run also reproduces: test_ws11_orchestrator)."""
    for n in M1_NAMES:
        doc = fx.load_fixture(n)
        cl, key = doc["classification"], n.split("/")[1][:3]
        if key == "f1a":
            assert cl == {"file": "bundles/m0/classification.json", "episode": "F1-A"}
        else:
            assert cl == {"episode_dir": f"bundles/experiments/gate_{key}/B0/ep0", "version": 1}
            scene, observed = fx._inputs(doc)
            assert observed == _observed(REPO_ROOT / cl["episode_dir"])


def test_fixture_inputs_refuse_a_bundle_of_another_scene():
    doc = fx.load_fixture("m1/f1c_m1_fix")
    doc = dict(doc, classification={"episode_dir": "bundles/experiments/gate_f1b/B0/ep0", "version": 1})
    with pytest.raises(fx.ArchitectConfigError, match="not the fixture's scene"):
        fx._inputs(doc)
