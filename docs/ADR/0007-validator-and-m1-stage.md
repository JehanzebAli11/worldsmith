# ADR 0007: The WS-06 validator's rules, and the M1 patch stage

- **Status:** accepted for WS-06 (9 Nov 2026); the owner confirms in the WS-06 HANDOFF open
  questions. SPEC §6 requires an ADR for each widening of the patch ops; this is the first.
- **Context:** SPEC §7 items 1–4 (schema and bounds, protection, difficulty, structure) need
  exact, geometric, deterministic rules, and §16's abuse suite must be rejected while legit
  patches pass. The SPEC leaves the detour measure, the allowed bridge/platform join and what
  counts as a floor undefined. WS-05 left M0 as the only stage. Code:
  `validator/validate.py`, `validator/patch.py`; codes: `docs/VALIDATOR.md`.

## 1. The M1 stage (patch ops widened)

`worldsmith.validator.patch.STAGES["M1"]`, exactly:

| Op | Target | Bound |
| --- | --- | --- |
| `set` | `bridge_1.length`, `bridge_1.width`, `bridge_1.lateral_offset` (a Bridge) | `Bridge.*` in `bounds.yaml`; a number |
| `move` | `lever_1`, `gate_1` (a Lever, a Gate): sets `pos` | `Lever.pos`, `Gate.pos` per component |
| `add` | a new **Wall** or **Decor** (Decor `palette` is the closed enum `crate`, `pillar`, `lamp`, `plant`, `banner`) | the scene schema and `bounds.yaml` after apply |

**M0 is unchanged** (`set bridge_1.length` only, `worldsmith.patch/0.1` only; every WS-05 test
still passes, with the same error layers). Not widened, on purpose:

- `bridge_1.thickness` and `spans`, any Gate `size` or `opens_on`, any Lever `on` or `controls`:
  re-linking the gate is the later "gate not linked" family and needs its own ADR.
- **"support" fields** (SPEC §6): the closed type set has no support entity, so there is nothing
  to whitelist; adding one is a schema change (open question in the HANDOFF).
- `remove` (never), Ramp/Platform/Bridge additions (never in F1), Lever/Gate/Trigger/Spawn adds.

Rules name the F1 ids (`bridge_1`, `lever_1`, `gate_1`), like M0, so `check_patch` stays
scene-free; `apply_patch` still checks the target's type.

**Patch grammar `worldsmith.patch/0.2`** (`grammar/patch_schema_0.2.json`): ops `set` (value: a
number, an ID or `[x, y, z]`), `move` (`pos`), `add` (`entity`, any object with `id` and `type`;
its content is checked by the scene schema once applied) and `remove` (`id`). Closed objects,
1–3 ops. **What a patch may say is wider than what a stage applies**: the extra forms exist so the
validator can name what a forbidden op tried (for example `remove gate_1` is reported as
`GATE_LEVER_UNLINKED`, not just "unknown op"). `STAGE_SCHEMAS`: M0 accepts 0.1; M1 accepts 0.1
and 0.2. `worldsmith.patch/0.1` and its schema file are unchanged.

## 2. How the validator judges a candidate

- **Input:** the base scene (must be valid; otherwise `ValueError`, a caller bug) and either a
  patch (object or text) or a whole patched scene, plus the stage.
- **Every op is simulated**, on a copy of the base, whatever the stage allows; the stage verdict
  (`STAGE_OP_FORBIDDEN`) and the scene verdicts are reported together. A patch that is malformed
  (apart from its op count) or stale is judged on the patch layer only.
- **A patched scene** is diffed against the base (`version` ignored: ADR 0006 bookkeeping); each
  changed field, added or removed entity, or changed top-level key counts as one op for the stage
  rules and the 3-op cap.
- **No-op:** an op that sets a field to the value it already has (compared at canonical
  precision, so 6.0004 equals 6), or a moving entity to its own `pos`, is `NOOP_PATCH`, even beside
  a real change: every op of a revision must change something (control-thread default, 9 Nov). A
  patched scene equal to its base is `NOOP_PATCH`.
- **Geometry runs only inside the grammar** (schema, anchored strings and bounds pass), and uses
  the compiler's own part boxes (`compiler/geometry.py`), so the validator judges what would be
  built. Solids are every part built with CanCollide on, **plus Decor**: Decor is built
  non-collidable, but the validator does not rely on a template detail.
- **Touched** entities are those added, or whose built parts differ from the base (a bridge is
  touched when its length changes). Structure checks (penetration, room box, walkway, new floors)
  apply to touched entities; the base fixtures are trusted, and a test proves they have no solid
  overlaps and nothing outside the room. Gap, gate, lever, layout and protection rules apply to
  the whole candidate.

## 3. The geometric rules (exact)

All lengths in studs; `EPS = 0.001` (the canonical precision, ADR 0002). Two boxes **overlap**
when they overlap by more than `EPS` on all three axes; touching faces never count. The F1 frame
comes from the base scene's gap: axis `i` (Z for `+Z`), across axis `j` (X), `from` platform
(the corridor), `to` platform; the **walking surface** is the `from` platform's top (y 0 in F1).

1. **Walkway** (for `PATH_BLOCKED`), from the base scene:
   - the **walkway strip**: across, the `from` platform's width (x −5 to 5 in F1); along, from the
     near edge of the spawn platform to the far edge of the goal platform (z 0 to 68); up, from
     the walking surface to **`CLEARANCE` = 6 studs** above it (the M0 character is 5.19 studs
     tall, `bundles/m0/character/measure.json`, n = 1, rounded up);
   - each lever's platform column: that platform's footprint, from its top up 6 studs.
   An added or changed Wall or Decor that overlaps a walkway volume is `PATH_BLOCKED`. Gates,
   levers and the bridge are judged by their own rules instead.
2. **Gap column** (for `NEW_FLOOR`): along, strictly between the gap's two faces (z 36 to 52 in
   F1); across, the whole room; up, from the room floor to 6 studs above the walking surface. An
   added or changed Wall or Decor that overlaps it is a new floor: nothing new may sit in the gap.
3. **Floor-like box** (for `NEW_FLOOR`): an added or changed Wall or Decor whose top is within
   **`FLOOR_BAND` = 1 stud** of the walking surface and whose top face is at least
   **`FLOOR_MIN_SPAN` = 1 stud** on both horizontal axes, unless its footprint lies inside one
   base Platform or base Bridge footprint (then it adds no standable area). Size, height and
   position decide; ids and palettes never do.
4. **Added floors:** any added Platform, Ramp or Bridge is `NEW_FLOOR` (SPEC §6: never in F1).
5. **Allowed bridge join** (for `PENETRATION`): a Bridge may overlap **only its gap's `to`
   platform**, and only by at most **`JOIN_MAX` = 1 stud** along the gap axis (so length 17 on
   the 16-stud gap is fine; 19 is not). It is anchored at the `from` face by construction
   (ADR 0002) and never overlaps the `from` platform. Every other solid overlap that involves a
   touched entity is `PENETRATION`.
6. **Room box** (`OUTSIDE_ROOM`): every built part of a touched entity, solid or not (trigger
   volumes and the lever's touch zone included), lies inside `bounds` (±`EPS`).
7. **Lever detour** (`DETOUR_TOO_SHORT`): the horizontal distance across the path from the
   lever's base position to the path centreline, `|lever.pos[j] − from.pos[j]|` (15 in F1),
   must be at least the **base** scene's `min_lever_detour` (10). It is a lateral detour, a
   deterministic stand-in for walking distance; WS-07's graph does not replace it.
8. **Lever support** (`UNSUPPORTED`): a lever's base (`LEVER_BASE`, 2 × 2 footprint) stands
   on its `on` platform's top (±`EPS`) and inside its footprint.
9. **Gate slot** (`GATE_BYPASSED`): for each base gate, the candidate gate's box covers the base
   gate's extent across the path and in height, and still overlaps the base gate's extent
   along the path. A gate moved off its slot, lifted, or shrunk is permanently open.
   `GATE_LEVER_UNLINKED`: the base gate and its lever both exist and point at each other.
10. **Gap** (`GAP_CHANGED`, `GAP_BELOW_MIN`): the gap entity is unchanged, and the distance
    between its platforms' faces equals the base distance and `width`; both are at least the
    **base** `min_gap`. Lowering `min_gap` itself is `CONSTRAINTS_CHANGED`.
11. **Layout** (`LAYOUT_BROKEN`): exactly one Spawn, one Goal and one Gap; the goal is on the
    gap's `to` platform; the spawn's platform lies wholly on the near side of the gap's `from`
    face.
12. **Teleport** (`TELEPORT`): a second spawn point, an added Spawn, or an added entity whose
    type is one of the exact names in `TELEPORT_TYPES`.
13. **Physics** (`PHYSICS_CHANGED`): `constraints.player_physics` is not `locked`, or an op
    field or a scene key is one of the exact names in `PHYSICS_NAMES` (WalkSpeed, JumpPower,
    JumpHeight, UseJumpPower, Gravity, HipHeight, MaxSlopeAngle, AutoJumpEnabled; compared
    lower-case without underscores). The grammar has no such field, so these are rejected by the
    schema anyway; the name list only says what was tried.
14. **Payload** (`CODE_PAYLOAD`): any key or string holding a character outside
    `[A-Za-z0-9_./+-]` (Luau, URLs with `:`, quotes, brackets, spaces, newlines).

The constants are design choices, not measurements (except where a file is cited), and are
`[ESTIMATE]` like `bounds.yaml`; changing one is a new ADR. The step height and jump reach were
not measured in M0 (P0 never jumps), so `FLOOR_BAND` is deliberately strict.

## Alternatives considered

- **Judge only what the stage allows** (stop at `STAGE_OP_FORBIDDEN`): simpler, but the safety
  layer (ROADMAP §4.4) could then only count "forbidden op", not *which* cheat was tried.
- **Path-length detour from a walk graph:** more faithful, but it needs WS-07's approximation;
  the lateral distance is exact and cannot be gamed by moving the lever along the ledge.
- **Lock the gate geometry outright:** simplest, but M1's "lever and gate fields" would be empty;
  the slot rule allows harmless nudges and rejects every bypass in the suite.
- **Trust Decor's CanCollide off:** fewer rejections, but one template change would turn a
  Decor slab into a real floor.
- **Name heuristics** (ids like `floor_1`): rejected; ids are free and prove nothing.

## Consequences

- The abuse suite (`tests/abuse/reject/`, 69 cases) and the legit cases (`tests/abuse/accept/`,
  13) are fixtures; every code is produced by at least one case (test).
- Whatever `apply_patch` rejects, the validator rejects; whatever the validator accepts,
  `apply_patch` applies to the same scene hash (test over every patch case).
- WS-07 fills `graph`; WS-09 (random sampler) and WS-10 (architect) should call
  `validate(...)` and count a rejection as a used revision.
