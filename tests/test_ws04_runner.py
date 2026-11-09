"""WS-04: P0 controller template, M0 config, attempt classification, runner, bundles, gate.

All CPU. The live M0 run and its results are in docs/M0.md and bundles/m0/, not here.
"""

import copy
import json
import shutil

import pytest
import yaml

from _m0fake import (FELL_EVENTS, ORIGIN, SUCCESS_EVENTS, FakeStudio, play_result)
from _repo import REPO_ROOT
from worldsmith.compiler.templates import TemplateError
from worldsmith.grammar.scene import canonical_hash, load_scene
from worldsmith.runner import attempts, classify, p0
from worldsmith.runner.config import ConfigError, load_config

CONFIG = REPO_ROOT / "config" / "m0.yaml"
ROUTE = REPO_ROOT / "config" / "p0_route_F1.json"
SNAPSHOT = REPO_ROOT / "tests" / "snapshots" / "ws04" / "p0_controller_F1.luau"
F1A = REPO_ROOT / "scenes" / "F1-A" / "scene.json"
FIX = REPO_ROOT / "scenes" / "F1-A" / "fix.json"
F1E = REPO_ROOT / "scenes" / "F1-E" / "scene.json"
N1, N2, N3 = "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2", "c3c3c3c3c3c3c3c3"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG, REPO_ROOT)


@pytest.fixture(scope="module")
def params(cfg):
    return p0.expected_params(cfg["_route"], cfg["timing"])


@pytest.fixture(scope="module")
def area():
    return classify.gap_area(load_scene(F1A), "gap_1", 4)


# --- controller template ------------------------------------------------------------------

def test_controller_matches_reviewed_snapshot(cfg):
    assert cfg["_controller_text"] == SNAPSHOT.read_text(encoding="utf-8")


def test_controller_hash_is_declared_and_stable(cfg):
    route = p0.load_route(ROUTE)
    a = p0.render_controller(route, cfg["timing"])
    b = p0.render_controller(copy.deepcopy(route), dict(cfg["timing"]))
    assert a == b
    assert p0.controller_sha256(a) == cfg["controller"]["sha256"] == p0.sha256_text(SNAPSHOT.read_text())


def test_controller_has_no_scene_input(cfg):
    # The same text (and hash) is sent to every scene: nothing scene-specific is rendered in.
    text = cfg["_controller_text"]
    for h in (canonical_hash(load_scene(F1A)), canonical_hash(load_scene(FIX))):
        assert h not in text
    assert "bridge_1" not in text and "length" not in text


def test_controller_only_moves_never_jumps_or_teleports(cfg):
    code = "\n".join(l for l in cfg["_controller_text"].splitlines() if not l.lstrip().startswith("--"))
    for banned in ("PivotTo", "CFrame", "WalkSpeed =", "JumpHeight =", "JumpPower =", "Gravity =",
                   ".Jump =", "Jump = true", "MoveTo(origin + Vector3", "SetPrimaryPartCFrame"):
        if banned == "MoveTo(origin + Vector3":
            assert code.count(banned) == 1
        else:
            assert banned not in code, banned
    assert "Enum.HumanoidStateType.Jumping" in code        # it counts jumps, so a jump is visible


def test_instantiate_fills_only_the_nonce(cfg):
    text = cfg["_controller_text"]
    s1, s2 = p0.instantiate(text, N1), p0.instantiate(text, N2)
    assert f'local NONCE = "{N1}"' in s1 and "{{" not in s1
    diff = [(x, y) for x, y in zip(s1.splitlines(), s2.splitlines()) if x != y]
    assert diff == [(f'local NONCE = "{N1}"', f'local NONCE = "{N2}"')]


@pytest.mark.parametrize("bad", ["", "ABCDEF12", "a1a1", 'a1a1a1a1"; os.exit()', "g" * 16, 12345678])
def test_instantiate_rejects_bad_nonces(cfg, bad):
    with pytest.raises(TemplateError):
        p0.instantiate(cfg["_controller_text"], bad)


def _route():
    return json.loads(ROUTE.read_text())


@pytest.mark.parametrize("mutate, msg", [
    (lambda r: r["waypoints"][0].update(pos=[0, 0, float("nan")]), "finite"),
    (lambda r: r["waypoints"][0].update(pos=[0, 0, "20"]), "finite"),
    (lambda r: r["waypoints"][0].update(pos=[0, 0]), "finite"),
    (lambda r: r["waypoints"][0].update(name="Corridor"), "bad name"),
    (lambda r: r["waypoints"][0].update(name='x", 1 }) os.exit() --'), "bad name"),
    (lambda r: r["waypoints"][1].update(name="corridor"), "duplicate"),
    (lambda r: r["waypoints"][0].update(speed=20), "exactly name and pos"),
    (lambda r: r.update(waypoints=[]), "1..16"),
    (lambda r: r.update(waypoints=r["waypoints"] * 2), "1..16"),
    (lambda r: r.update(teleport=True), "unknown key"),
])
def test_route_validation_rejects(mutate, msg):
    r = _route()
    mutate(r)
    errs = p0.validate_route(r)
    assert any(msg in e for e in errs), errs
    with pytest.raises(p0.RouteError):
        p0.render_controller(r, {"t_attempt": 60, "t_waypoint": 10, "settle": 2, "tick": 0.05})


def test_route_must_lie_inside_scene_bounds():
    r = _route()
    r["waypoints"][0]["pos"] = [0, 0, 500]
    assert any("outside the scene bounds" in e for e in p0.validate_route(r, load_scene(F1A)["bounds"]))
    assert p0.validate_route(_route(), load_scene(F1A)["bounds"]) == []


@pytest.mark.parametrize("timing", [
    {"t_attempt": 0, "t_waypoint": 10, "settle": 2, "tick": 0.05},
    {"t_attempt": 60, "t_waypoint": 10, "settle": 2, "tick": 0},
    {"t_attempt": 60, "t_waypoint": 10, "settle": 2},
    {"t_attempt": 60, "t_waypoint": 10, "settle": 2, "tick": 0.05, "extra": 1},
    {"t_attempt": float("inf"), "t_waypoint": 10, "settle": 2, "tick": 0.05},
])
def test_timing_validation(timing):
    with pytest.raises(p0.RouteError):
        p0.render_controller(_route(), timing)


# --- config -------------------------------------------------------------------------------

def test_config_declares_owner_decisions(cfg):
    assert cfg["timing"]["t_attempt"] == 60
    assert (cfg["n_attempts"], cfg["pass_k"]) == (3, 3)
    assert cfg["player"]["tier"] == "P0" and "never jumps" in cfg["player"]["driver"]
    assert "Not an Autopilot or agent result" in cfg["player"]["claim"]
    assert cfg["route"]["sha256"] == p0.sha256_file(ROUTE)
    assert cfg["_sha256"] == p0.sha256_file(CONFIG)
    assert cfg["tool_timeouts_s"]["run_script_in_play_mode"] > cfg["timing"]["t_attempt"]
    assert cfg["gap_area_margin_studs"] > 3.5          # the measured position lag (ADR 0004)


def _cfg_variant(tmp_path, edit):
    d = tmp_path / "repo"
    (d / "config").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "scenes", d / "scenes")
    shutil.copy(ROUTE, d / "config" / ROUTE.name)
    data = yaml.safe_load(CONFIG.read_text())
    edit(data, d)
    (d / "config" / "m0.yaml").write_text(yaml.safe_dump(data))
    return load_config(d / "config" / "m0.yaml", d)


@pytest.mark.parametrize("edit", [
    lambda c, d: c.update(extra=1),
    lambda c, d: c["controller"].update(sha256="0" * 64),
    lambda c, d: c["route"].update(sha256="0" * 64),
    lambda c, d: (d / "config" / "p0_route_F1.json").write_text(
        (d / "config" / "p0_route_F1.json").read_text().replace("35.5", "36")),
    lambda c, d: c["scenes"]["after"].update(canonical_hash="0" * 64),
    lambda c, d: c.update(pass_k=2),
    lambda c, d: c.update(n_attempts=5, pass_k=5),
    lambda c, d: c["tool_timeouts_s"].update(run_script_in_play_mode=60),
    lambda c, d: c["timing"].update(t_attempt=600),
    lambda c, d: c["player"].update(driver="key-hold"),
    lambda c, d: c["gate"]["before"].update(expect="SUCCESS"),
])
def test_config_refuses_mismatches(tmp_path, edit):
    with pytest.raises((ConfigError, p0.RouteError)):
        _cfg_variant(tmp_path, edit)


def test_config_copy_loads(tmp_path):
    assert _cfg_variant(tmp_path, lambda c, d: None)["timing"]["t_attempt"] == 60


# --- report parsing and classification ----------------------------------------------------

def test_report_ignores_foreign_and_other_nonce_lines(params):
    r = play_result(N1, params, SUCCESS_EVENTS, extra_logs=[
        f"WSP|{N2}|RESULT|1|1|{{}}", "WSP|nonsense", "[robloxstudio-mcp] /ready failed"])
    rep = classify.parse_report(classify.log_text(r), N1)
    assert rep["nonce"] == N1 and rep["status"] == "route_done"


def test_report_missing_or_broken_is_harness_error(params, area):
    r = play_result(N1, params, SUCCESS_EVENTS, drop_report=True)
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "HARNESS_ERROR"
    r = play_result(N1, params, SUCCESS_EVENTS)
    r["logs"] = [x for x in r["logs"] if "|RESULT|2|" not in x["message"]]
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "HARNESS_ERROR"


def test_report_with_wrong_nonce_inside_is_harness_error(params, area):
    r = play_result(N1, params, SUCCESS_EVENTS)
    bad = json.dumps({"nonce": N2})
    r["logs"].append({"message": f"WSP|{N1}|RESULT|1|1|{bad}", "level": "output"})
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "HARNESS_ERROR"


def test_success_needs_bridge_then_goal(params, area):
    r = play_result(N1, params, SUCCESS_EVENTS)
    out = classify.classify_attempt(r, N1, params, area)
    assert out["outcome"] == "SUCCESS" and out["tags"] == ["SUCCESS"] and out["events_agree"] is True


def test_goal_without_bridge_is_not_success(params, area):
    ev = [e for e in SUCCESS_EVENTS if e[0] != "BRIDGE_CROSSED"]
    assert classify.classify_attempt(play_result(N1, params, ev), N1, params, area)["outcome"] == "UNCLASSIFIED"


def test_goal_before_bridge_is_not_success(params, area):
    ev = [SUCCESS_EVENTS[0], SUCCESS_EVENTS[1], SUCCESS_EVENTS[3], SUCCESS_EVENTS[2]]
    assert classify.classify_attempt(play_result(N1, params, ev), N1, params, area)["outcome"] == "UNCLASSIFIED"


def test_other_nonce_events_never_make_a_success(params, area):
    r = play_result(N1, params, FELL_EVENTS, status="died", died=True)
    for name, id_ in (("BRIDGE_CROSSED", "trig_bridge"), ("GOAL_TOUCHED", "trig_goal")):
        r["logs"].append({"message": f"WS|{N2}|{name}|id={id_}|pos=0.00,4.00,55.00|t=1.000", "level": "output"})
    out = classify.classify_attempt(r, N1, params, area)
    assert out["outcome"] == "FELL" and [e["event"] for e in out["events"]] == [e[0] for e in FELL_EVENTS]


def test_death_mid_route_is_fell_not_success(params, area):
    # MoveToFinished reports reached = true even on death (WS-03); the outcome must not care.
    r = play_result(N1, params, FELL_EVENTS, status="died", died=True)
    out = classify.classify_attempt(r, N1, params, area)
    assert out["outcome"] == "FELL" and out["fell_in_area"] is True and out["cause"] == "UNCLASSIFIED"


def test_fell_after_crossing_but_before_goal_is_fell(params, area):
    ev = SUCCESS_EVENTS[:3] + [("KILL_FLOOR_TOUCHED", "0.00,-12.00,58.00"), ("GOAL_TOUCHED", "0.00,4.00,60.00")]
    out = classify.classify_attempt(play_result(N1, params, ev, died=True), N1, params, area)
    assert out["outcome"] == "FELL"


def test_fell_outside_gap_area_is_flagged(params, area):
    ev = FELL_EVENTS[:2] + [("KILL_FLOOR_TOUCHED", "-15.00,-12.00,24.00")]
    out = classify.classify_attempt(play_result(N1, params, ev, died=True), N1, params, area)
    assert out["outcome"] == "FELL" and out["fell_in_area"] is False


def test_gap_area_uses_the_gap_footprint_plus_margin():
    a = classify.gap_area(load_scene(F1A), "gap_1", 4)
    assert a["z"] == [32, 56] and a["x"] == [-12, 12]
    assert classify.in_area((0, -12.44, 49.2), a) and classify.in_area((0, -20, 54.5), a)
    assert not classify.in_area((0, -20, 57), a) and not classify.in_area(None, a)


def test_death_without_kill_floor_event_is_unclassified(params, area):
    r = play_result(N1, params, FELL_EVENTS[:2], status="died", died=True)
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "UNCLASSIFIED"


def test_character_removed_without_dying_is_harness_error(params, area):
    # Seen in the first F1-E smoke run: the auto-loaded character replaced the controller's.
    r = play_result(N1, params, [], status="died", removed=True)
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "HARNESS_ERROR"


def test_deadline_is_timeout_with_missing_event_tags(params, area):
    out = classify.classify_attempt(play_result(N1, params, [], status="deadline"), N1, params, area)
    assert out["outcome"] == "TIMEOUT" and out["tags"] == ["TIMEOUT", "LEVER_NOT_ACTIVATED"]
    out = classify.classify_attempt(play_result(N1, params, FELL_EVENTS[:1], status="deadline"), N1, params, area)
    assert out["tags"] == ["TIMEOUT", "GATE_NOT_OPENED"]
    out = classify.classify_attempt(play_result(N1, params, FELL_EVENTS[:2], status="deadline"), N1, params, area)
    assert out["tags"] == ["TIMEOUT"]


def test_tool_timeout_and_call_errors_are_connector_errors(params, area):
    r = play_result(N1, params, [], status="deadline", is_timeout=True)
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "CONNECTOR_ERROR"
    assert classify.classify_attempt(None, N1, params, area, call_error="timeout after 90 s")["outcome"] == "CONNECTOR_ERROR"
    assert classify.classify_attempt({"oops": 1}, N1, params, area)["outcome"] == "CONNECTOR_ERROR"


def test_params_echo_mismatch_is_harness_error(params, area):
    bad = copy.deepcopy(params)
    bad["waypoints"][6]["pos"][2] = 41
    r = play_result(N1, params, SUCCESS_EVENTS, params_override=bad)
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "HARNESS_ERROR"


def test_a_jump_breaks_the_p0_contract(params, area):
    r = play_result(N1, params, SUCCESS_EVENTS, jumps=1)
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "HARNESS_ERROR"


def test_controller_harness_error_status(params, area):
    r = play_result(N1, params, [], status="harness_error")
    assert classify.classify_attempt(r, N1, params, area)["outcome"] == "HARNESS_ERROR"


def test_captured_copy_disagreeing_is_recorded_not_used(params, area):
    r = play_result(N1, params, SUCCESS_EVENTS, captured=[])
    out = classify.classify_attempt(r, N1, params, area)
    assert out["outcome"] == "SUCCESS" and out["events_agree"] is False


# --- runner, bundle, gate -----------------------------------------------------------------

def _episode(cfg, tmp_path, role, scene, plays, nonces=(N1, N2, N3), **fake_kw):
    d = tmp_path / role
    attempts.init_episode(cfg, role, d, role, nonces=list(nonces))
    params = p0.expected_params(cfg["_route"], cfg["timing"])
    studio = FakeStudio(load_scene(scene), list(nonces), params,
                        {i: p(params, n) for i, (p, n) in enumerate(zip(plays, nonces), start=1)}, **fake_kw)
    return d, studio, attempts.run_episode(studio, cfg, d)


def fell(params, nonce):
    return play_result(nonce, params, FELL_EVENTS, status="died", died=True)


def success(params, nonce):
    return play_result(nonce, params, SUCCESS_EVENTS)


def test_episode_builds_reads_back_then_runs_n_attempts(cfg, tmp_path):
    d, studio, s = _episode(cfg, tmp_path, "before", F1A, [fell] * 3)
    tools = [t for t, _, _ in studio.log]
    assert tools[:3] == ["get_studio_mode", "run_code", "run_code"]
    assert tools[3:] == ["get_studio_mode", "run_script_in_play_mode"] * 3 + ["get_studio_mode"]
    assert s["status"] == "complete" and s["build"]["ok"] and s["build"]["readback_differences"] == 0
    assert [a["outcome"] for a in s["attempts"]] == ["FELL"] * 3
    assert attempts.validate_bundle(d) == []


def test_every_call_carries_its_declared_timeout(cfg, tmp_path):
    _, studio, _ = _episode(cfg, tmp_path, "before", F1A, [fell] * 3)
    tt = cfg["tool_timeouts_s"]
    for tool, timeout_s, arg in studio.log:
        assert timeout_s == tt[tool]
        if tool == "run_script_in_play_mode":
            assert arg == tt[tool] > cfg["timing"]["t_attempt"]


def test_readback_mismatch_is_build_failed_and_no_attempt_runs(cfg, tmp_path):
    from _m0fake import readback_text
    text = readback_text(load_scene(F1A)).replace('"pos": [0, -30', '"pos": [0, -29')
    d, studio, s = _episode(cfg, tmp_path, "before", F1A, [fell] * 3, readback=text)
    assert s["status"] == "BUILD_FAILED" and s["attempted"] == 0
    assert "run_script_in_play_mode" not in [t for t, _, _ in studio.log]


def test_build_without_ok_line_is_build_failed(cfg, tmp_path):
    _, _, s = _episode(cfg, tmp_path, "before", F1A, [fell] * 3, build_ok=False)
    assert s["status"] == "BUILD_FAILED" and s["attempted"] == 0


def test_hung_play_call_times_out_as_connector_error(cfg, tmp_path):
    hung = lambda params, nonce: attempts.CallTimeout("no answer")
    _, _, s = _episode(cfg, tmp_path, "before", F1A, [fell, hung, fell])
    assert [a["outcome"] for a in s["attempts"]] == ["FELL", "CONNECTOR_ERROR", "FELL"]


def test_studio_stuck_in_play_stops_the_episode(cfg, tmp_path):
    class Stuck(FakeStudio):
        def call(self, tool, args, timeout_s):
            if tool == "start_stop_play":
                self.log.append((tool, timeout_s, None))
                from worldsmith.runner.attempts import CallResult
                return CallResult(text="ok")
            return super().call(tool, args, timeout_s)
    d = tmp_path / "before"
    attempts.init_episode(cfg, "before", d, "before", nonces=[N1, N2, N3])
    params = p0.expected_params(cfg["_route"], cfg["timing"])
    s = attempts.run_episode(Stuck(load_scene(F1A), [N1, N2, N3], params, {}, mode="start_play"), cfg, d)
    assert s["status"] == "CONNECTOR_ERROR" and s["attempted"] == 0


def test_trace_lines_follow_spec_section_11(cfg, tmp_path):
    d, _, _ = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    lines = [json.loads(l) for l in (d / "trace.jsonl").read_text().splitlines()]
    assert len(lines) == 3 * (len(SUCCESS_EVENTS) + 1)
    for rec in lines:
        assert rec["player"]["tier"] == "P0" and rec["player"]["controller_sha"] == cfg["controller"]["sha256"]
        assert rec["player"]["t_attempt_s"] == 60 and rec["config_sha256"] == cfg["_sha256"]
        assert rec["poll_interval_ms"] == 50 and rec["max_polls"] == 1200
    outcomes = [r for r in lines if r["event"] == "SUCCESS"]
    assert len(outcomes) == 3 and all(r["tool_timeout_s"] == 90 for r in outcomes)


def test_bundle_schema_detects_tampering(cfg, tmp_path):
    d, _, _ = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    assert attempts.validate_bundle(d) == []
    s = json.loads((d / "summary.json").read_text())
    s["successes"] = 2
    (d / "summary.json").write_text(json.dumps(s))
    assert attempts.validate_bundle(d)
    (d / "controller.luau").write_text("print('other')")
    assert any("controller" in e for e in attempts.validate_bundle(d))


def test_bundle_never_overwritten(cfg, tmp_path):
    d, _, _ = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    with pytest.raises(FileExistsError):
        attempts.init_episode(cfg, "after", d, "after")


def test_gate_episodes_use_declared_scene_and_n(cfg, tmp_path):
    with pytest.raises(attempts.GateError):
        attempts.init_episode(cfg, "before", tmp_path / "x", "x", scene_path=F1E)
    with pytest.raises(attempts.GateError):
        attempts.init_episode(cfg, "after", tmp_path / "y", "y", n_attempts=2)


def test_gate_pass_needs_3_of_3_each(cfg, tmp_path):
    b, _, _ = _episode(cfg, tmp_path, "before", F1A, [fell] * 3)
    a, _, _ = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    g = attempts.evaluate_gate(cfg, b, a)
    assert g["result"] == "PASS" and g["before"]["hits"] == 3 and g["after"]["hits"] == 3
    assert g["controller_sha256"] == cfg["controller"]["sha256"]


def test_gate_2_of_3_fails_and_is_not_rounded_up(cfg, tmp_path):
    b, _, _ = _episode(cfg, tmp_path, "before", F1A, [fell] * 3)
    a, _, _ = _episode(cfg, tmp_path, "after", FIX, [success, fell, success])
    g = attempts.evaluate_gate(cfg, b, a)
    assert g["result"] == "FAIL" and g["after"]["hits"] == 2 and g["before"]["pass"] is True


def test_gate_fell_outside_gap_area_does_not_count(cfg, tmp_path):
    def fell_elsewhere(params, nonce):
        ev = FELL_EVENTS[:2] + [("KILL_FLOOR_TOUCHED", "-15.00,-12.00,24.00")]
        return play_result(nonce, params, ev, status="died", died=True)
    b, _, _ = _episode(cfg, tmp_path, "before", F1A, [fell, fell, fell_elsewhere])
    a, _, _ = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    assert attempts.evaluate_gate(cfg, b, a)["result"] == "FAIL"


def test_gate_refuses_different_controller_hashes(cfg, tmp_path):
    b, _, _ = _episode(cfg, tmp_path, "before", F1A, [fell] * 3)
    a, _, _ = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    for name in ("summary.json", "episode.json"):
        x = json.loads((a / name).read_text())
        x["controller_sha256"] = "f" * 64
        (a / name).write_text(json.dumps(x))
    with pytest.raises(attempts.GateError):
        attempts.evaluate_gate(cfg, b, a)


def test_runner_refuses_a_changed_controller(cfg, tmp_path):
    d = tmp_path / "before"
    attempts.init_episode(cfg, "before", d, "before", nonces=[N1, N2, N3])
    (d / "controller.luau").write_text(cfg["_controller_text"].replace("TICK = 0.05", "TICK = 0.1"))
    with pytest.raises(attempts.GateError):
        attempts.run_episode(FakeStudio(load_scene(F1A), [N1, N2, N3], {}, {}), cfg, d)


def test_gate_refuses_swapped_roles(cfg, tmp_path):
    b, _, _ = _episode(cfg, tmp_path, "before", F1A, [fell] * 3)
    a, _, _ = _episode(cfg, tmp_path, "after", FIX, [success] * 3)
    with pytest.raises(attempts.GateError):
        attempts.evaluate_gate(cfg, a, b)


# --- relay client -------------------------------------------------------------------------

def test_relay_stops_at_the_first_unanswered_call_and_replays(cfg, tmp_path):
    d = tmp_path / "before"
    attempts.init_episode(cfg, "before", d, "before", nonces=[N1, N2, N3])
    scene = load_scene(F1A)
    params = p0.expected_params(cfg["_route"], cfg["timing"])
    fake = FakeStudio(scene, [N1, N2, N3], params, {i: fell(params, n) for i, n in enumerate([N1, N2, N3], 1)})
    answered = 0
    while True:
        try:
            s = attempts.run_episode(attempts.RelayClient(d / "calls"), cfg, d)
            break
        except attempts.Pending as p:
            req = p.request
            args = dict(req["args"])
            if "code_file" in req:
                args[req["code_arg"]] = (d / "calls" / req["code_file"]).read_text()
            r = fake.call(req["tool"], args, req["timeout_s"])
            attempts.respond(d / "calls", p.index, r.text)
            answered += 1
            assert answered < 50
    assert s["status"] == "complete" and [a["outcome"] for a in s["attempts"]] == ["FELL"] * 3
    assert answered == 10
    assert attempts.validate_bundle(d) == []
    with pytest.raises(attempts.RelayError):
        attempts.respond(d / "calls", 1, "stop")             # answers are never replaced


def test_relay_detects_a_changed_request(cfg, tmp_path):
    d = tmp_path / "before"
    attempts.init_episode(cfg, "before", d, "before", nonces=[N1, N2, N3])
    with pytest.raises(attempts.Pending):
        attempts.run_episode(attempts.RelayClient(d / "calls"), cfg, d)
    req = json.loads((d / "calls" / "001.request.json").read_text())
    req["timeout_s"] = 1
    (d / "calls" / "001.request.json").write_text(json.dumps(req))
    with pytest.raises(attempts.RelayError):
        attempts.run_episode(attempts.RelayClient(d / "calls"), cfg, d)


def test_runner_template_is_package_data():
    import importlib.resources
    assert importlib.resources.files("worldsmith.runner").joinpath("templates", "p0_controller.luau").is_file()


# --- the recorded live M0 run (bundles/m0, committed evidence) ----------------------------

M0_DIR = REPO_ROOT / "bundles" / "m0"


@pytest.mark.skipif(not (M0_DIR / "gate.json").exists(), reason="no recorded M0 bundles")
def test_recorded_m0_bundles_validate_and_reproduce_the_gate(cfg):
    for ep in ("F1-A", "F1-A-fix", "smoke-F1-E", "smoke2-F1-E"):
        assert attempts.validate_bundle(M0_DIR / ep) == [], ep
    recorded = json.loads((M0_DIR / "gate.json").read_text())
    again = attempts.evaluate_gate(cfg, M0_DIR / "F1-A", M0_DIR / "F1-A-fix")
    assert again == recorded
    doc = (REPO_ROOT / "docs" / "M0.md").read_text()
    assert f"**Result: {recorded['result']}**" in doc
    assert recorded["controller_sha256"] in doc and recorded["config_sha256"] in doc


@pytest.mark.skipif(not (M0_DIR / "gate.json").exists(), reason="no recorded M0 bundles")
def test_recorded_m0_replays_from_raw_answers(cfg, tmp_path):
    # Re-run both episodes from their stored raw answers: the same summaries come out.
    for ep in ("F1-A", "F1-A-fix"):
        d = tmp_path / ep
        shutil.copytree(M0_DIR / ep, d)
        for f in ("summary.json", "trace.jsonl", "calls.json"):
            (d / f).unlink()
        s = attempts.run_episode(attempts.RelayClient(d / "calls"), cfg, d)
        assert s == json.loads((M0_DIR / ep / "summary.json").read_text())


@pytest.mark.skipif(not (M0_DIR / "manifest.json").exists(), reason="no recorded M0 bundles")
def test_recorded_m0_manifest_hashes(cfg):
    import hashlib
    m = json.loads((M0_DIR / "manifest.json").read_text())
    for rel, sha in m["files"].items():
        assert hashlib.sha256((M0_DIR / rel).read_bytes()).hexdigest() == sha, rel
