# F1-A: bridge too short (the M0 fixture)

Hand-authored F1 room (SPEC §5). These files are fixtures, not a result: nothing here has been
built in Studio or played.

- `scene.json`: **known defect, by construction.** `bridge_1.length` is 6 studs and `gap_1.width`
  is 16, so the bridge stops 10 studs short of `plat_b`. The defect was put there on purpose; any
  `FELL` near the bridge in this room is expected, and a later diagnosis of it must say so.
- `fix.json`: the paired hand-authored fix (baseline B1). It is identical to `scene.json` except
  `bridge_1.length` = 16 (SPEC §6); `tests/test_scene_fixtures.py` proves that one-field diff.
- `patches/m0_fix.json`: the same fix as a `worldsmith.patch/0.1` patch on `scene.json` (WS-05).
  Applied with `worldsmith.validator.patch.apply_patch(..., stage="M0")` it gives `fix.json` with
  `version` 2 (ADR 0006); `tests/test_ws05_patch.py` proves it.

Layout (studs, Y up, path along +Z; values are design choices, `[ESTIMATE]` until M0 measures the
character): spawn on `plat_a` → `corridor` with walls → side opening to `ledge_1`, where `lever_1`
stands 15 studs left of the corridor centreline (the detour) → `gate_1` across the corridor end,
open while `lever_1` is active → `bridge_1` across `gap_1` → `plat_b` with `goal`. `kill_floor`
lies 29 studs below the walking surface, under the whole room. Hashes: `../HASHES.md`.
