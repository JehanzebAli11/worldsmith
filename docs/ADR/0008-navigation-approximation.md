# ADR 0008: The navigation approximation (walk graph, WS-07)

- **Status:** accepted for WS-07 (9 Nov 2026); the owner confirms in the WS-07 HANDOFF open
  questions.
- **Context:** SPEC §7 item 5 asks for "a coarse walk graph over top surfaces, using the step and
  jump limits and capsule measured in M0", output `GRAPH_CONNECTED` or `GRAPH_DISCONNECTED`
  *under that declared approximation*. M0 measured the default R15 character once
  (`bundles/m0/character/measure.json`, n = 1): its bounding box, its Humanoid and Workspace
  properties, one 60-stud walk and one level jump. It measured **no step height and nothing else
  about jumping** (P0 never jumps, ADR 0004). Code: `validator/nav.py`; tests:
  `tests/test_ws07_nav.py`.

## What it is, and what it is not

The graph is an **approximation, never a physical proof** of solvability or unsolvability.
**Actual runs decide** (SPEC §7). It is an early, cheap signal for the architect, the sampler and
the people reading a bundle; it is wrong wherever Roblox physics differs from the model below.
So the validator only reports it: it fills `graph` and adds the warning
`GRAPH_DISCONNECTED_WALK` when the walk mode finds no path. It **never** turns `ok` false and
never adds a violation (a test forces both modes to DISCONNECTED and checks `ok` is unchanged).

## Parameters (one config block: `nav.PARAMS`)

Every value, its basis and its source. *measured*: read from `measure.json` (Studio, n = 1);
*derived*: computed from measured values; *assumed*: neither, a stated guess; *design*: a
modelling choice.

| Name | Value | Basis | Source |
| --- | --- | --- | --- |
| `cell` | 1 stud | design | Grid spacing on X and Z. F1 part faces sit on whole or half studs. |
| `body_height` | 5.19 studs | measured | `bbox[1]` = 5.187, rounded up to 0.01. |
| `body_radius` | 2.09 studs | derived | Half the horizontal diagonal of the measured bbox 4.000 × 1.198 (`bbox[0]`, `bbox[2]`) = 2.088, rounded up: a vertical cylinder that holds the character in any facing. Conservative: narrow openings close first. |
| `step_up` | 1 stud | assumed | Not measured in M0 (P0 never stepped up). Set to ADR 0007's `FLOOR_BAND`. |
| `walk_speed` | 16 studs/s | measured | `walk_speed` (Humanoid.WalkSpeed). |
| `jump_height` | 7.2 studs | measured | `jump_height` (Humanoid.JumpHeight; `use_jump_power` false). |
| `gravity` | 196.2 studs/s² | measured | `gravity` (Workspace.Gravity). |
| `jump_rise` | 7.2 studs | derived | Highest landing above take-off: the ballistic apex, = `jump_height`. The one measured jump rose 7.30 (`jump.apex_rise`, n = 1). |
| `jump_reach_flat` | 8.669 studs | derived | `walk_speed` × 2·√(2·`jump_height`/`gravity`), centre to centre on a level jump (air time 0.542 s). The one measured jump went 8.75 studs in 0.549 s (`jump.horizontal`, `jump.air_time`, n = 1): within 1 %, a consistency check, not a calibration. |

`measure.json` stores Studio's single-precision values (196.1999969…, 7.199999809…); the block
uses the values as set in Studio (196.2, 7.2). A test checks every *measured* value against the
file and every *derived* value against its formula. For a landing `dh` studs above take-off,
`reach(dh)` = `walk_speed` × (v₀ + √(v₀² − 2·g·dh)) / g with v₀ = √(2·g·`jump_height`); no jump
when `dh` > `jump_rise` (for example 4.33 studs at `dh` = 7.2, 10.63 at `dh` = −8).

## The model

1. **Parts:** the compiler's own boxes (`compiler/geometry.py`, as in ADR 0007), so the graph
   sees what would be built. **Solid** = built with CanCollide on. Decor is built CanCollide off,
   so here it neither blocks nor supports (ADR 0007's conservative rules already reject Decor
   that could act as a floor or a wall on the walkway). Triggers and the lever's touch zone are
   not solid.
2. **Grid:** square columns of `cell` studs over the room box, centres at half a cell from
   `bounds.min`. In each column the solid intervals that contain the column centre are merged;
   the top of each merged block is a **surface**. Columns holding only KillFloor are dropped.
3. **Node:** a surface where the body fits: nothing solid in the column for `body_height` above
   it, and no solid within `body_radius` of the centre reaching into the band from `step_up` to
   `body_height` above the surface. A surface whose top part is a KillFloor, or a body that
   would touch a KillFloor, is a **hazard**: never a node, never a landing. The support rule is
   centre-over-surface.
4. **Walk edges** (both modes): to each of the 4 neighbouring columns, landing on the highest
   surface at most `step_up` above the current one. Higher than that is a wall; lower is a
   **drop** (straight down; Roblox has no fall damage by default). Landing on a hazard or a
   surface where the body does not fit gives no edge.
5. **Jump edges** (`jump` mode only, labelled **derived**): from a *rim* node (one with a
   4-neighbour it cannot walk to at about its own level) to any unreached node at most
   `jump_rise` higher and within `reach(dh)` horizontally, centre to centre. The body follows
   the ballistic arc (take-off speed from `jump_height` and `gravity`, horizontal speed at most
   `walk_speed`, air control assumed), sampled at most 0.5 studs apart horizontally and 2 studs
   vertically; any sample where the body cylinder hits a solid (or touches a KillFloor) kills
   the edge.
6. **Gate precondition:** a gate is solid (closed) until a reachable node's body touches the
   touch zone of the lever its `opens_on` names; then it is open for good (the lever latches,
   WS-03 template) and the search reruns, to a fixpoint. So "lever reachable from spawn ⇒ gate
   passable". The lever's ProximityPrompt (8 studs) is not modelled; P0 uses the touch zone.
7. **Start and goal:** start is the spawn's top surface at its centre column; the goal is
   reached at any node whose body overlaps the `GOAL_TOUCHED` trigger volume.

## Output (`nav.graph(scene)`, the validator's `graph` field)

```jsonc
{"model": "worldsmith.nav/0.1",
 "note": "an approximation under ADR 0008 ...; never a proof ...; actual runs decide ...",
 "params": {"cell": 1.0, "body_height": 5.19, ...},
 "walk": {"result": "CONNECTED|DISCONNECTED", "basis": "approximation",
          "path": [{"pos": [x, y, z], "on": "<entity id>", "via": "start|walk|drop|jump"}, ...] | null,
          "gates_opened": [...], "gates_closed": [...], "reached_on": [...], "nodes": 710,
          "closest": null | {"pos": [...], "on": "...", "distance_to_goal": d}},
 "jump": {..., "basis": "derived"}}
```

`path` is one deterministic path (breadth-first, not necessarily the shortest), from the spawn to
the first node touching the goal trigger; `closest` (when DISCONNECTED) is the reached node
nearest the goal trigger. `graph` is `null` when the candidate is not judged geometrically
(patch layer stopped it, or it is outside the grammar) or has no single spawn or no
`GOAL_TOUCHED` trigger. The result depends only on the canonical scene (cached by its canonical
JSON); it is identical across processes (test).

## Fixture results (from `tests/test_ws07_nav.py`)

| Scene | walk | jump |
| --- | --- | --- |
| F1-A (6-stud bridge, 10-stud hole) | DISCONNECTED; gate opened; closest node on `bridge_1` at z 41.5 | DISCONNECTED (derived; **not asserted**) |
| F1-A fix (bridge 16) | CONNECTED | CONNECTED |
| F1-E (control) | CONNECTED | CONNECTED |
| F1-E + an 8-stud Wall across the corridor at z 16–17 | DISCONNECTED (lever not reached) | DISCONNECTED |
| F1-A, bridge 17 (the 1-stud join, ADR 0007) | CONNECTED | CONNECTED |
| F1-E, ledge opening sealed by a Wall | DISCONNECTED; gate stays closed; stops at z 30.5 | DISCONNECTED |
| F1-A, bridge 12 (4-stud hole) | DISCONNECTED | CONNECTED, with a jump edge |
| F1-A, bridge 4 (12-stud hole) | DISCONNECTED | DISCONNECTED |

F1-A in jump mode: the hole is 10 studs; the derived level reach is 8.669 centre to centre. M0
measured one jump and nothing about edges, so the test asserts only that the result is
deterministic and labelled `derived`. The walk results for F1-A and its fix agree with the M0 gate
(`docs/M0.md`: F1-A FELL 3/3, the fix SUCCESS 3/3, P0 never jumps), which is consistency, not a
validation of the model.

## Limits (why it is never a physical proof)

- **One measurement.** The body, speed, jump height and gravity come from one character
  measurement (n = 1); avatars, scales and the place's settings can differ.
- **Step-up is assumed** (1 stud). Real Humanoid stepping depends on HipHeight and the climbing
  logic; the real limit may be higher or lower than 1 stud.
- **Body:** a vertical cylinder of radius 2.09 (holds the 4.00 × 1.20 box in any facing). It
  closes openings narrower than 4.18 studs that a character turned sideways might pass. Limbs,
  rotation and collision groups are not modelled.
- **Grid:** 1-stud columns with centre support. Ledges, holes and openings are quantised to the
  grid; a surface narrower than one cell between centres can disappear. Jump reach is centre to
  centre between cell centres, each at least half a cell inside its surface, so the widest level
  hole the model crosses is about one cell less than `jump_reach_flat` (an 8-stud hole after a
  bridge ending on a whole stud is not crossed); a real character can take off and land with its
  centre beyond the edge. The jump mode therefore *under*-estimates reach.
- **Jumps are derived, not measured:** ballistic arcs with full air control at walk speed; no
  coyote time, no momentum loss, no head-bumping slides, no wall-jumps. One level jump agreed
  within 1 %; nothing else was measured.
- **Falls** are straight down (no horizontal drift); no fall damage; nothing about timing.
- **Not modelled:** moving parts, the gate's opening animation (it is open at once), the lever's
  ProximityPrompt, `BRIDGE_CROSSED` and the event order (only "can the body reach the goal
  trigger"), Ramp slopes (a Ramp is treated as its bounding box; F1 adds no Ramps), terrain, and
  anything outside the room box.

## Alternatives considered

- **Surface-to-surface graph** (one node per part top): cheaper, but cannot see a wall standing on
  a platform, so the blocked-corridor case would pass.
- **Navmesh / Roblox PathfindingService:** closer to the engine, but needs Studio; the validator
  must run on CPU, deterministically, without the engine.
- **Make DISCONNECTED a rejection:** SPEC §7 says actual runs decide; a wrong model would then
  reject good patches. Rejected; it is a warning.
- **Measure step-up and jumps first:** better parameters, but needs live runs the M0 gate did not
  include. Left as an open question; the parameters are one block and each change is an ADR.

## Consequences

- `Result.graph` is a dict (or `None`), no longer reserved; `GRAPH_DISCONNECTED_WALK` joins the
  warning codes in `docs/VALIDATOR.md`. Every F1-A-based legit case that keeps the 6-stud bridge
  now also carries that warning (test fixtures updated).
- WS-09 (sampler) and WS-10 (architect) may read `graph` as a hint; WS-11 should store it in the
  bundle next to the run outcome, so the model can be compared with real runs over time.
- Changing a parameter, the body shape, or the step and jump rules is a new ADR.
