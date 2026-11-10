# ADR 0011: One driver, the right place: preflight, player p0.v2, start_pos rule, null answers

- **Status:** accepted for the harness (10 Nov 2026), after the smoke_f1e_p0v1 debug
  (`docs/notes/smoke_f1e_p0v1_debug.md`). CPU only: no Studio call and no paid call was made to
  write it. The owner confirms in the HANDOFF open questions.
- **Context:** the first live p0.v1 smoke built into the Autopilot test place (`testplace.rbxl`:
  foreign server scripts, a second neutral SpawnLocation, `LoadCharacterAppearance` true) while
  an Autopilot bench run (`noise/run-03/F`) drove the same Studio. Attempt 1's character left the
  spawn before any MoveTo (start_pos 5.25 studs from the spawn centre) and was scored FELL;
  attempt 3's play call returned a bare `null`. ADR 0005 (results from the play tool), ADR 0010
  (orchestrator, players, declared-before-runs).
- **Code:** `runner/preflight.py`, `runner/templates/preflight_v1.luau`,
  `runner/templates/p0_controller_v2.luau`, `runner/players.py`, `runner/classify.py`
  (`spawn_centre`, `START_POS_MAX_STUDS`), `runner/episode.py`, `runner/experiment.py`
  (optional `preflight` key, `PreflightFailed`), `runner/dryrun.py`, `scripts/run_experiment.py`, `scripts/mark_place.py`; configs
  `config/experiments/smoke_f1e_p0v2.yaml`, `config/experiments/m2_f1a.yaml`; tests
  `tests/test_ws11_harness_guard.py`, fixture `tests/fixtures/play_answer_null.txt`.

## 0. The rule

**Only one driver of Studio at a time; Worldsmith runs only in `places/worldsmith-lab.rbxl`.**
No Autopilot bench run, no other plugin's play session, and no other place open while a
Worldsmith episode runs. A bundle made while that rule was broken is kept as history, marked
`CONTAMINATED.md`, and never counted (`bundles/experiments/smoke_f1e_p0v1/`).

The code below cannot enforce the rule (nothing in Studio locks out another plugin); it makes a
broken rule visible as a system outcome instead of a scene outcome.

## 1. Preflight (config key `preflight: {script, sha256}`)

Before **every build** (each scene version of an episode), when the experiment declares it:

1. `get_studio_mode` must be `stop`. Otherwise the probe is not sent and **nothing is stopped**:
   another driver may own that play session, and the runner never stops someone else's run.
2. One read-only `run_code` sends the fixed template `runner/templates/preflight_v1.luau`
   (SHA-256 `29ffe3d7d717a08910e831054040bcd24098d90a540dc6b7e359627023fdbd6c`, declared in the
   config and re-checked by the loader). It sets nothing, creates and destroys nothing, and prints
   one line `WS_PREFLIGHT|<json>` (schema `worldsmith.preflight_probe/0.1`).

Checks (`preflight.CHECKS`, all must pass):

| Check | Passes when |
| --- | --- |
| `studio_mode_stop` | `get_studio_mode` = `stop` and `RunService:IsRunning()` is false |
| `place_marker` | `Workspace:GetAttribute("ws_place") == "worldsmith-lab"` |
| `load_character_appearance_off` | `StarterPlayer.LoadCharacterAppearance == false` |
| `character_auto_loads_on` | `Players.CharacterAutoLoads == true` |
| `no_starter_character` | no `StarterPlayer.StarterCharacter` |
| `one_spawn_outside_worldsmith` | exactly one SpawnLocation outside `Workspace.Worldsmith` (the Baseplate template's at (0, 0.5, 0), as saved in worldsmith-lab.rbxl; `places/README.md`) |
| `no_foreign_scripts` | no script (any `LuaSourceContainer`) in ServerScriptService, StarterPlayerScripts, StarterCharacterScripts or StarterGui, except Worldsmith-owned ones: name starts with `Worldsmith` **and** attribute `ws_owned == true` (none exist today) |

The spawn check follows the debug notes ("exactly one SpawnLocation outside
`Workspace.Worldsmith`"): Autopilot's place had two (its Baseplate spawn and `Workspace.Start`),
which, with Worldsmith's own spawn, let Autopilot's characters spawn on `Worldsmith.spawn`.
Requiring **zero** outside spawns would fail the saved worldsmith-lab place (its Baseplate
spawn) until the owner deletes it; that is a possible tightening, not done here.

Outcome: all pass → the usual stop check, build and read-back follow. Any check fails, or the
probe's line is missing or malformed → the version's status is **HARNESS_ERROR** and the episode
stops before the build: no attempt, no patch, no architect call. A call that errors → **CONNECTOR_ERROR**.
The record (studio mode, the probe, every check with observed and wanted values) is written to
`v<k>/preflight.json` either way; `validate_episode_bundle` requires it when the config
declares a preflight. After an episode that failed its preflight, **no further episode of that
experiment starts**, on this `step` or any later one (`experiment.PreflightFailed`, CLI exit 4):
the place or the driver was wrong, so the experiment is spent; fix the place and declare a new
experiment id. Configs declared before this ADR (`smoke_f1e_p0v1`) have no key and run
without it, so their locked hashes and replays are unchanged.

### The place marker (set once by the owner, never by the runner)

The preflight must be able to detect a wrong place, so the runner never writes the marker. The
owner sets it once in the saved place: open `places/worldsmith-lab.rbxl` (File → Open from File),
Edit mode, then

```lua
workspace:SetAttribute("ws_place", "worldsmith-lab")
```

(Command Bar, or `run_code` with the payload `python scripts/mark_place.py` prints, which also
refuses in play mode and prints `WS_PLACE_MARKED|<game name>|worldsmith-lab`), then File → Save
to File over `places/worldsmith-lab.rbxl`. Never in any other place.

## 2. Player p0.v2 (a new labelled player; p0.v0 and p0.v1 untouched)

`p0.v2` = p0.v1 (same timing keys, samples every 1 s) plus a spawn check before the route:

1. After the reset at `Worldsmith.spawn`, wait (at most 3 s, `GROUND_WAIT`, bounded by the
   deadline) until `Humanoid.FloorMaterial` is not `Air`.
2. `spawn_offset` = XZ distance of the HRP from the spawn centre at that moment.
3. The 1 s settle: on every tick, the XZ distance from the spawn centre must stay ≤ 1 stud
   (`MAX_OFFSET`); `settle_max_offset` is the largest, `settle_drift` the largest XZ distance
   from the settle's start position. Grounded again at the end.
4. Otherwise the attempt ends **before any MoveTo** as `harness_error`
   `moved_before_route: spawn_offset=… settle_drift=… settle_max_offset=… grounded=…`
   (a death during these steps: `died_before_route`). The report always carries `spawn_pos`,
   `spawn_offset`, `settle_drift` and `settle_max_offset` (−1 = could not be read).

The template diff from p0.v1 changes only the header comment and the settle loop's condition
(it also ends on a death), and adds no MoveTo, Jump, CFrame, teleport or LoadCharacter (tested).
Controller hash (route `b9514a4b…`, t_attempt 60, t_waypoint 10, settle 2, tick 0.05,
sample_every 1): **`a7472c9cbbea9096f678be02a663ffb503cb2619eb9ea1fe136fcbf2a7858cac`**.
p0.v0 (`3743d998…`) and p0.v1 (`dbc1f325…`) render byte-identically to before.

The server sees the character with a lag (ADR 0004 finding 2); with the controller as the only
driver, M0's recorded attempts all started at exactly x 0.00, z 6.00, so the 1-stud bound leaves
margin. Whether 1 stud is right for the live rig is checked by the p0.v2 smoke, not assumed.

## 3. Classifier: start_pos far from the spawn is HARNESS_ERROR (every player)

`classify_attempt(..., spawn_xz=classify.spawn_centre(scene))`: if the report's `start_pos` (the
HRP at the route start) is more than **2 studs** (`START_POS_MAX_STUDS`, XZ) from the spawn
centre, the attempt is HARNESS_ERROR (`moved_before_route: start_pos … is … studs …`), never FELL
or SUCCESS. Applied after the controller's own `harness_error` (whose detail wins) and before
every observed outcome. A missing `start_pos` (`na`: no route start) leaves the rule out. The
orchestrator, the M0 attempt runner and the WS-08 re-classifier all pass the spawn centre.
Every recorded M0 attempt (8, `bundles/m0/*/attempts/*.json`) started at x 0.00, z 6.00, so
`bundles/m0/gate.json` and `classification.json` reproduce unchanged (tested).

## 4. A bare `null` (any CONNECTOR_ERROR attempt) stops the episode

The play tool's `null` was already CONNECTOR_ERROR ("tool result is not a
run_script_in_play_mode result"). Now any CONNECTOR_ERROR attempt also ends the version at once:
no further attempt is sent, no stop check follows, and the episode's status is
CONNECTOR_ERROR. Studio's state is unknown after such an answer, so sending more would only add
noise. (Before, the remaining attempts still ran and the episode ended SYSTEM_ERROR.) The test
fixture `tests/fixtures/play_answer_null.txt` holds exactly `null`.

## 5. What changed for existing experiments

- `smoke_f1e_p0v1`: closed with `respond 9` (`null`) → CONNECTOR_ERROR; attempt 1 re-scored
  HARNESS_ERROR by §3; marked CONTAMINATED. Its config and lock are unchanged.
- `smoke_f1e_p0v2` (new): F1-E, B0, N = 3, p0.v2, preflight. The re-run of the smoke.
- `m2_f1a_pilot`: had never run (no `experiment.lock.json`), so its config moved to p0.v2 + the
  preflight in place (config SHA-256 changed; worst case unchanged, $2.961).
