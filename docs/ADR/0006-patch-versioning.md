# ADR 0006: Patch apply bumps `version` by 1; patches live in `validator/patch.py`

- **Status:** accepted for WS-05 (9 Nov 2026); listed in the WS-05 HANDOFF open questions for the
  owner to confirm.
- **Answers:** WS-02 HANDOFF open question "`fix.json` keeps `version` 1 … WS-05's patched scenes
  may want a version bump".
- **Context:** SPEC §6 says a patch is applied only if `base_hash` matches the current scene, and
  ADR 0002 puts `version` inside the canonical hash. The hand-authored `scenes/F1-A/fix.json`
  keeps `version` 1 (SPEC: it differs from F1-A only in `bridge_1.length`), so only its hash
  tells it apart from F1-A. WS-05 needs a rule for what an automated patch does to `version`.

## Decision

1. **`apply_patch` sets `version` to the base scene's `version` plus 1** (bumps it by 1, once per
   applied patch, whatever the number of ops). `scene_id` is unchanged. `version` is the
   number of patches applied since the hand-authored fixture, plus 1.
2. **The canonical hash is recomputed** over the whole patched scene, `version` included (ADR 0002
   is unchanged). So a patched scene never shares a hash with its base, even when every op sets a
   field to the value it already had.
3. **Consequence for staleness:** a patch names its base by `base_hash`. Applying it moves the
   scene to a new hash, so **the same patch applied a second time is stale** (`StalePatch`). A
   replayed or duplicated architect output cannot be applied twice.
4. **The M0 fix, reproduced:** `scenes/F1-A/patches/m0_fix.json`
   (`{"op":"set","id":"bridge_1","field":"length","value":16}` on F1-A,
   `base_hash` `92f866098bfb850cf4c464179887a862f305c143c8e0b38efdc7978493cddb1d`) yields a scene
   equal to `scenes/F1-A/fix.json` except `version` (1 → 2). Its canonical hash is
   `297b23c884e196a80f9a19aa32d006768e1ace84cfbd4bd1e932981fdabf0535`; `fix.json`'s is
   `49371fa767666577d2f1c30571231069af8eeaeb2eace76e6e832f38c281a396`. Setting `version` back to
   1 gives exactly the `fix.json` hash (test). The M0 bundles were run on `fix.json`, so their
   scene hash stays `49371fa7…`; a later run on the patched scene will carry `297b23c8…`.
5. **Stored values are canonical:** a patch value is stored at the canonical precision
   (3 decimals, ADR 0002), so the scene object and its canonical form agree (16.0004 → 16). The
   bounds check runs on the raw value first, so a value just past a bound (64.0004) is rejected
   rather than rounded into range.

**Layout.** Code in `validator/patch.py` (`worldsmith.validator.patch`, ADR 0001: no new root
module). The patch JSON Schema sits beside the scene schema in `grammar/patch_schema.json` (the
grammar holds the language: scene schema, patch schema, `bounds.yaml`) and ships as package data.
Which ops a stage allows is code (`STAGES`), not data, so it is reviewed with the code: **M0** is
`set bridge_1.length` only (and `bridge_1` must be a Bridge). Values are bounded by the
`Bridge.length` entry in `bounds.yaml`. The stage is declared by the caller, never by the patch.
Widening a stage (M1+) is a new ADR (SPEC §6).

**Not here (WS-06):** protection, the locked gap, difficulty, structure, the navigation
approximation and the revision budget. WS-05 only re-runs the WS-02 scene checks (schema,
strings, bounds, references) on the patched scene.

## Alternatives considered

- **Keep `version`** (as `fix.json` does): the patched scene would equal `fix.json` and share its
  hash, which ties M0's evidence to automated output neatly, but a patch that sets a field to its
  current value would leave the hash unchanged, so that patch could be re-applied forever and
  "revision N" would not be visible in the scene.
- **Exclude `version` from the hash:** keeps geometry-identical scenes on one hash, but changes
  ADR 0002 and every recorded hash, and the build stamps `ws_version` on the root anyway.
- **Bump per op:** makes `version` depend on how the architect split its edit; per-patch is the
  unit the revision budget counts.

## Consequences

- Patched scenes are new hashes; bundles and the classifier must key on the hash, not on
  `scene_id`.
- `version` is bounded by `scene.version` in `bounds.yaml` (max 1000); a patch past it is rejected
  (`scene:` layer). Episodes use at most 3 revisions (SPEC §6), so this never binds in practice.
- `fix.json` is unchanged and remains the M0 evidence scene.
