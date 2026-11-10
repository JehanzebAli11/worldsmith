#!/usr/bin/env python3
"""Refresh (or --check) the request hashes in architect/fixtures/*.json (WS-10).

Run after a deliberate change to architect/prompt.md, the context builder or a fixture's
inputs. The replies and expected verdicts are hand-written and never touched here.
"""

import argparse
import json
import sys

from worldsmith.architect import fixtures as fx
from worldsmith.architect.client import fixture_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify only; exit 1 if any hash is stale")
    args = ap.parse_args(argv)
    stale = []
    for name in fx.fixture_names():
        doc = fx.load_fixture(name)
        shas = fx.compute_request_shas(name)
        if [t["request_sha"] for t in doc["turns"]] != shas:
            stale.append(name)
            if not args.check:
                for t, sha in zip(doc["turns"], shas):
                    t["request_sha"] = sha
                fixture_path(name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    if stale:
        print(("stale: " if args.check else "updated: ") + ", ".join(stale))
        return 1 if args.check else 0
    print("architect fixtures: request hashes up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
