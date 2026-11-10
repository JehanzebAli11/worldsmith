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
