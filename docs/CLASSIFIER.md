# Classifier (WS-08): SPEC §10 failure taxonomy v0 over recorded traces

Code: `runner/taxonomy.py` (on top of WS-04's `runner/classify.py`). Config:
`config/classifier.yaml` (its SHA-256 is written into every classification). Output for M0:
`bundles/m0/classification.json`, rebuilt by `python scripts/ws_classify.py` (`--check` compares).
Inputs are recorded bundles only: each attempt is re-classified from its raw play-tool answer
(`attempts/<n>.raw.txt`, ADR 0005), and a re-classification that disagrees with the bundle's
summary is a bundle problem, not a result.

## Tags

| Tag | Kind | Rule as implemented |
| --- | --- | --- |
| `SUCCESS` | observed | This attempt's nonce: first `BRIDGE_CROSSED` before the first `GOAL_TOUCHED`, in console order and server time, with no death before the goal. Cause: none. |
| `FELL` | observed | `KILL_FLOOR_TOUCHED` before any `GOAL_TOUCHED`; records the kill position and whether it lies in the gap_1 area. |
| `TIMEOUT` | observed | The controller's server-side `T_attempt` deadline fired (report status `deadline`) with no goal or fall. |
| `LEVER_NOT_ACTIVATED` / `GATE_NOT_OPENED` | observed | Extra tags on `TIMEOUT`: no `LEVER_ACTIVATED`; or lever but no `GATE_OPENED`. |
| `STUCK` | observed | See below. An extra tag when observed; the outcome only if nothing else was observed (it replaces `UNCLASSIFIED`). |
| `HARNESS_ERROR` | system | Controller report missing/broken/wrong nonce, params echo mismatch, a jump (P0 contract), controller `harness_error`, character removed without dying, or a script error from the play tool (`error` other than `nil`). Other plugins' warnings are not ours and are ignored. |
| `CONNECTOR_ERROR` | system | Play call timed out or failed, an answer that is not a play result, or Studio would not stop (also an episode tag when it ends the episode). |
| `BUILD_FAILED` | system | Episode tag: no matching `WS_BUILD_OK` line or a read-back difference. No attempt runs. |
| `BRIDGE_SHORT_HYPOTHESIS` | hypothesis | A `FELL` cause, only when all three conditions below hold. |
| `UNCLASSIFIED` | — | The default outcome when nothing above applies (e.g. a goal line without a crossing before it, a death without a kill-floor event) and the default cause of every failure. Never forced into a tag. |

Console parsing is WS-03's: exact prefix, well-formed fields and this attempt's nonce; other
nonces and other plugins' lines are ignored.

## STUCK: displacement < ε over a window

| Parameter | Value | Source |
| --- | --- | --- |
| `epsilon_studs` | 2.0 | HumanoidRootPart width, `bundles/m0/character/measure.json` (`hrp_size` 2 × 2 × 1) |
| `window_s` | 10 | `config/m0.yaml` `timing.t_waypoint`: the controller's own limit for one waypoint |
| `max_sample_gap_s` | 1.0 | **[ESTIMATE]**, not measured: at WalkSpeed 16 studs/s a 1 s gap can hide 16 studs of motion |

Positions are the P0 controller's own samples (route start, then each waypoint's end, on the
controller clock), only while alive, over the route phase (route start to death or the end).
There is no live position stream (ADR 0005).

- Route phase shorter than the window: `NOT_STUCK` (no window fits).
- `STUCK`: samples i < j at least `window_s` apart, every sample between them within ε of
  sample i, no gap between them above `max_sample_gap_s`.
- `NOT_STUCK`: no such window, and the samples cover the route phase with no gap (including
  the tail to the end) above `max_sample_gap_s`.
- Otherwise **`NOT_EVALUABLE`**: the rule does not guess. With today's P0 controller a long,
  blocked attempt gives a sample only when `MoveTo` gives up (about every 8 s), so it is
  `NOT_EVALUABLE`; making it evaluable needs denser samples (a controller change, which would
  change the frozen controller hash, or route (b) polling).
- **Player p0.v1 (WS-11, ADR 0010)** is that change, as a separate, labelled player: the M0
  controller plus a `samples` list (position about every 1 s while alive). `position_samples`
  adds those samples when a report has them; p0.v0 reports have none, so every M0 result above
  is unchanged.

All six M0 gate attempts are `NOT_STUCK` by the first rule (route phases 5.785 to 7.083 s,
`bundles/m0/classification.json`).

## BRIDGE_SHORT_HYPOTHESIS (SPEC §10)

For a `FELL` attempt of episode E, all of:

1. **Fell near the bridge**: the `KILL_FLOOR_TOUCHED` position lies in the gap_1 footprint grown
   by `gap_area_margin_studs` (4, `config/m0.yaml`; ADR 0005 §6, since server positions lag 2 to
   3.5 studs).
2. **Independent geometry check** on E's bundled `scene.json` (not the trace): exactly one Bridge
   spans gap_1; the declared gap width equals the distance between the two platforms' facing
   edges; bridge length < gap width; and the built bridge's far end stops short of the far
   platform (uncovered studs > 0).
3. **Paired fix**: some other recorded episode bundle with no bundle problems, the same
   controller, config and route hashes (the same frozen player), status complete with build OK
   and 0 read-back differences, `pass_k`/`n_attempts` = 3/3 `SUCCESS` (re-derived from its raw
   answers), on a scene whose only change from E's scene is the bridge's `length`, lengthened
   (a `version` bump is allowed, ADR 0006). The evidence is linked by bundle path, run id, scene
   hash, controller/config/route hashes, nonces, outcomes and the SHA-256 of each bundle file.

Otherwise the cause is `UNCLASSIFIED`, and every condition is still reported with its reason,
along with each rejected candidate bundle.

Disclosure (also in the output): the M0 defect is known by construction, and P0 never jumps,
so the tag is functional evidence under one frozen controller, not a causal diagnosis of an
arbitrary room and not an agent result. A fall near a bridge alone does not separate "bridge
short" from "missed jump" or player error (SPEC §10).

## ADR 0011: start_pos rule (every player version)

An attempt whose recorded `start_pos` (the HRP at the route start, before any MoveTo) is more than
2 studs in XZ from the spawn centre (`classify.spawn_centre(scene)`) is **HARNESS_ERROR**
(`moved_before_route`), never FELL or SUCCESS: something other than the controller moved the
character. A controller `harness_error` keeps its own detail; `start_pos` `na` leaves the rule out.
All 8 recorded M0 attempts started at x 0.00, z 6.00, so the M0 classification is unchanged.
Player p0.v2 checks the same thing itself, tighter (1 stud, through the settle). A CONNECTOR_ERROR
attempt (e.g. a bare `null` play answer) now ends the episode at once.
