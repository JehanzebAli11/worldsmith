#!/usr/bin/env python3
"""Print WS-03 Luau for a scene, the expected tree, or compare a saved read-back.

    python scripts/ws_compile.py build    scenes/F1-A/scene.json [--origin 0 100 0]
    python scripts/ws_compile.py readback
    python scripts/ws_compile.py arm      <nonce>
    python scripts/ws_compile.py expected scenes/F1-A/scene.json [--origin 0 100 0]
    python scripts/ws_compile.py compare  scenes/F1-A/scene.json readback_output.txt

`compare` exits 1 and lists the differences if the read-back does not match.
"""

import argparse
import json
import sys
from pathlib import Path

from worldsmith.compiler import build, geometry
from worldsmith.grammar.scene import load_scene


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["build", "readback", "arm", "expected", "compare"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--origin", nargs=3, type=float, default=(0, 0, 0))
    a = ap.parse_args(argv)
    origin = tuple(int(v) if v == int(v) else v for v in a.origin)
    if a.what == "readback":
        sys.stdout.write(build.compile_readback())
    elif a.what == "arm":
        sys.stdout.write(build.compile_arm(a.args[0]))
    elif a.what == "build":
        sys.stdout.write(build.compile_build(load_scene(a.args[0]), origin=origin))
    elif a.what == "expected":
        print(json.dumps(geometry.expected_tree(load_scene(a.args[0]), origin=origin), indent=1))
    else:
        expected = geometry.expected_tree(load_scene(a.args[0]), origin=origin)
        actual = build.parse_readback(Path(a.args[1]).read_text(encoding="utf-8"))
        errs = build.compare_tree(expected, actual)
        for e in errs:
            print(e)
        print(f"{len(expected['nodes'])} expected nodes, {len(actual.get('nodes') or [])} read back,"
              f" {len(errs)} differences (tolerance {build.TOLERANCE})")
        return 1 if errs else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
