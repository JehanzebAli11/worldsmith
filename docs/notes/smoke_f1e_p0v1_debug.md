# smoke_f1e_p0v1: why attempt 1 fell and attempt 3 returned `null` (dbg1, 10 Nov 2026)

Diagnosis only: no controller, config or place changes. Evidence: the partial bundle
`bundles/experiments/smoke_f1e_p0v1/` (commit 7e5b2ff), `bundles/m0/`, read-only `run_code`
queries in Studio (Edit mode, about 08:44 to 08:47 UTC), the saved `places/worldsmith-lab.rbxl`
(parsed offline), and the Autopilot repo's noise-check output
(`~/lab/autopilot/bench/results/noise/run-03/`).

## Root cause: the episode did not run alone, and not in the Worldsmith place

1. **Studio had the Autopilot test place open, not `worldsmith-lab.rbxl`.** `game.Name` was
   `testplace.rbxl`; the place holds the Rojo-built "Autopilot Test Place"
   (`autopilot/examples/roblox_testplace`): `ServerScriptService.Build`, `.Game`, `.Debug`,
   `ReplicatedStorage.Config` + `Remotes.StageReached`, `StarterGui.Hud`,
   `StarterPlayerScripts.Hud`, and a second neutral `SpawnLocation` `Workspace.Start`.
   The smoke's build (call 002) put `Workspace.Worldsmith` (hash f8a8e654…) into that place.
   The saved `places/worldsmith-lab.rbxl` is fine: `LoadCharacterAppearance` false, one
   `SpawnLocation`, none of the Autopilot scripts.
2. **An Autopilot bench run was driving the same Studio at the same time.** Autopilot noise-check
   `run-03/F` ("Verify that the door can be walked through while it is shut") ran from
   08:37:03 UTC for 626 s (4 episodes: holding W into the door, running and jumping). The smoke's
   calls ran 08:36:40 to 08:41:10 UTC. Autopilot's `run-03/F/console.txt` contains:
   - Worldsmith's own attempt-1 lines (`WS|78af08d0…|KILL_FLOOR_TOUCHED`, `WSP|78af08d0…|RESULT`),
     so Autopilot's plugin was attached to that play session;
   - Autopilot's own characters on the Worldsmith course with nonce `-` (two neutral spawns, so
     its characters spawned on `Worldsmith.spawn`): BRIDGE_CROSSED and GOAL_TOUCHED 08:38:24,
     KILL_FLOOR 08:38:41, LEVER/GATE 08:39:39, KILL_FLOOR 08:40:04, more at 08:45 (during dbg1's
     read-only queries; those query prints are in that console too).
   `run-04` started at 08:47 UTC and was running when dbg1 stopped querying Studio.

## Attempt 1 (FELL, 0 samples)

- Not the controller: p0.v1 differs from p0.v0 only by the sampler (`diff runner/templates/…`);
  the first sample is due 1 s after the route start, and the character died 0.43 s after it, so
  0 samples is expected.
- The character left the spawn **before any MoveTo**: `start_pos` (taken at the end of the 1 s
  settle, before the first `MoveTo`) is `0.00,4.51,0.75`, 5.25 studs from the spawn centre
  (z 6) and off the 6 × 6 spawn pad (z 3 to 9), on plat_a 0.75 studs from its −z edge. Every M0
  attempt, smoke2 and attempt 2 started exactly at x 0.00, z 6.00. The drift is pure −z
  (x stayed 0.00), the direction a character spawned on an unrotated `SpawnLocation` faces,
  i.e. "forward". With an Autopilot agent holding W in its episodes and its plugin attached to
  this play session, that is the most likely mover. (Server positions lag the client; the
  client character was already further along, which is why it died 0.43 s into the route.)
- The `KILL_FLOOR_TOUCHED` y of −7.85 (floor top at −29) is the known server-side lag (M0 FELLs
  show the same, e.g. −14.29), not a second anomaly.
- Also different from M0: `LoadCharacterAppearance` is **true** in testplace.rbxl, so the
  character was the owner's avatar (standing HRP y 4.51 on plat_a vs 3.00 in M0, and 4.71 vs
  3.20 on the spawn). Attempt 2's SUCCESS is therefore not with the measured M0 rig either.

## Attempt 3 (`null`)

At 08:41:10 Autopilot's run F was mid-episode (its play sessions start and stop play themselves).
Studio has one play session; a `run_script_in_play_mode` that collides with another session
being started or stopped plausibly returns nothing usable: the bare `null` has none of the
fields every real result had (`logs`, `errors`, `duration`, `error`, `isTimeout`; ADR 0005), no
WS lines reached any console, and Studio was back in `stop`. Not a controller error (it would
have produced `errors` / logs) and not the tool's own timeout (that reports `isTimeout`).
The MCP server's source was not read (out of reach), so the exact code path is unverified.

The runner already handles it: `respond 9` with a file holding `null` → `json.loads` → `None` →
`classify_result` → `CONNECTOR_ERROR` ("tool result is not a run_script_in_play_mode result").

## Not causes

- `[UI Workflow] Plugin loaded!`, `[robloxstudio-mcp] … ConnectFail` and
  `rbxassetid://203785492 … Asset type does not match` appear in the M0 raw answers too
  (`places/README.md`); they come from plugins/engine at play start, not from the place.
  What references 203785492 was not looked up (Studio was busy with Autopilot run-04).
- The Autopilot place's `Game.server` (checkpoints, lava, coin, lever/door by CollectionService
  tag) builds at world y 0 to 7; Worldsmith uses attributes, not tags, so no tag cross-talk.

## Proposed fixes (not implemented)

Owner actions (first):
1. One Studio, one driver: do not run Autopilot bench runs (or any other play-driving plugin
   session) while a Worldsmith episode runs; close the Autopilot test place first.
2. Reopen `places/worldsmith-lab.rbxl` (File → Open from file) before any Worldsmith episode.
3. Tell the Autopilot side that `noise/run-03/F` (and any run while `Workspace.Worldsmith` sat in
   testplace.rbxl, including run-04 if the folder is still there) ran with a foreign course and a
   second spawn in its place: evidence contaminated. Removing `Workspace.Worldsmith` from
   testplace.rbxl (or not saving it) is his call.
4. Optionally uninstall/disable the Autopilot plugin (and the robloxstudio-mcp plugin) in Studio
   during Worldsmith runs, since both attach to every play session.

Code (Worldsmith), each a reviewed change with a new hash:
5. Preflight check in the runner (a `run_code` before the build, recorded in the bundle): refuse
   with HARNESS_ERROR unless `LoadCharacterAppearance == false`, `CharacterAutoLoads == true`, no
   `StarterCharacter`, exactly one `SpawnLocation` outside `Workspace.Worldsmith`, and no
   scripts in `ServerScriptService` / `StarterPlayerScripts` / `StarterCharacterScripts` /
   `StarterGui` other than an allow-list (empty for worldsmith-lab). Optionally a place marker
   attribute (e.g. `Workspace:GetAttribute("ws_place") == "worldsmith-lab"`) set once by the
   owner.
6. Player p0.v2 (new controller hash; p0.v1 stays frozen): after the reset, wait until the HRP
   is within 1 stud (XZ) of the spawn centre and `FloorMaterial ~= Air`, then require it to stay
   there through the settle (refuse the route with `harness_error` "moved before route" if it
   drifts more than 1 stud before the first MoveTo). Report `spawn_offset` and the settle drift.
   Optional: disable client input for the controlled character during the attempt is not
   possible server-side without changing the player; the drift check makes interference
   visible instead of a FELL.
7. Record the play tool's `null` (or any non-object answer) explicitly as CONNECTOR_ERROR (it
   already is) and stop the episode there; add a test with a `null` answer fixture.
8. Classifier/report: an attempt whose `start_pos` is more than X studs from the spawn is
   HARNESS_ERROR, not FELL (so a disturbed attempt cannot count against a scene).

Re-run: after 1, 2 and 5, run the smoke again under a new experiment id (this partial bundle
stays as history; respond 9 with `null` to close it as CONNECTOR_ERROR).
