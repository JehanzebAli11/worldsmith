# ADR 0002: F1 scene schema, bounds and canonical hash

- **Status:** accepted (WS-02, 9 Nov 2026); Trigger hosts and regions amended by ADR 0003
  (`BRIDGE_CROSSED` now sits on the far side of the gap).
- **Context:** SPEC §6 asks for a closed scene grammar (`worldsmith.scene/0.1`) with a closed
  entity-type set, hard numeric bounds in `grammar/bounds.yaml`, no code or free-text fields,
  unknown keys rejected, and `canonical_hash` = SHA-256 of canonical JSON with fixed float
  precision. The SPEC excerpt is illustrative and incomplete, so WS-02 fills in the gaps below.
  Files: `grammar/schema.json`, `grammar/bounds.yaml`, `grammar/scene.py`.

## Decisions

**Three layers, one job each.**

| Layer | Where | Checks |
| --- | --- | --- |
| Shape | `grammar/schema.json` (JSON Schema draft 2020-12) | Required fields, closed objects (`additionalProperties: false`; the entity dispatcher uses `unevaluatedProperties: false`), the closed type set, enums, ID pattern, vector length, number vs integer |
| Ranges | `grammar/bounds.yaml`, checked by `scene.check_bounds` | Every number in `[min, max]` for its field; a vector's bound applies per component; room `bounds.min < bounds.max`; a numeric field without an entry is rejected (fail closed) |
| References | `scene.check_references` | IDs unique; `bounds` is reserved; each reference exists and points at the right type; lever and gate point at each other; every `protected` name exists |

The schema carries **no** numeric ranges, so there is one source of truth for them, and a test
proves every numeric schema field has a bound (and no bound is stale). Later layers run only when
the schema passes, so their messages are about a well-formed scene.

**Not here (WS-06/WS-07):** protection against a base scene, the locked gap, `min_gap`,
`min_lever_detour`, solid penetration and designed joins, inside-the-room checks, "exactly one
spawn/goal" structure, F1 patch rules (no Ramp/Platform additions), and the navigation
approximation. WS-02 only records the constraint values that WS-06 enforces.

**IDs.** `^[a-z][a-z0-9_]{0,31}$` for entity IDs, `scene_id` and every reference. That excludes
colons, dots, slashes, spaces, quotes, brackets and upper case, so `game:GetService`, URLs and
`require(...)` cannot appear. JSON Schema's `$` also matches before a trailing newline, so
`scene.check_strings` re-checks every string with an anchored full match. Decor takes a
`palette` enum (`crate`, `pillar`, `lamp`, `plant`, `banner`), never a name, asset or URL.

**Strict JSON.** Duplicate keys, `NaN` and `Infinity` (and overflowing literals) are rejected at
parse time; a scene must be a JSON object.

**Entity fields** (all required; there are no optional fields, so absent-vs-default never changes
a hash). Positions are box centres in studs, Y up; the path runs along `Gap.axis`.

| Type | Fields |
| --- | --- |
| Spawn | `on` (Platform; placed at its top centre) |
| Platform, Wall, KillFloor | `pos`, `size` |
| Gap | `from`, `to` (Platforms), `axis` (`+X/-X/+Z/-Z`), `width`; not built |
| Bridge | `spans` (Gap), `length`, `width`, `thickness`, `lateral_offset`; anchored at the gap's `from` edge, top flush with the `from` platform |
| Ramp | `pos`, `size`, `rises_towards` (axis) |
| Lever | `on` (Platform), `pos` (base), `controls` (Gate) |
| Gate | `pos`, `size`, `opens_on` (Lever); open while the lever is active |
| Goal | `on` (Platform; top centre), `size` |
| Trigger | `event` (`BRIDGE_CROSSED`, `GOAL_TOUCHED`), `on` (Bridge or Goal), `region` (`far_half`, `whole`), `height` |
| Decor | `palette`, `pos`, `size` |

Triggers are **relative to their host**, so a bridge-length patch moves `BRIDGE_CROSSED` with the
bridge and the M0 fix still differs from F1-A in exactly one field. The other events
(`LEVER_ACTIVATED`, `GATE_OPENED`, `KILL_FLOOR_TOUCHED`) come from the Lever, Gate and KillFloor
templates in WS-03.

**Room bounds and the corridor.** The closed type set has no Bounds or Corridor type. The room box
is a required top-level `bounds` object (`min`, `max`); `protected` may name it by the reserved
word `bounds`. The corridor is a Platform (`corridor`), and `gap_1` runs from `corridor` to
`plat_b` (the SPEC excerpt's `from: plat_a` is illustrative). All four F1 platforms, the corridor
walls and both triggers are protected in the fixtures.

**Canonical JSON.**
- Numbers are rounded to **3 decimals** (0.001 stud, about 0.3 mm at the kit's 0.28 m/stud
  convention), written in fixed point with trailing zeros and a trailing `.` removed, so `7`,
  `7.0`, `7.000` and `7.0000001` are all `7`, and `-0` / `-0.0001` become `0`. No exponents
  (bounds keep values small). Rounding is Python's correctly rounded `f"{x:.3f}"`.
- Keys sorted, no whitespace, `separators=(",", ":")`, ASCII.
- `entities` sorted by `id`, `protected` sorted: their order carries no meaning, so reordering
  them does not change the hash. `required_events` keeps its order (it states the event order).
- `canonical_hash` = SHA-256 hex of the canonical JSON in UTF-8. It covers the whole scene,
  including `scene_id` and `version`.
- `normalize_scene` returns the scene exactly as its canonical JSON parses back, so
  load → canonical → load round-trips to equal objects.

**Bounds values** are design choices labelled `[ESTIMATE] until M0 measures the character`. They
are revised by a later ADR once M0 has measured step height, jump reach and the capsule.

## Alternatives considered

- **Ranges inside the JSON Schema** (`minimum`/`maximum`): one file, but SPEC §6 names
  `bounds.yaml`, and a second copy of every range would drift.
- **`oneOf` over entity types:** valid, but its errors are "not valid under any of the given
  schemas"; `if`/`then` dispatch reports the actual unknown key or bad field.
- **RFC 8785 (JCS) canonical JSON:** exact float round-trip, but no fixed precision, so `7` and
  `7.0000001` would hash differently, which SPEC §6 rules out.
- **Hash the file bytes:** breaks on whitespace and key order.

## Consequences

- A new numeric field needs a `bounds.yaml` entry or every scene using it is rejected.
- A new entity type, field or enum value is a schema version change and an ADR.
- Any fixture change needs `python scripts/scene_hashes.py`; the tests fail until
  `scenes/HASHES.md` matches.
- WS-03's compiler must place entities by the conventions above (top-centre placement, bridge
  anchoring, host-relative triggers).
