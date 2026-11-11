"""WS-14 step 3: the declared experiment suite config/experiments/m2_hard.yaml (ADR 0014).

CPU only: the config is loaded and validated, never run (no Studio, no network, no key). Prices
for the worst case come from a stub that reproduces the per-call figures recorded in the repo
(ADR 0010 section 6, HANDOFF WS-10 smoke); jzapi itself prints the authoritative figure with
`plan` on the Mac before any approval.
"""

import hashlib
import json
import sys

import pytest
import yaml

from _repo import REPO_ROOT
from worldsmith.architect import prompt as pr
from worldsmith.grammar import scene as sc
from worldsmith.runner import dryrun
from worldsmith.runner import episode as epi
from worldsmith.runner import experiment as ex
from worldsmith.validator.validate import validate

EXPERIMENTS = REPO_ROOT / "config" / "experiments"
M2H = EXPERIMENTS / "m2_hard.yaml"
B3 = EXPERIMENTS / "m2_f1a_b3.yaml"
HARDNESS = REPO_ROOT / "bundles" / "hardness" / "summary.json"
PARTS = ["m2_hard_f1a", "m2_hard_f1b", "m2_hard_f1c", "m2_hard_f1d"]
SCENE_OF = {"m2_hard_f1a": "F1-A", "m2_hard_f1b": "F1-B", "m2_hard_f1c": "F1-C", "m2_hard_f1d": "F1-D"}
CONDITIONS = ["B0", "B1", "B2", "B3-haiku", "B3-sonnet", "B3-opus"]
V3_SHA = "cd9a7a0a21ccd32ea7e14990f17b7bba58083f1bfeb2a81e77cf2e122a2d0f8c"
JOB_CAP, JOB_SPENT = 90.0, 0.04          # ROADMAP section 9 / TICKETS WS-14: $90 approved, $0.04 spent

# $/token, derived from the repo's recorded jzapi figures (ADR 0010 s6: haiku $0.007000, sonnet
# $0.220000, opus $0.760000 per call at 24000 prompt tokens and the ladder's max_tokens; the ladder's
# output rates sonnet $10/M, opus $20/M; HANDOFF WS-10: haiku $0.005464 at 11715 prompt tokens).
RATES = {"claude-haiku-5-5": (0.125e-6, 0.5e-6), "claude-sonnet-5-5": (2.5e-6, 10e-6),
         "claude-opus-5-5": (5e-6, 20e-6)}


def stub_price(model_id, *, prompt_tokens, max_tokens, cache_ttl):
    assert cache_ttl == "5m"
    pin, pout = RATES[model_id]
    return pin * prompt_tokens + pout * max_tokens


@pytest.fixture(scope="module")
def parts():
    return {c["experiment"]: c for c in ex.load_suite(M2H, REPO_ROOT)}


@pytest.fixture(scope="module")
def b3():
    return ex.load_experiment(B3, REPO_ROOT)


def test_stub_reproduces_the_recorded_per_call_figures():
    for model, want in (("claude-haiku-5-5", 0.007), ("claude-sonnet-5-5", 0.22), ("claude-opus-5-5", 0.76)):
        mt = {"claude-haiku-5-5": 8000, "claude-sonnet-5-5": 16000, "claude-opus-5-5": 32000}[model]
        assert stub_price(model, prompt_tokens=24000, max_tokens=mt, cache_ttl="5m") == pytest.approx(want, abs=1e-9)
    assert stub_price("claude-haiku-5-5", prompt_tokens=11715, max_tokens=8000, cache_ttl="5m") == pytest.approx(0.005464, abs=1e-6)


# --- the suite loads, and each part is an ordinary experiment ---------------------------------

def test_suite_lists_its_parts():
    assert ex.suite_parts(M2H) == PARTS


def test_every_part_loads_and_validates(parts):
    assert list(parts) == PARTS
    sha = hashlib.sha256(M2H.read_bytes()).hexdigest()
    for pid, cfg in parts.items():
        assert cfg["_sha256"] == sha                     # one declared file: one hash for every part
        assert cfg["_suite"] == {"suite": "m2_hard", "part": pid, "parts": PARTS}
        assert cfg["schema"] == ex.EXPERIMENT_SCHEMA and cfg["experiment"] == pid


def test_scenes_are_f1a_plus_every_fixture_that_passed_hardness(parts):
    h = json.loads(HARDNESS.read_text())
    assert [SCENE_OF[p] for p in PARTS] == ["F1-A", *h["passed"]]
    assert h["dropped"] == []
    for pid, cfg in parts.items():
        path = f"scenes/{SCENE_OF[pid]}/scene.json"
        assert cfg["scene"] == {"file": path, "canonical_hash": sc.canonical_hash(sc.load_scene(REPO_ROOT / path))}


def test_every_part_declares_the_ticket_fields(parts):
    for pid, cfg in parts.items():
        assert cfg["stage"] == "M1"
        pl = cfg["player"]
        assert pl["version"] == "p0.v3" and pl["controller_sha256"] == V3_SHA
        assert pl["route"]["sha256"] == "b9514a4b5bba46e9c77e7554486515e7de6e26ff0cfe1d77a62e26e1b525b36a"
        assert cfg["n_attempts"] == 3 and cfg["pass_k"] == 3 and cfg["max_revisions"] == 3
        assert cfg["caps"] == {"attempts_per_episode": 12, "architect_calls_per_episode": 3}
        assert [c["id"] for c in cfg["conditions"]] == CONDITIONS
        assert all(c["episodes"] == 3 for c in cfg["conditions"])
        c = {x["id"]: x for x in cfg["conditions"]}
        assert c["B0"]["proposer"] == "none"
        assert c["B2"] == {"id": "B2", "proposer": "random_valid", "episodes": 3, "seeds": [0, 1, 2]}
        assert [(c[r]["proposer"], c[r]["role"]) for r in CONDITIONS[3:]] == [
            ("architect", "haiku"), ("architect", "sonnet"), ("architect", "opus")]
        name = SCENE_OF[pid]
        want = "scenes/F1-A/patches/m0_fix.json" if name == "F1-A" else f"scenes/{name}/patches/m1_fix.json"
        assert c["B1"]["proposer"] == "hand_fix" and c["B1"]["patch"] == want
        patch = cfg["_hand_patches"]["B1"]
        res = validate(cfg["_scene"], patch, stage="M1")
        assert res.ok and res.graph["walk"]["result"] == "CONNECTED"


def test_common_fields_equal_m2_f1a_b3_except_stage_and_prompt_cap(parts, b3):
    keep = ("player", "n_attempts", "pass_k", "max_revisions", "caps", "origin", "gap_area_margin_studs",
            "tool_timeouts_s", "preflight", "classifier", "dry_run")
    for cfg in parts.values():
        for k in keep:
            assert cfg[k] == b3[k], k
        a, b = dict(cfg["architect"]), dict(b3["architect"])
        assert a.pop("prompt_bytes_cap") == 32000 and b.pop("prompt_bytes_cap") == 24000
        assert a == b                                    # same ladder, job, baseline prompt
        assert b3["stage"] == "M0" and cfg["stage"] == "M1"


def test_header_says_declared_and_not_run():
    text = M2H.read_text(encoding="utf-8")
    head = text.split("schema:", 1)[0].lower()
    assert "declared" in head and "before any run" in head and "not run" in head
    for pid in PARTS:
        assert not (REPO_ROOT / "bundles" / "experiments" / pid).exists()      # never run, never locked


# --- part selection ---------------------------------------------------------------------------

def test_a_suite_needs_a_part():
    with pytest.raises(ex.ExperimentError, match="m2_hard_f1b"):
        ex.load_experiment(M2H, REPO_ROOT)
    with pytest.raises(ex.ExperimentError, match="no part"):
        ex.load_experiment(M2H, REPO_ROOT, part="m2_hard_f1z")


def test_a_single_experiment_takes_no_part():
    with pytest.raises(ex.ExperimentError, match="not a suite"):
        ex.load_experiment(B3, REPO_ROOT, part="m2_hard_f1a")


def _suite_doc():
    return yaml.safe_load(M2H.read_text())


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["parts"].append(dict(d["parts"][0])), "duplicate"),
    (lambda d: d["parts"][1].update(experiment="other_f1b"), "starts with"),
    (lambda d: d["common"].update(scene=d["parts"][0]["scene"]), "common"),
    (lambda d: d["parts"][0].update(stage="M0"), "part keys"),
    (lambda d: d.update(extra=1), "suite keys"),
    (lambda d: d["parts"][1].update(scene=dict(d["parts"][0]["scene"])), "scene"),
])
def test_malformed_suites_are_refused(tmp_path, mutate, msg):
    d = _suite_doc()
    mutate(d)
    f = tmp_path / "config" / "experiments" / "bad.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(yaml.safe_dump(d, sort_keys=False))
    with pytest.raises(ex.ExperimentError, match=msg):
        ex.load_suite(f, REPO_ROOT)


# --- budget -------------------------------------------------------------------------------------

def test_worst_case(parts):
    per_part = [ex.worst_case(cfg, stub_price) for cfg in parts.values()]
    for wc in per_part:
        rows = {r["condition"]: r for r in wc["conditions"]}
        assert {k: r["calls_max"] for k, r in rows.items()} == {"B3-haiku": 9, "B3-sonnet": 9, "B3-opus": 9}
        assert rows["B3-haiku"]["per_call_usd"] == pytest.approx(0.008)
        assert rows["B3-sonnet"]["per_call_usd"] == pytest.approx(0.24)
        assert rows["B3-opus"]["per_call_usd"] == pytest.approx(0.80)
        assert wc["total_usd"] == pytest.approx(9.432)
    suite = ex.suite_worst_case(list(parts.values()), stub_price)
    assert suite["total_usd"] == pytest.approx(37.728) and suite["calls_max"] == 108
    assert [p["experiment"] for p in suite["parts"]] == PARTS
    assert suite["total_usd"] < 0.5 * (JOB_CAP - JOB_SPENT)          # comfortably inside the job's headroom


def test_prompt_cap_holds_a_pessimistic_third_revision(parts):
    """Revision 3 with two rejected 3-op revisions (6 violations each) and three STUCK attempts:
    at M1 such a request on F1-D bounds to more than the earlier 24000 cap, so m2_hard declares 32000."""
    p = pr.load_prompt("baseline")
    att = {"outcome": "STUCK", "tags": ["STUCK"], "cause": "UNCLASSIFIED", "events": [], "fell_in_area": None,
           "detail": "controller status 'route_done', no goal event" + " x" * 200,
           "stuck": {"result": "STUCK"}}
    obs = pr.observed_from_classification({"attempts": [dict(att, attempt=i) for i in (1, 2, 3)], "episode_tags": []})
    sizes = {}
    for pid, cfg in parts.items():
        s = cfg["_scene"]
        patch = {"schema": "worldsmith.patch/0.2", "base_hash": sc.canonical_hash(s), "ops": [
            {"op": "add", "entity": {"id": "wall_extra_1", "type": "Wall", "pos": [-123.456, 45.678, -200.123],
                                     "size": [100.5, 90.25, 110.75]}},
            {"op": "move", "id": "lever_1", "pos": [-123.456, 45.678, -200.123]},
            {"op": "set", "id": "bridge_1", "field": "width", "value": 12.345}]}
        r = validate(s, patch, stage="M1").to_dict()
        fb = {"verdict": "REJECTED", "malformed_reason": None, "patch": patch,
              "violations": r["violations"], "warnings": r["warnings"]}
        ctx = pr.build_context(s, obs, stage="M1", revision=3, max_revisions=3,
                               previous=[dict(fb, revision=1), dict(fb, revision=2)])
        sizes[pid] = epi.request_bytes_bound(*pr.render(p, ctx), True)
        assert sizes[pid] < cfg["architect"]["prompt_bytes_cap"]
    assert max(sizes.values()) > 24000


# --- CLI: a part is chosen with --part ------------------------------------------------------------

def _cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_experiment", REPO_ROOT / "scripts" / "run_experiment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_refuses_a_suite_without_part(tmp_path, capsys):
    rc = _cli().main(["--config", str(M2H), "status", "--out", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1 and all(p in err for p in PARTS)
    assert list(tmp_path.iterdir()) == []


def test_cli_status_of_one_part_writes_nothing(tmp_path, capsys):
    rc = _cli().main(["--config", str(M2H), "--part", "m2_hard_f1c", "status", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "experiment m2_hard_f1c" in out and "player p0.v3" in out
    assert out.count(" todo") == 18                                   # 6 conditions x 3 episodes
    assert list(tmp_path.iterdir()) == []


# --- the parts run through the episode machinery (simulated; NOT a result) ------------------------

def test_dry_run_of_the_non_model_conditions(parts, tmp_path):
    """FakeOperator decides outcomes by bridge length only (not physics, not Roblox: it "passes"
    F1-B and F1-C, whose bridges are full length), and the WS-10 dry-run fixtures are F1-A M0
    replies, so only B0, B1 and B2 of the F1-D part are simulated here (its short bridge makes the
    fake fail v1, so proposals happen): the point is that an M1 part, its two-op 0.2 hand patch and
    B2 at M1 pass through the orchestrator and the bundle checks. Not a result."""
    pid = "m2_hard_f1d"
    cfg = dict(parts[pid])
    cfg["_conditions"] = {k: v for k, v in cfg["_conditions"].items() if k in ("B0", "B1", "B2")}
    res = dryrun.run_dry(cfg, tmp_path)
    assert [c["condition"] for c in res["conditions"]] == ["B0", "B1", "B2"]
    for c in res["conditions"]:
        assert c["episodes_done"] == 3 and all(s == "complete" for s in c["statuses"]), c["statuses"]
    assert res["dry_run"] is True
    b1 = json.loads((tmp_path / pid / "B1" / "ep0" / "summary.json").read_text())
    assert b1["revisions"]["verdicts"] == ["ACCEPTED"] and b1["repaired"] is True
    assert b1["final"]["scene_hash"] == sc.canonical_hash(json.loads(
        (REPO_ROOT / "scenes" / "F1-D" / "fix.json").read_text()))
