"""WS-08: the SPEC section 10 failure taxonomy over recorded traces, and the hypothesis rule.

All CPU. Observed tags come from the play tool's recorded result (ADR 0005); system tags from the
episode bundle; BRIDGE_SHORT_HYPOTHESIS only when all three section 10 conditions hold, with the
paired-fix evidence linked by bundle path and hashes. Everything else: cause UNCLASSIFIED.
"""

import copy
import dataclasses
import hashlib
import json

import pytest
import yaml

from _m0fake import FELL_EVENTS, SUCCESS_EVENTS, FakeStudio, play_result
from _repo import REPO_ROOT
from _ws08fake import result, standing, walking, wp
from worldsmith.grammar.scene import load_scene
from worldsmith.runner import attempts, classify, p0, taxonomy
from worldsmith.runner.config import load_config

CONFIG = REPO_ROOT / "config" / "m0.yaml"
CCONFIG = REPO_ROOT / "config" / "classifier.yaml"
F1A = REPO_ROOT / "scenes" / "F1-A" / "scene.json"
FIX = REPO_ROOT / "scenes" / "F1-A" / "fix.json"
F1E = REPO_ROOT / "scenes" / "F1-E" / "scene.json"
M0 = REPO_ROOT / "bundles" / "m0"
N1, N2, N3 = "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2", "c3c3c3c3c3c3c3c3"
OTHER = "dededededededede"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG, REPO_ROOT)


@pytest.fixture(scope="module")
def ccfg():
    return taxonomy.load_classifier_config(CCONFIG)


@pytest.fixture(scope="module")
def stuck(ccfg):
    return ccfg["stuck"]


@pytest.fixture(scope="module")
def params(cfg):
    return p0.expected_params(cfg["_route"], cfg["timing"])


@pytest.fixture(scope="module")
def area():
    return classify.gap_area(load_scene(F1A), "gap_1", 4)


def run(r, params, area, stuck, nonce=N1, **kw):
    return taxonomy.classify_result(r, nonce, params, area, stuck, **kw)


# --- the classifier config (STUCK rule) ---------------------------------------------------

def test_classifier_config_declares_the_stuck_rule_and_its_sources(cfg, ccfg):
    s = ccfg["stuck"]
    assert s["epsilon_studs"] == 2.0 and s["window_s"] == 10 and s["max_sample_gap_s"] == 1.0
    # Sources: the window is the controller's own per-waypoint limit; epsilon is the measured
    # HumanoidRootPart width; the sample gap is an estimate and says so.
    assert s["window_s"] == cfg["timing"]["t_waypoint"]
    measure = json.loads((M0 / "character" / "measure.json").read_text())
    assert s["epsilon_studs"] == measure["hrp_size"][0]
    src = ccfg["sources"]
    assert "t_waypoint" in src["window_s"] and "hrp_size" in src["epsilon_studs"]
    assert "ESTIMATE" in src["max_sample_gap_s"]
    assert len(ccfg["_sha256"]) == 64


@pytest.mark.parametrize("edit", [
    lambda c: c.update(extra=1),
    lambda c: c["stuck"].update(epsilon_studs=0),
    lambda c: c["stuck"].update(window_s=-1),
    lambda c: c["stuck"].update(max_sample_gap_s=20),     # must be below the window
    lambda c: c["stuck"].pop("window_s"),
    lambda c: c["sources"].pop("epsilon_studs"),
    lambda c: c.update(schema="other"),
])
def test_classifier_config_refuses_bad_files(tmp_path, edit):
    c = yaml.safe_load(CCONFIG.read_text())
    edit(c)
    p = tmp_path / "classifier.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(taxonomy.ClassifierConfigError):
        taxonomy.load_classifier_config(p)


# --- STUCK: displacement < epsilon over a window, or "not evaluable" ------------------------

def _report(**kw):
    base = {"t_route_start": 1.6, "start_pos": "0.00,3.20,6.00", "waypoints": [], "died": False,
            "t_died": -1, "t_end": 9.5, "status": "route_done"}
    base.update(kw)
    return base


def test_stuck_cannot_hold_when_the_route_phase_is_shorter_than_the_window(stuck):
    out = taxonomy.evaluate_stuck(_report(waypoints=walking()), stuck)
    assert out["result"] == "NOT_STUCK" and "shorter than the window" in out["reason"]
    # Death ends the alive phase: the route phase is measured up to t_died.
    out = taxonomy.evaluate_stuck(_report(died=True, t_died=7.5, t_end=30.0), stuck)
    assert out["result"] == "NOT_STUCK"


def test_dense_stationary_samples_over_the_window_are_stuck(stuck):
    samples = standing(2.0, 14.0, 0.5)
    out = taxonomy.evaluate_stuck(_report(start_pos="0.00,3.00,30.00", waypoints=samples, t_end=14.2), stuck)
    assert out["result"] == "STUCK"
    t_a, t_b = out["window"]
    assert t_b - t_a >= stuck["window_s"]


def test_sparse_stationary_samples_are_not_evaluable(stuck):
    # The P0 controller samples only at waypoint ends; a blocked MoveTo gives up after ~8 s.
    samples = [wp(i, 1.6 + 8.0 * i, (0.0, 3.0, 30.0), reached=False) for i in range(1, 7)]
    out = taxonomy.evaluate_stuck(_report(start_pos="0.00,3.00,30.00", waypoints=samples,
                                          t_end=60.0, status="deadline"), stuck)
    assert out["result"] == "NOT_EVALUABLE" and out["max_gap_s"] > stuck["max_sample_gap_s"]


def test_dense_moving_samples_are_not_stuck(stuck):
    samples = walking(n=30, dt=0.5, dz=1.5)
    out = taxonomy.evaluate_stuck(_report(waypoints=samples, t_end=samples[-1]["t"] + 0.2), stuck)
    assert out["result"] == "NOT_STUCK"


def test_a_long_run_whose_samples_stop_early_is_not_evaluable(stuck):
    samples = walking(n=6, dt=0.5, dz=3.0)          # samples end at t = 4.6
    out = taxonomy.evaluate_stuck(_report(waypoints=samples, t_end=40.0, status="deadline"), stuck)
    assert out["result"] == "NOT_EVALUABLE"


def test_no_position_samples_is_not_evaluable(stuck):
    out = taxonomy.evaluate_stuck(_report(t_route_start=-1, start_pos="na", t_end=60.0, status="deadline"), stuck)
    assert out["result"] == "NOT_EVALUABLE"
    assert taxonomy.evaluate_stuck(None, stuck)["result"] == "NOT_EVALUABLE"


def test_samples_after_death_are_not_used(stuck):
    # Standing still after dying (the body on the kill floor) is not STUCK.
    alive = walking(n=4, dt=0.5, dz=3.0)
    dead = [wp(10 + i, 3.8 + 0.5 * i, (0.0, -15.5, 49.5), died=True) for i in range(30)]
    out = taxonomy.evaluate_stuck(_report(waypoints=alive + dead, died=True, t_died=3.7, t_end=20.0), stuck)
    assert out["result"] == "NOT_STUCK"


# --- observed tags over synthetic traces -------------------------------------------------

def test_success_is_bridge_crossed_then_goal_touched(params, area, stuck):
    out = run(result(N1, params, SUCCESS_EVENTS, waypoints=walking()), params, area, stuck)
    assert out["outcome"] == "SUCCESS" and out["tags"] == ["SUCCESS"]
    assert out["cause"] is None and out["hypothesis"] is None
    assert out["stuck"]["result"] == "NOT_STUCK"


def test_fell_without_episode_context_has_cause_unclassified(params, area, stuck):
    out = run(result(N1, params, FELL_EVENTS, status="died", died=True, t_died=7.5), params, area, stuck)
    assert out["outcome"] == "FELL" and out["tags"] == ["FELL"] and out["fell_in_area"] is True
    assert out["cause"] == "UNCLASSIFIED"


def test_timeout_with_lever_not_activated(params, area, stuck):
    out = run(result(N1, params, [], status="deadline", t_end=60.0), params, area, stuck)
    assert out["outcome"] == "TIMEOUT" and out["tags"] == ["TIMEOUT", "LEVER_NOT_ACTIVATED"]
    assert out["cause"] == "UNCLASSIFIED" and out["stuck"]["result"] == "NOT_EVALUABLE"


def test_timeout_with_gate_not_opened(params, area, stuck):
    out = run(result(N1, params, FELL_EVENTS[:1], status="deadline", t_end=60.0), params, area, stuck)
    assert out["tags"] == ["TIMEOUT", "GATE_NOT_OPENED"]


def test_timeout_while_stuck_carries_both_tags(params, area, stuck):
    samples = standing(2.0, 59.5, 0.5)
    r = result(N1, params, FELL_EVENTS[:2], status="deadline", t_end=60.0, start_pos="0.00,3.00,30.00",
               waypoints=samples)
    out = run(r, params, area, stuck)
    assert out["outcome"] == "TIMEOUT" and out["tags"] == ["TIMEOUT", "STUCK"]


def test_stuck_is_the_outcome_when_nothing_else_was_observed(params, area, stuck):
    samples = standing(2.0, 14.0, 0.5)
    r = result(N1, params, FELL_EVENTS[:2], status="route_done", t_end=14.2, start_pos="0.00,3.00,30.00",
               waypoints=samples)
    out = run(r, params, area, stuck)
    assert out["outcome"] == "STUCK" and out["tags"] == ["STUCK"] and out["cause"] == "UNCLASSIFIED"


def test_sparse_samples_never_force_stuck(params, area, stuck):
    samples = [wp(i, 1.6 + 8.0 * i, (0.0, 3.0, 30.0), reached=False) for i in range(1, 4)]
    r = result(N1, params, FELL_EVENTS[:2], status="route_done", t_end=26.0, start_pos="0.00,3.00,30.00",
               waypoints=samples)
    out = run(r, params, area, stuck)
    assert out["outcome"] == "UNCLASSIFIED" and "STUCK" not in out["tags"]
    assert out["stuck"]["result"] == "NOT_EVALUABLE"


def test_harness_errors(params, area, stuck):
    r = result(N1, params, SUCCESS_EVENTS)
    r["logs"] = [x for x in r["logs"] if "|RESULT|" not in x["message"]]
    assert run(r, params, area, stuck)["outcome"] == "HARNESS_ERROR"
    assert run(result(N1, params, SUCCESS_EVENTS, jumps=2), params, area, stuck)["outcome"] == "HARNESS_ERROR"
    assert run(result(N1, params, [], removed=True, status="died"), params, area, stuck)["outcome"] == "HARNESS_ERROR"


def test_a_script_error_beside_a_report_is_a_harness_error(params, area, stuck):
    # SPEC section 10: exceptions and console errors are system tags; never a SUCCESS.
    r = result(N1, params, SUCCESS_EVENTS, error="ServerScriptService.Script:12: attempt to index nil")
    out = run(r, params, area, stuck)
    assert out["outcome"] == "HARNESS_ERROR" and "attempt to index nil" in out["detail"]


def test_foreign_warnings_are_not_harness_errors(params, area, stuck):
    # Every recorded M0 answer has other plugins' warnings in `errors`; they are not ours.
    r = result(N1, params, SUCCESS_EVENTS)
    r["errors"].append({"message": "ContentId: rbxassetid://1 failed", "ts": 1.0, "level": "warning"})
    assert run(r, params, area, stuck)["outcome"] == "SUCCESS"


def test_connector_errors(params, area, stuck):
    assert run(result(N1, params, [], is_timeout=True), params, area, stuck)["outcome"] == "CONNECTOR_ERROR"
    out = run(None, params, area, stuck, call_error="timeout after 90 s")
    assert out["outcome"] == "CONNECTOR_ERROR" and out["stuck"]["result"] == "NOT_EVALUABLE"
    assert run("not a dict", params, area, stuck)["outcome"] == "CONNECTOR_ERROR"


def test_death_without_a_kill_floor_event_is_unclassified(params, area, stuck):
    r = result(N1, params, FELL_EVENTS[:2], status="died", died=True, t_died=6.0)
    out = run(r, params, area, stuck)
    assert out["outcome"] == "UNCLASSIFIED" and out["cause"] == "UNCLASSIFIED"


# --- console parsing: wrong nonce, foreign lines, order ------------------------------------

def test_wrong_nonce_events_are_ignored(params, area, stuck):
    r = result(N1, params, SUCCESS_EVENTS, event_nonce=OTHER, captured=[])
    out = run(r, params, area, stuck)
    assert out["events"] == [] and out["outcome"] == "UNCLASSIFIED" and out["outcome"] != "SUCCESS"


def test_foreign_and_malformed_lines_are_ignored(params, area, stuck):
    foreign = [
        f"[robloxstudio-mcp] WS|{N1}|GOAL_TOUCHED|id=trig_goal|pos=0.00,4.00,60.00|t=1.000",
        f"WS|{N1}|GOAL_TOUCHED|id=trig_goal|pos=0,4,60|t=1.000",
        f"WS|{N1}|GOAL_TOUCHED|id=trig_goal|pos=0.00,4.00,60.00|t=1.000 trailing",
        f"WS|{N1}|TELEPORTED|id=trig_goal|pos=0.00,4.00,60.00|t=1.000",
        f"WS|{N1.upper()}|BRIDGE_CROSSED|id=trig_bridge|pos=0.00,4.00,50.00|t=1.000",
        f"WSP|{OTHER}|RESULT|1|1|{{}}",
    ]
    r = result(N1, params, FELL_EVENTS[:2], extra_logs=foreign)
    out = run(r, params, area, stuck)
    assert [e["event"] for e in out["events"]] == ["LEVER_ACTIVATED", "GATE_OPENED"]
    assert out["outcome"] == "UNCLASSIFIED"


def test_goal_before_bridge_is_not_success(params, area, stuck):
    ev = [SUCCESS_EVENTS[0], SUCCESS_EVENTS[1], SUCCESS_EVENTS[3], SUCCESS_EVENTS[2]]
    out = run(result(N1, params, ev), params, area, stuck)
    assert out["outcome"] == "UNCLASSIFIED" and "SUCCESS" not in out["tags"]


def test_goal_then_bridge_then_goal_again_is_not_success(params, area, stuck):
    ev = [SUCCESS_EVENTS[0], SUCCESS_EVENTS[1], SUCCESS_EVENTS[3], SUCCESS_EVENTS[2], SUCCESS_EVENTS[3]]
    assert run(result(N1, params, ev), params, area, stuck)["outcome"] != "SUCCESS"


def test_bridge_printed_first_but_timed_after_the_goal_is_not_success(params, area, stuck):
    ev = [(SUCCESS_EVENTS[0][0], SUCCESS_EVENTS[0][1], 100.0), (SUCCESS_EVENTS[1][0], "na", 100.5),
          ("BRIDGE_CROSSED", "0.00,4.34,50.43", 105.0), ("GOAL_TOUCHED", "0.00,4.34,56.12", 104.0)]
    assert run(result(N1, params, ev), params, area, stuck)["outcome"] != "SUCCESS"


def test_death_then_goal_is_fell_not_success(params, area, stuck):
    ev = [FELL_EVENTS[0], FELL_EVENTS[1], FELL_EVENTS[2], SUCCESS_EVENTS[2], SUCCESS_EVENTS[3]]
    out = run(result(N1, params, ev, status="died", died=True, t_died=7.0), params, area, stuck)
    assert out["outcome"] == "FELL" and "SUCCESS" not in out["tags"]


def test_death_without_kill_event_then_goal_is_not_success(params, area, stuck):
    # Died some other way, then a goal line arrives (respawned body, stray part): never SUCCESS.
    r = result(N1, params, SUCCESS_EVENTS, status="died", died=True, t_died=5.0, pos_died="0.00,3.00,40.00")
    out = run(r, params, area, stuck)
    assert out["outcome"] == "UNCLASSIFIED" and "SUCCESS" not in out["tags"]
    # The WS-04 per-attempt classifier agrees (same rule, one place).
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "UNCLASSIFIED"


def test_success_then_a_later_death_stays_success(params, area, stuck):
    ev = SUCCESS_EVENTS + [("KILL_FLOOR_TOUCHED", "0.00,-12.00,70.00")]
    out = run(result(N1, params, ev, status="died", died=True, t_died=9.0), params, area, stuck)
    assert out["outcome"] == "SUCCESS"


def test_observed_outcomes_match_the_ws04_classifier(params, area, stuck):
    # WS-08 extends WS-04: wherever WS-04 decides, the outcome is the same.
    cases = [play_result(N1, params, SUCCESS_EVENTS), play_result(N1, params, FELL_EVENTS, died=True, status="died"),
             play_result(N1, params, [], status="deadline"), play_result(N1, params, FELL_EVENTS[:1], status="deadline"),
             play_result(N1, params, [], status="harness_error"), play_result(N1, params, [], is_timeout=True)]
    for r in cases:
        a = classify.classify_attempt(r, N1, params, area)
        b = run(r, params, area, stuck)
        assert (b["outcome"], b["tags"]) == (a["outcome"], a["tags"])


# --- geometry and the scene change ----------------------------------------------------------

def test_geometry_check_reads_the_scene():
    g = taxonomy.geometry_check(load_scene(F1A), "gap_1")
    assert g["ok"] is True and g["bridge"] == "bridge_1"
    assert g["bridge_length"] == 6 and g["gap_width_declared"] == 16 and g["gap_width_measured"] == 16
    assert g["uncovered_studs"] == 10
    assert taxonomy.geometry_check(load_scene(FIX), "gap_1")["ok"] is False
    assert taxonomy.geometry_check(load_scene(F1E), "gap_1")["ok"] is False


def test_geometry_check_refuses_inconsistent_or_missing_parts():
    s = load_scene(F1A)
    s2 = copy.deepcopy(s)
    next(e for e in s2["entities"] if e["id"] == "gap_1")["width"] = 20      # platforms say 16
    g = taxonomy.geometry_check(s2, "gap_1")
    assert g["ok"] is False and "measured" in g["reason"]
    s3 = copy.deepcopy(s)
    s3["entities"] = [e for e in s3["entities"] if e["id"] != "bridge_1"]
    assert taxonomy.geometry_check(s3, "gap_1")["ok"] is False
    assert taxonomy.geometry_check(s, "no_such_gap")["ok"] is False


def test_scene_change_is_bridge_length_only_for_the_m0_fix():
    assert taxonomy.scene_change(load_scene(F1A), load_scene(FIX)) == [
        {"entity": "bridge_1", "field": "length", "before": 6, "after": 16}]
    ch = taxonomy.scene_change(load_scene(F1A), load_scene(F1E))
    assert len(ch) > 1 and {"entity": None, "field": "scene_id", "before": "f1_a", "after": "f1_e"} in ch


# --- recorded M0 bundles ----------------------------------------------------------------------

def _load(cfg, ccfg, name):
    return taxonomy.load_episode(M0 / name, cfg, ccfg, REPO_ROOT)


@pytest.fixture(scope="module")
def m0(cfg, ccfg):
    return {n: _load(cfg, ccfg, n) for n in ("F1-A", "F1-A-fix", "smoke-F1-E", "smoke2-F1-E")}


def test_recorded_episodes_reclassify_like_their_summaries(m0):
    for name in ("F1-A", "F1-A-fix", "smoke2-F1-E"):
        ep = m0[name]
        assert ep.problems == [], name
        s = json.loads((M0 / name / "summary.json").read_text())
        assert [a["outcome"] for a in ep.attempts] == [a["outcome"] for a in s["attempts"]]
        assert ep.episode_tags == []
    assert [a["outcome"] for a in m0["F1-A"].attempts] == ["FELL"] * 3
    assert [a["outcome"] for a in m0["F1-A-fix"].attempts] == ["SUCCESS"] * 3
    # The first smoke run used another controller and config: recorded, never reinterpreted.
    assert m0["smoke-F1-E"].problems and m0["smoke-F1-E"].attempts == []


def test_recorded_m0_attempts_are_not_stuck(m0):
    for name in ("F1-A", "F1-A-fix"):
        for a in m0[name].attempts:
            assert a["stuck"]["result"] == "NOT_STUCK", (name, a["attempt"])


def test_bridge_short_hypothesis_holds_for_each_recorded_f1a_fall(cfg, m0):
    manifest = json.loads((M0 / "manifest.json").read_text())["files"]
    cands = list(m0.values())
    for a in m0["F1-A"].attempts:
        h = taxonomy.bridge_short_hypothesis(m0["F1-A"], a, cands, cfg)
        assert h["tag"] == "BRIDGE_SHORT_HYPOTHESIS" and h["cause"] == "BRIDGE_SHORT_HYPOTHESIS"
        c = h["conditions"]
        assert c["fell_in_gap_area"]["ok"] and c["geometry"]["ok"] and c["paired_fix"]["ok"]
        [ev] = c["paired_fix"]["evidence"]
        assert ev["bundle"] == "bundles/m0/F1-A-fix" and ev["outcomes"] == ["SUCCESS"] * 3
        assert ev["k"] == 3 and ev["n"] == 3
        assert ev["controller_sha256"] == m0["F1-A"].controller_sha256 == cfg["controller"]["sha256"]
        assert ev["scene_change"] == [{"entity": "bridge_1", "field": "length", "before": 6, "after": 16}]
        for rel, sha in ev["files"].items():
            assert manifest[f"F1-A-fix/{rel}"] == sha, rel
        rejected = {r["bundle"]: r["reasons"] for r in c["paired_fix"]["rejected"]}
        assert set(rejected) == {"bundles/m0/F1-A", "bundles/m0/smoke-F1-E", "bundles/m0/smoke2-F1-E"}
        assert rejected["bundles/m0/F1-A"][0] == "the same episode as the fall"
        assert any("3/3" in r for r in rejected["bundles/m0/smoke2-F1-E"])
        assert any("bridge" in r for r in rejected["bundles/m0/smoke2-F1-E"])


def test_without_paired_fix_evidence_the_cause_is_unclassified(cfg, m0):
    a = m0["F1-A"].attempts[0]
    for cands in ([], [m0["F1-A"]], [m0["smoke2-F1-E"]], [m0["smoke-F1-E"]]):
        h = taxonomy.bridge_short_hypothesis(m0["F1-A"], a, cands, cfg)
        assert h["tag"] is None and h["cause"] == "UNCLASSIFIED"
        assert h["conditions"]["paired_fix"]["ok"] is False
        # The other two conditions are still evaluated and reported.
        assert h["conditions"]["geometry"]["ok"] and h["conditions"]["fell_in_gap_area"]["ok"]


def _fix_variant(m0, **changes):
    ep = m0["F1-A-fix"]
    return dataclasses.replace(ep, attempts=copy.deepcopy(ep.attempts), scene=copy.deepcopy(ep.scene),
                               **changes)


def _bridge(scene):
    return next(e for e in scene["entities"] if e["id"] == "bridge_1")


@pytest.mark.parametrize("why", ["two_of_three", "controller", "config", "other_field", "shorter",
                                 "problems", "not_complete", "readback"])
def test_paired_fix_evidence_is_strict(cfg, m0, why):
    v = _fix_variant(m0)
    if why == "two_of_three":
        v.attempts[1]["outcome"] = "FELL"
    elif why == "controller":
        v = dataclasses.replace(v, controller_sha256="0" * 64)
    elif why == "config":
        v = dataclasses.replace(v, config_sha256="0" * 64)
    elif why == "other_field":
        _bridge(v.scene)["width"] = 7
    elif why == "shorter":
        _bridge(v.scene)["length"] = 4
    elif why == "problems":
        v = dataclasses.replace(v, problems=["attempt 1: raw tool result missing or changed"])
    elif why == "not_complete":
        v = dataclasses.replace(v, status="CONNECTOR_ERROR")
    elif why == "readback":
        v = dataclasses.replace(v, readback_differences=1)
    h = taxonomy.bridge_short_hypothesis(m0["F1-A"], m0["F1-A"].attempts[0], [v], cfg)
    assert h["cause"] == "UNCLASSIFIED" and h["conditions"]["paired_fix"]["ok"] is False
    assert h["conditions"]["paired_fix"]["rejected"][0]["reasons"]


def test_fall_outside_the_gap_area_is_unclassified(cfg, m0):
    a = copy.deepcopy(m0["F1-A"].attempts[0])
    a["fell_in_area"] = False
    h = taxonomy.bridge_short_hypothesis(m0["F1-A"], a, list(m0.values()), cfg)
    assert h["cause"] == "UNCLASSIFIED" and h["conditions"]["fell_in_gap_area"]["ok"] is False


def test_failed_geometry_check_is_unclassified(cfg, m0):
    ep = dataclasses.replace(m0["F1-A"], scene=copy.deepcopy(m0["F1-A"].scene))
    _bridge(ep.scene)["length"] = 16          # the scene no longer has a short bridge
    h = taxonomy.bridge_short_hypothesis(ep, ep.attempts[0], list(m0.values()), cfg)
    assert h["cause"] == "UNCLASSIFIED" and h["conditions"]["geometry"]["ok"] is False


def test_no_hypothesis_for_anything_but_fell(cfg, m0):
    a = m0["F1-A-fix"].attempts[0]
    h = taxonomy.bridge_short_hypothesis(m0["F1-A-fix"], a, list(m0.values()), cfg)
    assert h["tag"] is None and h["cause"] is None


# --- synthetic bundles: system tags and the hypothesis end to end -----------------------------

def _episode(cfg, tmp_path, role, scene, plays, nonces=(N1, N2, N3), studio_cls=FakeStudio, **kw):
    d = tmp_path / role
    attempts.init_episode(cfg, role, d, role, nonces=list(nonces))
    params = p0.expected_params(cfg["_route"], cfg["timing"])
    studio = studio_cls(load_scene(scene), list(nonces), params,
                        {i: p(params, n) for i, (p, n) in enumerate(zip(plays, nonces), start=1)}, **kw)
    attempts.run_episode(studio, cfg, d)
    return d


def fell(params, nonce):
    return play_result(nonce, params, FELL_EVENTS, status="died", died=True)


def success(params, nonce):
    return play_result(nonce, params, SUCCESS_EVENTS)


def test_build_failed_is_an_episode_system_tag(cfg, ccfg, tmp_path):
    d = _episode(cfg, tmp_path, "before", F1A, [fell] * 3, build_ok=False)
    ep = taxonomy.load_episode(d, cfg, ccfg, tmp_path)
    assert ep.episode_tags == ["BUILD_FAILED"] and ep.attempts == [] and ep.problems == []


def test_connector_error_is_an_episode_system_tag(cfg, ccfg, tmp_path):
    class Stuck(FakeStudio):
        def call(self, tool, args, timeout_s):
            if tool == "start_stop_play":
                return attempts.CallResult(text="ok")
            return super().call(tool, args, timeout_s)
    d = _episode(cfg, tmp_path, "before", F1A, [], studio_cls=Stuck, mode="start_play")
    ep = taxonomy.load_episode(d, cfg, ccfg, tmp_path)
    assert ep.episode_tags == ["CONNECTOR_ERROR"] and ep.attempts == []


def test_hung_play_call_is_a_connector_error_attempt(cfg, ccfg, tmp_path):
    hung = lambda params, nonce: attempts.CallTimeout("no answer")
    d = _episode(cfg, tmp_path, "before", F1A, [fell, hung, fell])
    ep = taxonomy.load_episode(d, cfg, ccfg, tmp_path)
    assert [a["outcome"] for a in ep.attempts] == ["FELL", "CONNECTOR_ERROR", "FELL"]
    assert ep.problems == []


def test_tampered_raw_answer_is_a_problem_not_a_result(cfg, ccfg, tmp_path):
    d = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    raw = d / "attempts" / "2.raw.txt"
    raw.write_text(raw.read_text().replace("GOAL_TOUCHED", "GOAL_TOUCHEX"))
    ep = taxonomy.load_episode(d, cfg, ccfg, tmp_path)
    assert ep.problems


def test_synthetic_pair_gives_the_hypothesis_and_2_of_3_does_not(cfg, ccfg, tmp_path):
    b = taxonomy.load_episode(_episode(cfg, tmp_path / "ok", "before", F1A, [fell] * 3), cfg, ccfg, tmp_path)
    a = taxonomy.load_episode(_episode(cfg, tmp_path / "ok", "after", FIX, [success] * 3), cfg, ccfg, tmp_path)
    a2 = taxonomy.load_episode(_episode(cfg, tmp_path / "two", "after", FIX, [success, fell, success]),
                               cfg, ccfg, tmp_path)
    h = taxonomy.bridge_short_hypothesis(b, b.attempts[0], [a], cfg)
    assert h["cause"] == "BRIDGE_SHORT_HYPOTHESIS"
    assert h["conditions"]["paired_fix"]["evidence"][0]["bundle"] == "ok/after"
    h = taxonomy.bridge_short_hypothesis(b, b.attempts[0], [a2], cfg)
    assert h["cause"] == "UNCLASSIFIED"


def test_a_fall_elsewhere_in_a_synthetic_episode_is_unclassified(cfg, ccfg, tmp_path):
    def fell_elsewhere(params, nonce):
        ev = FELL_EVENTS[:2] + [("KILL_FLOOR_TOUCHED", "-15.00,-12.00,24.00")]
        return play_result(nonce, params, ev, status="died", died=True)
    b = taxonomy.load_episode(_episode(cfg, tmp_path, "before", F1A, [fell, fell, fell_elsewhere]), cfg, ccfg, tmp_path)
    a = taxonomy.load_episode(_episode(cfg, tmp_path, "after", FIX, [success] * 3), cfg, ccfg, tmp_path)
    doc = taxonomy.classify_episodes([b, a], [b, a], cfg, ccfg)
    causes = [x["cause"] for x in doc["episodes"][0]["attempts"]]
    assert causes == ["BRIDGE_SHORT_HYPOTHESIS", "BRIDGE_SHORT_HYPOTHESIS", "UNCLASSIFIED"]


# --- bundles/m0/classification.json -----------------------------------------------------------

CLASSIFICATION = M0 / "classification.json"


def test_m0_classification_file_is_reproducible(cfg, ccfg):
    doc = taxonomy.classify_m0(REPO_ROOT, cfg, ccfg)
    assert json.loads(CLASSIFICATION.read_text()) == doc
    assert CLASSIFICATION.read_text() == taxonomy.dumps(doc)


def test_m0_classification_content():
    doc = json.loads(CLASSIFICATION.read_text())
    assert doc["schema"] == "worldsmith.classification/0.1"
    assert "known by construction" in doc["disclosure"]
    eps = {e["episode"]: e for e in doc["episodes"]}
    assert list(eps) == ["F1-A", "F1-A-fix"]
    fa, fx = eps["F1-A"], eps["F1-A-fix"]
    assert [(a["outcome"], a["cause"]) for a in fa["attempts"]] == [("FELL", "BRIDGE_SHORT_HYPOTHESIS")] * 3
    assert [(a["outcome"], a["cause"]) for a in fx["attempts"]] == [("SUCCESS", None)] * 3
    assert [a["tags"] for a in fa["attempts"]] == [["FELL", "BRIDGE_SHORT_HYPOTHESIS"]] * 3
    for a in fa["attempts"]:
        assert a["hypothesis"]["conditions"]["paired_fix"]["evidence"][0]["bundle"] == "bundles/m0/F1-A-fix"
        raw = (M0 / "F1-A" / a["raw_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == a["raw_sha256"]
    assert doc["counts"] == {"BRIDGE_SHORT_HYPOTHESIS": 3, "FELL": 3, "SUCCESS": 3}
    assert doc["classifier"]["config_sha256"] == hashlib.sha256(CCONFIG.read_bytes()).hexdigest()


def test_classify_script_writes_the_same_file(tmp_path):
    import subprocess
    import sys
    out = tmp_path / "c.json"
    r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "ws_classify.py"), "--out", str(out)],
                       capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == CLASSIFICATION.read_text()
