# ADR 0012: Player p0.v3 (a spawn check that counts physics, not wall time); closing the m2_f1a pilot

- **Status:** accepted for the harness (10 Nov 2026) after the m2_f1a pilot's B3 system errors.
  CPU only: no Studio call and no paid call was made to write it. The owner confirms in the
  HANDOFF open questions. Amends ADR 0011 §2 (p0.v2 stays as it is; p0.v3 is a new player).
- **Context:** ADR 0005 (results from the play tool), ADR 0010 (orchestrator, labelled players,
  declared before runs), ADR 0011 (preflight, p0.v2 spawn check);
  `bundles/experiments/m2_f1a_pilot/CLOSED.md`.
- **Code:** `runner/templates/p0_controller_v3.luau`, `runner/players.py` (p0.v3, pinned template
  hashes), `runner/classify.py` (new report fields), `runner/experiment.py` (`CLOSED_FILE`,
  `ExperimentClosed`), `runner/dryrun.py`, `scripts/run_experiment.py`; configs
  `config/experiments/smoke_f1e_p0v3.yaml`, `config/experiments/m2_f1a_b3.yaml`; tests
  `tests/test_ws11_p0v3.py`.

## Problem (numbers from the pilot's attempt raw files)

In `m2_f1a_pilot` (player p0.v2, controller `a7472c9c…`), three attempts ended HARNESS_ERROR with
the controller detail `moved_before_route: spawn_offset=0.00 settle_drift=0.00
settle_max_offset=0.00 grounded=false` and `start_pos` 0.00,3.11,6.00: the character stood on the
spawn (centre x 0, z 6), it had not moved. Those play sessions ran very few controller ticks:

| Attempt | Controller ticks | Per `task.wait(0.05)` |
| --- | --- | --- |
| B3-haiku v2 attempt 2 | 5 ticks in 1.54 s | 0.31 s |
| B3-haiku v2 attempt 3 | 6 ticks in 2.40 s | 0.40 s |
| B3-sonnet v1 attempt 2 | 6 ticks in 1.33 s | 0.22 s |

The pilot's 20 attempts that ran their route took 115 to 148 ticks in 7.97 to 10.04 s, 0.065 to
0.070 s per tick (B3-haiku v2 attempt 1: 0.087 s per tick, route start at 3.01 s, a slow start
that still passed). A separate play-mode health probe measured 0.083 s per tick over 40 waits
(reported by the control thread; not a file in this repo).

So the first ~1.5 s of some play sessions run slowly. p0.v2's spawn check is wall-clock based: it
waits at most `GROUND_WAIT` = 3 s for `Humanoid.FloorMaterial` ~= Air, then settles for 1 s of
`task.wait(0.05)` ticks, and requires FloorMaterial ~= Air again at the end. When few physics
steps run in that time, FloorMaterial can still read Air on a character standing on the spawn,
and the check fails (`grounded=false`). The runner turns any HARNESS_ERROR attempt into a
SYSTEM_ERROR version verdict that stops the episode (`runner/episode.py` `SYSTEM_OUTCOMES`,
`STOP_STATUSES`), so B3-haiku and B3-sonnet ended without a scene outcome.

## Decision

### 1. Close the pilot (owner decision A)

`bundles/experiments/m2_f1a_pilot/CLOSED.md` records the close: B0, B1 and B2 are valid results;
B3-haiku and B3-sonnet are SYSTEM_ERROR (the flake above); B3-opus was not run; architect spend
$0.001562 (n = 2 haiku calls). The B3 conditions move to `m2_f1a_b3`.

The runner honours a closed experiment: if `CLOSED.md` exists in the experiment's bundle folder,
`Experiment.run` raises `ExperimentClosed` before reading or sending anything, and the CLI's
`step`, `respond` and `architect` refuse with **exit 4** (message names the file); nothing is
written, no relay request, no answer, no architect call. `status` and `plan` still print (and
say it is closed). The pilot's pending call 010 (`get_studio_mode`) therefore stays unanswered.

### 2. Player p0.v3: p0.v2 with only the spawn check changed

`runner/templates/p0_controller_v3.luau`; the spawn check counts physics steps
(`RunService.Heartbeat:Wait()`), not wall-clock ticks:

1. **Warm-up.** After the new character exists, wait until `WARM_FRAMES` = 10 consecutive
   Heartbeat steps each have dt ≤ `WARM_MAX_DT` = 0.1 s, bounded by `WARM_WAIT` = 10 s of wall
   clock and the attempt deadline. Never warm → `harness_error`
   `not_warm: warm_frames=… warm_wall_s=… max_dt_warm=… warm_run=… dts=…` (the observed dts, up
   to 64, also in `warm_dts`).
2. **Grounded.** Within `GROUND_STEPS` = 120 Heartbeat steps (and the deadline): grounded =
   `FloorMaterial` ~= Air (`ground_test` "floor") **or** a downward ray from the
   HumanoidRootPart, excluding the character, hits a BasePart within `HipHeight` + 1.5 studs
   (`ground_test` "ray", `ray_dist`). The measured M0 rig (`bundles/m0/character/measure.json`:
   R15, hip height 2, HRP 2×2×1) stands with its root 3.0 studs above the floor, inside the
   3.5-stud ray. Never grounded → `not_grounded: before the settle, ground_steps=…`.
3. **Settle.** `SETTLE_STEPS` = 30 Heartbeat steps with p0.v2's XZ checks (`MAX_OFFSET` 1 stud
   from the spawn centre; `spawn_offset`, `settle_drift`, `settle_max_offset` as before). An
   offset failure keeps p0.v2's detail `moved_before_route: …` (a real move). Grounded again at
   the end by the same test (`ground_test_end`), else `not_grounded: at the settle end, …`.
   A death in any step is `died_before_route`, as before.

Reported in addition to every p0.v2 field: `warm_frames`, `warm_wall_s`, `max_dt_warm`,
`warm_dts`, `ground_steps`, `ground_test`, `ray_dist`, `settle_steps`, `ground_test_end`. The
classifier keeps them in the attempt's `report` when present (p0.v0 to p0.v2 reports are
unchanged).

**Unchanged:** everything before the spawn check (player and character waits, the one
`LoadCharacter` reset, death and jump watches, the samples) and everything from the route start
on (waypoints, `MoveTo` per waypoint, `watch()` ticks and 1 s samples, events, settle at the end,
the chunked report). A test checks those two parts are line-for-line p0.v2's, and that the spawn
check adds no MoveTo, Jump, CFrame, PivotTo, WalkSpeed, gravity, Anchored, teleport,
LoadCharacter or position write. The timing keys and slots are p0.v2's; only the nonce, the
declared timing and the waypoint lines are substituted.

Hashes: template file `58ca4bce6abd0a4315194a05544bfea05fad9575449fb0c7d254f7d3540c66d6`;
controller (route `b9514a4b…`, t_attempt 60, t_waypoint 10, settle 2, tick 0.05,
sample_every 1) **`cd9a7a0a21ccd32ea7e14990f17b7bba58083f1bfeb2a81e77cf2e122a2d0f8c`**.
Every template file's SHA-256 is now pinned in the player registry (`PlayerVersion.template_sha256`,
p0.v0 to p0.v3); `players.load_template` refuses a template whose bytes differ and
`load_experiment` reports it as a config error. p0.v0 to p0.v2 render byte-identically to before.

### 3. HARNESS_ERROR still stops an episode

p0.v3 changes how the spawn is checked, not what a failed check means. A `not_warm`,
`not_grounded` or `moved_before_route` attempt is HARNESS_ERROR, and the version is SYSTEM_ERROR
and the episode stops, as before. Why not retry, or count it as a scene outcome: the attempt
says nothing about the scene (the route never started), a retry would make the number of play
calls depend on the harness and break the declared caps (≤ 12 attempts per episode), and
quietly re-running would lower a pre-declared gate. The fix is upstream: count physics, then
show the check passes repeatedly (`smoke_f1e_p0v3`: 6/6 SUCCESS expected; any HARNESS_ERROR
means stop and debug) before spending model calls on `m2_f1a_b3`.

## New experiments (declared and committed before any run)

- `config/experiments/smoke_f1e_p0v3.yaml`: F1-E passing control, B0, p0.v3, preflight, 2
  episodes (6 attempts). Purpose: the spawn check passes repeatedly. Not a result.
- `config/experiments/m2_f1a_b3.yaml` (`m2_f1a_b3`): F1-A, M0, p0.v3, preflight, B3-haiku,
  B3-sonnet, B3-opus, one episode each; every other field copied from `m2_f1a.yaml` (same
  ladder, baseline prompt `df1b7a12…`, caps, N 3, k 3, max_revisions 3, job `architect_ladder`).
  Worst case $2.961000 (9 calls), as the pilot's.

## Consequences

- p0.v3 has never run in Roblox; the smoke is its first live use. Whether 10 steps of dt ≤ 0.1 s,
  120 ground steps and the 3.5-stud ray suit the live rig is checked there, not assumed.
- The pilot's B0, B1, B2 (p0.v2) and m2_f1a_b3's B3 (p0.v3) are results of two player versions;
  they are reported side by side with their player, never pooled.
- A slow start longer than `WARM_WAIT` is reported as `not_warm` with its dts instead of being
  mistaken for a character that left the spawn.
