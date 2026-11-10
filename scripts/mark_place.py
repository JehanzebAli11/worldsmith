#!/usr/bin/env python3
"""Print the one-time `run_code` payload that marks the owner's Worldsmith place (ADR 0011).

The preflight (runner/preflight.py) refuses to build unless `Workspace` carries
`ws_place = "worldsmith-lab"`. The runner never sets it: the owner sets it ONCE, by hand, in the
saved place, so the preflight can tell the right place from any other place Studio has open.

    python scripts/mark_place.py           # the payload as JSON: {"command": "..."} for run_code
    python scripts/mark_place.py --luau    # only the Luau

Steps for the owner: open places/worldsmith-lab.rbxl (File -> Open from File), stay in Edit mode,
send the payload with the Studio MCP `run_code` tool (or paste the one line into the Command Bar),
check the printed WS_PLACE_MARKED|<game name>|worldsmith-lab line, then File -> Save to File over
places/worldsmith-lab.rbxl. Never run it in any other place. This script itself sends nothing.
"""

from __future__ import annotations

import argparse
import json
import sys

from worldsmith.runner import preflight


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--luau", action="store_true", help="print only the Luau command")
    a = ap.parse_args(argv)
    cmd = preflight.mark_place_command()
    if a.luau:
        sys.stdout.write(cmd)
    else:
        print(json.dumps({"command": cmd}))
    print(f"# one line, if typed by hand in the Command Bar: {preflight.MARK_PLACE_LUAU}", file=sys.stderr)
    print("# then File -> Save to File over places/worldsmith-lab.rbxl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
