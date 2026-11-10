# Clip recording runs, 11 Nov 2026

Re-runs of the M0 gate scenes for the episode-1 screen recording, with the same frozen P0
controller (3743d998...c034), config (15884b5a...7c6) and route as `bundles/m0/`. Declared before
any call: the clip shows attempt 1 of each scene, whatever its outcome; attempts 2 and 3 are
logged and counted here but not shown. If attempt 1's outcome differs from the gate
(F1-A not falling in the gap, or the fix failing), no clip is made from these runs.
These are not gate results; the gate's numbers stay in `bundles/m0/gate.json`.

## Outcome

Both episodes completed with 3 attempts each (`*/summary.json`):

| Scene | Attempt 1 | All attempts |
| --- | --- | --- |
| F1-A (bridge 6, scene 92f86609) | FELL | FELL 3 of 3 |
| F1-A-fix (bridge 16, scene 49371fa7) | SUCCESS | SUCCESS 3 of 3 |

Attempt 1 matched the gate in both scenes, so the clip rule above was met.

## What the footage shows (and why)

These logged runs could not be filmed: `run_script_in_play_mode` runs the play session without
drawing its client view in Studio's viewport, so the recording showed only the edit camera.
The footage in `docs/media/episode1.mp4` is therefore an on-camera replay under Studio's normal
Play: the same nine waypoints and MoveTo-only movement, a fixed overhead camera, no result
logging. Its source and the exact steps are in `tools/clip/replay_take.luau`. Both temporary
scripts were removed afterwards and the place was not saved. The clip labels the footage as a
replay and cites the logged gate runs (`bundles/m0/gate.json`) for its numbers; these clip
re-runs agree with them.
