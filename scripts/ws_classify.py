#!/usr/bin/env python3
"""Classify the recorded M0 attempts (SPEC section 10, WS-08) and write the result.

    python scripts/ws_classify.py                 # writes bundles/m0/classification.json
    python scripts/ws_classify.py --out FILE      # writes FILE instead
    python scripts/ws_classify.py --check         # exit 1 if the committed file differs

Reads only the recorded bundles under bundles/m0/ (never Studio, never the network).
"""

import argparse
import sys
from pathlib import Path

from worldsmith.runner import taxonomy
from worldsmith.runner.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(ROOT / "config" / "m0.yaml"))
    ap.add_argument("--classifier-config", default=str(ROOT / "config" / "classifier.yaml"))
    ap.add_argument("--out", default=str(ROOT / "bundles" / "m0" / "classification.json"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, ROOT)
    ccfg = taxonomy.load_classifier_config(a.classifier_config)
    text = taxonomy.dumps(taxonomy.classify_m0(ROOT, cfg, ccfg))
    out = Path(a.out)
    if a.check:
        same = out.is_file() and out.read_text(encoding="utf-8") == text
        print("classification up to date" if same else f"{out} differs from a fresh classification")
        return 0 if same else 1
    if out.exists() and out.read_text(encoding="utf-8") == text:
        print(f"{out} unchanged")
        return 0
    out.write_text(text, encoding="utf-8")
    for ep in taxonomy.json.loads(text)["episodes"]:
        for x in ep["attempts"]:
            print(f"{ep['episode']} #{x['attempt']}: {x['outcome']} tags={x['tags']} cause={x['cause']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
