# Episode-1 clip tooling

- `make_cards.py` renders the text cards and caption overlays (1080x1350) with headless Chrome.
  Fonts come from npm `@fontsource-variable/bodoni-moda` and `geist` in a local `node_modules/`.
- `assemble.sh REC_BEFORE REC_AFTER OUT.mp4` trims each recording only at its ends (real speed,
  no cuts inside), lays the overlay on top and joins it with the cards.
  The 11 Nov cut: `CROP=1190:850:96:52 TRIM_BEFORE_START=558.6 TRIM_BEFORE_END=568.4
  TRIM_AFTER_START=619 TRIM_AFTER_END=630.5`, both takes from one screen recording.
- `replay_take.luau` is the on-camera replay that the footage shows, and why it exists.
  The clip's numbers come from logged runs, not from the replay.
