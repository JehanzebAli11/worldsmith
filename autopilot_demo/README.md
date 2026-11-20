# Autopilot as the player (Worldsmith x Autopilot demo)

Worldsmith's results so far use a scripted player (P0). This demo swaps in **Autopilot**: its agent researches the
room and Jev plays it, through Autopilot's `roblox-mcp` engine (Studio's own MCP server). Same room, same patch,
same validator: only the player changes.

| Step | What happens | Expected |
| --- | --- | --- |
| cheat | `cheat_shrink_gap.json` (narrow the protected `gap_1`) goes to the validator | refused at M1, nothing built |
| B0 | F1-A-wide: `bridge_1` 2 studs over a 16-stud gap (14 left). Autopilot verifies the goal can be reached | `DENIED` |
| B1 | `f1_a_wide_fix.json` (bridge 16) accepted by the validator at M1, built, verified again | `CONFIRMED` (saved as a regression) |

## One-time setup (Mac)

```bash
# 1. Autopilot, from a clone (Python 3.12+, uv, Bun), with Worldsmith installed into the same venv
git clone https://github.com/ramenvr/autopilot ~/lab/aura && cd ~/lab/aura
uv venv && uv pip install -e ".[dev]" && uv pip install -e ~/lab/worldsmith
cp .env.example .env    # set ANTHROPIC_API_KEY and TYPESAFE_API_KEY (or sign in to Aura)

# 2. A demo copy of the lab place, so places/worldsmith-lab.rbxl stays as Worldsmith's preflight expects
cp ~/lab/worldsmith/places/worldsmith-lab.rbxl ~/lab/worldsmith/places/worldsmith-autopilot-demo.rbxl
```

3. Open `places/worldsmith-autopilot-demo.rbxl` in Roblox Studio and turn on Studio's MCP server once:
   **Assistant** (top bar, far right) → **…** → **Settings** → **MCP Servers** → **Enable Studio as MCP server**
   (it shows **MCP Ready**). Stay in Edit mode. Nothing else should drive Studio while the demo runs.

## Run

```bash
cd ~/lab/worldsmith
AUTOPILOT_DIR=~/lab/aura autopilot_demo/run_demo.sh cheat    # seconds, no keys used
AUTOPILOT_DIR=~/lab/aura autopilot_demo/run_demo.sh all      # cheat, B0, B1: up to MINUTES (20) per run
```

Results: `autopilot_demo/results/<time>/{b0,b1}/summary.json`, with each run's report, harness and stills.
`build_room.py --dry-run` validates and compiles without Studio.

## What the build does to the place

`build_room.py` compiles the scene with Worldsmith's own compiler (same templates, same hash) and runs it in
Studio's **Edit** data model: it replaces `Workspace.Worldsmith` (origin 0, 100, 0) and turns off every other
SpawnLocation so the player spawns in the room. Don't save the demo place over the lab place.

## Results (20 Nov 2026, macOS, Studio's MCP server, Autopilot `main` at 19 Nov)

Each run used input only: no direct changes to the game in play or in research (`level_modified` and
`research_modified` empty in every report). One run per condition.

| Room | Autopilot | Time | Cost |
| --- | --- | --- | --- |
| cheat patch (narrow `gap_1`) | refused by the validator at M1, nothing built | - | - |
| F1-A as authored (bridge 6, 10-stud gap) | **CONFIRMED**: an edge-precise running jump clears it | 339 s | $2.48 |
| F1-A-wide (bridge 2, 14-stud gap) | **DENIED**: three running jumps fell short | 276 s | $2.74 |
| F1-A-wide + `f1_a_wide_fix` (bridge 16) | **CONFIRMED**, saved as an Autopilot regression | 222 s | $1.90 |

F1-A was the surprise. `nav.py` and P0 both call it unreachable, but the character keeps its footing about 2.3 studs
past the bridge's edge, so a jump taken there lands. `nav.py` measures reach from the edge, so it's too strict for a
player that jumps. That is why the demo uses F1-A-wide (`scenes/F1-A-wide.json`: F1-A with `bridge_1.length` 2).

Logs, harnesses and reports go to `autopilot_demo/results/` (git-ignored: they hold local paths and full agent logs).

## Honest limits

- n = 1 per condition: a demonstration, not a rate.
- The lever's ProximityPrompt has `RequiresLineOfSight = false` and a range of 8, so F1-C and F1-D (lever behind a
  wall) are probably solvable by pressing E through the wall. Not run yet.
- Codex: for a place file (not Rojo), the connector reads scripts from the Edit data model after play has started,
  which Studio refuses, so the agent starts with an empty codex and learns the room from probes. Upstream issue.
- The agent reads the room's scripts (Autopilot builds a codex), like P0's instrumented reads.
- `roblox-mcp` is experimental upstream (no pause; about 14 decisions a second at most).
