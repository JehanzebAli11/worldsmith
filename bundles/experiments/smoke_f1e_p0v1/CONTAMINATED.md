# smoke_f1e_p0v1: CONTAMINATED, not evidence about player p0.v1

This bundle is kept as history. **It says nothing about p0.v1 or about scene F1-E.** Do not count
it, cite it, or compare it with anything. The smoke is re-run under a new id
(`config/experiments/smoke_f1e_p0v2.yaml`, player p0.v2 with the ADR 0011 preflight).

## Why (from `docs/notes/smoke_f1e_p0v1_debug.md`, dbg1, 10 Nov 2026)

1. **Wrong place.** Studio had the Autopilot test place open (`game.Name` = `testplace.rbxl`, the
   Rojo-built "Autopilot Test Place"), not `places/worldsmith-lab.rbxl`. That place has its own
   server scripts (`ServerScriptService.Build`, `.Game`, `.Debug`), a HUD in `StarterGui` and
   `StarterPlayerScripts`, a second neutral `SpawnLocation` (`Workspace.Start`), and
   `LoadCharacterAppearance = true` (the owner's avatar, not the measured M0 rig). The smoke's
   build (call 002) put `Workspace.Worldsmith` into that place.
2. **Two drivers at once.** An Autopilot bench run (noise-check `run-03/F`, from 08:37:03 UTC for
   626 s, holding W into a door and running/jumping) drove the same Studio while the smoke's calls
   ran (08:36:40 to 08:41:10 UTC). Autopilot's console holds Worldsmith's own attempt-1 lines, and
   its own characters spawned on `Worldsmith.spawn` (two neutral spawns) and walked the course.

## What the bundle records (re-classified under ADR 0011)

| Attempt | Outcome | Why |
| --- | --- | --- |
| 1 | HARNESS_ERROR | `start_pos` 0.00, 4.51, 0.75 is 5.25 studs (XZ) from the spawn centre (z 6): the character moved before any MoveTo (most likely Autopilot's held W). Under the rule before ADR 0011 this was a FELL. |
| 2 | SUCCESS | Recorded, but with the owner's avatar (`LoadCharacterAppearance` true; HRP y 4.71 on the spawn vs 3.20 in M0) in a place with foreign scripts and another driver attached. Not the M0 rig, not a clean run. |
| 3 | CONNECTOR_ERROR | The play tool answered a bare `null` (no `logs`, `errors`, `duration`, `error` or `isTimeout`), most likely because Autopilot's play session was starting or stopping. The episode stops there. |

Episode status: **CONNECTOR_ERROR** (`B0/ep0/summary.json`), 3 attempts, no proposals, no model call.

## How it was closed

Call 009's answer was saved after the debug, on 10 Nov 2026 at 09:00 UTC, with
`scripts/run_experiment.py --config config/experiments/smoke_f1e_p0v1.yaml respond 9 <file>`, the
file holding exactly `null` (the text the operator received at 08:41 UTC). Its `relay_elapsed_s`
(1147.6 s) therefore includes the debug time, not Studio time. Then `step` replayed calls 001 to
009 (no Studio call, no paid call) and wrote the bundle. The config and its lock are unchanged
(`3d8e93f3…`).

## Rule from now on (ADR 0011)

Only one driver of Studio at a time, and Worldsmith runs only in `places/worldsmith-lab.rbxl`
(checked by the preflight's `ws_place` marker before every build).
