# ADR 0003: BRIDGE_CROSSED is anchored to the far side of the gap

- **Status:** accepted (owner decision, 9 Nov 2026; implemented in WS-03)
- **Amends:** ADR 0002 (Trigger fields) and SPEC §5's wording "a trigger volume on the bridge's
  far half".

## Context

WS-02 followed SPEC §5 and put `trig_bridge` on `bridge_1`, region `far_half`. Triggers are
relative to their host, so the volume moved with the bridge. On F1-A's 6-stud bridge the far
half is studs 3 to 6 of the bridge, which a walking player enters before falling, so
`BRIDGE_CROSSED` could fire without a crossing (HANDOFF, WS-02 open question). SUCCESS still
needed `GOAL_TOUCHED` after it, so it was not a false success, but the event name misled and the
event's meaning depended on the bridge length, which is exactly the field the architect patches.

## Decision

`BRIDGE_CROSSED` is anchored to the **far side of `gap_1`**: the first **2 studs** of the gap's
`to` platform (`plat_b`), so it fires only on a real crossing, whatever the bridge length.

- Schema: `Trigger.on` may name a **Gap** or the **Goal** (no longer a Bridge). `Trigger.region`
  is `landing` or `whole` (`far_half` is removed).
- `grammar/scene.py` enforces one host type and region per event (`TRIGGER_PAIRS`):
  `BRIDGE_CROSSED` → Gap + `landing`; `GOAL_TOUCHED` → Goal + `whole`. Anything else is a `ref:`
  error.
- Geometry (compiler, `compiler/geometry.py`): `landing` is the box on top of the gap's `to`
  platform that starts at its gap-facing edge and runs `LANDING_DEPTH = 2` studs along the gap
  axis, across the platform's full width, from the platform top up by `height`. For F1 (axis `+Z`,
  `plat_b` z 52 to 68, 16 wide) that is x −8 to 8, z 52 to 54, y 0 to 6.
- The 2-stud depth is a compiler constant, not a scene field: it is part of the locked event
  definition, not something a patch may tune. Changing it is a new ADR.
- Fixtures: `trig_bridge` is `{"on": "gap_1", "region": "landing"}` in F1-A, its fix and F1-E.
  `scenes/HASHES.md` was regenerated with `scripts/scene_hashes.py`. `fix.json` still differs from
  F1-A only in `bridge_1.length` (test).

## Consequences

- A bridge-length patch no longer moves the trigger; `trig_bridge` and `gap_1` are both protected,
  so the event's meaning cannot be patched away.
- A player who jumps the remaining gap without the bridge also fires it. That is still a real
  crossing; whether the bridge was used is a support/contact question for the validator and the
  classifier (WS-06, WS-08), as SPEC §5 already says.
- The trigger touches `plat_b`'s top only; a body falling short of `plat_b` stays below it and
  does not fire it.
- The SPEC text is not edited (owner's document); this ADR records the deviation.
