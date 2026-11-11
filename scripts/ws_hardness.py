#!/usr/bin/env python3
"""Offline hardness of the WS-14 broken F1 fixtures (ADR 0013): B2 at M1, validator and graph only.

    python scripts/ws_hardness.py            # writes bundles/hardness/{summary.json,proposals.jsonl.gz}
    python scripts/ws_hardness.py --check    # exit 1 if the committed files differ

Exactly the `scripts/ws_b2_offline.py` method: for each fixture, stage M1, seeds 0..199, episode
0, 3 raw proposals per seed (`random_valid.propose_episode`), every proposal validated once
against the unchanged scene. Nothing is played (no Studio, no network, no model calls). F1-A is
computed the same way as a reference (it must reproduce b2_offline's M1 stage).

Pre-declared bar (ADR 0013), per broken fixture, all of: episodes with an accepted, walk-CONNECTED
proposal at most 5 % (<= 10 of 200); the broken scene walk-DISCONNECTED; its hand fix
(`patches/m1_fix.json`) ACCEPTED at M1 and walk-CONNECTED. A fixture that misses is redesigned or
dropped (recorded in ADR 0013), never re-scored with another bar or method.

proposals.jsonl.gz holds one record per proposal (gzip, mtime 0): the b2_offline record plus a
`fixture` key; the summary's records.sha256 is of the uncompressed JSONL.
"""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

from worldsmith.baselines import random_valid as rv
from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt
from worldsmith.validator.validate import validate

ROOT = Path(__file__).resolve().parent.parent
STAGE = "M1"
SEEDS = range(200)
EPISODE = 0
OUT = "bundles/hardness"
REFERENCE = "F1-A"
BROKEN = ("F1-B", "F1-C", "F1-D")
BAR = {"max_share": 0.05, "max_n": 10, "of": 200,
       "rule": "declared in ADR 0013 before the statistic was computed; a fixture that misses is redesigned "
               "or dropped (recorded in ADR 0013), never re-scored with another bar or method"}
INPUTS = ("grammar/bounds.yaml", "grammar/schema.json", "grammar/patch_schema.json",
          "grammar/patch_schema_0.2.json", "validator/patch.py", "validator/validate.py",
          "validator/nav.py", "compiler/geometry.py", "baselines/random_valid.py")
DISCLOSURE = (
    "Offline validator/graph-only statistic: each proposal is a uniformly random patch from the M1"
    " op set (values uniform within grammar/bounds.yaml), judged by the WS-06 validator and the"
    " WS-07 walk-graph approximation against the unchanged fixture scene. It is not a play result"
    " and not a repair rate: no Studio runs, no player, no model calls. 'accepted' means the"
    " validator would let the patch be tried; 'walk_connected' is the ADR 0008 approximation, never"
    " a proof of solvability. Only actual runs decide whether anything repairs a room.")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk(res):
    return (res.graph or {}).get("walk", {}).get("result") if res.graph else None


def _fixture(root: Path, name: str):
    scene_rel = f"scenes/{name}/scene.json"
    scene = sc.load_scene(root / scene_rel)
    recs = [r.to_dict() for seed in SEEDS for r in rv.propose_episode(scene, STAGE, seed, episode=EPISODE)]
    base = validate(scene, scene, stage=STAGE)          # the scene judged as itself: its graph only
    entry = {
        "role": "reference" if name == REFERENCE else "broken",
        "scene": {"path": scene_rel, "scene_id": scene["scene_id"], "canonical_hash": sc.canonical_hash(scene)},
        "base_walk": _walk(base),
        "stats": rv.stage_summary(recs, rv.MAX_PROPOSALS),
    }
    if name == REFERENCE:
        entry["passes"] = None
        entry["note"] = ("reference only, not judged: F1-A at M1 recomputed with the same method; equals "
                         "bundles/b2_offline/summary.json stages.M1")
        return entry, recs
    patch_rel = f"scenes/{name}/patches/m1_fix.json"
    patch = pt.load_patch(root / patch_rel)
    res = validate(scene, patch, stage=STAGE)
    entry["hand_fix"] = {"path": patch_rel, "sha256": _sha(root / patch_rel), "ops": patch["ops"],
                         "ok": res.ok, "codes": sorted(res.codes), "walk": _walk(res),
                         "scene_hash": sc.canonical_hash(res.scene) if res.scene else None}
    n = entry["stats"]["episodes_with_accepted_walk_connected"]["n"]
    entry["checks"] = {
        "hardness_at_most_5_percent": n <= BAR["max_n"],
        "broken_walk_disconnected": entry["base_walk"] == "DISCONNECTED",
        "hand_fix_accepted": res.ok,
        "hand_fix_walk_connected": entry["hand_fix"]["walk"] == "CONNECTED",
    }
    entry["passes"] = all(entry["checks"].values())
    return entry, recs


def build(root: Path = ROOT):
    fixtures, records = {}, []
    for name in (REFERENCE, *BROKEN):
        entry, recs = _fixture(root, name)
        fixtures[name] = entry
        records += [{**r, "fixture": name} for r in recs]
    lines = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records)
    inputs = list(INPUTS) + [f"scenes/{n}/scene.json" for n in (REFERENCE, *BROKEN)] \
        + [f"scenes/{n}/patches/m1_fix.json" for n in BROKEN]
    summary = {
        "kind": "offline validator/graph statistic (no play): WS-14 hardness",
        "not_a_play_result": True,
        "disclosure": DISCLOSURE,
        "method": {
            "stage": STAGE,
            "same_as": "scripts/ws_b2_offline.py (bundles/b2_offline), stage M1",
            "sampler": {
                "id": rv.SAMPLER_ID,
                "module": "worldsmith/baselines/random_valid.py",
                "source_sha256": _sha(root / "baselines" / "random_valid.py"),
                "budget": rv.MAX_PROPOSALS,
                "seeds": {"first": SEEDS[0], "last": SEEDS[-1], "n": len(SEEDS)},
                "episode": EPISODE,
                "seed_derivation": "random.Random(first 8 bytes, big-endian, of SHA-256("
                                   "'worldsmith.b2/0.1|seed=<s>|episode=<e>|proposal=<i>'))",
                "model_calls": 0,
            },
            "statistic": "episodes_with_accepted_walk_connected",
            "bar": BAR,
            "also_required": ["broken scene walk-DISCONNECTED (ADR 0008 approximation)",
                              "hand fix ACCEPTED at M1 and walk-CONNECTED"],
        },
        "inputs_sha256": {p: _sha(root / p) for p in inputs},
        "records": {"file": f"{OUT}/proposals.jsonl.gz", "format": "gzip of JSONL, one record per proposal "
                    "(the b2_offline record plus `fixture`)",
                    "sha256": hashlib.sha256(lines.encode("utf-8")).hexdigest(), "n": len(records)},
        "fixtures": fixtures,
        "passed": [n for n in BROKEN if fixtures[n]["passes"]],
        "dropped": [n for n in BROKEN if not fixtures[n]["passes"]],
    }
    return json.dumps(summary, indent=1) + "\n", lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=str(ROOT / OUT))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    summary, lines = build()
    out = Path(a.out_dir)
    summary_path, records_path = out / "summary.json", out / "proposals.jsonl.gz"

    def records_text():
        return gzip.decompress(records_path.read_bytes()).decode("utf-8") if records_path.is_file() else None

    same_summary = summary_path.is_file() and summary_path.read_text(encoding="utf-8") == summary
    same_records = records_text() == lines
    if a.check:
        stale = [str(p) for p, same in ((summary_path, same_summary), (records_path, same_records)) if not same]
        print("hardness up to date" if not stale else f"differs: {', '.join(stale)}")
        return 1 if stale else 0
    out.mkdir(parents=True, exist_ok=True)
    if not same_summary:
        summary_path.write_text(summary, encoding="utf-8")
    if not same_records:
        records_path.write_bytes(gzip.compress(lines.encode("utf-8"), compresslevel=9, mtime=0))
    for name, fx in json.loads(summary)["fixtures"].items():
        e = fx["stats"]["episodes_with_accepted_walk_connected"]
        print(f"{name}: episodes with an accepted, walk-connected patch {e['n']}/{e['of']}; base walk "
              f"{fx['base_walk']}; hand fix {fx.get('hand_fix', {}).get('ok')} {fx.get('hand_fix', {}).get('walk')}; "
              f"passes {fx['passes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
