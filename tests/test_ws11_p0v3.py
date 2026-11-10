"""Closing the m2_f1a pilot, player p0.v3 (physics-counted spawn check) and the configs declared
before its runs (ADR 0012).

CPU only: copies of recorded relay files, synthetic play results and the simulated operator
(shape, not physics, not Roblox). No Studio, no network, no key. The CLI tests run against a
COPY of the pilot bundle in tmp_path, never the real experiment folder.
"""

import dataclasses
import difflib
import hashlib
import json
import re
import shutil
import sys

import pytest
import yaml

from _repo import REPO_ROOT
from _ws08fake import result
from _ws11sim import Direct, ScriptedOperator
from worldsmith.compiler.templates import TemplateError
from worldsmith.grammar.scene import load_scene
from worldsmith.runner import classify, dryrun, p0, players, taxonomy
from worldsmith.runner import episode as epi
from worldsmith.runner import experiment as ex

JZLAB = REPO_ROOT.parent / "JzLab"
try:
    import jzapi
except ImportError:
    if (JZLAB / "jzapi" / "__init__.py").is_file():
        sys.path.insert(0, str(JZLAB))
        import jzapi
    else:
        jzapi = None
needs_jzapi = pytest.mark.skipif(jzapi is None, reason="jzapi is not installed and JzLab is not beside worldsmith")

TEMPLATES = REPO_ROOT / "runner" / "templates"
EXPERIMENTS = REPO_ROOT / "config" / "experiments"
PILOT = REPO_ROOT / "bundles" / "experiments" / "m2_f1a_pilot"
M2 = EXPERIMENTS / "m2_f1a.yaml"
SMOKE2 = EXPERIMENTS / "smoke_f1e_p0v2.yaml"
SMOKE3 = EXPERIMENTS / "smoke_f1e_p0v3.yaml"
B3 = EXPERIMENTS / "m2_f1a_b3.yaml"
F1E = REPO_ROOT / "scenes" / "F1-E" / "scene.json"
F1E_HASH = "f8a8e654274354e33b340da77235f6b27ea0aa01fcdd55235fd2c98d45df0db3"
F1A_HASH = "92f866098bfb850cf4c464179887a862f305c143c8e0b38efdc7978493cddb1d"

# Template files, pinned in the player registry (SHA-256 of the file's bytes).
TEMPLATE_SHA = {
    "p0.v0": "c246c3f6773adc23db2aed23a8f21f7676a41821cbe03f23de6ed4468d00dd12",
    "p0.v1": "91748051fea911731353e2dcef5cac0b9f288a929908080cde0bd7f731cc145e",
    "p0.v2": "7eeff15ee5222a1027c5eebe2704737ef88bbf24be336380312566c5f0f5ff09",
    "p0.v3": "58ca4bce6abd0a4315194a05544bfea05fad9575449fb0c7d254f7d3540c66d6",
}
# Rendered controllers (route b9514a4b..., t_attempt 60, t_waypoint 10, settle 2, tick 0.05, sample_every 1).
V2_SHA = "a7472c9cbbea9096f678be02a663ffb503cb2619eb9ea1fe136fcbf2a7858cac"
V3_SHA = "cd9a7a0a21ccd32ea7e14990f17b7bba58083f1bfeb2a81e77cf2e122a2d0f8c"
N1 = "a1a1a1a1a1a1a1a1"


@pytest.fixture(scope="module")
def m2():
    return ex.load_experiment(M2, REPO_ROOT)


@pytest.fixture(scope="module")
def smoke3():
    return ex.load_experiment(SMOKE3, REPO_ROOT)


@pytest.fixture(scope="module")
def b3():
    return ex.load_experiment(B3, REPO_ROOT)


@pytest.fixture(scope="module")
def stuck():
    return taxonomy.load_classifier_config(REPO_ROOT / "config" / "classifier.yaml")["stuck"]


def _cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_experiment", REPO_ROOT / "scripts" / "run_experiment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _switch_files(tmp_path):
    b = tmp_path / "budgets.yaml"
    b.write_text(yaml.safe_dump({"total_usd": 400, "jobs": {"architect_ladder": {"cap_usd": 90, "approved": True}}}))
    a = tmp_path / "architect.yaml"
    a.write_text(yaml.safe_dump({"schema": "worldsmith.architect_local/0.1", "live": True}))
    return ["--budgets", str(b), "--architect-config", str(a)]


def _snapshot(d):
    return {str(f.relative_to(d)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.rglob("*")) if f.is_file()}


# =========================================================================== 1. the pilot is closed

def _reply_costs():
    return [json.loads(f.read_text())["cost_usd"] for f in sorted(PILOT.glob("*/ep*/architect/replies/*.json"))
            if not f.name.endswith(".error.json")]


def test_pilot_closed_md_states_the_decision_and_the_recorded_outcomes():
    text = (PILOT / "CLOSED.md").read_text(encoding="utf-8")
    assert "2026-11-10" in text and "(A)" in text
    for cond in ("B0", "B1", "B2", "B3-haiku"):
        s = json.loads((PILOT / cond / "ep0" / "summary.json").read_text())
        assert cond in text and s["status"] in text
        for v in s["versions"]:
            assert ", ".join(v["outcomes"]) in text, (cond, v["outcomes"])
    assert "B3-sonnet" in text and "B3-opus" in text and "not run" in text
    assert "SYSTEM_ERROR" in text and "HARNESS_ERROR" in text
    # the tick numbers re-read from the attempt raw files
    for ticks, secs in (("5", "1.54"), ("6", "2.40"), ("6", "1.33")):
        assert re.search(rf"\b{ticks} ticks in {re.escape(secs)} s", text), (ticks, secs)
    # architect spend: the sum of the stored replies' cost_usd, with n
    costs = _reply_costs()
    assert len(costs) == 2
    assert f"${sum(costs):.6f}" in text and "n = 2" in text
    assert "m2_f1a_b3" in text


def test_the_real_pilot_folder_is_closed_and_untouched(m2):
    exp = ex.Experiment(m2, REPO_ROOT / "bundles" / "experiments")
    assert exp.closed()
    lock = json.loads((PILOT / "experiment.lock.json").read_text())
    assert lock["config_sha256"] == m2["_sha256"]                   # m2_f1a.yaml unchanged
    assert not (PILOT / "results.json").exists()
    assert not (PILOT / "B3-opus").exists()
    assert not (PILOT / "B3-sonnet" / "ep0" / "calls" / "010.response.json").exists()


@pytest.fixture()
def pilot_copy(tmp_path):
    out = tmp_path / "experiments"
    shutil.copytree(PILOT, out / "m2_f1a_pilot")
    if not (out / "m2_f1a_pilot" / "CLOSED.md").exists():      # the real file is added by this change
        (out / "m2_f1a_pilot" / "CLOSED.md").write_text("closed (test copy)\n")
    return out


@pytest.mark.parametrize("what", [["step"], ["step", "--architect-elsewhere"], ["architect"], ["respond", "10", "ANSWER"]])
def test_cli_refuses_step_respond_architect_on_a_closed_experiment(what, pilot_copy, tmp_path, capsys):
    answer = tmp_path / "answer.txt"
    answer.write_text("stop")
    what = [str(answer) if w == "ANSWER" else w for w in what]
    before = _snapshot(pilot_copy)
    rc = _cli().main(["--config", str(M2), *what, "--out", str(pilot_copy), *_switch_files(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 4
    assert "closed" in err and "CLOSED.md" in err
    assert _snapshot(pilot_copy) == before                          # nothing written, nothing sent


def test_cli_status_and_plan_still_work_on_a_closed_experiment(pilot_copy, tmp_path, capsys):
    assert _cli().main(["--config", str(M2), "status", "--out", str(pilot_copy)]) == 0
    out = capsys.readouterr().out
    assert "closed" in out and "B3-opus" in out


def test_experiment_run_refuses_a_closed_experiment_and_sends_nothing(tmp_path):
    cfg = ex.load_experiment(SMOKE2, REPO_ROOT)
    exp = ex.Experiment(cfg, tmp_path)
    exp.dir.mkdir(parents=True)
    (exp.dir / "CLOSED.md").write_text("closed\n")
    called = []
    with pytest.raises(ex.ExperimentClosed, match="CLOSED.md"):
        exp.run(lambda d: called.append(d))
    assert called == [] and sorted(p.name for p in exp.dir.iterdir()) == ["CLOSED.md"]
    assert issubclass(ex.ExperimentClosed, ex.ExperimentError)


def test_an_open_experiment_is_not_closed(tmp_path):
    cfg = ex.load_experiment(SMOKE2, REPO_ROOT)
    assert not ex.Experiment(cfg, tmp_path).closed()
    assert ex.CLOSED_FILE == "CLOSED.md"


# =========================================================================== 2. player p0.v3

def test_p0_v3_is_registered_like_p0_v2():
    v2, v3 = players.get("p0.v2"), players.get("p0.v3")
    assert v3.template == "p0_controller_v3.luau" and v3.tier == "P0" and v3.samples
    assert v3.timing_keys == v2.timing_keys and v3.driver == v2.driver
    assert "ADR 0012" in v3.note


@pytest.mark.parametrize("version", sorted(TEMPLATE_SHA))
def test_template_hashes_are_pinned_in_the_registry(version):
    pv = players.get(version)
    got = hashlib.sha256((TEMPLATES / pv.template).read_bytes()).hexdigest()
    assert pv.template_sha256 == got == TEMPLATE_SHA[version]


def test_a_changed_template_is_refused(monkeypatch):
    bad = dataclasses.replace(players.get("p0.v3"), template_sha256="0" * 64)
    monkeypatch.setitem(players.PLAYER_VERSIONS, "p0.v3", bad)
    players.load_template.cache_clear()
    try:
        with pytest.raises(TemplateError, match="sha256"):
            players.load_template("p0.v3")
    finally:
        players.load_template.cache_clear()


def test_a_config_whose_template_pin_fails_is_refused(monkeypatch):
    bad = dataclasses.replace(players.get("p0.v3"), template_sha256="0" * 64)
    monkeypatch.setitem(players.PLAYER_VERSIONS, "p0.v3", bad)
    players.load_template.cache_clear()
    try:
        with pytest.raises(ex.ExperimentError, match="template"):
            ex.load_experiment(SMOKE3, REPO_ROOT)
    finally:
        players.load_template.cache_clear()


def _route_timing(cfg):
    return cfg["_route"], cfg["player"]["timing"]


def test_p0_v3_renders_with_its_pinned_controller_hash(smoke3, b3):
    route, timing = _route_timing(smoke3)
    text = players.render_controller("p0.v3", route, timing)
    assert players.controller_sha256(text) == V3_SHA
    for cfg in (smoke3, b3):
        assert cfg["player"]["version"] == "p0.v3"
        assert cfg["player"]["template"] == "runner/templates/p0_controller_v3.luau"
        assert cfg["player"]["controller_sha256"] == V3_SHA == players.controller_sha256(cfg["_controller_text"])
    assert V3_SHA != V2_SHA
    assert players.controller_sha256(players.render_controller("p0.v2", route, timing)) == V2_SHA   # v2 unchanged


def test_p0_v3_render_substitutes_only_the_allowed_slots(smoke3):
    route, timing = _route_timing(smoke3)
    tpl = (TEMPLATES / "p0_controller_v3.luau").read_text().splitlines()
    out = players.render_controller("p0.v3", route, timing).splitlines()
    assert "{{" not in "\n".join(out).replace(p0.NONCE_SLOT, "")
    assert "\n".join(out).count(p0.NONCE_SLOT) == 1
    slots = [l for l in tpl if "{{" in l]
    assert [re.sub(r"\{\{[a-z]+:[a-z_]+\}\}", "_", l) for l in slots if p0.NONCE_SLOT not in l] == [
        "local T_ATTEMPT = _", "local T_WAYPOINT = _", "local SETTLE = _", "local TICK = _", "local SAMPLE_EVERY = _"]
    changed = [l[2:] for l in difflib.ndiff(tpl, out) if l.startswith("- ")]
    assert changed == [l for l in slots if p0.NONCE_SLOT not in l] + [p0._WAYPOINTS_MARKER]


def _split(text):
    """(header, before the spawn check, the spawn check, from the route start on)."""
    lines = text.splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("-- Spawn check"))
    j = next(k for k, l in enumerate(lines) if l.startswith("result.t_route_start = os.clock() - t_start"))
    return lines[:6], lines[6:i], lines[i:j], lines[j:]


def test_p0_v3_changes_only_the_spawn_check():
    v2 = (TEMPLATES / "p0_controller_v2.luau").read_text()
    v3 = (TEMPLATES / "p0_controller_v3.luau").read_text()
    h2, pre2, chk2, post2 = _split(v2)
    h3, pre3, chk3, post3 = _split(v3)
    assert pre3 == pre2 and post3 == post2            # route, waypoints, sampling, events, reporting unchanged
    assert h3[0].startswith("-- Worldsmith P0 controller, player version p0.v3") and "ADR 0012" in h3[0]
    code = "\n".join(l for l in chk3 if not l.lstrip().startswith("--"))
    for banned in ("MoveTo", "Jump", "CFrame", "PivotTo", "WalkSpeed", "Gravity", "LoadCharacter", "Teleport",
                   "Anchored", ".Position =", "GROUND_WAIT", "task.wait", "watch()"):
        assert banned not in code, banned
    for want in ("RunService.Heartbeat:Wait()", "WARM_FRAMES = 10", "WARM_MAX_DT = 0.1", "WARM_WAIT = 10",
                 "GROUND_STEPS = 120", "SETTLE_STEPS = 30", "MAX_OFFSET = 1", "RAY_EXTRA = 1.5",
                 "hum.FloorMaterial ~= Enum.Material.Air", "workspace:Raycast(hrp.Position", "hum.HipHeight + RAY_EXTRA",
                 "FilterDescendantsInstances = { char }", '"not_warm: ', '"not_grounded: ', '"moved_before_route: ',
                 '"died_before_route"', "result.warm_frames", "result.warm_wall_s", "result.max_dt_warm",
                 "result.warm_dts", "result.ground_steps", "result.ground_test", "result.settle_steps",
                 "result.spawn_offset", "result.settle_drift", "result.settle_max_offset", "result.spawn_pos"):
        assert want in code, want
    assert v3.count("hum:MoveTo(") == 1 and v3.isascii()
    assert code.index('"not_warm: ') < code.index('"not_grounded: ') < code.index('"moved_before_route: ')
    assert v3.index("moved_before_route") < v3.index("route_started = true") < v3.index("hum:MoveTo(")


def _blocks(text):
    """Luau block balance (comments and strings stripped): openers function/if/do vs end."""
    text = re.sub(r"--[^\n]*", "", text)
    text = re.sub(r'"(\\.|[^"\\])*"', '""', text)
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
    return sum(words.count(w) for w in ("function", "if", "do")), words.count("end")


@pytest.mark.parametrize("name", ["p0_controller_v2.luau", "p0_controller_v3.luau"])
def test_template_blocks_balance(name):
    opened, ended = _blocks((TEMPLATES / name).read_text())
    assert opened == ended


# --------------------------------------------------------------------------- parser and classification

def _report(nonce, **kv):
    r = result(nonce, {"x": 1}, [], status=kv.pop("status", "harness_error"), start_pos="0.00,3.11,6.00",
               t_route_start=-1, t_end=1.2)
    msgs = [m["message"] for m in r["logs"]]
    chunks = [m for m in msgs if m.startswith(f"WSP|{nonce}|RESULT|")]
    rep = json.loads("".join(c.split("|", 5)[5] for c in chunks))
    rep.update(kv)
    s = json.dumps(rep)
    n = -(-len(s) // 800)
    keep = [m for m in msgs if not m.startswith(f"WSP|{nonce}|RESULT|")]
    keep += [f"WSP|{nonce}|RESULT|{j + 1}|{n}|{s[j * 800:(j + 1) * 800]}" for j in range(n)]
    return {**r, "logs": [{"message": m, "ts": 1.0, "level": "output"} for m in keep]}


V3_FIELDS = {"warm_frames": 14, "warm_wall_s": 1.62, "max_dt_warm": 0.392, "warm_dts": [0.392, 0.31, 0.017],
             "ground_steps": 0, "ground_test": "floor", "ray_dist": -1, "settle_steps": 30, "ground_test_end": "floor",
             "spawn_pos": "0.00,0.10,6.00", "spawn_offset": 0, "settle_drift": 0, "settle_max_offset": 0}


@pytest.mark.parametrize("detail", [
    "not_warm: warm_frames=180 warm_wall_s=10.00 max_dt_warm=0.400 warm_run=3 dts=0.400,0.017",
    "not_grounded: before the settle, ground_steps=120",
    "not_grounded: at the settle end, settle_steps=30 spawn_offset=0.00 settle_max_offset=0.00",
    "moved_before_route: spawn_offset=0.20 settle_drift=1.60 settle_max_offset=1.75 grounded=true",
])
def test_p0_v3_harness_errors_classify_as_harness_error_with_the_new_fields(detail, stuck):
    scene = load_scene(F1E)
    area = classify.gap_area(scene, "gap_1", 4)
    r = _report(N1, detail=detail, **V3_FIELDS)
    out = taxonomy.classify_result(r, N1, {"x": 1}, area, stuck, spawn_xz=classify.spawn_centre(scene))
    assert out["outcome"] == "HARNESS_ERROR" and out["detail"] == detail
    for k, v in V3_FIELDS.items():
        assert out["report"][k] == v, k
    assert "HARNESS_ERROR" in epi.SYSTEM_OUTCOMES


def test_the_parser_adds_nothing_to_a_p0_v2_report(stuck):
    scene = load_scene(F1E)
    area = classify.gap_area(scene, "gap_1", 4)
    r = _report(N1, status="harness_error", detail="moved_before_route: x", spawn_pos="0.00,0.10,6.00",
                spawn_offset=0, settle_drift=0, settle_max_offset=0)
    out = classify.classify_attempt(r, N1, {"x": 1}, area, spawn_xz=classify.spawn_centre(scene))
    assert not {"warm_frames", "ground_test", "settle_steps", "warm_dts"} & set(out["report"])


class HarnessAt(ScriptedOperator):
    """Answers attempt `k` of the first build with a p0.v3 harness_error (`detail`)."""

    def __init__(self, cfg, k, detail, **kw):
        super().__init__(cfg, **kw)
        self.k, self.detail = k, detail

    def play(self, scene, nonce, i, outcome=None):
        r = super().play(scene, nonce, i, outcome)
        if self.builds == 1 and i == self.k:
            msgs = [m["message"] for m in r["logs"]]
            rep = json.loads("".join(m.split("|", 5)[5] for m in msgs if m.startswith(f"WSP|{nonce}|RESULT|")))
            rep.update(status="harness_error", detail=self.detail, t_route_start=-1, samples=[], events=[])
            s = json.dumps(rep)
            n = -(-len(s) // 800)
            keep = [m for m in msgs if not m.startswith(f"WSP|{nonce}|RESULT|") and not m.startswith("WS|")]
            keep += [f"WSP|{nonce}|RESULT|{j + 1}|{n}|{s[j * 800:(j + 1) * 800]}" for j in range(n)]
            r = {**r, "logs": [{"message": m, "ts": 1.0, "level": "output"} for m in keep]}
        return r


@pytest.mark.parametrize("detail", ["not_warm: warm_frames=180 warm_wall_s=10.00 max_dt_warm=0.400 warm_run=0 dts=0.400",
                                    "not_grounded: before the settle, ground_steps=120"])
def test_a_p0_v3_harness_error_still_stops_the_episode(detail, smoke3, tmp_path):
    d = tmp_path / "B0" / "ep0"
    epi.init_episode(smoke3, "B0", 0, d)
    op = HarnessAt(smoke3, 2, detail)
    s = epi.run_episode(Direct(op, d), smoke3, d)
    assert s["status"] == "SYSTEM_ERROR"
    assert s["versions"][0]["outcomes"][1] == "HARNESS_ERROR"
    row = json.loads((d / "v1" / "attempts" / "2.json").read_text())
    assert row["outcome"] == "HARNESS_ERROR" and row["detail"] == detail
    assert epi.validate_episode_bundle(d, smoke3) == []


# =========================================================================== 3. configs (declared before runs)

def test_smoke_f1e_p0v3_config(smoke3):
    raw = SMOKE3.read_text()
    assert "declared" in raw.lower() and "before any run" in raw.lower()
    assert smoke3["experiment"] == "smoke_f1e_p0v3" and smoke3["scene"]["canonical_hash"] == F1E_HASH
    assert smoke3["stage"] == "M0" and smoke3["n_attempts"] == 3 and smoke3["pass_k"] == 3
    assert list(smoke3["_conditions"]) == ["B0"] and smoke3["_conditions"]["B0"]["episodes"] == 2
    assert smoke3["preflight"] == ex.load_experiment(SMOKE2, REPO_ROOT)["preflight"]
    assert not (REPO_ROOT / "bundles" / "experiments" / "smoke_f1e_p0v3").exists()


def test_m2_f1a_b3_config_copies_the_pilot_except_player_id_and_conditions(b3, m2):
    raw = B3.read_text()
    assert "declared" in raw.lower() and "before any run" in raw.lower()
    assert b3["experiment"] == "m2_f1a_b3" and b3["scene"]["canonical_hash"] == F1A_HASH and b3["stage"] == "M0"
    assert [(c["id"], c["proposer"], c["episodes"], c["role"]) for c in b3["conditions"]] == [
        ("B3-haiku", "architect", 1, "haiku"), ("B3-sonnet", "architect", 1, "sonnet"), ("B3-opus", "architect", 1, "opus")]
    a, b = yaml.safe_load(M2.read_text()), yaml.safe_load(raw)
    for doc in (a, b):
        doc.pop("experiment")
        doc.pop("conditions")
        for k in ("version", "template", "controller_sha256"):
            doc["player"].pop(k)
    assert a == b                                     # every other field unchanged
    assert b3["architect"]["prompt_sha256"].startswith("df1b7a12") and b3["architect"]["job"] == "architect_ladder"
    assert b3["max_revisions"] == 3 and b3["caps"] == {"attempts_per_episode": 12, "architect_calls_per_episode": 3}
    assert b3["preflight"] == m2["preflight"]
    assert not (REPO_ROOT / "bundles" / "experiments" / "m2_f1a_b3").exists()


@needs_jzapi
def test_m2_f1a_b3_worst_case_is_the_pilots(b3, m2):
    price = lambda mdl, **kw: jzapi.worst_case_usd(mdl, batch=False, **kw)
    assert ex.worst_case(b3, price)["total_usd"] == pytest.approx(2.961)
    assert ex.worst_case(b3, price)["total_usd"] == pytest.approx(ex.worst_case(m2, price)["total_usd"])


def test_dry_run_smoke_f1e_p0v3(smoke3, tmp_path):
    res = dryrun.run_dry(smoke3, tmp_path)
    assert res["conditions"][0]["statuses"] == ["complete", "complete"]
    for i in (0, 1):
        d = tmp_path / "smoke_f1e_p0v3" / "B0" / f"ep{i}"
        s = json.loads((d / "summary.json").read_text())
        assert s["versions"][0]["outcomes"] == ["SUCCESS"] * 3 and s["player"]["version"] == "p0.v3"
        row = json.loads((d / "v1" / "attempts" / "1.json").read_text())
        assert {"warm_frames", "warm_wall_s", "max_dt_warm", "ground_steps", "ground_test", "settle_steps",
                "spawn_offset"} <= set(row["report"])
        assert json.loads((d / "v1" / "preflight.json").read_text())["ok"] is True


def test_dry_run_m2_f1a_b3_equals_the_pilots_b3_episodes(b3, m2, tmp_path):
    res = dryrun.run_dry(b3, tmp_path / "b3")
    pilot = dryrun.run_dry(m2, tmp_path / "pilot")
    got = {c["condition"]: [(e["status"], e["canonical"], e["revisions"]["verdicts"]) for e in c["episodes"]]
           for c in res["conditions"]}
    want = {c["condition"]: [(e["status"], e["canonical"], e["revisions"]["verdicts"]) for e in c["episodes"]]
            for c in pilot["conditions"] if c["condition"].startswith("B3-")}
    assert got == want and list(got) == ["B3-haiku", "B3-sonnet", "B3-opus"]
    two = dryrun.run_dry(b3, tmp_path / "two", two_machine=True)
    assert [c["statuses"] for c in two["conditions"]] == [c["statuses"] for c in res["conditions"]]


@pytest.mark.parametrize("cfg_path,extra", [(SMOKE3, []), (B3, []), (B3, ["--architect-elsewhere"])])
def test_cli_dry_runs(cfg_path, extra, tmp_path, capsys):
    rc = _cli().main(["--config", str(cfg_path), "--dry-run", *extra, "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "NOT A RESULT" in out and "player p0.v3" in out
    if cfg_path == B3 and "experiment worst case" in out:
        assert "experiment worst case: $2.961000" in out
