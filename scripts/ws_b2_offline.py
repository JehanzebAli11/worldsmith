#!/usr/bin/env python3
"""Offline B2 statistic (WS-09): random patches against F1-A, validator and graph only.

    python scripts/ws_b2_offline.py            # writes bundles/b2_offline/{summary.json,proposals.jsonl.gz}
    python scripts/ws_b2_offline.py --check    # exit 1 if the committed files differ

For stages M0 and M1, seeds 0..199, 3 raw proposals per seed (SPEC section 12 budget), every
proposal is validated against the unchanged F1-A scene. Nothing is played (no Studio, no network,
no model calls): the summary estimates how often a random patch could even be tried, not a
repair rate. proposals.jsonl.gz holds one record per proposal (gzip, mtime 0); the summary's
records.sha256 is of the uncompressed JSONL, and --check compares uncompressed content.
"""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

from worldsmith.baselines import random_valid as rv
from worldsmith.grammar import scene as sc

ROOT = Path(__file__).resolve().parent.parent
SCENE = "scenes/F1-A/scene.json"
STAGES = ("M0", "M1")
SEEDS = range(200)
OUT = "bundles/b2_offline"
INPUTS = ("grammar/bounds.yaml", "grammar/schema.json", "grammar/patch_schema.json",
          "grammar/patch_schema_0.2.json", "validator/patch.py", "validator/validate.py",
          "validator/nav.py", "compiler/geometry.py")
DISCLOSURE = (
    "Offline validator/graph-only statistic: each proposal is a uniformly random patch from the"
    " stage's op set (values uniform within grammar/bounds.yaml), judged by the WS-06 validator"
    " and the WS-07 walk-graph approximation against the unchanged F1-A scene. It is not a play"
    " result and not a repair rate: no Studio runs, no player, no model calls. 'accepted' means"
    " the validator would let the patch be tried; 'walk_connected' is the ADR 0008 approximation,"
    " never a proof of solvability. Only actual runs (WS-11) decide whether B2 repairs F1-A.")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root: Path = ROOT):
    scene = sc.load_scene(root / SCENE)
    records = [r.to_dict() for stage in STAGES for seed in SEEDS
               for r in rv.propose_episode(scene, stage, seed)]
    lines = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records)
    summary = {
        "kind": "offline validator/graph statistic (no play)",
        "not_a_play_result": True,
        "disclosure": DISCLOSURE,
        "scene": {"path": SCENE, "scene_id": scene["scene_id"],
                  "canonical_hash": sc.canonical_hash(scene)},
        "sampler": {
            "id": rv.SAMPLER_ID,
            "module": "worldsmith/baselines/random_valid.py",
            "source_sha256": _sha(root / "baselines" / "random_valid.py"),
            "budget": rv.MAX_PROPOSALS,
            "seeds": {"first": SEEDS[0], "last": SEEDS[-1], "n": len(SEEDS)},
            "episode": 0,
            "seed_derivation": "random.Random(first 8 bytes, big-endian, of SHA-256("
                               "'worldsmith.b2/0.1|seed=<s>|episode=<e>|proposal=<i>'))",
            "model_calls": 0,
        },
        "inputs_sha256": {p: _sha(root / p) for p in INPUTS},
        "records": {"file": f"{OUT}/proposals.jsonl.gz", "format": "gzip of JSONL, one record per proposal",
                    "sha256": hashlib.sha256(lines.encode("utf-8")).hexdigest(),
                    "n": len(records)},
        "stages": {st: rv.stage_summary([r for r in records if r["stage"] == st], rv.MAX_PROPOSALS)
                   for st in STAGES},
    }
    return json.dumps(summary, indent=1, sort_keys=True) + "\n", lines


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
        print("b2_offline up to date" if not stale else f"differs: {', '.join(stale)}")
        return 1 if stale else 0
    out.mkdir(parents=True, exist_ok=True)
    if not same_summary:
        summary_path.write_text(summary, encoding="utf-8")
    if not same_records:
        records_path.write_bytes(gzip.compress(lines.encode("utf-8"), compresslevel=9, mtime=0))
    for st, s in json.loads(summary)["stages"].items():
        print(st, "accepted", s["accepted"], "walk_connected", s["walk_connected"],
              "accepted&connected", s["accepted_and_walk_connected"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
