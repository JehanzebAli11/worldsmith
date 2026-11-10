"""WS-09: the capped random valid sampler, baseline B2 (SPEC section 12).

B2 proposes at most 3 raw patches per episode, validator rejects included; no unlimited
resampling; its failures are reported. The sampler draws ops uniformly from the stage's allowed
op set (`validator.patch.STAGES`) and values uniformly within `grammar/bounds.yaml`. It never
consults the validator to filter: every raw proposal is validated once and counts against the
budget, accepted or not. Seeded and replayable across processes.

The offline summary (bundles/b2_offline/) is a validator/graph-only statistic: how often a random
patch could even be tried, never a play result or a repair rate (no Studio runs here).
"""

import copy
import gzip
import hashlib
import json
import os
import random
import subprocess
import sys

import pytest

from _repo import REPO_ROOT
from _scenes import F1A, F1E, raw
from worldsmith.baselines import random_valid as rv
from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt
from worldsmith.validator import validate as vd

OFFLINE = REPO_ROOT / "bundles" / "b2_offline"
SUMMARY = OFFLINE / "summary.json"
RECORDS = OFFLINE / "proposals.jsonl.gz"   # gzip of the JSONL; hashes are of the JSONL text
SOURCE = REPO_ROOT / "baselines" / "random_valid.py"
DECOR_PALETTE = {"crate", "pillar", "lamp", "plant", "banner"}


@pytest.fixture(scope="module")
def f1a():
    return raw(F1A)


@pytest.fixture(scope="module")
def f1a_hash(f1a):
    return sc.canonical_hash(f1a)


def _on_grid(x):
    return abs(x * 1000 - round(x * 1000)) < 1e-6


def _bound(key):
    return sc.load_bounds()[key]


# --- the budget (SPEC section 12: at most 3 raw proposals, rejects included) ------------------

def test_budget_constant_is_the_spec_cap():
    assert rv.MAX_PROPOSALS == 3


@pytest.mark.parametrize("stage", ["M0", "M1"])
def test_episode_returns_exactly_the_budget(f1a, stage):
    recs = rv.propose_episode(f1a, stage, seed=0)
    assert len(recs) == 3
    assert [r.index for r in recs] == [0, 1, 2]
    assert len(rv.propose_episode(f1a, stage, seed=0, budget=1)) == 1


@pytest.mark.parametrize("budget", [0, -1, 4, 10])
def test_budget_outside_one_to_three_is_refused(f1a, budget):
    with pytest.raises(ValueError):
        rv.propose_episode(f1a, "M0", seed=0, budget=budget)


@pytest.mark.parametrize("budget", [True, 2.0, "3", None])
def test_budget_must_be_an_int(f1a, budget):
    with pytest.raises((TypeError, ValueError)):
        rv.propose_episode(f1a, "M0", seed=0, budget=budget)


def test_rejects_are_returned_and_count(f1a):
    # M1 values drawn over bounds.yaml (positions in [-256, 256]) are mostly rejected; the
    # rejected proposals stay in the episode and use up its budget: no resampling.
    seen_reject = False
    for seed in range(5):
        recs = rv.propose_episode(f1a, "M1", seed=seed)
        assert len(recs) == 3
        seen_reject |= any(not r.ok for r in recs)
        for r in recs:
            assert r.ok == (not r.codes)
    assert seen_reject


def test_unknown_stage_is_a_caller_error(f1a):
    with pytest.raises(ValueError):
        rv.propose_episode(f1a, "M9", seed=0)


# --- no peeking: sampling never calls the validator; each proposal is validated once ----------

def test_sampling_does_not_consult_the_validator(f1a, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the sampler consulted the validator")
    monkeypatch.setattr(vd, "validate", boom)
    monkeypatch.setattr(vd, "validate_patch", boom)
    monkeypatch.setattr(rv, "validate", boom, raising=False)
    for stage in ("M0", "M1"):
        for i in range(20):
            rv.sample_patch(f1a, stage, rv.proposal_rng(i, 0))


def test_each_proposal_is_validated_exactly_once(f1a, monkeypatch):
    calls = []
    real = rv.validate

    def counting(base, cand, *, stage):
        calls.append(stage)
        return real(base, cand, stage=stage)
    monkeypatch.setattr(rv, "validate", counting)
    recs = rv.propose_episode(f1a, "M1", seed=3)
    assert len(calls) == 3 and len(recs) == 3


def test_record_matches_a_fresh_validation(f1a):
    for stage in ("M0", "M1"):
        for r in rv.propose_episode(f1a, stage, seed=11):
            res = vd.validate(f1a, r.patch, stage=stage)
            assert r.ok == res.ok
            assert list(r.codes) == sorted(res.codes)
            assert list(r.warnings) == sorted({w.code for w in res.warnings})
            walk = res.graph["walk"]["result"] if res.graph else None
            assert r.graph_walk == walk


# --- seeding, derivation, replay -------------------------------------------------------------

def test_seed_derivation_is_pinned():
    material = "worldsmith.b2/0.1|seed=7|episode=0|proposal=2".encode("utf-8")
    expected = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    assert rv.derive_seed(7, 2) == expected
    assert rv.derive_seed(7, 2, episode=0) == expected
    assert rv.derive_seed(7, 2, episode=1) != expected
    assert rv.derive_seed(7, 1) != expected
    r = rv.proposal_rng(7, 2)
    assert r.random() == random.Random(expected).random()


def test_same_seed_same_records(f1a):
    for stage in ("M0", "M1"):
        a = [r.to_dict() for r in rv.propose_episode(f1a, stage, seed=42)]
        b = [r.to_dict() for r in rv.propose_episode(f1a, stage, seed=42)]
        assert a == b


def test_different_seeds_differ(f1a):
    for stage in ("M0", "M1"):
        hashes = {rv.sample_patch(f1a, stage, rv.proposal_rng(s, 0))["ops"][0].__repr__()
                  for s in range(10)}
        assert len(hashes) == 10


def test_proposals_within_an_episode_differ(f1a):
    recs = rv.propose_episode(f1a, "M0", seed=5)
    assert len({r.patch_hash for r in recs}) == 3


def test_record_carries_the_rng_used(f1a, f1a_hash):
    r = rv.propose_episode(f1a, "M0", seed=9)[1]
    d = r.to_dict()
    assert d["seed"] == 9 and d["episode"] == 0 and d["index"] == 1 and d["stage"] == "M0"
    assert d["rng"]["seed"] == rv.derive_seed(9, 1)
    assert d["rng"]["derivation"] == "worldsmith.b2/0.1|seed=9|episode=0|proposal=1"
    state = random.Random(rv.derive_seed(9, 1)).getstate()
    assert d["rng"]["state_sha256"] == hashlib.sha256(
        json.dumps(state, separators=(",", ":")).encode()).hexdigest()
    assert d["base_hash"] == f1a_hash
    assert d["patch_hash"] == pt.patch_hash(d["patch"])
    assert set(d) >= {"patch", "patch_hash", "ok", "codes", "warnings", "graph_walk", "graph_jump"}


def test_replay_regenerates_the_patch(f1a):
    for stage in ("M0", "M1"):
        for r in rv.propose_episode(f1a, stage, seed=17):
            assert rv.replay_patch(r.to_dict()) == r.patch
            assert rv.replay_patch(json.loads(json.dumps(r.to_dict()))) == r.patch


def test_deterministic_across_processes_and_hash_seeds():
    code = (
        "import json,sys\n"
        "from worldsmith.baselines import random_valid as rv\n"
        "s=json.load(open(sys.argv[1]))\n"
        "out=[r.to_dict() for st in ('M0','M1') for r in rv.propose_episode(s, st, seed=123)]\n"
        "print(json.dumps(out, sort_keys=True))\n")
    outs = set()
    for seed in ("0", "1", "4242", "random"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.add(subprocess.run([sys.executable, "-c", code, str(F1A)], env=env, check=True,
                                capture_output=True, text=True, cwd=REPO_ROOT).stdout)
    assert len(outs) == 1


def test_inputs_not_mutated(f1a):
    before = copy.deepcopy(f1a)
    rv.propose_episode(f1a, "M1", seed=1)
    rv.sample_patch(f1a, "M1", rv.proposal_rng(1, 0))
    assert f1a == before


# --- what is sampled: the stage's op set, values uniform within bounds.yaml -------------------

def test_m0_patches_are_bridge_length_only(f1a, f1a_hash):
    b = _bound("Bridge.length")
    for s in range(300):
        p = rv.sample_patch(f1a, "M0", rv.proposal_rng(s, 0))
        assert p["schema"] == pt.PATCH_SCHEMA_ID
        assert p["base_hash"] == f1a_hash
        assert len(p["ops"]) == 1
        op = p["ops"][0]
        assert set(op) == {"op", "id", "field", "value"}
        assert (op["op"], op["id"], op["field"]) == ("set", "bridge_1", "length")
        assert b["min"] <= op["value"] <= b["max"] and _on_grid(op["value"])
        assert pt.check_patch(p, stage="M0") == []


def test_m0_values_are_roughly_uniform(f1a):
    b = _bound("Bridge.length")
    vals = [rv.sample_patch(f1a, "M0", rv.proposal_rng(s, 0))["ops"][0]["value"]
            for s in range(4000)]
    mid = (b["min"] + b["max"]) / 2
    assert abs(sum(vals) / len(vals) - mid) < 1.0
    lo = sum(v < mid for v in vals) / len(vals)
    assert 0.46 < lo < 0.54
    assert min(vals) < b["min"] + 1 and max(vals) > b["max"] - 1


def test_m1_patches_use_only_the_m1_op_set(f1a, f1a_hash):
    rules = pt.STAGES["M1"]
    seen_rules, seen_counts = set(), set()
    for s in range(400):
        p = rv.sample_patch(f1a, "M1", rv.proposal_rng(s, 0), proposal=0)
        assert p["schema"] == pt.PATCH_SCHEMA_ID_0_2
        assert p["base_hash"] == f1a_hash
        assert 1 <= len(p["ops"]) <= pt.MAX_OPS
        seen_counts.add(len(p["ops"]))
        # Inside the grammar and the stage: no shape, stage, conflict or bounds error.
        assert pt.check_patch(p, stage="M1") == [], p
        ids = []
        for op in p["ops"]:
            rule = pt._match(rules, op)
            assert rule is not None
            seen_rules.add(rule)
            if op["op"] == "add":
                ent = op["entity"]
                ids.append(ent["id"])
                assert ent["id"] not in {e["id"] for e in f1a["entities"]}
                if ent["type"] == "Decor":
                    assert set(ent) == {"id", "type", "palette", "pos", "size"}
                    assert ent["palette"] in DECOR_PALETTE
                else:
                    assert set(ent) == {"id", "type", "pos", "size"}
                for key in ("pos", "size"):
                    b = _bound(f"{ent['type']}.{key}")
                    assert len(ent[key]) == 3
                    assert all(b["min"] <= v <= b["max"] and _on_grid(v) for v in ent[key])
            elif op["op"] == "move":
                b = _bound(f"{rule.type}.pos")
                assert all(b["min"] <= v <= b["max"] and _on_grid(v) for v in op["pos"])
        assert len(ids) == len(set(ids))
    assert seen_rules == set(rules)
    assert seen_counts == {1, 2, 3}


def test_added_ids_differ_across_proposals(f1a):
    ids = set()
    for i in range(3):
        for s in range(60):
            p = rv.sample_patch(f1a, "M1", rv.proposal_rng(s, i), proposal=i)
            for op in p["ops"]:
                if op["op"] == "add":
                    assert op["entity"]["id"].startswith(f"b2_{op['entity']['type'].lower()}_{i}_")
                    ids.add((s, op["entity"]["id"]))
    assert ids


def test_m1_never_produces_grammar_violations(f1a):
    # The sampler stays inside the patch grammar (schema, stage, one op per target, bounds);
    # whatever the validator rejects, it rejects for scene reasons.
    grammar = {"PATCH_MALFORMED", "SCHEMA_INVALID", "STAGE_OP_FORBIDDEN", "OP_CONFLICT",
               "OUT_OF_BOUNDS", "STALE_BASE", "TOO_MANY_OPS", "CODE_PAYLOAD"}
    for s in range(8):
        for r in rv.propose_episode(f1a, "M1", seed=s):
            assert not (set(r.codes) & grammar), r.codes


def test_works_on_f1e():
    s = raw(F1E)
    recs = rv.propose_episode(s, "M1", seed=0)
    assert len(recs) == 3 and all(r.base_hash == sc.canonical_hash(s) for r in recs)


# --- the equal-budget harness hook (WS-11 calls it with the architect's max_revisions) --------

def test_proposer_matches_the_episode(f1a):
    prop = rv.make_b2_proposer(seed=8, stage="M1", max_revisions=3)
    assert prop.max_revisions == 3 and prop.remaining == 3 and prop.used == 0
    got = [prop.propose(f1a) for _ in range(3)]
    assert prop.used == 3 and prop.remaining == 0
    assert [r.to_dict() for r in got] == [r.to_dict() for r in rv.propose_episode(f1a, "M1", seed=8)]
    assert [r.to_dict() for r in prop.records] == [r.to_dict() for r in got]


def test_proposer_refuses_past_its_budget(f1a):
    prop = rv.make_b2_proposer(seed=0, stage="M0")
    for _ in range(3):
        prop.propose(f1a)
    with pytest.raises(rv.BudgetExhausted):
        prop.propose(f1a)
    assert prop.used == 3


def test_proposer_counts_rejects(f1a):
    prop = rv.make_b2_proposer(seed=0, stage="M1")
    recs = [prop.propose(f1a) for _ in range(3)]
    assert prop.used == 3 and any(not r.ok for r in recs)


@pytest.mark.parametrize("n", [0, 4, -1])
def test_proposer_budget_bounds(n):
    with pytest.raises(ValueError):
        rv.make_b2_proposer(seed=0, stage="M0", max_revisions=n)


def test_proposer_proposes_against_the_scene_it_is_given(f1a):
    # The orchestrator decides which scene a revision patches; the proposal's base hash follows.
    fix = raw(REPO_ROOT / "scenes" / "F1-A" / "fix.json")
    prop = rv.make_b2_proposer(seed=2, stage="M0")
    a = prop.propose(f1a)
    b = prop.propose(fix)
    assert a.base_hash == sc.canonical_hash(f1a) and b.base_hash == sc.canonical_hash(fix)
    assert b.index == 1


# --- the offline summary (validator/graph only; not a play result) ----------------------------

@pytest.fixture(scope="module")
def summary():
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def records():
    text = gzip.decompress(RECORDS.read_bytes()).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def test_summary_is_labelled_offline_not_play(summary):
    assert summary["not_a_play_result"] is True
    assert "offline" in summary["kind"].lower()
    text = summary["disclosure"].lower()
    for phrase in ("not a play result", "no studio", "not a repair rate"):
        assert phrase in text


def test_summary_provenance(summary, f1a_hash):
    assert summary["scene"]["canonical_hash"] == f1a_hash
    assert summary["scene"]["path"] == "scenes/F1-A/scene.json"
    assert summary["sampler"]["id"] == rv.SAMPLER_ID
    assert summary["sampler"]["budget"] == 3
    assert summary["sampler"]["seeds"] == {"first": 0, "last": 199, "n": 200}
    assert summary["sampler"]["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert summary["records"]["file"] == "bundles/b2_offline/proposals.jsonl.gz"
    assert summary["records"]["sha256"] == hashlib.sha256(gzip.decompress(RECORDS.read_bytes())).hexdigest()
    assert summary["records"]["n"] == 1200


def test_summary_counts_match_the_records(summary, records):
    assert set(summary["stages"]) == {"M0", "M1"}
    for stage, st in summary["stages"].items():
        rs = [r for r in records if r["stage"] == stage]
        assert st["proposals"] == len(rs) == 600
        assert st["episodes"] == 200
        acc = [r for r in rs if r["ok"]]
        assert st["accepted"]["n"] == len(acc)
        assert st["accepted"]["share"] == round(len(acc) / len(rs), 4)
        conn = [r for r in rs if r["graph_walk"] == "CONNECTED"]
        assert st["walk_connected"]["n"] == len(conn)
        both = [r for r in acc if r["graph_walk"] == "CONNECTED"]
        assert st["accepted_and_walk_connected"]["n"] == len(both)
        assert st["walk_connected_given_accepted"]["n"] == len(both)
        assert st["walk_connected_given_accepted"]["of"] == len(acc)
        assert st["rejected"] == len(rs) - len(acc)
        codes = {}
        for r in rs:
            for c in r["codes"]:
                codes[c] = codes.get(c, 0) + 1
        assert st["rejection_codes"] == dict(sorted(codes.items()))
        eps = {}
        for r in rs:
            eps.setdefault(r["seed"], []).append(r)
        assert sorted(eps) == list(range(200))
        assert all(len(v) == 3 for v in eps.values())
        assert st["episodes_with_accepted"]["n"] == sum(any(r["ok"] for r in v) for v in eps.values())


@pytest.mark.parametrize("stage,seed", [("M0", 0), ("M0", 199), ("M1", 0), ("M1", 137)])
def test_records_reproduce(f1a, records, stage, seed):
    want = [r for r in records if r["stage"] == stage and r["seed"] == seed]
    got = [json.loads(json.dumps(r.to_dict())) for r in rv.propose_episode(f1a, stage, seed=seed)]
    assert got == want
