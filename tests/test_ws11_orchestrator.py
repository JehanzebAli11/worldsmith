"""WS-11: orchestrator, caps, player p0.v1, declared-before-runs, resume, budget, P2 freeze
manifest, CLI dry run (SPEC sections 9, 11, 12, 15; ADR 0010).

CPU only: a simulated Studio operator (worldsmith.runner.dryrun.FakeOperator; shape, not physics)
and fake or fixture architects. No Studio, no network, no key, nothing paid.
"""

import copy
import difflib
import hashlib
import json
import socket
import sys
from pathlib import Path

import pytest
import yaml

from _repo import REPO_ROOT
from _ws11sim import Direct, ScriptedOperator, SeqArchitect, provider_error, set_len
from worldsmith.architect import client as acl
from worldsmith.grammar import scene as sc
from worldsmith.runner import attempts as at
from worldsmith.runner import classify, dryrun, freeze, p0, players, taxonomy
from worldsmith.runner import episode as epi
from worldsmith.runner import experiment as ex
from worldsmith.runner.config import load_config

CFG = REPO_ROOT / "config" / "experiments" / "m2_f1a.yaml"
V1_SHA = "dbc1f325ce64bda8b046bc4f08a18050cced76a123b843af5937b85b65754c6d"
V2_SHA = "a7472c9cbbea9096f678be02a663ffb503cb2619eb9ea1fe136fcbf2a7858cac"
M0_CONTROLLER_SHA = "3743d998e900729eaec583dfcae9f584651184a66e133eb14944045aee18c034"
M0_FIRST_REQUEST = "57f003382a7ef366af5e9073fffeea3bd485946880c74bda9032953964cf461d"
F1A = "92f866098bfb850cf4c464179887a862f305c143c8e0b38efdc7978493cddb1d"

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


@pytest.fixture(scope="module")
def cfg():
    return ex.load_experiment(CFG, REPO_ROOT)


def variant(tmp_path, edit, name="variant.yaml"):
    """A copy of the pilot config with `edit(dict)` applied, loaded against the repo."""
    doc = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    edit(doc)
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


def load_variant(tmp_path, edit, name="variant.yaml"):
    return ex.load_experiment(variant(tmp_path, edit, name), REPO_ROOT)


def only(*ids):
    def edit(d):
        d["conditions"] = [c for c in d["conditions"] if c["id"] in ids]
    return edit


def run_direct(cfg, cond_id, tmp_path, *, op=None, architect=None, proposer=None):
    d = tmp_path / cond_id / "ep0"
    epi.init_episode(cfg, cond_id, 0, d)
    op = op or ScriptedOperator(cfg)
    client = Direct(op, d)
    s = epi.run_episode(client, cfg, d, architect_factory=(lambda: architect) if architect else None,
                        proposer=proposer)
    return s, d, client


# --------------------------------------------------------------------------- players

def test_p0_v0_is_still_the_m0_controller():
    m0 = load_config(REPO_ROOT / "config" / "m0.yaml", REPO_ROOT)
    text = players.render_controller("p0.v0", m0["_route"], m0["timing"])
    assert text == m0["_controller_text"]
    assert players.controller_sha256(text) == M0_CONTROLLER_SHA == m0["controller"]["sha256"]


def test_p0_v1_hash_is_the_declared_one():
    # the pilot moved to p0.v2 before any run (ADR 0011); p0.v1 is declared by smoke_f1e_p0v1
    v1 = ex.load_experiment(REPO_ROOT / "config" / "experiments" / "smoke_f1e_p0v1.yaml", REPO_ROOT)
    assert v1["player"]["version"] == "p0.v1"
    assert players.controller_sha256(v1["_controller_text"]) == V1_SHA == v1["player"]["controller_sha256"]
    assert V1_SHA != M0_CONTROLLER_SHA


def test_pilot_declares_p0_v2(cfg):
    assert cfg["player"]["version"] == "p0.v2"
    assert players.controller_sha256(cfg["_controller_text"]) == V2_SHA == cfg["player"]["controller_sha256"]


def test_p0_v1_template_only_adds_position_samples():
    v0 = (REPO_ROOT / "runner" / "templates" / "p0_controller.luau").read_text().splitlines()
    v1 = (REPO_ROOT / "runner" / "templates" / "p0_controller_v1.luau").read_text().splitlines()
    removed = [l[2:] for l in difflib.ndiff(v0, v1) if l.startswith("- ")]
    assert removed == [
        "-- Worldsmith P0 controller (fixed, reviewed template; WS-04, ADR 0004 and ADR 0005).",
        "local params = { t_attempt = T_ATTEMPT, t_waypoint = T_WAYPOINT, settle = SETTLE, tick = TICK, waypoints = {} }",
        "    if not dead() and hum.FloorMaterial ~= Enum.Material.Air then",
        "        result.last_supported = where()",
    ]
    added = "\n".join(l[2:] for l in difflib.ndiff(v0, v1) if l.startswith("+ "))
    for banned in ("MoveTo", "Jump", "CFrame", "PivotTo", "WalkSpeed", "Gravity", "LoadCharacter"):
        assert banned not in added, banned


def test_p0_v1_only_moves_never_jumps_or_teleports(cfg):
    code = "\n".join(l for l in cfg["_controller_text"].splitlines() if not l.lstrip().startswith("--"))
    for banned in ("PivotTo", "CFrame", "WalkSpeed =", "JumpHeight =", "JumpPower =", "Gravity =",
                   ".Jump =", "Jump = true", "SetPrimaryPartCFrame"):
        assert banned not in code, banned
    assert code.count("MoveTo(origin + Vector3") == 1
    assert "local SAMPLE_EVERY = 1" in code and "sample_every = SAMPLE_EVERY" in code


def test_p0_v1_timing_rules():
    t = {"t_attempt": 60, "t_waypoint": 10, "settle": 2, "tick": 0.05, "sample_every": 1}
    assert players.validate_timing("p0.v1", t) == []
    assert players.validate_timing("p0.v1", {k: v for k, v in t.items() if k != "sample_every"})
    assert players.validate_timing("p0.v1", {**t, "sample_every": 0.1})          # <= 2 * tick
    assert any("unknown timing key" in e for e in players.validate_timing("p0.v0", t))
    with pytest.raises(players.PlayerError):
        players.get("p0.v9")


def test_p0_v1_template_is_package_data():
    from importlib import resources
    assert resources.files("worldsmith.runner").joinpath("templates", "p0_controller_v1.luau").is_file()


def _report(samples, *, t_end=20.0, t_route_start=1.0):
    return {"t_route_start": t_route_start, "start_pos": "0.00,3.00,8.00", "waypoints": [], "died": False,
            "t_end": t_end, "samples": samples}


def test_v1_samples_make_stuck_evaluable():
    stuck_cfg = {"epsilon_studs": 2.0, "window_s": 10, "max_sample_gap_s": 1.0}
    still = [{"t": 1.0 + 0.95 * j, "pos": "0.00,3.00,8.50"} for j in range(1, 21)]
    assert taxonomy.evaluate_stuck(_report(still), stuck_cfg)["result"] == "STUCK"
    moving = [{"t": 1.0 + 0.95 * j, "pos": f"0.00,3.00,{8 + 3 * j:.2f}"} for j in range(1, 21)]
    assert taxonomy.evaluate_stuck(_report(moving), stuck_cfg)["result"] == "NOT_STUCK"
    # p0.v0 reports (no samples) over a long blocked run stay NOT_EVALUABLE, as in WS-08
    assert taxonomy.evaluate_stuck(_report([]), stuck_cfg)["result"] == "NOT_EVALUABLE"
    sparse = still[::2]                                     # 1.9 s gaps: never a guess
    assert taxonomy.evaluate_stuck(_report(sparse), stuck_cfg)["result"] == "NOT_EVALUABLE"


def test_classifier_keeps_samples_only_when_the_report_has_them(cfg):
    op = dryrun.FakeOperator(cfg)
    scene = cfg["_scene"]
    area = classify.gap_area(scene, "gap_1", 4)
    r = op.play(scene, "0123456789abcdef", 1)
    out = classify.classify_attempt(r, "0123456789abcdef", cfg["_params"], area)
    assert out["report"]["samples"] and out["report"]["samples_dropped"] == 0
    rep = json.loads("".join(m["message"].split("|", 5)[5] for m in r["logs"] if m["message"].startswith("WSP|")))
    rep.pop("samples"), rep.pop("samples_dropped")
    s = json.dumps(rep)
    r["logs"] = [m for m in r["logs"] if not m["message"].startswith("WSP|")] + \
        [{"message": f"WSP|0123456789abcdef|RESULT|1|1|{s}", "ts": 1.0, "level": "output"}]
    params = {k: v for k, v in cfg["_params"].items()}
    out = classify.classify_attempt(r, "0123456789abcdef", params, area)
    assert "samples" not in out["report"]


# --------------------------------------------------------------------------- P2 freeze manifest

def _manifest(tmp_path, **over):
    comps = {}
    for name in freeze.COMPONENTS:
        f = tmp_path / "frozen" / f"{name}.bin"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(f"fake {name} export".encode())
        comps[name] = {"path": f"frozen/{name}.bin", "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    m = {"schema": freeze.FREEZE_SCHEMA, "autopilot": {"repo": "ramenvr/autopilot", "commit": "a" * 40},
         "components": comps, "models": {"llm": "claude-sonnet-5-5", "jev": "jev-1.0"}, "evolve": False,
         "memory_reset_before_each_attempt": True, "autopilot_share": 0, "wall_clock_cap_s": 1800}
    for k, v in over.items():
        if v is KeyError:
            m.pop(k)
        else:
            m[k] = v
    p = tmp_path / "freeze.json"
    p.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return p, m


def _codes(p, sha, tmp_path, **kw):
    return sorted({r["code"] for r in freeze.check_manifest(p, declared_sha256=sha, root=tmp_path, **kw)})


def test_freeze_manifest_that_matches_passes(tmp_path):
    p, m = _manifest(tmp_path)
    sha = freeze.manifest_sha256(m)
    assert freeze.check_manifest(p, declared_sha256=sha, root=tmp_path, runtime={"evolve": False},
                                 env={"AUTOPILOT_SHARE": "0"}) == []
    assert freeze.require_p2(p, declared_sha256=sha, root=tmp_path) == sha
    p.write_text(json.dumps(m), encoding="utf-8")                    # whitespace does not change the hash
    assert freeze.check_manifest(p, declared_sha256=sha, root=tmp_path) == []


def test_freeze_refuses_a_manifest_hash_mismatch(tmp_path):
    p, m = _manifest(tmp_path)
    assert _codes(p, "0" * 64, tmp_path) == ["MANIFEST_HASH_MISMATCH"]
    assert _codes(p, None, tmp_path) == ["MANIFEST_HASH_MISMATCH"]
    with pytest.raises(freeze.P2Refused) as e:
        freeze.require_p2(p, declared_sha256="0" * 64, root=tmp_path)
    assert e.value.codes == ["MANIFEST_HASH_MISMATCH"]


def test_freeze_refuses_evolve_enabled(tmp_path):
    p, m = _manifest(tmp_path, evolve=True)
    assert _codes(p, freeze.manifest_sha256(m), tmp_path) == ["EVOLVE_ENABLED"]
    p, m = _manifest(tmp_path)
    assert _codes(p, freeze.manifest_sha256(m), tmp_path, runtime={"evolve": True}) == ["EVOLVE_ENABLED"]


def test_freeze_refuses_a_missing_memory_snapshot(tmp_path):
    p, m = _manifest(tmp_path)
    comps = dict(m["components"])
    comps.pop("memory_snapshot")
    p, m = _manifest(tmp_path, components=comps)
    assert _codes(p, freeze.manifest_sha256(m), tmp_path) == ["MEMORY_SNAPSHOT_MISSING"]
    p, m = _manifest(tmp_path)
    (tmp_path / "frozen" / "memory_snapshot.bin").rename(tmp_path / "frozen" / "gone.bin")
    assert _codes(p, freeze.manifest_sha256(m), tmp_path) == ["MEMORY_SNAPSHOT_MISSING"]


@pytest.mark.parametrize("over,code", [
    ({"memory_reset_before_each_attempt": False}, "MEMORY_RESET_DISABLED"),
    ({"autopilot_share": 1}, "AUTOPILOT_SHARE_NOT_ZERO"),
    ({"wall_clock_cap_s": KeyError}, "MANIFEST_INVALID"),
    ({"wall_clock_cap_s": 0}, "WALL_CLOCK_CAP_MISSING"),
    ({"extra": 1}, "MANIFEST_INVALID"),
    ({"autopilot": {"repo": "x", "commit": "main"}}, "MANIFEST_INVALID"),
])
def test_freeze_refusal_codes(tmp_path, over, code):
    p, m = _manifest(tmp_path, **over)
    assert code in _codes(p, freeze.manifest_sha256(m), tmp_path)


def test_freeze_refuses_changed_components_and_env(tmp_path):
    p, m = _manifest(tmp_path)
    (tmp_path / "frozen" / "harness.bin").write_bytes(b"regenerated harness")
    assert _codes(p, freeze.manifest_sha256(m), tmp_path) == ["COMPONENT_HASH_MISMATCH"]
    p, m = _manifest(tmp_path)
    assert _codes(p, freeze.manifest_sha256(m), tmp_path, env={"AUTOPILOT_SHARE": "1"}) == ["AUTOPILOT_SHARE_NOT_ZERO"]
    assert _codes(tmp_path / "nope.json", "0" * 64, tmp_path) == ["MANIFEST_MISSING"]


def test_p2_experiment_is_refused(tmp_path):
    p, m = _manifest(tmp_path, evolve=True)

    def edit(d):
        d["player"] = {"tier": "P2", "freeze_manifest": {"file": "freeze.json", "sha256": freeze.manifest_sha256(m)}}
    path = variant(tmp_path, edit)
    with pytest.raises(freeze.P2Refused) as e:
        ex.load_experiment(path, tmp_path)
    assert "EVOLVE_ENABLED" in e.value.codes
    p, m = _manifest(tmp_path)
    path = variant(tmp_path, lambda d: d.update(player={"tier": "P2", "freeze_manifest": {
        "file": "freeze.json", "sha256": freeze.manifest_sha256(m)}}), "v2.yaml")
    with pytest.raises(freeze.P2Refused) as e:
        ex.load_experiment(path, tmp_path)
    assert e.value.codes == ["P2_NOT_IMPLEMENTED"]             # no Autopilot runs in WS-11


# --------------------------------------------------------------------------- the experiment config

def test_pilot_config_declares_the_pilot(cfg):
    assert cfg["experiment"] == "m2_f1a_pilot" and cfg["stage"] == "M0"
    assert cfg["scene"]["canonical_hash"] == F1A
    assert list(cfg["_conditions"]) == ["B0", "B1", "B2", "B3-haiku", "B3-sonnet", "B3-opus"]
    assert all(c["episodes"] == 1 for c in cfg["_conditions"].values())
    assert cfg["_conditions"]["B2"]["seeds"] == [0]
    assert [cfg["_conditions"][f"B3-{r}"]["role"] for r in ("haiku", "sonnet", "opus")] == ["haiku", "sonnet", "opus"]
    assert (cfg["n_attempts"], cfg["pass_k"], cfg["max_revisions"]) == (3, 3, 3)
    assert cfg["caps"] == {"attempts_per_episode": 12, "architect_calls_per_episode": 3}
    assert cfg["player"]["timing"]["t_attempt"] == 60 and cfg["player"]["tier"] == "P0"
    assert cfg["_hand_patches"]["B1"]["ops"] == [{"op": "set", "id": "bridge_1", "field": "length", "value": 16}]


@pytest.mark.parametrize("edit,msg", [
    (lambda d: d.update(n_attempts=2), "n_attempts = 3"),
    (lambda d: d.update(pass_k=2), "pass_k = 3"),
    (lambda d: d.update(max_revisions=4), "max_revisions"),
    (lambda d: d["caps"].update(attempts_per_episode=15), "attempts_per_episode"),
    (lambda d: d["caps"].update(architect_calls_per_episode=4), "architect_calls_per_episode"),
    (lambda d: d["player"].update(controller_sha256="0" * 64), "controller_sha256"),
    (lambda d: d["player"].update(version="p0.v0"), "unknown timing key"),
    (lambda d: d["player"].update(version="p1.v0"), "unknown player version"),
    (lambda d: d["player"]["timing"].update(t_attempt=120), "controller_sha256"),
    (lambda d: d["player"].update(claim="an agent result"), "claim limit"),
    (lambda d: d["conditions"][2].update(seeds=[0, 1]), "seeds"),
    (lambda d: d["conditions"][2].update(episodes=2, seeds=[4, 4]), "seeds"),
    (lambda d: d["conditions"][3].update(role="gpt"), "not in the ladder"),
    (lambda d: d["conditions"][1].update(patch_sha256="0" * 64), "patch: sha256"),
    (lambda d: d["conditions"].append({"id": "B0", "proposer": "none", "episodes": 1}), "duplicate"),
    (lambda d: d["conditions"].append({"id": "B9", "proposer": "oracle", "episodes": 1}), "proposer must be"),
    (lambda d: d["architect"].update(prompt="redteam"), "never a baseline"),
    (lambda d: d["architect"].update(prompt_sha256="0" * 64), "prompt_sha256"),
    (lambda d: d["classifier"].update(sha256="0" * 64), "classifier"),
    (lambda d: d["tool_timeouts_s"].update(run_script_in_play_mode=60), "exceed t_attempt"),
    (lambda d: d["scene"].update(canonical_hash="0" * 64), "canonical hash"),
    (lambda d: d.update(surprise=1), "unknown key"),
])
def test_config_refusals(tmp_path, edit, msg):
    with pytest.raises(ex.ExperimentError, match=msg):
        load_variant(tmp_path, edit)


# --------------------------------------------------------------------------- one episode, caps

def test_caps_unit():
    c = epi.EpisodeCaps(12, 3, 3)
    for _ in range(12):
        c.play()
    with pytest.raises(epi.CapExceeded):
        c.play()
    c = epi.EpisodeCaps(12, 3, 3)
    for _ in range(3):
        c.proposal(architect=True)
    with pytest.raises(epi.CapExceeded):
        c.proposal(architect=True)


def test_b0_runs_the_original_only(cfg, tmp_path):
    s, d, client = run_direct(cfg, "B0", tmp_path)
    assert s["status"] == "complete" and s["attempts_used"] == 3 and s["revisions"]["used"] == 0
    assert s["final"] == {"version": 1, "scene_hash": F1A, "successes": 0, "n": 3, "verdict": "FAIL"}
    assert not s["repaired"] and client.log.count("run_script_in_play_mode") == 3
    assert epi.validate_episode_bundle(d, cfg) == []


def test_b1_is_one_hand_patch(cfg, tmp_path):
    s, d, _ = run_direct(cfg, "B1", tmp_path)
    assert s["revisions"]["verdicts"] == ["ACCEPTED"] and s["attempts_used"] == 6 and s["repaired"]
    assert s["final"]["scene_hash"] == "297b23c884e196a80f9a19aa32d006768e1ace84cfbd4bd1e932981fdabf0535"
    assert json.loads((d / "v2" / "patch.json").read_text())["base_hash"] == F1A
    assert epi.validate_episode_bundle(d, cfg) == []


def test_rejected_architect_revisions_cost_no_attempts(cfg, tmp_path):
    arch = SeqArchitect(["prose", set_len(65), "```" + set_len(16) + "```"])
    s, d, client = run_direct(cfg, "B3-haiku", tmp_path, architect=arch)
    assert s["revisions"]["verdicts"] == ["MALFORMED", "REJECTED", "MALFORMED"]
    assert s["architect_calls"] == 3 == arch.calls and s["attempts_used"] == 3
    assert not s["repaired"] and s["status"] == "complete"
    assert epi.validate_episode_bundle(d, cfg) == []


def test_stubborn_architect_hits_12_attempts_and_3_calls(cfg, tmp_path):
    arch = SeqArchitect([set_len(7), set_len(8), set_len(9), set_len(10)])
    s, d, client = run_direct(cfg, "B3-haiku", tmp_path, architect=arch)
    assert s["attempts_used"] == 12 and s["architect_calls"] == 3 and arch.calls == 3
    assert [v["version"] for v in s["versions"]] == [1, 2, 3, 4]
    assert client.log.count("run_script_in_play_mode") == 12
    # revision i+1 patches the last ACCEPTED (built and played) scene
    for k in (2, 3, 4):
        patch = json.loads((d / f"v{k}" / "patch.json").read_text())
        assert patch["base_hash"] == s["versions"][k - 2]["scene_hash"]
    assert epi.validate_episode_bundle(d, cfg) == []


def test_a_proposer_ignoring_its_budget_is_stopped_by_the_cap(cfg, tmp_path):
    class Rogue:
        source, architect, max_revisions, exhausted, calls = "rogue", False, 9, False, 0

        def propose(self, scene, observed):
            self.calls += 1
            return epi.Proposal(self.calls, "rogue", "REJECTED")
    rogue = Rogue()
    with pytest.raises(epi.CapExceeded):
        run_direct(cfg, "B2", tmp_path, proposer=rogue)
    assert rogue.calls == 3


def test_episode_after_a_rejection_proposes_on_the_same_scene(cfg, tmp_path):
    arch = SeqArchitect([set_len(65), set_len(16)])
    s, d, _ = run_direct(cfg, "B3-haiku", tmp_path, architect=arch)
    props = json.loads((d / "proposals.json").read_text())
    assert [p["base_hash"] for p in props] == [F1A, F1A]
    assert s["repaired"] and s["revisions"]["verdicts"] == ["REJECTED", "ACCEPTED"]


def test_inconsistent_version_stops_the_episode(cfg, tmp_path):
    op = ScriptedOperator(cfg, outcomes={1: ["SUCCESS", "FELL", "FELL"]})
    arch = SeqArchitect([set_len(16)])
    s, d, _ = run_direct(cfg, "B3-haiku", tmp_path, op=op, architect=arch)
    assert s["status"] == "INCONSISTENT" and arch.calls == 0 and s["final"]["successes"] == 1


def test_a_hung_play_call_stops_the_episode_without_a_proposal(cfg, tmp_path):
    op = ScriptedOperator(cfg, outcomes={1: ["FELL", "CALL_TIMEOUT", "FELL"]})
    arch = SeqArchitect([set_len(16)])
    s, d, _ = run_direct(cfg, "B3-haiku", tmp_path, op=op, architect=arch)
    # a CONNECTOR_ERROR attempt stops the episode at once (ADR 0011): no third attempt
    assert s["status"] == "CONNECTOR_ERROR" and arch.calls == 0
    assert s["versions"][0]["outcomes"] == ["FELL", "CONNECTOR_ERROR"] and s["attempts_used"] == 2


def test_build_failure_ends_the_episode(cfg, tmp_path):
    op = ScriptedOperator(cfg, build_ok=False)
    s, d, client = run_direct(cfg, "B1", tmp_path, op=op)
    assert s["status"] == "BUILD_FAILED" and s["attempts_used"] == 0 and s["revisions"]["used"] == 0


def test_provider_error_uses_the_slot_and_is_connector_error(cfg, tmp_path):
    arch = SeqArchitect([provider_error()])
    s, d, _ = run_direct(cfg, "B3-haiku", tmp_path, architect=arch)
    assert s["status"] == "CONNECTOR_ERROR" and s["architect_calls"] == 1 and s["revisions"]["errors"] == 1
    errs = list((d / "architect" / "replies").glob("*.error.json"))
    assert len(errs) == 1
    # the stored error replays as the same error, without a second call
    cache = epi.CachingArchitect(lambda: arch, d / "architect" / "replies", model_role="haiku")
    from worldsmith.architect import prompt as pr
    rows = [json.loads((d / "v1" / "attempts" / f"{i}.json").read_text()) for i in (1, 2, 3)]
    obs = epi.observed_of({"rows": rows})
    req = pr.make_request(pr.build_context(cfg["_scene"], obs, stage="M0", revision=1, max_revisions=3), cfg["_prompt"])
    assert errs[0].name == f"{req.request_sha}.error.json" == f"{M0_FIRST_REQUEST}.error.json"
    with pytest.raises(acl.ArchitectCallError):
        cache.propose(req)
    assert arch.calls == 1


def test_a_request_over_the_prompt_cap_is_never_sent(tmp_path):
    cfg2 = load_variant(tmp_path, lambda d: d["architect"].update(prompt_bytes_cap=1000))
    arch = SeqArchitect([set_len(16)])
    with pytest.raises(epi.PromptCapExceeded):
        run_direct(cfg2, "B3-haiku", tmp_path, architect=arch)
    assert arch.calls == 0


def test_episode_refuses_another_player(cfg, tmp_path):
    d = tmp_path / "B0" / "ep0"
    epi.init_episode(cfg, "B0", 0, d)
    ep = json.loads((d / "episode.json").read_text())
    ep["player"]["controller_sha256"] = M0_CONTROLLER_SHA
    (d / "episode.json").write_text(json.dumps(ep))
    with pytest.raises(epi.EpisodeError, match="refusing to mix players"):
        epi.run_episode(Direct(ScriptedOperator(cfg), d), cfg, d)


def test_bundles_are_never_overwritten(cfg, tmp_path):
    s, d, _ = run_direct(cfg, "B0", tmp_path)
    with pytest.raises(epi.BundleConflict):
        epi.init_episode(cfg, "B0", 0, d)
    with pytest.raises(epi.BundleConflict):
        epi.write_once(d / "summary.json", "{}")


# --------------------------------------------------------------------------- experiment: lock, resume

@pytest.fixture(scope="module")
def dry(tmp_path_factory, cfg):
    out = tmp_path_factory.mktemp("dry")
    res = dryrun.run_dry(cfg, out)
    return out / cfg["experiment"], res


def test_dry_run_results(dry):
    d, res = dry
    rows = {c["condition"]: c["episodes"][0] for c in res["conditions"]}
    assert res["dry_run"] is True and "DRY RUN" in res["disclosure"]
    assert rows["B0"]["final"]["verdict"] == "FAIL" and rows["B0"]["attempts_used"] == 3
    assert rows["B1"]["repaired"] and rows["B1"]["revisions"]["verdicts"] == ["ACCEPTED"]
    assert rows["B2"]["model_calls"] == 0 and rows["B2"]["revisions"]["used"] <= 3
    assert rows["B3-haiku"]["revisions"]["verdicts"] == ["ACCEPTED"] and rows["B3-haiku"]["repaired"]
    assert rows["B3-sonnet"]["revisions"]["verdicts"] == ["MALFORMED", "REJECTED", "ACCEPTED"]
    assert rows["B3-opus"]["revisions"]["verdicts"] == ["REJECTED", "REJECTED", "MALFORMED"]
    assert rows["B3-opus"]["architect_calls"] == 3 and rows["B3-opus"]["attempts_used"] == 3
    for c in res["conditions"]:
        for e in c["episodes"]:
            assert e["attempts_used"] <= 12 and e["architect_calls"] <= 3


def test_dry_run_bundles_validate_and_say_dry_run(dry, cfg):
    d, _ = dry
    for ep in sorted(d.glob("*/ep*")):
        assert epi.validate_episode_bundle(ep, cfg) == [], ep
        s = json.loads((ep / "summary.json").read_text())
        assert s["dry_run"] is True and "DRY RUN" in s["disclosure"]
        assert json.loads((ep / "episode.json").read_text())["dry_run"] is True
    lock = json.loads((d / "experiment.lock.json").read_text())
    assert lock["config_sha256"] == cfg["_sha256"] and lock["dry_run"] is True
    assert (d / "config.yaml").read_bytes() == CFG.read_bytes()


def test_architect_sees_what_the_m0_smoke_call_saw(dry):
    d, _ = dry
    props = json.loads((d / "B3-haiku" / "ep0" / "proposals.json").read_text())
    assert props[0]["request_sha"] == M0_FIRST_REQUEST


def test_position_samples_arrive_in_the_attempts(dry):
    d, _ = dry
    row = json.loads((d / "B0" / "ep0" / "v1" / "attempts" / "1.json").read_text())
    assert row["report"]["samples"] and row["stuck"]["result"] == "NOT_STUCK"
    trace = [json.loads(l) for l in (d / "B0" / "ep0" / "trace.jsonl").read_text().splitlines()]
    assert all(t["player"]["version"] == "p0.v2" and t["player"]["controller_sha"] == V2_SHA for t in trace)


def test_dry_run_bundle_holds_no_secret(dry):
    d, _ = dry
    for f in d.rglob("*"):
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            assert "sk-ant" not in text and "ANTHROPIC_API_KEY" not in text, f


def test_config_change_after_the_first_episode_is_refused(dry, tmp_path):
    d, _ = dry
    path = tmp_path / "m2_f1a.yaml"
    path.write_bytes(CFG.read_bytes() + b"# an edit after the runs started\n")
    cfg2 = ex.load_experiment(path, REPO_ROOT)
    with pytest.raises(ex.ExperimentLocked, match="new experiment id"):
        ex.Experiment(cfg2, d.parent, dry_run=True).run(lambda x: None)


def test_live_runner_refuses_a_dry_run_folder(dry, cfg):
    d, _ = dry
    with pytest.raises(ex.ExperimentLocked, match="dry-run"):
        ex.Experiment(cfg, d.parent).check_declared()


def test_mixed_players_are_refused(cfg, tmp_path):
    out = tmp_path / "out"
    cfg1 = load_variant(tmp_path, only("B0"))
    dryrun.run_dry(cfg1, out)
    f = out / cfg1["experiment"] / "B0" / "ep0" / "episode.json"
    ep = json.loads(f.read_text())
    ep["player"]["t_attempt_s"] = 120
    f.write_text(json.dumps(ep))
    with pytest.raises(ex.MixedPlayers):
        ex.Experiment(cfg1, out, dry_run=True).check_declared()


class CountingFixtures:
    def __init__(self, cfg):
        self.cfg, self.calls = cfg, 0

    def __call__(self, cond):
        inner = acl.FixtureArchitect.from_names(self.cfg["dry_run"]["fixtures"][cond["role"]])
        outer = self

        class Counting:
            model_role, model_id, provider = inner.model_role, inner.model_id, inner.provider

            def propose(self, req):
                outer.calls += 1
                return inner.propose(req)
        return Counting()


def test_interrupted_experiment_resumes_without_redoing_paid_calls(cfg, dry, tmp_path):
    out = tmp_path / "out"
    op = dryrun.FakeOperator(cfg)                       # Studio keeps its state across the restart
    counter = CountingFixtures(cfg)
    exp = ex.Experiment(cfg, out, dry_run=True)
    steps = 0
    while steps < 75:                                   # stop part-way (inside B3-sonnet)
        try:
            exp.run(lambda d: at.RelayClient(d / "calls"), counter)
            break
        except at.Pending as p:
            ep_dir = exp.dir / exp.pending()["episode_dir"]
            at.respond(ep_dir / "calls", p.index, op.answer(ep_dir, p.request))
            steps += 1
    pending = exp.pending()
    assert pending and pending["episode_dir"].startswith("B3-")
    before = {f: f.read_bytes() for f in exp.dir.rglob("calls/*.json")}
    paid_before = counter.calls
    stored_before = len(list(exp.dir.rglob("architect/replies/*.json")))
    assert paid_before == stored_before > 0

    exp2 = ex.Experiment(cfg, out, dry_run=True)        # a new process
    while True:
        try:
            res = exp2.run(lambda d: at.RelayClient(d / "calls"), counter)
            break
        except at.Pending as p:
            ep_dir = exp2.dir / exp2.pending()["episode_dir"]
            at.respond(ep_dir / "calls", p.index, op.answer(ep_dir, p.request))
    stored = list(exp2.dir.rglob("architect/replies/*.json"))
    assert counter.calls == len(stored) == 7          # each request paid exactly once (1 + 3 + 3)
    for f, b in before.items():
        assert f.read_bytes() == b                      # answered calls are never re-sent or rewritten
    assert res == dry[1]                               # same results as the uninterrupted dry run


def test_ctrl_c_during_an_architect_call_leaves_a_resumable_episode(cfg, tmp_path):
    d = tmp_path / "B3-haiku" / "ep0"
    epi.init_episode(cfg, "B3-haiku", 0, d)
    op = ScriptedOperator(cfg)
    arch = SeqArchitect([KeyboardInterrupt(), set_len(16)])
    with pytest.raises(KeyboardInterrupt):
        epi.run_episode(Direct(op, d), cfg, d, architect_factory=lambda: arch)
    assert not (d / "summary.json").exists() and not list((d / "architect").rglob("*.json"))
    s = epi.run_episode(Direct(ScriptedOperator(cfg), d), cfg, d, architect_factory=lambda: arch)
    assert s["repaired"] and arch.calls == 2 and epi.validate_episode_bundle(d, cfg) == []


# --------------------------------------------------------------------------- budget

def _price(model, *, prompt_tokens, max_tokens, cache_ttl):
    return {"claude-haiku-5-5": 1, "claude-sonnet-5-5": 10, "claude-opus-5-5": 100}[model] * 1e-6 * (prompt_tokens + max_tokens)


def test_worst_case_counts_every_possible_call(cfg):
    wc = ex.worst_case(cfg, _price)
    rows = {r["condition"]: r for r in wc["conditions"]}
    assert set(rows) == {"B3-haiku", "B3-sonnet", "B3-opus"}
    assert rows["B3-opus"]["calls_max"] == 3 and rows["B3-opus"]["per_call_usd"] == pytest.approx(100e-6 * (24000 + 32000))
    assert wc["total_usd"] == pytest.approx(sum(r["usd_max"] for r in rows.values()))


def test_remaining_worst_case_drops_with_stored_replies(cfg, dry):
    d, _ = dry
    done = ex.worst_case(cfg, _price, ex.Experiment(cfg, d.parent, dry_run=True))
    assert done["remaining_usd"] == 0


def test_headroom_check():
    ex.check_headroom(2.0, 2.0)
    with pytest.raises(ex.BudgetRefused):
        ex.check_headroom(2.961, 1.0)


@needs_jzapi
def test_pilot_worst_case_at_jzapi_prices(cfg):
    price = lambda m, **kw: jzapi.worst_case_usd(m, batch=False, **kw)
    wc = ex.worst_case(cfg, price)
    per = {r["condition"]: r["per_call_usd"] for r in wc["conditions"]}
    assert per == pytest.approx({"B3-haiku": 0.007, "B3-sonnet": 0.22, "B3-opus": 0.76})
    assert wc["total_usd"] == pytest.approx(2.961)


@needs_jzapi
def test_request_bound_is_jzapis(cfg):
    from jzapi.api import bytes_upper_bound
    from worldsmith.architect import prompt as pr
    obs = {"attempts": [], "episode_tags": [], "n": 0, "successes": 0}
    req = pr.make_request(pr.build_context(cfg["_scene"], obs, stage="M0", revision=1), cfg["_prompt"])
    for cache in (True, False):
        p = jzapi.build_params(model="haiku", max_tokens=10, system=req.system, user=req.user, cache_system=cache)
        assert epi.request_bytes_bound(req.system, req.user, cache) == bytes_upper_bound(p)


# --------------------------------------------------------------------------- CLI

def _cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_experiment", REPO_ROOT / "scripts" / "run_experiment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_dry_run_makes_no_network_call(tmp_path, monkeypatch, capsys, dry):
    def no_net(*a, **k):
        raise AssertionError("network used in a dry run")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    rc = _cli().main(["--config", str(CFG), "--dry-run", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "NOT A RESULT" in out
    if jzapi is not None:
        assert "experiment worst case: $2.961000" in out
    res = json.loads((tmp_path / "m2_f1a_pilot" / "results.json").read_text())
    assert res == dry[1]                                # deterministic across runs


def test_cli_refuses_a_changed_config_after_runs(tmp_path, capsys):
    path = variant(tmp_path, only("B0"))
    assert _cli().main(["--config", str(path), "--dry-run", "--out", str(tmp_path / "o")]) == 0
    path.write_text(path.read_text() + "# edited\n")
    assert _cli().main(["--config", str(path), "--dry-run", "--out", str(tmp_path / "o")]) == 4


def _switch_files(tmp_path, approved=True, cap=90, live=True):
    b = tmp_path / "budgets.yaml"
    b.write_text(yaml.safe_dump({"total_usd": 400, "jobs": {"architect_ladder": {"cap_usd": cap, "approved": approved}}}))
    a = tmp_path / "architect.yaml"
    a.write_text(yaml.safe_dump({"schema": "worldsmith.architect_local/0.1", "live": live}))
    return ["--budgets", str(b), "--architect-config", str(a)]


@needs_jzapi
@pytest.mark.parametrize("kw", [{"approved": False}, {"live": False}, {"cap": 1}])
def test_cli_live_step_refuses_without_switches_or_headroom(tmp_path, capsys, kw):
    out = tmp_path / "exp"
    rc = _cli().main(["--config", str(CFG), "step", "--out", str(out), *_switch_files(tmp_path, **kw)])
    assert rc == 4
    assert not out.exists()                             # nothing started


@needs_jzapi
def test_cli_live_step_stops_at_the_first_studio_call(tmp_path, capsys):
    out = tmp_path / "exp"
    rc = _cli().main(["--config", str(CFG), "step", "--out", str(out), *_switch_files(tmp_path)])
    text = capsys.readouterr().out
    assert rc == 3 and "PENDING B0/ep0 call 001 get_studio_mode" in text
    assert (out / "m2_f1a_pilot" / "experiment.lock.json").is_file()
    rc = _cli().main(["--config", str(CFG), "status", "--out", str(out)])
    assert rc == 0
