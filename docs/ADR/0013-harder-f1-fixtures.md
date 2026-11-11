# ADR 0013: Harder F1 rooms: broken fixtures F1-B, F1-C, F1-D and their offline hardness test

- **Status:** proposed for WS-14 (11 Nov 2026), CPU parts only. No Studio call, no network call
  and no paid call was made to write it. The live gate (below) and the owner's approval of
  `m2_hard`'s worst case are still to come.
- **Context:** `docs/TICKETS.md` WS-14. On F1-A every model found the reference fix on its first
  call and B2 (random valid patch) also repaired it (HANDOFF 10 Nov 22:45, `m2_f1a_b3`), so F1-A
  cannot separate architects. ADR 0007 (validator, M1 stage), ADR 0008 (walk graph), ADR 0004
  (P0: server-scripted `Humanoid:MoveTo`, never jumps), ADR 0012 (player p0.v3).
- **Files:** `scenes/F1-B/`, `scenes/F1-C/`, `scenes/F1-D/` (each `scene.json`, `fix.json`,
  `patches/m1_fix.json`, `README.md`); `scenes/HASHES.md`; `tests/test_ws14_fixtures.py`; build
  snapshots `tests/snapshots/ws03/F1-{B,C,D}_{scene,fix}.build.luau`.

## Rules every fixture keeps

Each fixture is F1-A's room plus its declared defect and nothing else (tested field for field):
the same room box, constraints (`min_gap` 12, `min_lever_detour` 10, physics locked, the two
required events), the same protected entities with the same values (spawn, goal, `plat_a`,
corridor, `ledge_1`, `plat_b`, `gap_1` 16 studs wide, the three corridor walls, both triggers, the
kill floor), the same gate and decor. Each `scene.json` validates against the scene schema; base
and fix have no solid overlap and nothing outside the room. The player is unchanged: **p0.v3**,
route `config/p0_route_F1.json` (`b9514a4b…`, the same nine waypoints), T_attempt 60 s,
T_waypoint 10 s, settle 2 s. No validator rule, stage, grammar or player was changed.

Each hand fix is one `worldsmith.patch/0.2` patch, `patches/m1_fix.json`, that the **M1 stage**
accepts (and M0 rejects); `fix.json` is the scene it produces (`version` 2, ADR 0006).

| Fixture | Defect (by construction) | Hand fix (M1) | Ops | Broken walk graph | Fix |
| --- | --- | --- | --- | --- | --- |
| F1-B | full-length bridge built 10 studs beside the path (`bridge_1.lateral_offset` 10: x 7 to 13) | `set bridge_1.lateral_offset 0` | 1 | DISCONNECTED (gate opens; stops at the corridor end, z 35.5) | ACCEPTED, CONNECTED |
| F1-C | lever walled off: protected `wall_ledge` across the ledge (x -13 to -11), `lever_1` behind it at [-16, 0, 24]; bridge full length | `move lever_1` to [-10, 0, 24] | 1 | DISCONNECTED (gate stays closed; stops at z 30.5) | ACCEPTED, CONNECTED |
| F1-D | F1-C's walled-off lever **and** F1-A's short bridge (`length` 6) | `move lever_1` to [-10, 0, 24] + `set bridge_1.length 16` | 2 | DISCONNECTED (gate stays closed) | ACCEPTED, CONNECTED |

Two fixes are not `set bridge_1.length` (F1-B, F1-C); F1-D needs two ops: each of its ops alone
is accepted but leaves the walk graph DISCONNECTED (tested). F1-B's fixed room is F1-A's
`fix.json` room (only `scene_id` and `version` differ), and F1-D's fixed room is F1-C's.

The walk graph is the ADR 0008 **approximation, never a proof**: actual runs decide.

### Why these defects, and how each fix stays inside the M1 rules

- **F1-B (bridge beside the path).** A "built in the wrong place" bridge: the right length,
  wrong across-axis position. The corridor's walkable end is x -5 to 5 (between the walls,
  x -4 to 4), so a bridge from x 7 to 13 joins nothing; its far end still overlaps `plat_b`
  (x -8 to 8) by one stud across, so it looks plausible in the JSON. Offset 10 (not less) so
  that no width the grammar allows (at most 16) can reach the corridor without also moving it
  (width 15 and 16 alone: accepted, walk DISCONNECTED). Any offset with |offset| <= 4.5 joins
  the walk graph again (checked at 4, 4.5, 5 and -4.5, -5), so the B2 sampler, which draws the
  offset uniformly in -32 to 32, can hit a working value; the hardness test measures how often.
- **F1-C (lever walled off).** SPEC §5 lists "lever unreachable behind a wall" as a later
  family. The wall is protected and the M1 stage has no `remove`, so the only legal repair is to
  move the lever. ADR 0007's lever rules fix where it may go: on `ledge_1`'s top (y 0), inside
  its footprint, at least 10 studs from the corridor centreline (detour), and its solid base
  (2 x 2 x 1) may not overlap the wall. With the wall's near face at x -11 that leaves exactly
  x = -10 (base x -11 to -9, touching the wall face, which is not an overlap). The lever was moved
  out to x -16 in the broken room so that its touch zone (6 x 5 x 6, x -19 to -13) ends 2 studs
  behind the wall's near face (tested): the player pressed against the wall cannot reach it
  (R15 limbs do not collide and can swing past the torso; 2 studs is a margin for that, a
  design choice, not a measurement).
- **F1-D (two defects, one masked).** The gate stops the player before the short bridge, so
  the failure seen is F1-C's; the bridge defect only shows after the lever is fixed. Revisions
  patch the last accepted scene (ADR 0010), so an architect can also repair it in two revisions
  of one op each; the offline test below allows only one patch on the unchanged room.

Gate moves were considered and rejected as fixes: ADR 0007's slot rule judges a gate against the
base scene's gate, so any gate defect large enough to matter cannot be moved back
(`GATE_BYPASSED`). Added walls and decor cannot help a straight-line MoveTo walker (a wall only
blocks; decor is not solid). Gate re-linking is outside M1.

## What the frozen p0.v3 route does in each room (prediction; the live gate decides)

p0.v3 resets the character on the spawn (x 0, z 6), then walks the nine waypoints with
`Humanoid:MoveTo`, one at a time, in straight lines: corridor (0, 20), ledge_entry (-6, 24),
lever_zone (-13, 24), corridor_mid (0, 24), before_gate (0, 31), past_gate (0, 35.5),
bridge (0, 40), landing (0, 55), goal (0, 60). It never jumps. A waypoint ends when MoveTo
reports finished (Roblox gives up after about 8 s, the value `config/m0.yaml` states; not
measured here) or after T_waypoint 10 s. The attempt ends at a death, after the route plus the
2 s settle, or at T_attempt 60 s. Walking speed 16 studs/s (`measure.json`, n = 1). Recorded
p0.v3 attempts (`m2_f1a_b3`, `smoke_f1e_p0v3b`; n = 24) started their route 1.685 to 3.387 s after
the script started, with position samples 0.900 to 0.967 s apart.

Classifier (`runner/classify.py`, `runner/taxonomy.py`, `docs/CLASSIFIER.md`): `KILL_FLOOR_TOUCHED`
before any goal is **FELL** (with "in the gap_1 area" when within the gap footprint + 4 studs:
x -12 to 12, z 32 to 56); a route that ends (`route_done`) with no goal and no death is
UNCLASSIFIED, which becomes **STUCK** when samples at most 1 s apart stay within 2 studs for 10 s;
`TIMEOUT` (with `LEVER_NOT_ACTIVATED` / `GATE_NOT_OPENED`) needs the 60 s deadline to fire.

### F1-B: expected FELL in the gap_1 area, cause UNCLASSIFIED

As F1-A up to the gate: the lever is F1-A's (x -15), so the route's lever_zone waypoint is inside
its zone: `LEVER_ACTIVATED`, `GATE_OPENED`. From past_gate (z 35.5, still on the corridor, which
ends at z 36) the player walks along x = 0 towards bridge (z 40). Nothing is under that line
(the bridge is at x 7 to 13), so it drops off the corridor end into the gap and touches the kill
floor below it (top y -29; about 0.54 s of fall), x near 0, z between 36 and roughly 45 by the
fall time and walking speed: inside the gap_1 area. Outcome **FELL**, `fell_in_area` true. The
cause stays **UNCLASSIFIED**: BRIDGE_SHORT_HYPOTHESIS needs a bridge shorter than its gap
(condition 2), and this bridge is 16 = 16. The classifier has no "bridge misaligned" hypothesis
and none is added.

Fix: the bridge is back at x -3 to 3, F1-A's fixed room, which passed **SUCCESS** 3/3 each time
it was played live: `docs/M0.md` (fix.json), `m2_f1a_pilot` B1 and `m2_f1a_b3` B3-haiku,
B3-sonnet and B3-opus (the patched room `297b23c8…`, HANDOFF 10 Nov).

### F1-C: expected STUCK, with no LEVER_ACTIVATED

1. corridor, ledge_entry: as F1-A (about 1.3 s of walking).
2. lever_zone (-13, 24): the wall's near face is at x -11, so the player is held there, about
   0.6 studs from it, for the rest of MoveTo's ~8 s. The lever's zone ends at x -13: no touch,
   **no `LEVER_ACTIVATED`**, so `gate_1` stays closed.
3. corridor_mid, before_gate (z 31): reached (about 1.1 s).
4. past_gate, bridge, landing, goal: all straight ahead along x = 0; the closed gate (z 33 to 34)
   holds the player at about z 32.4 for each, about 8 s each.
5. The route ends about 42 s after it started (0.9 + 0.45 + 8 + 0.65 + 0.44 + 4 x 8), then the
   2 s settle: about 44 s after the route start, about 46 to 48 s after the script start (route
   starts 1.7 to 3.4 s, above), before T_attempt 60 s. Even if MoveTo never gave up and every
   held waypoint used T_waypoint (10 s), the end would be about 52 + 2 + 3.4 = 57.4 s < 60 s.
   So the controller reports `route_done`, not `deadline`: no TIMEOUT tag.
6. No goal, no death: UNCLASSIFIED, then the STUCK rule: held at the gate from about 3 s into the
   route to its end (about 39 s), samples about 1 s apart within 2 studs: **STUCK** (outcome
   STUCK, tags `["STUCK"]`). No `KILL_FLOOR_TOUCHED`, `BRIDGE_CROSSED` or `GOAL_TOUCHED`.

If the 60 s deadline fired instead (a play session more than about 12 s slower before its route
than any recorded one), the outcome would be TIMEOUT with `STUCK` and `LEVER_NOT_ACTIVATED`; that
is not the declared expectation, and a live gate that sees it stops and debugs.

Fix (lever at [-10, 0, 24], zone x -13 to -7): walking from ledge_entry (x -6) towards x -13, the
player enters the zone within about one stud: **`LEVER_ACTIVATED`**, then `GATE_OPENED`. It is then
held by the lever (handle at x -10, base 1 stud high at x -11 to -9) and the wall until MoveTo
gives up (about 7.5 s held, under the 10 s STUCK window), walks back, passes the open gate, crosses
the full bridge (`BRIDGE_CROSSED`) and reaches the goal (`GOAL_TOUCHED`): **SUCCESS**, about 12 s
of route.

### F1-D: expected STUCK, with no LEVER_ACTIVATED

Identical to F1-C up to the end of the route: the short bridge lies beyond the closed gate and is
never reached. **STUCK**, no `LEVER_ACTIVATED`. Fix: F1-C's fixed room: **SUCCESS**. A lever-only
patch (accepted, walk DISCONNECTED) would show the second defect as F1-A does: FELL in the gap.

## Live gate (declared here, not run; Studio, p0.v3, N 3, k 3, read-back 0 differences)

| Scene | Expected, 3/3 attempts |
| --- | --- |
| F1-B `scene.json` (`2b54bbe7…`) | FELL, in the gap_1 area |
| F1-B `fix.json` (`74c137b1…`) | SUCCESS (`BRIDGE_CROSSED` then `GOAL_TOUCHED`) |
| F1-C `scene.json` (`e916fde2…`) | STUCK, no `LEVER_ACTIVATED` |
| F1-C `fix.json` (`53abb4ab…`) | SUCCESS |
| F1-D `scene.json` (`16d226d1…`) | STUCK, no `LEVER_ACTIVATED` |
| F1-D `fix.json` (`9cc6f35c…`) | SUCCESS |

Same rule as M0: an inconsistent result (for example 2/3, or TIMEOUT where STUCK is expected)
means stop and debug; the bar is never lowered and the player is never changed to fit a room. A
fixture whose defect does not show with the frozen route is dropped.

## Offline hardness (step 2; pre-declared before it was computed)

For each broken fixture: B2 (`baselines/random_valid.py`, sampler `worldsmith.b2/0.1`) at the M1
stage, seeds 0 to 199, episode 0, 3 raw proposals per seed, every proposal validated once
against the unchanged broken scene: exactly the `scripts/ws_b2_offline.py` method and seed
derivation (`SHA-256('worldsmith.b2/0.1|seed=<s>|episode=<e>|proposal=<i>')`). Nothing is played.

A fixture **passes** when all hold:

1. episodes with at least one accepted, walk-CONNECTED proposal: **at most 5 %** (at most 10 of 200);
2. the broken scene is walk-DISCONNECTED;
3. its hand fix is ACCEPTED at M1 and walk-CONNECTED.

A fixture that misses is redesigned or dropped, never re-scored with another bar or method; every
attempt is recorded below. F1-A at M1 is computed the same way for reference (b2_offline: 1/200).
Script: `scripts/ws_hardness.py`; output: `bundles/hardness/summary.json`.

## Dropped or redesigned attempts

None at the time of writing (before the hardness run). The design choices above (offset 10, the
lever at x -16, a 2-stud wall) were made from the geometry and the rules, before any B2 statistic
was computed for these rooms.

## Consequences

- Six new scene files in `scenes/HASHES.md` and in the shared fixture tests (schema, bounds,
  hashes, compiler snapshots); three `worldsmith.patch/0.2` hand patches, the first committed M1
  patches.
- The live gate needs the owner's Studio session; nothing here says what Roblox will do.
- F1-C and F1-D share one failure behaviour (STUCK at the closed gate) and one fixed room; F1-B
  and F1-A share one fixed room. The rooms differ in what must be changed, not in the fixed result.
