# places/

The owner's own Roblox Studio place for Worldsmith's live runs, saved here as
`worldsmith-lab.rbxl` with **File → Save to File**. Place files are git-ignored
(`places/*.rbxl`, and Studio's `*.rbxl.lock`); only this README is committed.

It is a local Baseplate place (PlaceId 0), never published. Worldsmith builds its room under
`Workspace.Worldsmith` (at origin (0, 100, 0), above the Baseplate) and rebuilds it from scene
JSON before every episode, so the saved copy of that folder does not matter.

## Required settings (WS-04 / M0)

| Setting | Value | Why |
| --- | --- | --- |
| `StarterPlayer.LoadCharacterAppearance` | `false` | Default R15 character instead of the owner's avatar, so sizes cannot change with his avatar (owner decision, 9 Nov 2026). |
| `Players.CharacterAutoLoads` | `true` | The P0 controller waits for the auto-loaded character, then resets once at `Worldsmith.spawn`. |
| `StarterPlayer` `StarterCharacter` | absent | The default rig is the measured one. |
| Workspace | Baseplate template: `Baseplate`, one `SpawnLocation` at (0, 0.5, 0) | The character measurement walks on the Baseplate. |
| `Workspace` attribute `ws_place` | `"worldsmith-lab"` | Set once by the owner (ADR 0011): the preflight refuses any place without it. `workspace:SetAttribute("ws_place", "worldsmith-lab")` in Edit mode, then File → Save to File (`scripts/mark_place.py` prints the `run_code` payload). |

Set by `run_code` in Edit mode on 9 Nov 2026 (`bundles/m0/character/place_settings.txt`).

## Character with these settings (measured once, n = 1)

`bundles/m0/character/measure.json` (script `scripts/luau/measure_character.luau`, server-side
reads in one play session): R15, 16 parts, no accessories or clothing; HumanoidRootPart
2 × 2 × 1; HipHeight 2; bounding box 4.00 × 5.19 × 1.20; HRP centre 3.00 above the floor when
standing on the Baseplate; WalkSpeed 16; `UseJumpPower` false, JumpHeight 7.2; MaxSlopeAngle 89;
Gravity 196.2; HRP network owner = the player's client. The P0 controller never jumps; the jump
numbers there are for reference only.

## Other things in the place that print to the console

Two plugins print into the same logs (`[UI Workflow]`, and `[robloxstudio-mcp]` failing to reach
`localhost:58741`), plus an engine warning about `rbxassetid://203785492`. The nonce rule ignores
them. `run_script_in_play_mode` adds a temporary Script to `ServerScriptService` while it runs.

## Only this place, only one driver (ADR 0011)

Worldsmith runs only in this place, and nothing else drives Studio meanwhile (no Autopilot bench
run, no other plugin's play session). The preflight before every build checks the marker and the
settings above, exactly one SpawnLocation outside `Workspace.Worldsmith` (the Baseplate's), and no
scripts in ServerScriptService, StarterPlayerScripts, StarterCharacterScripts or StarterGui.
