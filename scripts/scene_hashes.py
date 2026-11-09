#!/usr/bin/env python3
"""Write scenes/HASHES.md from the scene fixtures, or check that it is up to date.

    python scripts/scene_hashes.py            # rewrite scenes/HASHES.md
    python scripts/scene_hashes.py --check    # exit 1 if it is stale

Needs the package installed (`make test` does an editable install into the venv).
"""

import argparse
import sys
from pathlib import Path

try:
    from worldsmith.grammar.scene import SceneError, render_hashes_md
except ImportError as exc:  # pragma: no cover
    sys.exit(f"error: {exc}. Install first: pip install -e . (make test does this).")

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "scenes"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify only; do not write")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="scenes folder")
    args = ap.parse_args(argv)

    out = args.root / "HASHES.md"
    try:
        text = render_hashes_md(args.root)
    except SceneError as exc:
        print(f"error: a fixture does not validate: {exc}", file=sys.stderr)
        return 1
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"{out} is stale; run scripts/scene_hashes.py", file=sys.stderr)
            return 1
        print(f"{out} is up to date")
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
