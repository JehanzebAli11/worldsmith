"""Harness guards after the smoke_f1e_p0v1 debug (docs/notes/smoke_f1e_p0v1_debug.md, ADR 0011).

CPU only: recorded relay files, synthetic play results and the simulated operator (shape, not
physics, not Roblox). No Studio, no network, no key.

Part 1 (this file's first half):
- a bare `null` play answer is CONNECTOR_ERROR and the episode stops there (no more attempts);
- an attempt whose recorded start_pos is more than 2 studs (XZ) from the spawn centre is
  HARNESS_ERROR, never FELL or SUCCESS, for every player version; every recorded M0 attempt
  started on the spawn, so the M0 bundles reproduce unchanged;
- the partial smoke_f1e_p0v1 bundle, answered with `null` at call 009, closes as CONNECTOR_ERROR
  with attempt 1 HARNESS_ERROR (moved before the route).
"""

import json
import math
import shutil

import pytest

from _repo import REPO_ROOT
from _ws08fake import result
from _ws11sim import Direct, ScriptedOperator
from _m0fake import FELL_EVENTS, SUCCESS_EVENTS
from worldsmith.grammar.scene import load_scene
from worldsmith.runner import attempts as at
from worldsmith.runner import classify, taxonomy
from worldsmith.runner import episode as epi
from worldsmith.runner import experiment as ex

SMOKE1 = REPO_ROOT / "config" / "experiments" / "smoke_f1e_p0v1.yaml"
SMOKE1_BUNDLE = REPO_ROOT / "bundles" / "experiments" / "smoke_f1e_p0v1"
NULL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "play_answer_null.txt"
F1A = REPO_ROOT / "scenes" / "F1-A" / "scene.json"
F1E = REPO_ROOT / "scenes" / "F1-E" / "scene.json"
N1 = "a1a1a1a1a1a1a1a1"


@pytest.fixture(scope="module")
def smoke1():
    return ex.load_experiment(SMOKE1, REPO_ROOT)


@pytest.fixture(scope="module")
def stuck():
    return taxonomy.load_classifier_config(REPO_ROOT / "config" / "classifier.yaml")["stuck"]


# --------------------------------------------------------------------------- null answer

class NullAt(ScriptedOperator):
    """Answers play call number `k` (1-based, whole run) with the recorded bare `null`."""

    def __init__(self, cfg, k, **kw):
        super().__init__(cfg, **kw)
        self.k = k

    def answer_code(self, ep_dir, tool, code):
        if tool == "run_script_in_play_mode" and self.play_calls + 1 == self.k:
            self.play_calls += 1
            self.plays[str(ep_dir)] = self.plays.get(str(ep_dir), 0) + 1
            return NULL_FIXTURE.read_text(encoding="utf-8")
        return super().answer_code(ep_dir, tool, code)


def test_null_fixture_is_the_bare_null():
    assert NULL_FIXTURE.read_text(encoding="utf-8") == "null"
    assert json.loads(NULL_FIXTURE.read_text(encoding="utf-8")) is None


def test_bare_null_is_connector_error(stuck):
    area = classify.gap_area(load_scene(F1E), "gap_1", 4)
    out = taxonomy.classify_result(json.loads("null"), N1, {}, area, stuck)
    assert out["outcome"] == "CONNECTOR_ERROR"
    assert out["detail"] == "tool result is not a run_script_in_play_mode result"


def test_null_play_answer_stops_the_episode(smoke1, tmp_path):
    d = tmp_path / "B0" / "ep0"
    epi.init_episode(smoke1, "B0", 0, d)
    op = NullAt(smoke1, 2, outcomes={1: ["SUCCESS", "SUCCESS", "SUCCESS"]})
    client = Direct(op, d)
    s = epi.run_episode(client, smoke1, d)
    assert s["status"] == "CONNECTOR_ERROR"
    assert s["versions"][0]["outcomes"] == ["SUCCESS", "CONNECTOR_ERROR"]
    assert s["attempts_used"] == 2 and op.play_calls == 2
    assert client.log.count("run_script_in_play_mode") == 2
    assert client.log[-1] == "run_script_in_play_mode"          # nothing is called after the null
    row = json.loads((d / "v1" / "attempts" / "2.json").read_text())
    assert row["outcome"] == "CONNECTOR_ERROR" and (d / "v1" / "attempts" / "2.raw.txt").read_text() == "null"
    assert epi.validate_episode_bundle(d, smoke1) == []


# --------------------------------------------------------------------------- start_pos rule

def test_spawn_centre_is_read_from_the_scene():
    assert classify.spawn_centre(load_scene(F1A)) == (0.0, 6.0)
    assert classify.spawn_centre(load_scene(F1E)) == (0.0, 6.0)
    assert classify.START_POS_MAX_STUDS == 2


@pytest.mark.parametrize("start_pos,events,want", [
    ("0.00,4.51,0.75", FELL_EVENTS, "HARNESS_ERROR"),        # smoke_f1e_p0v1 attempt 1 (5.25 studs)
    ("0.00,4.51,0.75", SUCCESS_EVENTS, "HARNESS_ERROR"),
    ("2.10,3.00,6.00", SUCCESS_EVENTS, "HARNESS_ERROR"),
    ("0.00,3.20,6.00", FELL_EVENTS, "FELL"),                 # every M0 attempt
    ("0.00,3.00,8.00", SUCCESS_EVENTS, "SUCCESS"),           # exactly 2 studs: allowed
    ("1.40,3.00,7.40", SUCCESS_EVENTS, "SUCCESS"),           # 1.98 studs
    ("na", FELL_EVENTS, "FELL"),                             # no route start recorded: rule not applicable
])
def test_start_pos_far_from_the_spawn_is_harness_error(start_pos, events, want, stuck):
    scene = load_scene(F1A)
    area = classify.gap_area(scene, "gap_1", 4)
    params = {"x": 1}
    r = result(N1, params, events, start_pos=start_pos, status="died" if events is FELL_EVENTS else "route_done",
               died=events is FELL_EVENTS)
    spawn = classify.spawn_centre(scene)
    out = classify.classify_attempt(r, N1, params, area, spawn_xz=spawn)
    assert out["outcome"] == want, out["detail"]
    if want == "HARNESS_ERROR":
        assert out["detail"].startswith("moved_before_route: start_pos")
    assert taxonomy.classify_result(r, N1, params, area, stuck, spawn_xz=spawn)["outcome"] == want


def test_start_pos_rule_is_off_without_a_spawn(stuck):
    area = classify.gap_area(load_scene(F1A), "gap_1", 4)
    r = result(N1, {"x": 1}, FELL_EVENTS, start_pos="0.00,4.51,0.75", status="died", died=True)
    assert classify.classify_attempt(r, N1, {"x": 1}, area)["outcome"] == "FELL"


def _m0_attempts():
    out = []
    for f in sorted((REPO_ROOT / "bundles" / "m0").glob("*/attempts/*.json")):
        out.append((f, json.loads(f.read_text())))
    return out


def test_every_recorded_m0_attempt_started_on_the_spawn():
    rows = _m0_attempts()
    assert len(rows) == 8
    for f, row in rows:
        x, _, z = (float(c) for c in row["report"]["start_pos"].split(","))
        assert (x, z) == (0.0, 6.0), f
        assert math.hypot(x, z - 6.0) <= classify.START_POS_MAX_STUDS


class FarStart(ScriptedOperator):
    def play(self, scene, nonce, i, outcome=None):
        r = super().play(scene, nonce, i, outcome)
        return _with_start(r, nonce, "0.00,4.51,0.75") if i == 1 else r


def _with_start(r, nonce, pos):
    msgs = [m["message"] for m in r["logs"]]
    chunks = [m for m in msgs if m.startswith(f"WSP|{nonce}|RESULT|")]
    rep = json.loads("".join(c.split("|", 5)[5] for c in chunks))
    rep["start_pos"] = pos
    s = json.dumps(rep)
    n = -(-len(s) // 800)
    keep = [m for m in msgs if not m.startswith(f"WSP|{nonce}|RESULT|")]
    keep += [f"WSP|{nonce}|RESULT|{j + 1}|{n}|{s[j * 800:(j + 1) * 800]}" for j in range(n)]
    return {**r, "logs": [{"message": m, "ts": 1.0, "level": "output"} for m in keep]}


def test_episode_applies_the_start_pos_rule(smoke1, tmp_path):
    d = tmp_path / "B0" / "ep0"
    epi.init_episode(smoke1, "B0", 0, d)
    s = epi.run_episode(Direct(FarStart(smoke1), d), smoke1, d)
    assert s["versions"][0]["outcomes"] == ["HARNESS_ERROR", "SUCCESS", "SUCCESS"]
    assert s["status"] == "SYSTEM_ERROR"


# --------------------------------------------------------------------------- the partial smoke bundle

def _copy_partial(tmp_path):
    """The recorded smoke_f1e_p0v1 relay files up to call 009 (request only), in a temp folder."""
    out = tmp_path / "experiments" / "smoke_f1e_p0v1"
    (out / "B0" / "ep0" / "calls").mkdir(parents=True)
    for f in ("config.yaml", "controller.luau", "experiment.lock.json"):
        shutil.copyfile(SMOKE1_BUNDLE / f, out / f)
    src = SMOKE1_BUNDLE / "B0" / "ep0"
    for f in ("episode.json", "controller.luau", "v1/scene.json"):
        (out / "B0" / "ep0" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / f, out / "B0" / "ep0" / f)
    for f in sorted((src / "calls").iterdir()):
        if f.name.startswith("009.response"):
            continue
        shutil.copyfile(f, out / "B0" / "ep0" / "calls" / f.name)
    return out


def test_partial_smoke_bundle_closes_as_connector_error(smoke1, tmp_path):
    out = _copy_partial(tmp_path)
    exp = ex.Experiment(smoke1, tmp_path / "experiments")
    with pytest.raises(at.Pending) as p:
        exp.run(lambda d: at.RelayClient(d / "calls"))
    assert p.value.index == 9 and p.value.request["tool"] == "run_script_in_play_mode"
    at.respond(out / "B0" / "ep0" / "calls", 9, NULL_FIXTURE.read_text(encoding="utf-8"))
    res = exp.run(lambda d: at.RelayClient(d / "calls"))
    s = json.loads((out / "B0" / "ep0" / "summary.json").read_text())
    assert s["status"] == "CONNECTOR_ERROR"
    assert s["versions"][0]["outcomes"] == ["HARNESS_ERROR", "SUCCESS", "CONNECTOR_ERROR"]
    assert s["attempts_used"] == 3 and s["revisions"]["used"] == 0
    assert not (out / "B0" / "ep0" / "calls" / "010.request.json").exists()
    row1 = json.loads((out / "B0" / "ep0" / "v1" / "attempts" / "1.json").read_text())
    assert row1["report"]["start_pos"] == "0.00,4.51,0.75" and "5.25" in row1["detail"]
    assert res["conditions"][0]["statuses"] == ["CONNECTOR_ERROR"]
    assert epi.validate_episode_bundle(out / "B0" / "ep0", smoke1) == []


# =========================================================================== part 2
# Preflight (before every build), player p0.v2 (spawn check), the new smoke and the pilot config.

from worldsmith.runner import p0, players  # noqa: E402
from worldsmith.runner import preflight as pf  # noqa: E402
from worldsmith.runner import dryrun  # noqa: E402

SMOKE2 = REPO_ROOT / "config" / "experiments" / "smoke_f1e_p0v2.yaml"
M2 = REPO_ROOT / "config" / "experiments" / "m2_f1a.yaml"
PREFLIGHT_TPL = REPO_ROOT / "runner" / "templates" / "preflight_v1.luau"
V0_SHA = "3743d998e900729eaec583dfcae9f584651184a66e133eb14944045aee18c034"
V1_SHA = "dbc1f325ce64bda8b046bc4f08a18050cced76a123b843af5937b85b65754c6d"
F1E_HASH = "f8a8e654274354e33b340da77235f6b27ea0aa01fcdd55235fd2c98d45df0db3"


@pytest.fixture(scope="module")
def smoke2():
    return ex.load_experiment(SMOKE2, REPO_ROOT)


@pytest.fixture(scope="module")
def m2():
    return ex.load_experiment(M2, REPO_ROOT)


# --------------------------------------------------------------------------- preflight checks

def test_preflight_check_list():
    assert pf.CHECKS == ("studio_mode_stop", "place_marker", "load_character_appearance_off",
                         "character_auto_loads_on", "no_starter_character", "one_spawn_outside_worldsmith",
                         "no_foreign_scripts")
    assert pf.MARK_PLACE_LUAU == 'workspace:SetAttribute("ws_place", "worldsmith-lab")'


def test_preflight_passes_on_the_worldsmith_lab_probe():
    checks = pf.evaluate("stop", pf.simulated_probe())
    assert [c["check"] for c in checks] == list(pf.CHECKS) and all(c["ok"] for c in checks)


@pytest.mark.parametrize("over,failing", [
    ({"ws_place": "<none>"}, "place_marker"),
    ({"ws_place": "autopilot-testplace"}, "place_marker"),
    ({"load_character_appearance": True}, "load_character_appearance_off"),
    ({"character_auto_loads": False}, "character_auto_loads_on"),
    ({"starter_character": True}, "no_starter_character"),
    ({"spawns_outside": 2}, "one_spawn_outside_worldsmith"),
    ({"spawns_outside": 0}, "one_spawn_outside_worldsmith"),
    ({"foreign_scripts": 1, "foreign_scripts_list": [{"path": "ServerScriptService.Game", "class": "Script"}]},
     "no_foreign_scripts"),
    ({"is_running": True}, "studio_mode_stop"),
])
def test_each_preflight_check_fails_alone(over, failing):
    checks = pf.evaluate("stop", pf.simulated_probe(**over))
    assert [c["check"] for c in checks if not c["ok"]] == [failing]


def test_the_autopilot_test_place_fails_the_preflight():
    """The place the smoke actually ran in (debug notes): no marker, avatar on, a second spawn,
    Autopilot's scripts."""
    probe = pf.simulated_probe(
        game_name="testplace.rbxl", ws_place="<none>", load_character_appearance=True, spawns_outside=2,
        foreign_scripts=5, foreign_scripts_list=[{"path": f"ServerScriptService.{n}", "class": "Script"}
                                                 for n in ("Build", "Game", "Debug")]
        + [{"path": "StarterGui.Hud.Client", "class": "LocalScript"},
           {"path": "StarterPlayer.StarterPlayerScripts.Hud", "class": "LocalScript"}])
    failed = {c["check"] for c in pf.evaluate("stop", probe) if not c["ok"]}
    assert failed == {"place_marker", "load_character_appearance_off", "one_spawn_outside_worldsmith",
                      "no_foreign_scripts"}


class _Calls:
    def __init__(self, answers):
        self.answers, self.log = list(answers), []

    def __call__(self, tool, args, timeout_s):
        self.log.append(tool)
        text, err = self.answers.pop(0)
        return at.CallResult(text=text, error=err)


TT = {"get_studio_mode": 15, "run_code": 30}


def _probe_text(**over):
    return "[OUTPUT] WS_PREFLIGHT|" + json.dumps(pf.simulated_probe(**over))


def test_preflight_in_play_mode_sends_no_probe_and_stops_nothing():
    call = _Calls([("start", None)])
    rec = pf.run(call, "-- probe", TT)
    assert call.log == ["get_studio_mode"]
    assert rec["status"] == "HARNESS_ERROR" and not rec["ok"] and rec["probe"] is None
    assert "probe not sent, nothing stopped" in rec["detail"]


def test_preflight_pass_and_record():
    call = _Calls([("stop", None), (_probe_text(), None)])
    rec = pf.run(call, "-- probe", TT)
    assert call.log == ["get_studio_mode", "run_code"]
    assert rec["ok"] and rec["status"] == "pass" and rec["schema"] == pf.RECORD_SCHEMA
    assert rec["script_sha256"] == p0.sha256_text("-- probe")


def test_preflight_unreadable_probe_is_harness_error_and_errors_are_connector_errors():
    assert pf.run(_Calls([("stop", None), ("[OUTPUT] nothing", None)]), "x", TT)["status"] == "HARNESS_ERROR"
    two = _probe_text() + "\n" + _probe_text()
    assert pf.run(_Calls([("stop", None), (two, None)]), "x", TT)["status"] == "HARNESS_ERROR"
    assert pf.run(_Calls([("", "timeout after 15 s")]), "x", TT)["status"] == "CONNECTOR_ERROR"
    assert pf.run(_Calls([("stop", None), ("", "boom")]), "x", TT)["status"] == "CONNECTOR_ERROR"


def test_preflight_template_is_read_only_package_data():
    from importlib import resources
    assert resources.files("worldsmith.runner").joinpath("templates", "preflight_v1.luau").is_file()
    text = PREFLIGHT_TPL.read_text(encoding="utf-8")
    assert text.isascii() and "{{" not in text
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
    for banned in ("SetAttribute", "Destroy", "Instance.new", ":Clone", "Remove", ".Parent =", "LoadCharacter(", ":LoadCharacter",
                   "run_code", "SetPrimaryPartCFrame", "PivotTo", "require("):
        assert banned not in code, banned
    assert code.count('print("WS_PREFLIGHT|"') == 1


def test_mark_place_script_prints_the_payload(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mark_place", REPO_ROOT / "scripts" / "mark_place.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([]) == 0
    out = capsys.readouterr().out
    cmd = json.loads(out)["command"]
    assert cmd == pf.mark_place_command() and cmd.count(pf.MARK_PLACE_LUAU) == 1
    assert "IsRunning" in cmd


# --------------------------------------------------------------------------- preflight in episodes

class ProbeOp(ScriptedOperator):
    """Answers the preflight probe with `probe_over` applied (None = passing); records tools."""

    def __init__(self, cfg, probe_over=None, mode="stop", **kw):
        super().__init__(cfg, **kw)
        self.probe_over, self.mode, self.tools = probe_over or {}, mode, []

    def answer_code(self, ep_dir, tool, code):
        kind = tool
        if tool == "run_code":
            kind = "probe" if "WS_PREFLIGHT" in code else "build" if "WS_BUILD_OK" in code else "readback"
        self.tools.append(kind)
        if kind == "probe":
            return _probe_text(**self.probe_over)
        if tool == "get_studio_mode":
            return self.mode
        return super().answer_code(ep_dir, tool, code)


def _run(cfg, cond, tmp_path, op, architect=None):
    d = tmp_path / cond / "ep0"
    epi.init_episode(cfg, cond, 0, d)
    s = epi.run_episode(Direct(op, d), cfg, d, architect_factory=(lambda: architect) if architect else None)
    return s, d


def test_preflight_runs_before_every_build_and_is_recorded(m2, tmp_path):
    op = ProbeOp(m2)
    s, d = _run(m2, "B1", tmp_path, op)
    assert s["status"] == "complete" and s["repaired"] and len(s["versions"]) == 2
    builds = [i for i, t in enumerate(op.tools) if t == "build"]
    assert len(builds) == 2
    for b in builds:            # preflight (mode, probe), then the usual stop check, then the build
        assert op.tools[b - 3:b] == ["get_studio_mode", "probe", "get_studio_mode"]
    assert op.tools.count("probe") == 2
    for v in ("v1", "v2"):
        rec = json.loads((d / v / "preflight.json").read_text())
        assert rec["ok"] and rec["status"] == "pass" and rec["script_sha256"] == m2["preflight"]["sha256"]
    assert epi.validate_episode_bundle(d, m2) == []


def test_failed_preflight_stops_before_the_build(m2, tmp_path):
    from _ws11sim import SeqArchitect, set_len
    arch = SeqArchitect([set_len(16)])
    op = ProbeOp(m2, probe_over={"ws_place": "<none>", "foreign_scripts": 3})
    s, d = _run(m2, "B3-haiku", tmp_path, op, architect=arch)
    assert s["status"] == "HARNESS_ERROR"
    assert s["attempts_used"] == 0 and s["architect_calls"] == 0 and arch.calls == 0 and s["revisions"]["used"] == 0
    assert op.tools == ["get_studio_mode", "probe"]               # no build, no stop, no play
    assert "place_marker" in s["detail"] and "no_foreign_scripts" in s["detail"]
    rec = json.loads((d / "v1" / "preflight.json").read_text())
    assert rec["status"] == "HARNESS_ERROR" and not rec["ok"]
    assert epi.validate_episode_bundle(d, m2) == []


def test_preflight_never_stops_a_running_session(m2, tmp_path):
    op = ProbeOp(m2, mode="start")
    s, d = _run(m2, "B0", tmp_path, op)
    assert s["status"] == "HARNESS_ERROR" and op.tools == ["get_studio_mode"]
    assert "start_stop_play" not in op.tools


def test_no_preflight_without_the_config_key(smoke1, tmp_path):
    op = ProbeOp(smoke1)
    s, d = _run(smoke1, "B0", tmp_path, op)
    assert "probe" not in op.tools and not (d / "v1" / "preflight.json").exists()
    assert "preflight" not in smoke1 or smoke1.get("preflight") is None


def test_preflight_config_hash_is_checked(tmp_path):
    import yaml
    doc = yaml.safe_load(SMOKE2.read_text())
    doc["preflight"]["sha256"] = "0" * 64
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    with pytest.raises(ex.ExperimentError, match="preflight"):
        ex.load_experiment(p, REPO_ROOT)


# --------------------------------------------------------------------------- player p0.v2

def test_p0_v2_is_declared_with_its_own_hash(smoke2, m2):
    for cfg in (smoke2, m2):
        assert cfg["player"]["version"] == "p0.v2"
        assert cfg["player"]["template"] == "runner/templates/p0_controller_v2.luau"
        assert players.controller_sha256(cfg["_controller_text"]) == cfg["player"]["controller_sha256"]
    assert smoke2["player"]["controller_sha256"] == m2["player"]["controller_sha256"]
    assert smoke2["player"]["controller_sha256"] not in (V0_SHA, V1_SHA)
    assert players.get("p0.v2").timing_keys == players.get("p0.v1").timing_keys
    assert smoke2["player"]["timing"]["sample_every"] == 1


def test_p0_v0_and_v1_are_untouched(smoke1):
    from worldsmith.runner.config import load_config
    m0 = load_config(REPO_ROOT / "config" / "m0.yaml", REPO_ROOT)
    assert players.controller_sha256(players.render_controller("p0.v0", m0["_route"], m0["timing"])) == V0_SHA
    assert players.controller_sha256(smoke1["_controller_text"]) == V1_SHA


def test_p0_v2_template_only_adds_the_spawn_check():
    import difflib
    v1 = (REPO_ROOT / "runner" / "templates" / "p0_controller_v1.luau").read_text().splitlines()
    v2 = (REPO_ROOT / "runner" / "templates" / "p0_controller_v2.luau").read_text().splitlines()
    removed = [l[2:] for l in difflib.ndiff(v1, v2) if l.startswith("- ")]
    # the header comment, and the settle loop now also ends on a death (then died_before_route)
    assert removed == v1[:3] + ["while os.clock() - t_settle < 1 and os.clock() < deadline do"]
    added = [l[2:] for l in difflib.ndiff(v1, v2) if l.startswith("+ ")]
    code = "\n".join(l for l in added if not l.lstrip().startswith("--"))
    for banned in ("MoveTo", "Jump", "CFrame", "PivotTo", "WalkSpeed", "Gravity", "LoadCharacter", "Teleport",
                   "Anchored", ".Position ="):
        assert banned not in code, banned
    for want in ('"moved_before_route', "spawn_offset", "settle_drift", "MAX_OFFSET = 1", "FloorMaterial",
                 '"died_before_route"'):
        assert want in code, want
    text = "\n".join(v2)
    assert text.count("hum:MoveTo(") == 1
    # the spawn check comes before the route starts
    assert text.index("moved_before_route") < text.index("route_started = true") < text.index("hum:MoveTo(")


def _with_report(r, nonce, **kv):
    msgs = [m["message"] for m in r["logs"]]
    chunks = [m for m in msgs if m.startswith(f"WSP|{nonce}|RESULT|")]
    rep = json.loads("".join(c.split("|", 5)[5] for c in chunks))
    rep.update(kv)
    s = json.dumps(rep)
    n = -(-len(s) // 800)
    keep = [m for m in msgs if not m.startswith(f"WSP|{nonce}|RESULT|")]
    keep += [f"WSP|{nonce}|RESULT|{j + 1}|{n}|{s[j * 800:(j + 1) * 800]}" for j in range(n)]
    return {**r, "logs": [{"message": m, "ts": 1.0, "level": "output"} for m in keep]}


def test_moved_before_route_is_harness_error_with_the_offsets(stuck):
    scene = load_scene(F1E)
    area = classify.gap_area(scene, "gap_1", 4)
    params = {"x": 1}
    detail = "moved_before_route: spawn_offset=0.20 settle_drift=1.60 settle_max_offset=1.75 grounded=true"
    r = _with_report(result(N1, params, [], status="harness_error", start_pos="0.00,4.51,4.25"), N1,
                     detail=detail, spawn_pos="0.00,0.50,6.00", spawn_offset=0.2, settle_drift=1.6,
                     settle_max_offset=1.75, t_route_start=-1)
    out = taxonomy.classify_result(r, N1, params, area, stuck, spawn_xz=classify.spawn_centre(scene))
    assert out["outcome"] == "HARNESS_ERROR" and out["detail"] == detail
    assert out["report"]["spawn_offset"] == 0.2 and out["report"]["settle_drift"] == 1.6
    assert out["report"]["settle_max_offset"] == 1.75 and out["report"]["spawn_pos"] == "0.00,0.50,6.00"


# --------------------------------------------------------------------------- configs

def test_smoke_f1e_p0v2_config(smoke2):
    assert smoke2["experiment"] == "smoke_f1e_p0v2" and smoke2["scene"]["canonical_hash"] == F1E_HASH
    assert list(smoke2["_conditions"]) == ["B0"] and smoke2["_conditions"]["B0"]["episodes"] == 1
    assert smoke2["n_attempts"] == 3 and smoke2["pass_k"] == 3
    assert smoke2["preflight"] == {"script": "runner/templates/preflight_v1.luau",
                                   "sha256": p0.sha256_text(PREFLIGHT_TPL.read_text(encoding="utf-8"))}
    assert not (REPO_ROOT / "bundles" / "experiments" / "smoke_f1e_p0v2").exists()


def test_m2_pilot_uses_p0v2_and_the_preflight_and_never_ran(m2):
    assert m2["experiment"] == "m2_f1a_pilot" and m2["preflight"]["script"] == "runner/templates/preflight_v1.luau"
    assert m2["player"]["version"] == "p0.v2"
    assert not (REPO_ROOT / "bundles" / "experiments" / "m2_f1a_pilot" / "experiment.lock.json").exists()


def test_smoke_p0v1_config_still_matches_its_lock(smoke1):
    lock = json.loads((SMOKE1_BUNDLE / "experiment.lock.json").read_text())
    assert lock["config_sha256"] == smoke1["_sha256"]


def test_dry_run_with_preflight(smoke2, tmp_path):
    res = dryrun.run_dry(smoke2, tmp_path)
    d = tmp_path / "smoke_f1e_p0v2" / "B0" / "ep0"
    s = json.loads((d / "summary.json").read_text())
    assert s["status"] == "complete" and s["versions"][0]["outcomes"] == ["SUCCESS"] * 3
    assert json.loads((d / "v1" / "preflight.json").read_text())["ok"] is True
    assert res["conditions"][0]["statuses"] == ["complete"]
