# ADR 0005: Attempt results come from the play tool's result, not from console polling

- **Status:** accepted (WS-04, 9 Nov 2026). Implements the owner's choice of route (a) in ADR 0004.
- **Replaces:** SPEC §8's "the tool polls them via `get_console_output` plus batched position
  reads" and "polling uses a fixed interval, with `max_polls = T_attempt / interval`", for the
  P0 tier on the connected Studio MCP (`studio-rust-mcp-server`).

## Context (evidence)

- WS-03 (`bundles/ws03/`, ADR 0004 finding 1): during play, `run_code` runs in the **Edit** data
  model (`IsEdit` true, 0 players); `get_console_output` reads the Edit data model's buffer, which
  is reset at play start and keeps the first 10 000 characters. Called alongside a play run it
  returned none of the `WS|` lines. The server's prints came back only in
  `run_script_in_play_mode`'s result, after play stopped.
- WS-03: `MoveToFinished` reported `reached = true` for the waypoint during which F1-A's
  character died, and server-printed positions lag the client by about 2 to 3.5 studs.
- WS-04, observed in every `run_script_in_play_mode` result so far (the character measurement and
  the two F1-E smoke runs, `bundles/m0/character/`, `bundles/m0/smoke*-F1-E/`): the result holds
  `logs`, `errors`, `duration`, `error` and `isTimeout`; it has **no `value` or `success` field**,
  so the script's return value does not reach us. The printed lines do.
- WS-04, first F1-E smoke run (`bundles/m0/smoke-F1-E/`, controller `6d2e299c…`): the controller
  took the auto-loaded character, which `LoadCharacter` then replaced; the controller saw it
  removed before the first waypoint. Fixed before any comparison run (the controller now waits
  for the auto-loaded character, then for a different, new one) and recorded as
  `HARNESS_ERROR` if it ever happens again. The second smoke run (`smoke2-F1-E`, controller
  `3743d998…`, the frozen M0 controller) ended `SUCCESS`.

## Decision

1. **One play call per attempt.** Each attempt is exactly one `run_script_in_play_mode(code,
   "start_play", timeout)` call: it starts play, runs the P0 controller as a server script and
   stops play (stop → start → stop). `get_studio_mode` is checked before every attempt and after
   the last one; if Studio is not in `stop`, the runner sends `start_stop_play("stop")` once and
   checks again, else the episode ends `CONNECTOR_ERROR`.
2. **Bounded in two places.** The controller holds a server-side deadline `T_attempt` (60 s,
   `config/m0.yaml`) from the script's start: every wait in it, including waiting for the player
   and the character, checks the deadline, and the route loop, the per-waypoint wait
   (`t_waypoint` 10 s) and the settle wait (`settle` 2 s) are all bounded. The play call carries
   its own client timeout (the tool's `timeout` argument, 90 s > `T_attempt`, so the server
   deadline fires first). A play call that reports `isTimeout` is `CONNECTOR_ERROR`.
3. **The in-script watch loop is the bounded poll.** The controller checks death, support and
   the deadline every `tick` = 50 ms, so at most `T_attempt / tick` = 1200 polls; the count used
   is reported (`polls_used` in the trace). Results are read **once**, after play stops.
4. **What is read.** Events: the result's log lines, parsed by exact prefix and nonce
   (`runner.console.parse_events`); other plugins' lines and other nonces are ignored. The
   controller's report: printed as JSON in chunks `WSP|<nonce>|RESULT|i|n|<chunk>` and
   reassembled (`runner.classify.parse_report`; `value` is read too if the tool ever returns it).
   The report echoes the controller's parameters (route, timing, nonce), which the runner checks
   against the frozen route and config, since the MCP returns no copy of the script it ran. The
   controller also captures its own run's `WS|<nonce>|` lines from the server `LogService`; that
   copy is a cross-check (`events_agree`), not the source.
5. **Death is watched by the controller** (`Humanoid.Died`, `Health <= 0`, character removed),
   never inferred from `MoveToFinished`. Classification uses the events: a `KILL_FLOOR_TOUCHED`
   before any `GOAL_TOUCHED` is `FELL`, whatever the waypoints reported.
6. **"In the gap_1 area"** is judged from the gap's footprint (between the two platforms' facing
   edges, across the wider platform) grown by `gap_area_margin_studs` = 4, larger than the
   measured position lag; not from the exact printed point.
7. **Other calls' timeouts.** `run_code`, `get_studio_mode` and `start_stop_play` take no timeout
   argument on this MCP. Their declared timeouts (`tool_timeouts_s`) are recorded with every call
   and enforced by any `StudioClient` that can (the tests' fake raises `CallTimeout`, which becomes
   `CONNECTOR_ERROR`); in relay mode they are bounded only by the session's own tool-call limit.
   That is a known gap, stated here rather than hidden.
8. **Relay mode.** The Python runner cannot reach Studio from the Linux VM, so the Claude session
   holding the Studio MCP tools relays the calls: `scripts/m0.py step` writes the next request
   (tool, timeout, code file, code SHA-256) and stops; the operator makes exactly that call and
   stores the raw answer with `scripts/m0.py respond`. Answers are never replaced; a stored
   request that differs from the runner's next call stops the run (`RelayError`). The recorded
   `relay_elapsed_s` includes the operator's time and is an upper bound, not the call's duration;
   the play tool's own `duration` is logged separately.

## Consequences

- No live position stream during an attempt; positions come from event lines and the
  controller's waypoint log, both server-side and lagging.
- The code the operator sends cannot be verified byte-for-byte from Studio's side; the params
  echo and the `WS_BUILD_OK|hash|count` and read-back checks catch transcription errors in what
  matters (route numbers, timing, nonce, built tree).
- Route (b) (Studio's built-in MCP with play-time `execute_luau` and console reads) would allow
  real polling; it remains the path for P1/P2 (ADR 0004).
