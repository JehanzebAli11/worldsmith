# F1-B: bridge built beside the path (WS-14)

Hand-authored F1 room (SPEC §5), a broken fixture for the M1 stage. These files are fixtures,
not a result: nothing here has been built in Studio or played. Design, route reasoning and the
offline hardness test: `docs/ADR/0013-harder-f1-fixtures.md` (ADR 0013).

- `scene.json`: **known defect, by construction.** F1-A's room with a full-length bridge
  (`bridge_1.length` 16 = `gap_1.width`) that is shifted 10 studs off the corridor centreline
  (`bridge_1.lateral_offset` 10): it runs at x 7 to 13, the corridor's walkable end is x -5 to 5,
  so nothing joins the corridor to the bridge. Everything else is F1-A's (same platforms, gap,
  lever, gate, walls, kill floor, protected list); only `scene_id` (`f1_b`) and the two bridge
  fields differ.
- `patches/m1_fix.json`: the paired hand fix (baseline B1), a `worldsmith.patch/0.2` patch with
  one op, `set bridge_1.lateral_offset 0`. The M1 stage accepts it; it is not a bridge-length fix.
- `fix.json`: the scene that patch produces (`version` 2, ADR 0006). It is F1-A's `fix.json` room
  apart from `scene_id` and `version` (tested).

Expected with the frozen p0.v3 route (prediction, ADR 0013; the live gate decides): the player
walks x = 0 off the corridor end at z 36 and falls into the gap, `FELL` in the gap_1 area, cause
UNCLASSIFIED (the classifier's only hypothesis needs a bridge shorter than the gap). With the fix,
`SUCCESS`. Hashes: `../HASHES.md`.
