"""WS-14 step 2: offline hardness of the broken F1 fixtures (ADR 0013), validator and graph only.

`bundles/hardness/summary.json` is written by `scripts/ws_hardness.py` with exactly the
`scripts/ws_b2_offline.py` method (B2 at M1, seeds 0..199, episode 0, 3 raw proposals each). These
tests check its provenance, recount it from its records, replay sampled seeds, and check the
pre-declared bar. The full rebuild (`--check`, about 2.5 min) is not run here.
"""

import gzip
import hashlib
import json
import subprocess

import pytest

from _repo import REPO_ROOT
from worldsmith.baselines import random_valid as rv
from worldsmith.grammar import scene as sc

OUT = REPO_ROOT / "bundles" / "hardness"
SUMMARY = OUT / "summary.json"
RECORDS = OUT / "proposals.jsonl.gz"
B2_OFFLINE = REPO_ROOT / "bundles" / "b2_offline"
SCRIPT = REPO_ROOT / "scripts" / "ws_hardness.py"
BROKEN = ("F1-B", "F1-C", "F1-D")
BAR_N = 10          # at most 5 % of 200 episodes


@pytest.fixture(scope="module")
def summary():
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def records():
    text = gzip.decompress(RECORDS.read_bytes()).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bundle_is_committable():
    for f in (SUMMARY, RECORDS):
        rel = f.relative_to(REPO_ROOT).as_posix()
        res = subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO_ROOT)
        assert res.returncode == 1, f"{rel} is git-ignored"


def test_summary_is_labelled_offline_not_play(summary):
    assert summary["not_a_play_result"] is True
    assert "offline" in summary["kind"].lower()
    text = summary["disclosure"].lower()
    for phrase in ("not a play result", "no studio", "not a repair rate", "never a proof"):
        assert phrase in text


def test_method_is_b2_offlines(summary):
    b2 = json.loads((B2_OFFLINE / "summary.json").read_text())
    m = summary["method"]
    assert m["stage"] == "M1"
    assert m["sampler"]["id"] == rv.SAMPLER_ID == b2["sampler"]["id"]
    for k in ("budget", "episode", "seeds", "seed_derivation", "model_calls"):
        assert m["sampler"][k] == b2["sampler"][k], k
    assert m["sampler"]["source_sha256"] == _sha(REPO_ROOT / "baselines" / "random_valid.py")
    assert m["statistic"] == "episodes_with_accepted_walk_connected"
    assert m["bar"] == {"max_share": 0.05, "max_n": BAR_N, "of": 200, "rule": m["bar"]["rule"]}
    assert "never re-scored" in m["bar"]["rule"]


def test_input_hashes_are_current(summary):
    assert summary["inputs_sha256"], "no input hashes"
    for rel, sha in summary["inputs_sha256"].items():
        assert _sha(REPO_ROOT / rel) == sha, rel
    for name in ("F1-A", *BROKEN):
        assert f"scenes/{name}/scene.json" in summary["inputs_sha256"]
    for name in BROKEN:
        assert f"scenes/{name}/patches/m1_fix.json" in summary["inputs_sha256"]


def test_records_file_matches_summary(summary, records):
    raw = gzip.decompress(RECORDS.read_bytes())
    assert summary["records"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert summary["records"]["n"] == len(records) == 4 * 600
    assert summary["records"]["file"] == "bundles/hardness/proposals.jsonl.gz"


def test_fixture_scenes_are_the_committed_ones(summary):
    assert list(summary["fixtures"]) == ["F1-A", *BROKEN]
    for name, fx in summary["fixtures"].items():
        s = sc.load_scene(REPO_ROOT / fx["scene"]["path"])
        assert fx["scene"]["canonical_hash"] == sc.canonical_hash(s)
        assert fx["role"] == ("reference" if name == "F1-A" else "broken")


@pytest.mark.parametrize("name", ["F1-A", *BROKEN])
def test_stats_recount_from_records(summary, records, name):
    rs = [r for r in records if r["fixture"] == name]
    st = summary["fixtures"][name]["stats"]
    assert st["proposals"] == len(rs) == 600 and st["episodes"] == 200
    base = summary["fixtures"][name]["scene"]["canonical_hash"]
    assert all(r["base_hash"] == base and r["stage"] == "M1" for r in rs)
    eps = {}
    for r in rs:
        eps.setdefault(r["seed"], []).append(r)
    assert sorted(eps) == list(range(200)) and all(len(v) == 3 for v in eps.values())
    hit = sum(any(r["ok"] and r["graph_walk"] == "CONNECTED" for r in v) for v in eps.values())
    assert st["episodes_with_accepted_walk_connected"] == {"n": hit, "of": 200, "share": round(hit / 200, 4)}
    assert st == rv.stage_summary([{k: v for k, v in r.items() if k != "fixture"} for r in rs], 3)


@pytest.mark.parametrize("name,seed", [("F1-A", 0), ("F1-B", 0), ("F1-B", 199), ("F1-C", 57), ("F1-D", 123)])
def test_records_reproduce(records, name, seed):
    scene = sc.load_scene(REPO_ROOT / "scenes" / name / "scene.json")
    want = [{k: v for k, v in r.items() if k != "fixture"} for r in records if r["fixture"] == name and r["seed"] == seed]
    got = [json.loads(json.dumps(p.to_dict())) for p in rv.propose_episode(scene, "M1", seed=seed)]
    assert got == want


def test_f1a_reference_reproduces_b2_offline_m1(summary, records):
    b2 = json.loads((B2_OFFLINE / "summary.json").read_text())
    assert summary["fixtures"]["F1-A"]["stats"] == b2["stages"]["M1"]
    assert summary["fixtures"]["F1-A"]["stats"]["episodes_with_accepted_walk_connected"]["n"] == 1
    old = gzip.decompress((B2_OFFLINE / "proposals.jsonl.gz").read_bytes()).decode("utf-8")
    old_m1 = [json.loads(x) for x in old.splitlines() if x and json.loads(x)["stage"] == "M1"]
    new_a = [{k: v for k, v in r.items() if k != "fixture"} for r in records if r["fixture"] == "F1-A"]
    assert new_a == old_m1


@pytest.mark.parametrize("name", BROKEN)
def test_each_broken_fixture_is_judged_against_the_declared_bar(summary, name):
    fx = summary["fixtures"][name]
    n = fx["stats"]["episodes_with_accepted_walk_connected"]["n"]
    c = fx["checks"]
    assert c["hardness_at_most_5_percent"] is (n <= BAR_N)
    assert c["broken_walk_disconnected"] is (fx["base_walk"] == "DISCONNECTED")
    assert c["hand_fix_accepted"] is fx["hand_fix"]["ok"]
    assert c["hand_fix_walk_connected"] is (fx["hand_fix"]["walk"] == "CONNECTED")
    assert fx["passes"] is all(c.values())
    assert fx["hand_fix"]["path"] == f"scenes/{name}/patches/m1_fix.json"
    assert fx["hand_fix"]["sha256"] == _sha(REPO_ROOT / fx["hand_fix"]["path"])
    assert (name in summary["passed"]) is fx["passes"]
    assert (name in summary["dropped"]) is (not fx["passes"])


def test_reference_is_not_judged(summary):
    assert summary["fixtures"]["F1-A"]["passes"] is None and "F1-A" not in summary["passed"]
