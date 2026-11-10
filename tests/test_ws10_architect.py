"""WS-10: architect client. JSON-only output, fixture mode, budget guard.

SPEC section 7 item 6 (inputs disclosure), section 11 (architect call log), section 12 (B3, at
most 3 revisions, rejects count), section 15 (WS-10: fixture replay is deterministic; a malformed
output counts as a rejected revision; no live call without approval config). ROADMAP section 4.3
(B3 ladder with pinned IDs, B4 local later) and 4.4 (red-team architect, never a baseline).

Offline only: no network, no key, no jzapi needed (a fake session stands in for it).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from _repo import REPO_ROOT
from _scenes import F1A, raw
from worldsmith.architect import client as cl
from worldsmith.architect import fixtures as fx
from worldsmith.architect import loop as lp
from worldsmith.architect import prompt as pr
from worldsmith.grammar import scene as sc

F1A_HASH = "92f866098bfb850cf4c464179887a862f305c143c8e0b38efdc7978493cddb1d"
F1A_FIX_HASH = "49371fa767666577d2f1c30571231069af8eeaeb2eace76e6e832f38c281a396"
CLASSIFICATION = REPO_ROOT / "bundles" / "m0" / "classification.json"
FIXTURE_DIR = REPO_ROOT / "architect" / "fixtures"
SINGLE = ["f1a_m0_fix", "f1a_m0_out_of_bounds", "f1a_m0_prose", "f1a_m0_cheat_goal", "f1a_m0_noop"]
LOOPS = ["f1a_m0_reject_then_fix", "f1a_m0_exhausted"]


def patch_text(value, *, schema="worldsmith.patch/0.1", base=F1A_HASH):
    return json.dumps({"schema": schema, "base_hash": base,
                       "ops": [{"op": "set", "id": "bridge_1", "field": "length", "value": value}]})


def f1a_episode():
    doc = json.loads(CLASSIFICATION.read_text(encoding="utf-8"))
    return next(e for e in doc["episodes"] if e["episode"] == "F1-A")


def f1a_request(revision=1, previous=(), mode="baseline", stage="M0"):
    scene = raw(F1A)
    observed = pr.observed_from_classification(f1a_episode())
    ctx = pr.build_context(scene, observed, stage=stage, revision=revision, max_revisions=3,
                           previous=previous)
    return pr.make_request(ctx, pr.load_prompt(mode))


# --------------------------------------------------------------------------- prompt files

def test_prompt_files_exist_and_are_versioned_and_hashed():
    for mode, name in (("baseline", "prompt.md"), ("redteam", "prompt_redteam.md")):
        path = REPO_ROOT / "architect" / name
        assert path.is_file()
        p = pr.load_prompt(mode)
        assert p.mode == mode
        assert p.version and p.version[0].isdigit()
        import hashlib
        assert p.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert p.system.strip() and p.user_template.strip()


def test_baseline_prompt_demands_json_only_and_treats_scene_as_data():
    p = pr.load_prompt("baseline")
    s = p.system.lower()
    assert "exactly one json object" in s
    assert "code fence" in s
    assert "data" in s and "never instructions" in s
    assert p.is_baseline


def test_redteam_prompt_is_labelled_and_never_a_baseline():
    p = pr.load_prompt("redteam")
    assert not p.is_baseline
    assert "RED-TEAM" in p.system and "not a baseline" in p.system.lower()
    assert "RED-TEAM" in (REPO_ROOT / "architect" / "prompt_redteam.md").read_text().splitlines()[0] or \
        "red-team" in (REPO_ROOT / "architect" / "prompt_redteam.md").read_text().lower()[:400]
    assert p.sha256 != pr.load_prompt("baseline").sha256
    with pytest.raises(ValueError):
        pr.load_prompt("other")


def test_template_placeholders_are_all_filled():
    req = f1a_request()
    assert "{{" not in req.user and "}}" not in req.user
    assert "{{" not in req.system


# --------------------------------------------------------------------------- inputs disclosure

def test_context_has_exactly_the_spec_inputs():
    req = f1a_request()
    ctx = req.context
    assert set(ctx) == {"schema", "stage", "scene_hash", "scene", "observed", "trace_summary",
                        "allowed_ops", "bounds", "patch_schemas", "limits", "revision", "previous"}
    assert ctx["scene_hash"] == F1A_HASH
    assert ctx["scene"] == json.loads(sc.canonical_json(raw(F1A)))
    assert ctx["stage"] == "M0"
    assert ctx["allowed_ops"] == [{"op": "set", "type": "Bridge", "id": "bridge_1", "field": "length"}]
    assert ctx["bounds"] == {"Bridge.length": {"min": 1, "max": 64}}
    assert list(ctx["patch_schemas"]) == ["worldsmith.patch/0.1"]
    assert ctx["limits"] == {"max_ops": 3}
    assert ctx["revision"] == {"this": 1, "max": 3}
    assert ctx["previous"] == []


def test_m1_context_lists_the_m1_ops_and_both_schemas():
    ctx = f1a_request(stage="M1").context
    assert {"op": "move", "type": "Lever", "id": "lever_1", "field": "pos"} in ctx["allowed_ops"]
    assert {"op": "add", "type": "Wall", "id": None, "field": None} in ctx["allowed_ops"]
    assert list(ctx["patch_schemas"]) == ["worldsmith.patch/0.1", "worldsmith.patch/0.2"]
    assert "Wall.size" in ctx["bounds"] and "Bridge.width" in ctx["bounds"]


def test_observed_tags_keep_events_and_classification_but_withhold_paired_fix():
    obs = pr.observed_from_classification(f1a_episode())
    assert obs["n"] == 3 and obs["successes"] == 0
    a = obs["attempts"][0]
    assert set(a) <= {"attempt", "outcome", "tags", "cause", "events", "detail", "fell_in_area",
                      "stuck", "hypothesis"}
    assert a["outcome"] == "FELL"
    assert a["events"] == ["LEVER_ACTIVATED", "GATE_OPENED", "KILL_FLOOR_TOUCHED"]
    assert a["stuck"] == "NOT_STUCK"
    # The hypothesis was confirmed with post-fix evidence; the architect gets the pre-fix view.
    assert a["cause"] == "UNCLASSIFIED"
    assert "BRIDGE_SHORT_HYPOTHESIS" not in a["tags"]
    assert a["hypothesis"] == {"tag": "BRIDGE_SHORT_HYPOTHESIS", "status": "unconfirmed",
                               "conditions": {"fell_in_gap_area": True, "geometry": True}}


def test_trace_summary_is_short_and_from_observed():
    req = f1a_request()
    lines = req.context["trace_summary"]
    assert 1 <= len(lines) <= 8
    assert lines[0].startswith("0/3 SUCCESS")
    assert any("KILL_FLOOR_TOUCHED at [0.0, -14.29, 49.31]" in l for l in lines)


def test_no_harness_internals_reach_the_architect():
    req = f1a_request()
    text = req.system + req.user
    ep = f1a_episode()
    forbidden = [ep["controller_sha256"], ep["config_sha256"], ep["run_id"], F1A_FIX_HASH,
                 "F1-A-fix", "paired_fix", "controller", "nonce", "raw_sha256", "route",
                 "Autopilot", "Jev", "gap_area_margin"]
    forbidden += [a["nonce"] for a in ep["attempts"]] + [a["raw_sha256"] for a in ep["attempts"]]
    for s in forbidden:
        assert s not in text, s


def test_request_is_hashed_and_independent_of_dict_order():
    a = f1a_request()
    scene = raw(F1A)
    scene["entities"] = list(reversed(scene["entities"]))
    obs = pr.observed_from_classification(f1a_episode())
    ctx = pr.build_context(dict(reversed(list(scene.items()))), obs, stage="M0", revision=1,
                           max_revisions=3, previous=())
    b = pr.make_request(ctx, pr.load_prompt("baseline"))
    assert len(a.request_sha) == 64 and a.request_sha == b.request_sha
    assert a.context_sha == b.context_sha
    assert a.request_sha != f1a_request(mode="redteam").request_sha
    assert a.request_sha != f1a_request(revision=2).request_sha


def test_build_context_rejects_bad_revision_numbers():
    obs = pr.observed_from_classification(f1a_episode())
    for rev, mx in ((0, 3), (4, 3), (2, 1), (1, 4)):
        with pytest.raises(ValueError):
            pr.build_context(raw(F1A), obs, stage="M0", revision=rev, max_revisions=mx, previous=())
    with pytest.raises(ValueError):
        pr.build_context(raw(F1A), obs, stage="M9", revision=1, max_revisions=3, previous=())


# --------------------------------------------------------------------------- strict parsing

@pytest.mark.parametrize("text", [patch_text(16), "  \n" + patch_text(16) + "\n\n"])
def test_parse_accepts_one_json_object(text):
    r = lp.parse_response(text, stop_reason="end_turn")
    assert r.malformed is None and r.patch["ops"][0]["value"] == 16


@pytest.mark.parametrize("text,stop,reason", [
    ("", "end_turn", "EMPTY"),
    ("   \n", "end_turn", "EMPTY"),
    ("Here is the patch: " + patch_text(16), "end_turn", "NOT_JSON"),
    ("I would lengthen the bridge to 16 studs.", "end_turn", "NOT_JSON"),
    ("```json\n" + patch_text(16) + "\n```", "end_turn", "CODE_FENCE"),
    ("```\n" + patch_text(16) + "\n```", "end_turn", "CODE_FENCE"),
    (patch_text(16) + "\nThis closes the gap.", "end_turn", "TRAILING_TEXT"),
    (patch_text(16) + patch_text(17), "end_turn", "TRAILING_TEXT"),
    ('{"schema": "worldsmith.patch/0.1", "schema": "x", "base_hash": "a", "ops": []}', "end_turn", "DUPLICATE_KEY"),
    ('{"schema": "worldsmith.patch/0.1", "base_hash": "a", "ops": [{"value": NaN}]}', "end_turn", "NON_FINITE"),
    ('{"schema": "worldsmith.patch/0.1", "ops": [', "end_turn", "NOT_JSON"),
    ("[1, 2]", "end_turn", "NOT_JSON"),
    ('{"answer": 16}', "end_turn", "NOT_A_PATCH"),
    (json.dumps(raw(F1A)), "end_turn", "NOT_A_PATCH"),
    (patch_text(16)[:30], "max_tokens", "TRUNCATED"),
    (patch_text(16), "max_tokens", "TRUNCATED"),
    ("", "refusal", "REFUSED"),
])
def test_parse_marks_everything_else_malformed(text, stop, reason):
    r = lp.parse_response(text, stop_reason=stop)
    assert r.patch is None
    assert r.malformed["reason"] == reason
    assert reason in lp.MALFORMED_REASONS


# --------------------------------------------------------------------------- revision loop

class Scripted:
    """An architect that returns canned texts in order (test helper)."""
    model_role = "scripted"
    model_id = "scripted:test"
    provider = "test"

    def __init__(self, texts):
        self.texts = list(texts)
        self.requests = []

    def propose(self, req):
        self.requests.append(req)
        return cl.RawResponse(text=self.texts.pop(0), model_role=self.model_role,
                              model_id=self.model_id, provider=self.provider, stop_reason="end_turn")


def run(texts, max_revisions=3, stage="M0"):
    arch = Scripted(texts)
    log = lp.CallLog()
    res = lp.propose_until_valid(arch, scene=raw(F1A),
                                 observed=pr.observed_from_classification(f1a_episode()),
                                 stage=stage, budget=lp.RevisionBudget(max_revisions),
                                 prompt=pr.load_prompt("baseline"), episode="t", log=log)
    return arch, log, res


def test_valid_patch_is_accepted_on_first_revision():
    arch, log, res = run([patch_text(16)])
    assert res.accepted is not None and res.accepted.index == 1
    assert res.accepted.verdict == "ACCEPTED"
    assert res.used == 1 and res.rejected == 0 and not res.exhausted
    assert len(log.records) == 1


def test_malformed_counts_as_a_rejected_revision():
    arch, log, res = run(["I think the bridge is short.", patch_text(16)])
    assert [r.verdict for r in res.revisions] == ["MALFORMED", "ACCEPTED"]
    assert res.used == 2 and res.rejected == 1
    assert res.accepted.index == 2
    # The validator never saw the malformed text.
    assert log.records[0]["validator_result"] is None
    assert log.records[0]["malformed"]["reason"] == "NOT_JSON"


def test_rejects_and_malformed_exhaust_three_revisions():
    arch, log, res = run([patch_text(65), "```json\n" + patch_text(16) + "\n```", patch_text(6)])
    assert [r.verdict for r in res.revisions] == ["REJECTED", "MALFORMED", "REJECTED"]
    assert res.accepted is None and res.exhausted and res.used == 3 and res.rejected == 3
    assert arch.texts == []


def test_budget_refuses_a_fourth_call_and_more_than_three_revisions():
    b = lp.RevisionBudget(3)
    for _ in range(3):
        b.consume()
    assert b.exhausted and b.remaining == 0
    with pytest.raises(lp.BudgetExhausted):
        b.consume()
    for bad in (0, 4, -1, 3.0, True):
        with pytest.raises(ValueError):
            lp.RevisionBudget(bad)
    arch = Scripted([patch_text(16)])
    with pytest.raises(lp.BudgetExhausted):
        lp.revise(arch, scene=raw(F1A), observed={"attempts": [], "n": 0, "successes": 0,
                                                  "episode_tags": []},
                  stage="M0", budget=b, previous=(), prompt=pr.load_prompt("baseline"))
    assert arch.requests == []


def test_previous_verdicts_feed_the_next_request():
    arch, log, res = run(["prose", patch_text(65), patch_text(16)])
    prev = arch.requests[2].context["previous"]
    assert [p["verdict"] for p in prev] == ["MALFORMED", "REJECTED"]
    assert prev[0] == {"revision": 1, "verdict": "MALFORMED", "malformed_reason": "NOT_JSON",
                       "patch": None, "violations": [], "warnings": []}
    assert prev[1]["violations"] == [{"code": "OUT_OF_BOUNDS", "ref": "ops[0]"}] or \
        [v["code"] for v in prev[1]["violations"]] == ["OUT_OF_BOUNDS"]
    assert prev[1]["patch"]["ops"][0]["value"] == 65
    assert arch.requests[2].context["revision"] == {"this": 3, "max": 3}
    shas = [r.request_sha for r in arch.requests]
    assert len(set(shas)) == 3


def test_cheat_is_rejected_by_the_validator_and_logged_with_codes():
    cheat = json.dumps({"schema": "worldsmith.patch/0.2", "base_hash": F1A_HASH,
                        "ops": [{"op": "move", "id": "goal", "pos": [0, 0, 30]}]})
    arch, log, res = run([cheat], max_revisions=1)
    assert res.revisions[0].verdict == "REJECTED"
    codes = {v["code"] for v in log.records[0]["validator_result"]["violations"]}
    assert "STAGE_OP_FORBIDDEN" in codes


def test_call_log_record_has_the_spec_fields():
    arch, log, res = run([patch_text(16)])
    rec = log.records[0]
    for k in ("model_role", "model_id", "request_sha", "response_sha", "patch", "validator_result",
              "usage_if_reported", "latency_s"):
        assert k in rec, k
    assert rec["schema"] == "worldsmith.architect_call/0.1"
    assert rec["prompt_mode"] == "baseline" and rec["prompt_sha"] == pr.load_prompt("baseline").sha256
    assert rec["revision"] == 1 and rec["max_revisions"] == 3 and rec["episode"] == "t"
    assert rec["verdict"] == "ACCEPTED" and rec["validator_result"]["ok"] is True
    assert rec["candidate_scene_hash"] and len(rec["candidate_scene_hash"]) == 64
    import hashlib
    assert rec["response_sha"] == hashlib.sha256(patch_text(16).encode()).hexdigest()
    assert rec["request_sha"] == arch.requests[0].request_sha
    assert rec["context_sha"] == arch.requests[0].context_sha


def test_call_log_writes_jsonl(tmp_path):
    path = tmp_path / "calls.jsonl"
    log = lp.CallLog(path)
    lp.propose_until_valid(Scripted(["x", patch_text(16)]), scene=raw(F1A),
                           observed=pr.observed_from_classification(f1a_episode()), stage="M0",
                           budget=lp.RevisionBudget(3), prompt=pr.load_prompt("baseline"), log=log)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and [json.loads(l)["verdict"] for l in lines] == ["MALFORMED", "ACCEPTED"]


def test_a_provider_error_uses_the_call_and_is_logged_then_raised():
    class Boom(Scripted):
        def propose(self, req):
            raise cl.ArchitectCallError("connection reset")
    b = lp.RevisionBudget(3)
    log = lp.CallLog()
    with pytest.raises(cl.ArchitectCallError):
        lp.revise(Boom([]), scene=raw(F1A), observed=pr.observed_from_classification(f1a_episode()),
                  stage="M0", budget=b, previous=(), prompt=pr.load_prompt("baseline"), log=log)
    assert b.used == 1 and b.rejected == 0
    assert log.records[0]["verdict"] == "ERROR" and log.records[0]["error"]


# --------------------------------------------------------------------------- fixtures

def test_fixture_files_are_hand_written_and_well_formed():
    names = sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))
    assert names == sorted(SINGLE + LOOPS)
    for name in names:
        doc = fx.load_fixture(name)
        assert doc["schema"] == "worldsmith.architect_fixture/0.1"
        assert "not a model output" in doc["provenance"]
        assert doc["stage"] == "M0" and doc["scene"] == "scenes/F1-A/scene.json"
        assert doc["prompt_mode"] == "baseline"
        for t in doc["turns"]:
            assert len(t["request_sha"]) == 64


def test_fixture_request_shas_are_up_to_date():
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "architect_fixtures.py"),
                          "--check"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("name", SINGLE + LOOPS)
def test_fixture_replay_matches_its_declared_outcome(name):
    doc = fx.load_fixture(name)
    res, log = fx.replay(name)
    assert [r.verdict for r in res.revisions] == [t["expect"]["verdict"] for t in doc["turns"]]
    for rev, t in zip(res.revisions, doc["turns"]):
        assert set(t["expect"].get("codes", [])) <= set(rev.codes), (name, rev.codes)
        if "malformed_reason" in t["expect"]:
            assert rev.malformed["reason"] == t["expect"]["malformed_reason"]
    e = doc["expect"]
    assert res.used == e["revisions_used"] and res.rejected == e["rejected"]
    assert res.exhausted == e["exhausted"]
    assert (res.accepted.index if res.accepted else None) == e["accepted_on"]
    for rec in log.records:
        assert rec["provider"] == "fixture" and rec["model_role"] == "fixture"
        assert rec["model_id"] == f"fixture:{name}" and rec["latency_s"] is None


def test_single_fixture_outcomes():
    verdicts = {n: fx.replay(n)[0].revisions[0] for n in SINGLE}
    assert verdicts["f1a_m0_fix"].verdict == "ACCEPTED"
    assert verdicts["f1a_m0_out_of_bounds"].codes == {"OUT_OF_BOUNDS"}
    assert verdicts["f1a_m0_prose"].malformed["reason"] == "NOT_JSON"
    assert "STAGE_OP_FORBIDDEN" in verdicts["f1a_m0_cheat_goal"].codes
    assert verdicts["f1a_m0_noop"].codes == {"NOOP_PATCH"}


def test_fixture_replay_is_deterministic():
    for name in SINGLE + LOOPS:
        a = fx.replay(name)[1].dumps()
        b = fx.replay(name)[1].dumps()
        assert a == b


def test_fixture_replay_is_identical_across_processes():
    code = ("import sys; from worldsmith.architect import fixtures as fx; "
            "sys.stdout.write(fx.replay('f1a_m0_reject_then_fix')[1].dumps())")
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.add(subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                                capture_output=True, text=True, check=True).stdout)
    assert len(outs) == 1 and outs.pop().strip()


def test_first_turns_share_one_request_hash():
    shas = {fx.load_fixture(n)["turns"][0]["request_sha"] for n in SINGLE + LOOPS}
    assert len(shas) == 1
    assert shas.pop() == f1a_request().request_sha


def test_fixture_architect_misses_loudly_on_an_unknown_request():
    arch = cl.FixtureArchitect.from_names(["f1a_m0_fix"])
    with pytest.raises(cl.FixtureMiss):
        arch.propose(f1a_request(revision=2))
    r = arch.propose(f1a_request())
    assert r.provider == "fixture" and r.latency_s is None and r.usage is None


def test_fixture_architect_refuses_conflicting_fixtures():
    with pytest.raises(cl.FixtureConflict):
        cl.FixtureArchitect.from_names(["f1a_m0_fix", "f1a_m0_prose"])


# --------------------------------------------------------------------------- config and guard

def test_ladder_config_pins_the_three_models():
    cfg = cl.load_ladder_config()
    assert cfg["job"] == "architect_ladder"
    assert {r: cfg["roles"][r]["model_id"] for r in cfg["roles"]} == {
        "haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
    for r in cfg["roles"].values():
        assert isinstance(r["max_tokens"], int) and r["max_tokens"] >= 4000
    assert cfg["max_revisions"] == 3
    assert cfg["sampling"] == "api_default"
    text = (REPO_ROOT / "config" / "architect_ladder.yaml").read_text().lower()
    assert "thinking" in text and "max_tokens" in text


@pytest.mark.parametrize("mutate", [
    lambda c: c["roles"]["opus"].update(model_id="claude-opus-4-1"),
    lambda c: c["roles"].pop("haiku"),
    lambda c: c.update(max_revisions=4),
    lambda c: c["roles"]["sonnet"].update(temperature=0.2),
    lambda c: c["roles"]["sonnet"].update(max_tokens=0),
    lambda c: c.update(extra=1),
])
def test_ladder_config_is_strict(tmp_path, mutate):
    c = yaml.safe_load((REPO_ROOT / "config" / "architect_ladder.yaml").read_text())
    mutate(c)
    p = tmp_path / "l.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(cl.ArchitectConfigError):
        cl.load_ladder_config(p)


def test_local_architect_config_is_git_ignored_and_example_is_off():
    ex = yaml.safe_load((REPO_ROOT / "config" / "architect.example.yaml").read_text())
    assert ex["live"] is False
    out = subprocess.run(["git", "check-ignore", "-q", "config/architect.yaml"], cwd=REPO_ROOT)
    assert out.returncode == 0, "config/architect.yaml must be git-ignored"


def budgets(tmp_path, approved):
    p = tmp_path / "budgets.yaml"
    p.write_text(yaml.safe_dump({"total_usd": 400, "jobs": {
        "architect_ladder": {"cap_usd": 90, "approved": approved, "spend_row": "Architect ladder"}}}))
    return p


def local_cfg(tmp_path, live, **extra):
    p = tmp_path / "architect.yaml"
    p.write_text(yaml.safe_dump({"schema": "worldsmith.architect_local/0.1", "live": live,
                                 "local": {"endpoint": None, "model": None}, **extra}))
    return p


class FakeSession:
    def __init__(self):
        self.calls = []

    def message(self, **kw):
        self.calls.append(kw)

        class R:
            text = patch_text(16)
            model_id = "claude-sonnet-5-5"
            request_id = "req_fake"
            stop_reason = "end_turn"
            usage = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0,
                     "cache_read_input_tokens": 0}
            cost_usd = 0.0001
        return R()


@pytest.mark.parametrize("live,approved", [(False, False), (False, True), (True, False)])
def test_jzapi_architect_refuses_without_both_approvals(tmp_path, live, approved):
    made = []
    with pytest.raises(cl.LiveCallRefused):
        cl.JzapiArchitect("sonnet", config_path=local_cfg(tmp_path, live),
                          budgets_path=budgets(tmp_path, approved),
                          session_factory=lambda *a, **k: made.append(1) or FakeSession())
    assert made == []


def test_jzapi_architect_refuses_without_local_config_or_budgets(tmp_path):
    with pytest.raises(cl.LiveCallRefused):
        cl.JzapiArchitect("sonnet", config_path=tmp_path / "missing.yaml",
                          budgets_path=budgets(tmp_path, True), session_factory=lambda *a, **k: FakeSession())
    with pytest.raises(cl.LiveCallRefused):
        cl.JzapiArchitect("sonnet", config_path=local_cfg(tmp_path, True),
                          budgets_path=tmp_path / "nope.yaml", session_factory=lambda *a, **k: FakeSession())


@pytest.mark.parametrize("live", ["true", "yes", 1, None])
def test_jzapi_architect_needs_live_exactly_true(tmp_path, live):
    with pytest.raises(cl.LiveCallRefused):
        cl.JzapiArchitect("sonnet", config_path=local_cfg(tmp_path, live),
                          budgets_path=budgets(tmp_path, True), session_factory=lambda *a, **k: FakeSession())


def test_jzapi_architect_refuses_unknown_role(tmp_path):
    with pytest.raises(cl.ArchitectConfigError):
        cl.JzapiArchitect("gpt", config_path=local_cfg(tmp_path, True),
                          budgets_path=budgets(tmp_path, True), session_factory=lambda *a, **k: FakeSession())


def test_the_real_budgets_file_does_not_approve_the_ladder_today():
    p = REPO_ROOT.parent / "JzLab" / "budgets.yaml"
    if not p.is_file():
        pytest.skip("JzLab is not checked out beside worldsmith")
    jobs = yaml.safe_load(p.read_text())["jobs"]
    if jobs["architect_ladder"].get("approved") is True:
        pytest.skip("approved by the owner since WS-10")
    assert cl.budget_job_approved("architect_ladder", p) is False


def test_jzapi_architect_with_both_approvals_calls_the_session_without_sampling_params(tmp_path):
    sessions = []

    def factory(job, **kw):
        s = FakeSession()
        sessions.append((job, kw, s))
        return s
    arch = cl.JzapiArchitect("sonnet", config_path=local_cfg(tmp_path, True),
                             budgets_path=budgets(tmp_path, True), session_factory=factory)
    assert arch.model_id == "claude-sonnet-5-5" and arch.model_role == "sonnet"
    req = f1a_request()
    r = arch.propose(req)
    job, kw, s = sessions[0]
    assert job == "architect_ladder" and Path(kw["budgets_path"]) == tmp_path / "budgets.yaml"
    call = s.calls[0]
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(call)
    assert call["model"] == "claude-sonnet-5-5"
    assert call["max_tokens"] == cl.load_ladder_config()["roles"]["sonnet"]["max_tokens"]
    assert call["system"] == req.system and call["user"] == req.user
    assert call["cache_system"] is True
    assert r.text == patch_text(16) and r.provider == "jzapi" and r.usage["output_tokens"] == 5
    assert r.cost_usd == 0.0001 and r.latency_s is not None and r.latency_s >= 0


def test_jzapi_batch_request_uses_jzapi_request(tmp_path, monkeypatch):
    seen = []

    class FakeJz:
        @staticmethod
        def request(custom_id, **kw):
            seen.append((custom_id, kw))
            return {"custom_id": custom_id, "params": kw}
    monkeypatch.setitem(sys.modules, "jzapi", FakeJz)
    arch = cl.JzapiArchitect("haiku", config_path=local_cfg(tmp_path, True),
                             budgets_path=budgets(tmp_path, True), session_factory=lambda *a, **k: FakeSession())
    req = f1a_request()
    out = arch.batch_request(req)
    cid, kw = seen[0]
    assert len(cid) <= 64 and cid.startswith("haiku-") and req.request_sha[:20] in cid
    assert not {"temperature", "top_p", "top_k"} & set(kw)
    assert kw["model"] == "claude-haiku-5-5" and out["custom_id"] == cid

    class Item:
        custom_id = cid
        type = "succeeded"
        text = patch_text(16)
        model_id = "claude-haiku-5-5"
        stop_reason = "end_turn"
        usage = {"output_tokens": 3}
        cost_usd = 0.00001
        error = None
    r = arch.response_from_batch_item(Item, req)
    assert r.text == patch_text(16) and r.latency_s is None
    Item.type = "errored"
    with pytest.raises(cl.ArchitectCallError):
        arch.response_from_batch_item(Item, req)
    Item.type = "succeeded"
    Item.custom_id = "haiku-other"
    with pytest.raises(cl.ArchitectCallError):
        arch.response_from_batch_item(Item, req)


def test_local_architect_is_a_placeholder_that_refuses_without_config(tmp_path):
    with pytest.raises(cl.LiveCallRefused):
        cl.LocalArchitect(config_path=tmp_path / "missing.yaml")
    with pytest.raises(cl.LiveCallRefused):
        cl.LocalArchitect(config_path=local_cfg(tmp_path, False))
    p = tmp_path / "a2.yaml"
    p.write_text(yaml.safe_dump({"schema": "worldsmith.architect_local/0.1", "live": False,
                                 "local": {"endpoint": "http://127.0.0.1:8000/v1", "model": "gemma-4-31b"}}))
    arch = cl.LocalArchitect(config_path=p)
    assert arch.provider == "local" and arch.model_id == "gemma-4-31b"
    with pytest.raises(NotImplementedError):
        arch.propose(f1a_request())


def test_architect_package_never_imports_jzapi_at_import_time():
    code = ("import sys; import worldsmith.architect.client, worldsmith.architect.loop, "
            "worldsmith.architect.fixtures; print('jzapi' in sys.modules or 'anthropic' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == "False"
