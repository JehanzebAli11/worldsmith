#!/usr/bin/env bash
# Worldsmith x Autopilot demo: Autopilot (agent + Jev) is the player instead of the scripted P0.
#
#   AUTOPILOT_DIR=~/lab/aura autopilot_demo/run_demo.sh cheat    # validator refuses a cheat patch (no Studio needed)
#   AUTOPILOT_DIR=~/lab/aura autopilot_demo/run_demo.sh broken   # build B0, Autopilot verifies: expect DENIED
#   AUTOPILOT_DIR=~/lab/aura autopilot_demo/run_demo.sh fixed    # apply m0_fix via the validator, expect CONFIRMED
#   AUTOPILOT_DIR=~/lab/aura autopilot_demo/run_demo.sh all      # cheat, broken, fixed, then a summary
#
# Needs: Studio open on DEMO_PLACE (a copy of places/worldsmith-lab.rbxl), in Edit mode, with Studio's own MCP server
# on (Assistant > ... > Settings > MCP Servers > Enable Studio as MCP server); Autopilot's .venv with this repo
# installed in it; ANTHROPIC_API_KEY and TYPESAFE_API_KEY in Autopilot's .env (or Aura sign-in).
set -euo pipefail
WS="$(cd "$(dirname "$0")/.." && pwd)"
AP="${AUTOPILOT_DIR:?set AUTOPILOT_DIR to your Autopilot clone}"
PY="${AUTOPILOT_PYTHON:-$AP/.venv/bin/python}"
PLACE="${DEMO_PLACE:-$WS/places/worldsmith-autopilot-demo.rbxl}"
MINUTES="${MINUTES:-20}"
OUT="$WS/autopilot_demo/results/$(date +%Y%m%d-%H%M%S)"
export AUTOPILOT_ENGINE=roblox-mcp AUTOPILOT_SHARE=0   # Roblox support is experimental: share no run data

build() { (cd "$WS" && "$PY" autopilot_demo/build_room.py "$@"); }
bench() { mkdir -p "$OUT"; (cd "$AP" && "$PY" bench/run_autopilot.py --project "$PLACE" \
          --suite "$WS/autopilot_demo/$1" --minutes "$MINUTES" --out "$OUT/$2"); }

cheat()  { echo "== cheat: a patch that narrows the protected gap"
           if build scenes/F1-A/scene.json --patch autopilot_demo/patches/cheat_shrink_gap.json --stage M1; then
               echo "UNEXPECTED: the cheat was built"; return 1; fi
           echo "ok: refused before anything reached Studio"; }
broken() { echo "== B0: F1-A-wide, bridge 2 studs over a 16-stud gap (14 left)"; build autopilot_demo/scenes/F1-A-wide.json; bench suite_broken.json b0; }
fixed()  { echo "== B1: f1_a_wide_fix through the validator at M1 (bridge 16)"
           build autopilot_demo/scenes/F1-A-wide.json --patch autopilot_demo/patches/f1_a_wide_fix.json --stage M1; bench suite_fixed.json b1; }

[ -f "$PLACE" ] || { echo "No demo place at $PLACE: cp places/worldsmith-lab.rbxl \"$PLACE\" and open it in Studio"; exit 1; }
case "${1:-all}" in
  cheat) cheat ;;
  broken) broken ;;
  fixed) fixed ;;
  all) cheat; broken; fixed
       echo; echo "== summary (expected: B0 DENIED, B1 CONFIRMED)"
       for f in "$OUT"/*/summary.json; do "$PY" -c "import json,sys; s=json.load(open(sys.argv[1])); print(sys.argv[1].split('/')[-2], 'score', s['score'], '/', s['total'])" "$f"; done ;;
  *) echo "usage: $0 [cheat|broken|fixed|all]"; exit 2 ;;
esac
