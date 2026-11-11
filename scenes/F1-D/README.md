# F1-D: lever walled off and bridge too short (WS-14)

Hand-authored F1 room (SPEC §5), a broken fixture for the M1 stage that needs a two-op fix.
These files are fixtures, not a result: nothing here has been built in Studio or played.
Design, route reasoning and the offline hardness test: `docs/ADR/0013-harder-f1-fixtures.md`
(ADR 0013).

- `scene.json`: **known defects, by construction.** F1-C's room (protected `wall_ledge` across
  the side ledge, `lever_1` behind it at [-16, 0, 24]) with F1-A's short bridge
  (`bridge_1.length` 6 on the 16-stud gap). The short bridge is masked: the closed gate stops
  the player before it.
- `patches/m1_fix.json`: the paired hand fix (baseline B1), a `worldsmith.patch/0.2` patch with
  two ops, `move lever_1` to [-10, 0, 24] and `set bridge_1.length 16`. The M1 stage accepts it.
  Each op alone is also accepted but leaves the walk graph DISCONNECTED (tested).
- `fix.json`: the scene that patch produces (`version` 2, ADR 0006); the same room as F1-C's
  `fix.json` apart from `scene_id` (tested).

Expected with the frozen p0.v3 route (prediction, ADR 0013; the live gate decides): as F1-C,
`STUCK` with no `LEVER_ACTIVATED`. With the fix, `SUCCESS`. A lever-only fix would show the
second defect (a fall into the gap, as F1-A). Hashes: `../HASHES.md`.
