#!/usr/bin/env python3
"""Build a Worldsmith scene into the Roblox Studio place open now, through Studio's own MCP server (the one
Autopilot's roblox-mcp engine plays through), so an Autopilot run can play the room.

    python autopilot_demo/build_room.py scenes/F1-A/scene.json                     # broken room (B0)
    python autopilot_demo/build_room.py scenes/F1-A/scene.json \
        --patch scenes/F1-A/patches/m0_fix.json --stage M0                          # validator-checked fix (B1)
    python autopilot_demo/build_room.py scenes/F1-A/scene.json \
        --patch autopilot_demo/patches/cheat_shrink_gap.json --stage M1             # a cheat: refused, nothing built

Run it with the Python that has Autopilot installed (Autopilot's .venv), with this repo installed in it too
(`uv pip install -e <worldsmith>`). Studio must be open on the demo place, in Edit mode (not playing), with
Studio's MCP server on. Only this script edits the place: it builds Workspace.Worldsmith (the room, at
--origin) and turns off every other SpawnLocation so the player spawns in the room. Don't save the demo place
over places/worldsmith-lab.rbxl.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Appended to the build chunk (same chunk, so it sees WS_HASH and root): spawn in the room, and say what was built.
DEMO_SETUP = """
for _, d in ipairs(workspace:GetDescendants()) do
    if d:IsA("SpawnLocation") and not d:IsDescendantOf(root) then
        d.Enabled = false
    end
end
return "WS_BUILD_OK|" .. WS_HASH .. "|" .. tostring(#root:GetDescendants())
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scene")
    ap.add_argument("--patch", help="a worldsmith.patch file to check and apply before building")
    ap.add_argument("--stage", default="M1", help="the validator stage the patch is checked at (M0, M1)")
    ap.add_argument("--origin", nargs=3, type=float, default=(0, 100, 0))
    ap.add_argument("--dry-run", action="store_true", help="validate and compile only; don't touch Studio")
    a = ap.parse_args(argv)

    from worldsmith.compiler import build
    from worldsmith.grammar.scene import load_scene, canonical_hash
    from worldsmith.validator.patch import PatchError, apply_patch, load_patch

    scene = load_scene(a.scene)
    label = f"{scene['scene_id']} v{scene['version']} ({canonical_hash(scene)[:12]})"
    if a.patch:
        try:
            applied = apply_patch(scene, load_patch(a.patch), stage=a.stage)
        except PatchError as e:
            print(f"VALIDATOR REJECTED {a.patch}: nothing built.\n  " + "\n  ".join(map(str, e.args[0]
                  if e.args and isinstance(e.args[0], list) else e.args)))
            return 2
        scene = applied.scene
        label = f"{scene['scene_id']} v{applied.version} ({applied.scene_hash[:12]}), patch {applied.patch_hash[:12]}"
        print(f"validator accepted {a.patch} at {a.stage}")
    bridge = next((e for e in scene["entities"] if e["id"] == "bridge_1"), None)
    if bridge:
        print(f"bridge_1.length = {bridge['length']}")

    origin = tuple(int(v) if v == int(v) else v for v in a.origin)
    code = build.compile_build(scene, origin=origin) + DEMO_SETUP
    if a.dry_run:
        print(f"compiled {label}: {len(code)} chars of Luau (dry run, Studio not touched)")
        return 0

    from autopilot.engines.roblox_mcp import Studio
    studio = Studio.connect()  # starts Studio's MCP server and picks the one open Studio (AUTOPILOT_ROBLOX_STUDIO_ID if several)
    try:
        result = studio.execute_luau(code, "Edit", timeout=60)
    finally:
        studio.close()
    print(f"built {label} at origin {origin}: {result}")
    return 0 if "WS_BUILD_OK" in str(result) else 1


if __name__ == "__main__":
    sys.exit(main())
