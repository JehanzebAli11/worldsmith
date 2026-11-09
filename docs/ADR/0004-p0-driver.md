# ADR 0004: How the P0 player drives the character (route a vs route b)

- **Status:** proposed (WS-03, 9 Nov 2026). Recommendation below; the owner decides. Nothing
  here is an M0 result: each probe ran **once** on one place, as a feasibility check.
- **Evidence:** `bundles/ws03/` (raw tool outputs, `manifest.json` with SHA-256 of each file).

## Naming

Our labels follow the WS-03 brief. **Route (a)**: a server script driving the character with
`Humanoid:MoveTo`, sent through the Studio MCP that is connected now (the open-source
`studio-rust-mcp-server` plugin). **Route (b)**: Autopilot's `roblox-mcp` engine, which talks to
**Studio's built-in MCP server** (`StudioMCP`). Autopilot's own docs call that connector
"Route A" and a native Studio plugin "Route B"; that is a different split.

## What each route can call

Tools of the MCP connected in this session (from the session's tool list and the plugin source,
`Roblox/studio-rust-mcp-server` @ `b0b6474`): `run_code`, `insert_model`, `get_console_output`,
`start_stop_play(mode)`, `run_script_in_play_mode(code, mode, timeout)`, `get_studio_mode`.
No keyboard or mouse input, no screen capture, no code execution in the Client data model.

Tools Autopilot's `roblox-mcp` uses (Autopilot `docs/roblox-mcp.md` on `staging` @ `498252b`,
read 9 Nov 2026; its `docs/studio_tools.txt` lists the built-in server's tools):

| Autopilot needs | Built-in Studio MCP (route b) | Connected MCP (route a) |
| --- | --- | --- |
| `execute_luau(code, datamodel_type)` in Client or Server during play | yes | no: `run_code` runs in the **Edit** data model even during play (measured below) |
| `user_keyboard_input`, `user_mouse_input` | yes | no |
| `character_navigation` | yes | no |
| `screen_capture` (JPEG stills) | yes | no |
| `start_stop_play(is_start)` | yes | yes, as `start_stop_play(mode)` |
| `get_console_output` (Output window, incl. play) | yes, per Autopilot's doc | partial: Edit data model only, 10 000-char buffer (see below) |
| `get_studio_state`, `list_roblox_studios` | yes | `get_studio_mode` only |
| `script_read`, `script_grep`, `search_game_tree`, `inspect_instance` | yes | no (only `run_code` reads) |
| run one server script for a whole play session | not needed | `run_script_in_play_mode` (auto-stops play) |

## What was measured (route a), n = 1 each

Setup: the owner's unsaved local place (`Place1`, PlaceId 0, Baseplate template), Edit mode.
Rooms built with the WS-03 compiler at origin (0, 100, 0) and armed with a fresh nonce. The
probe controller `bundles/ws03/p0_probe_controller.luau` (SHA-256 `5ac0b859…9239c`) was sent
unchanged both times: reset by `player.RespawnLocation = Worldsmith.spawn` + `LoadCharacter()`,
then nine `Humanoid:MoveTo` waypoints in the scene frame (corridor → lever zone → back → gate →
bridge → landing → goal), 10 s per waypoint, 45 s per attempt, stop on `Humanoid.Died`.

| Scene (hash) | Events printed with the run's nonce, in order | Other |
| --- | --- | --- |
| F1-E (`f8a8e654…`) | `LEVER_ACTIVATED`, `GATE_OPENED`, `BRIDGE_CROSSED`, `GOAL_TOUCHED` | all 9 waypoints reached; goal waypoint at t = 6.93 s; server run 12.5 s |
| F1-A (`92f86609…`) | `LEVER_ACTIVATED`, `GATE_OPENED`, `KILL_FLOOR_TOUCHED` at pos (0.00, −12.44, 49.20) | died after waypoint 7 (bridge); no `BRIDGE_CROSSED`, no `GOAL_TOUCHED`; server run 11.5 s |

The fall position's z (49.20) lies inside `gap_1` (z 36 to 52), past the 6-stud bridge's end
(z 42). `worldsmith.runner.console.parse_events` recovers exactly these events from the raw logs
and ignores the other plugins' lines and the other run's nonce.

Findings that shape WS-04:

1. **No live polling of play events here.** `run_code` during play reports `IsEdit = true`,
   `IsRunning = false`, 0 players: it runs in the Edit data model. The plugin's
   `get_console_output` buffer listens to the Edit data model's `LogService`, is reset when play
   starts, and keeps only the first 10 000 characters. Called in parallel with the F1-E run it
   returned one engine warning and none of the `WS|` lines. The server's lines came back only in
   `run_script_in_play_mode`'s result, after play stopped. So under route (a) the attempt is
   bounded inside the script (`T_ATTEMPT`) and by the tool's `timeout`, and events are read
   once at the end. (Whether server prints ever reach that buffer is inferred from the source
   and one observation, not proven.)
2. **Event positions lag.** The character is owned by the client; the server prints the
   replicated `HumanoidRootPart`. `LEVER_ACTIVATED` printed x = −8.74 and −8.06 although the
   lever zone starts at x = −12; `BRIDGE_CROSSED` z = 50.43 for a volume starting at z = 52;
   `GOAL_TOUCHED` z = 56.12 for a pad starting at z = 58. At 16 studs/s that is about 2 to 3.5
   studs. WS-08's "near the bridge" must use the gap footprint or a declared margin, not the
   printed point.
3. `MoveToFinished` reported `reached = true` for the waypoint during which F1-A's character
   died. A controller must watch `Died` itself (the probe does).
4. `run_script_in_play_mode` injects a temporary Script into `ServerScriptService` of the open
   place and removes it after. That is outside `Workspace.Worldsmith`; disclose it.
5. Other plugins print into the same logs (`[UI Workflow]`, a second community plugin
   `[robloxstudio-mcp]` failing to reach `localhost:58741`). The nonce rule handles them.

## Is route (a) a fair P0?

SPEC §9 defines P0 as "deterministic key-hold waypoints through the real `roblox-mcp` input
tools", with the same controller code, hash and inputs before and after the patch, and the claim
"Substrate probe: the patch changes the real engine outcome".

- **Same controller, same hash:** yes. One file, sent byte-for-byte to both scenes; the WS-04
  version should be committed and hashed, and the runner should refuse a changed hash.
- **Real engine outcome:** yes. Real Roblox humanoid locomotion and physics in a Studio play
  session, real `Touched` events from the WS-03 templates, the real kill floor.
- **Deterministic inputs:** the waypoints are fixed. Physics timing is not frame-exact (no pause
  or step), as SPEC §3 already says.
- **Not as the SPEC words it:** the input path is `Humanoid:MoveTo` from a server script, not
  key presses through `user_keyboard_input`, and the controller never jumps. It must be
  disclosed as "server-scripted MoveTo waypoint controller". Because a walking-only controller
  cannot cross a 10-stud hole, F1-A's fall under P0 is expected by construction; that is what a
  substrate probe shows, not a player's skill.
- **Reset:** `LoadCharacter()` at `Worldsmith.spawn` before the clock starts. No teleports,
  WalkSpeed or gravity changes during an attempt (SPEC §7's locked physics); the controller must
  not use `PivotTo` after the reset.

Verdict: route (a) is a fair **P0 substrate probe** if SPEC §9's P0 wording is amended to the
input path above (an owner decision) and the disclosures are kept. It says nothing about an
agent's ability to play.

## What P1 (an outside Claude agent playing via MCP) would need

With the connected MCP an outside agent can only write a server script and run it; it cannot
observe and act during play: no keyboard or mouse input, no screen capture, no reads in the
running Client or Server data models, and no console polling of play output. That is "an agent
writes a controller", not "an agent plays". It needs route (b)'s tools: `user_keyboard_input` /
`user_mouse_input` (or `character_navigation`, which is privileged pathfinding and must be
disclosed as such), `execute_luau` in Client/Server for state reads, `screen_capture` for stills,
and `get_console_output` that includes play output, with `start_stop_play` keeping play running.

## What the owner must do for route (b)

1. In Studio: **Assistant** (top right) → **…** → **Settings** (or **Manage MCP Servers**) →
   **MCP Servers** tab → turn on **Enable Studio as MCP server** until it shows **MCP Ready**
   (Autopilot `docs/roblox-studio-setup.md` §3). On macOS the server is
   `/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP`.
2. Make that server reachable by whoever plays: register it as an MCP server for the Claude
   client that will run P1, or for this remote-session bridge the way the current Studio MCP is
   exposed; or run Autopilot's connector on the Mac
   (`python -m autopilot.engines.roblox_connector connect --project <place>`, Autopilot installed
   from a clone, `AUTOPILOT_SHARE=0`). Jev keys are needed only for P2.
3. Keep the place the owner's own unsaved or private place; one Studio, one play session at a
   time. The connected `studio-rust-mcp-server` plugin and the second community plugin can stay,
   but their console lines will keep appearing (the nonce rule ignores them).

Route (b) was **not** run in WS-03; nothing above about it is measured by us.

## Recommendation

1. **A (recommended): WS-04 uses route (a) for P0 now**, with the SPEC §9 wording amended by the
   owner, the controller committed and hashed, `T_attempt` enforced inside the script and by the
   tool timeout, and events parsed from the returned logs. It works today with no new install.
2. **In parallel, the owner enables route (b)** (steps above). It is required for P1/P2, for a
   SPEC-literal key-hold P0, and for `screen_capture` stills in the §4 demo. Once it answers, a
   one-off check should confirm `get_console_output` sees server prints during play.
3. **B (not recommended now):** wait for route (b) before WS-04. It blocks M0 on an install that
   has not been tried here.

## Consequences

- WS-04's runner reads events after each attempt, not during it; "bounded polling" becomes a
  bounded in-script wait plus a bounded tool call.
- The printed positions are approximate (finding 2); classification uses geometry footprints.
- The default character is the owner's avatar (`LoadCharacterAppearance = true`), so its size
  can change with his avatar; see HANDOFF open questions.
