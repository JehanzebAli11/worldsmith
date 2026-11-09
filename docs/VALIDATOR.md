# The validator: API and violation codes

The validator (`worldsmith.validator.validate`, WS-06) judges a proposed scene change against the
current scene, independently of whoever proposed it (SPEC §7). Its rules are in
[ADR 0007](ADR/0007-validator-and-m1-stage.md); the patch stages are in `validator/patch.py`
(`STAGES`). Every rejection counts against the episode's revision budget (SPEC §7); the counting
belongs to the orchestrator (WS-11).

## API

```python
from worldsmith.validator.validate import validate

result = validate(base_scene, candidate, stage="M0")   # or "M1"
result.ok          # True only when there are no violations
result.codes       # {"PENETRATION", ...}
result.to_dict()   # {"ok", "violations": [{"code", "message", "ref"}], "warnings": [...], "graph": None}
result.scene       # the candidate scene that was judged (None if the patch layer stopped it)
```

- `candidate` is a patch object (`worldsmith.patch/0.1` or `0.2`), patch text, or a whole patched
  scene (`worldsmith.scene/0.1`).
- `base_scene` must be a valid scene and `stage` a known stage; otherwise `ValueError` (a caller
  bug, not a verdict).
- `graph` is WS-07's navigation approximation ([ADR 0008](ADR/0008-navigation-approximation.md),
  `worldsmith.validator.nav`): `{"model", "note", "params", "walk", "jump"}`, where each mode has
  `result` (`CONNECTED` / `DISCONNECTED`), `basis` (`approximation` for walk, `derived` for
  jump), the `path` node list (or `null`), `gates_opened`, `gates_closed`, `reached_on`, `nodes`
  and `closest`. It is **an approximation, never a proof**: actual runs decide (SPEC §7). It never
  changes `ok` and never adds a violation; a DISCONNECTED walk graph adds the warning
  `GRAPH_DISCONNECTED_WALK`. `graph` is `None` when the candidate is not judged geometrically
  (stopped at the patch layer, or outside the grammar) or has no single spawn or no
  `GOAL_TOUCHED` trigger.
- `ref` locates a violation: `ops[i]`, `entity:<id>`, `pair:<a>+<b>`, `scene.<key>`, a patch path,
  or `null`. Messages are for people and may change; **codes are stable**.
- Pure and deterministic: inputs are never mutated, violations are sorted by (code, ref, message)
  and de-duplicated, and the output is identical across processes.

## Stable codes

These codes are an interface: the "does the architect cheat?" layer (ROADMAP §4.4) counts them
per architect. Adding a code is fine; renaming one, removing one or changing its meaning is a new
ADR. A test checks that this table and `CODES` / `WARNING_CODES` list the same codes, and that
every violation code is produced by at least one case in `tests/abuse/reject/`.

| Code | SPEC §7 item | Meaning | Example case |
| --- | --- | --- | --- |
| `PATCH_MALFORMED` | patch | The patch is not a strict JSON object (bad JSON, duplicate keys, NaN). | `not_json` |
| `SCHEMA_INVALID` | 1 | The patch or the patched scene breaks its JSON Schema or the anchored ID check (unknown keys, unknown types or ops, wrong value types). | `teleport_pad` |
| `CODE_PAYLOAD` | 1 | A key or string has a character outside `[A-Za-z0-9_./+-]`: Luau, a URL, quotes, spaces, newlines. | `luau_in_id` |
| `OUT_OF_BOUNDS` | 1 | A number is outside its `grammar/bounds.yaml` range. | `bridge_too_long_value` |
| `TOO_MANY_OPS` | patch | More than 3 ops; for a patched scene, more than 3 changes. | `four_ops` |
| `STAGE_OP_FORBIDDEN` | patch | An op (or a change) the stage does not allow, or a patch version the stage does not accept. | `m0_bridge_width` |
| `OP_CONFLICT` | patch | Two ops on one target. | `two_ops_one_target` |
| `STALE_BASE` | patch | `base_hash` is not the current scene's canonical hash. | `stale_base` |
| `BAD_TARGET` | patch | An op names an entity or field that does not exist, or adds an id that exists. | `walk_speed_m0` |
| `NOOP_PATCH` | patch | An op sets something to the value it already has, or the patched scene equals the base. | `noop_m0` |
| `PROTECTED_CHANGED` | 2 | A protected entity, the room box, the protected list, or the scene identity (`schema`, `scene_id`, `family`, `units`, `up`) changed or was removed. | `remove_spawn` |
| `CONSTRAINTS_CHANGED` | 2 | `min_gap` or `min_lever_detour` changed. | `lower_detour_rule` |
| `REQUIRED_EVENTS_CHANGED` | 2 | `required_events` or the set of event triggers changed (success without `BRIDGE_CROSSED`). | `drop_bridge_crossed` |
| `PHYSICS_CHANGED` | 2 | `player_physics` is not `locked`, or an op or key names a player physics property. | `physics_unlocked` |
| `TELEPORT` | 2 | A second spawn point, an added Spawn, or an added teleport-type entity. | `second_spawn` |
| `NEW_FLOOR` | 2 | An added Platform, Ramp or Bridge; an added or changed solid over the gap; a floor-like box outside every base floor. | `decor_as_floor` |
| `GAP_CHANGED` | 3 | The gap entity, or the distance between its platforms, changed. | `plat_b_closer` |
| `GAP_BELOW_MIN` | 3 | The gap is narrower than the base `min_gap`. | `shrink_gap_m0` |
| `GATE_LEVER_UNLINKED` | 3 | A gate is gone, or it and its lever no longer point at each other. | `gate_opens_on_missing_lever` |
| `GATE_BYPASSED` | 3 | A gate no longer fills its slot (moved off, lifted, shrunk): permanently open. | `gate_moved_away` |
| `DETOUR_TOO_SHORT` | 3 | A lever is closer to the path centreline than the base `min_lever_detour`. | `lever_at_corridor_edge` |
| `PATH_BLOCKED` | 3 | An added or changed Wall or Decor intrudes into the walkway (strip or lever ledge). | `wall_blocks_corridor` |
| `PENETRATION` | 4 | Solids overlap, beyond the allowed bridge join (1 stud into the far platform). | `bridge_beyond_join` |
| `DANGLING_REF` | 4 | A reference names no entity. | `remove_gate` |
| `REF_INVALID` | 4 | A reference names the wrong type; an id is duplicated or reserved; a trigger is mis-hosted. | `gate_opens_on_bridge` |
| `OUTSIDE_ROOM` | 4 | A built part of an added or changed entity leaves the room box. | `bridge_out_of_room` |
| `UNSUPPORTED` | 4 | A lever does not stand on its platform. | `lever_floating` |
| `LAYOUT_BROKEN` | 4 | Not exactly one spawn, goal and gap, or the spawn and goal are not on opposite sides of the gap. | `goal_behind_spawn` |

Warnings never reject a patch:

| Code | Meaning |
| --- | --- |
| `BRIDGE_SHORT` | A bridge ends before its gap's far platform (F1-A is built that way on purpose). |
| `GRAPH_DISCONNECTED_WALK` | The walk-mode graph (no jumps: what P0 can do) finds no path from spawn to goal under the ADR 0008 approximation (F1-A, with its 10-stud hole, has it). Information only; actual runs decide. |

## Order and stopping

1. **Patch layer** (patch input): parse → payload scan → op count → shape → stage, conflict and
   bounds → base hash. A malformed patch (apart from its op count) or a stale one stops here.
2. **Simulation:** every op is applied to a copy of the base, whatever the stage allows, so a
   forbidden op is also reported for what it would have done (for example `set gap_1.width 10`
   in M0 gives `STAGE_OP_FORBIDDEN`, `PROTECTED_CHANGED`, `GAP_CHANGED` and `GAP_BELOW_MIN`). A
   patched-scene input instead derives one op per change and checks those against the stage.
3. **Scene checks:** grammar (schema, strings, bounds, references), then protection; then, only
   for a scene inside the grammar, geometry (gap, gate, lever, walkway, new floors, penetration,
   room box, layout).
4. **Navigation** (only for a scene inside the grammar): the walk and jump graphs (ADR 0008) fill
   `graph`; a DISCONNECTED walk graph adds `GRAPH_DISCONNECTED_WALK`. Never a violation.

One case can carry several codes. The abuse suite asserts that each case carries at least its
expected codes, and exactly those where the case says `"exact": true`.

## Not here

- Classifying runs (`SUCCESS` needs `BRIDGE_CROSSED` then `GOAL_TOUCHED`): `runner/classify.py`
  (WS-04 tests `test_goal_without_bridge_is_not_success` and `test_goal_before_bridge_is_not_success`);
  the validator covers the scene side of "success without `BRIDGE_CROSSED`".
- Counting rejected revisions against the budget: the orchestrator (WS-11).
