# F1-C: lever walled off on its ledge (WS-14)

Hand-authored F1 room (SPEC §5), a broken fixture for the M1 stage. These files are fixtures,
not a result: nothing here has been built in Studio or played. Design, route reasoning and the
offline hardness test: `docs/ADR/0013-harder-f1-fixtures.md` (ADR 0013).

- `scene.json`: **known defect, by construction.** F1-A's room with three changes: a protected
  wall `wall_ledge` (2 x 8 x 8 studs, x -13 to -11) standing across the whole side ledge, the
  lever moved behind it (`lever_1.pos` [-16, 0, 24]; its touch zone ends at x -13, 2 studs
  behind the wall's near face) and the bridge full length (`bridge_1.length` 16). Nothing
  reachable touches the lever, so `gate_1` never opens. Platforms, gap, gate, other walls, kill
  floor and spawn/goal are F1-A's.
- `patches/m1_fix.json`: the paired hand fix (baseline B1), a `worldsmith.patch/0.2` patch with
  one op, `move lever_1` to [-10, 0, 24]: on the ledge, in front of the wall, exactly the F1
  minimum lever detour (10 studs). The M1 stage accepts it; it is not a bridge-length fix.
- `fix.json`: the scene that patch produces (`version` 2, ADR 0006).

Expected with the frozen p0.v3 route (prediction, ADR 0013; the live gate decides): no
`LEVER_ACTIVATED`; the player is held by the wall, then by the closed gate for the last four
waypoints, and the route ends before T_attempt: `STUCK`. With the fix, `SUCCESS` (the player
touches the moved lever's zone on its way to the wall). Hashes: `../HASHES.md`.
