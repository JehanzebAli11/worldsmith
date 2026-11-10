# m2_f1a_pilot: CLOSED

Closed on **2026-11-10** by owner decision **(A)**: stop the pilot here, keep what is valid, and
run the B3 conditions again under a new experiment id with player p0.v3 (ADR 0012). Nothing
in this folder was changed to close it; this file was added. The runner honours it:
`scripts/run_experiment.py ... step | respond | architect` refuse with exit 4 and send, write
and call nothing (`status` and `plan` still print). Config `config/experiments/m2_f1a.yaml`
and its lock (`4bfa57a5…`) are unchanged.

Player p0.v2 (controller `a7472c9c…`), scene F1-A (`92f86609…`), stage M0, N = 3, k = 3,
preflight on. One episode per condition: n = 1 each, a functional check of the loop on one
hand-authored fixture, not a comparison.

## Valid results (from each `<condition>/ep0/summary.json`)

| Condition | Status | Versions (outcomes per attempt) | Revisions | Repaired |
| --- | --- | --- | --- | --- |
| B0 (none) | complete | v1 `92f86609…`: FELL, FELL, FELL (0/3) | none | no |
| B1 (hand fix) | complete | v1: FELL, FELL, FELL (0/3); v2 `297b23c8…`: SUCCESS, SUCCESS, SUCCESS (3/3) | ACCEPTED | yes |
| B2 (random valid, seed 0) | complete | v1: FELL, FELL, FELL (0/3); v2 `b50b5baf…`: SUCCESS, SUCCESS, SUCCESS (3/3) | REJECTED, REJECTED, ACCEPTED | yes |

## Not valid (system errors, not scene outcomes)

| Condition | Status | What the bundle holds |
| --- | --- | --- |
| B3-haiku | SYSTEM_ERROR (`summary.json`, detail `version 2: SYSTEM_ERROR`) | proposal 1 REJECTED (PENETRATION; `bridge_1.length` 18), proposal 2 ACCEPTED (`bridge_1.length` 16, scene `297b23c8…`, the same canonical scene as B1's v2). v1: FELL, FELL, FELL; v2: SUCCESS, HARNESS_ERROR, HARNESS_ERROR. 6 attempts, 2 architect calls. |
| B3-sonnet | SYSTEM_ERROR by the runner's rule (a HARNESS_ERROR attempt makes the version SYSTEM_ERROR and stops the episode, `runner/episode.py` `SYSTEM_OUTCOMES` / `STOP_STATUSES`); no `summary.json` was written | v1 attempt 1 FELL, attempt 2 HARNESS_ERROR. Call 010 (`get_studio_mode`, the stop check before attempt 3) is pending in `pending.json` and stays unanswered. 0 architect calls (the episode never reached a proposal). |
| B3-opus | not run | no folder |

### The harness flake (re-read from the attempt raw files)

Every HARNESS_ERROR has the controller detail
`moved_before_route: spawn_offset=0.00 settle_drift=0.00 settle_max_offset=0.00 grounded=false`
and `start_pos` 0.00,3.11,6.00: the character stood on the spawn (spawn centre x 0, z 6), not
moved. The play sessions ran very few controller ticks (`ticks`, `t_end` in the report):

| Attempt | Ticks | Controller time | Per `task.wait(0.05)` |
| --- | --- | --- | --- |
| B3-haiku v2 attempt 2 (`v2/attempts/2.raw.txt`) | 5 | 5 ticks in 1.54 s | 0.31 s |
| B3-haiku v2 attempt 3 (`v2/attempts/3.raw.txt`) | 6 | 6 ticks in 2.40 s | 0.40 s |
| B3-sonnet v1 attempt 2 (`calls/009.response.json`) | 6 | 6 ticks in 1.33 s | 0.22 s |

The 20 attempts of this pilot that ran their route took 115 to 148 ticks in 7.97 to 10.04 s:
0.065 to 0.070 s per tick, except B3-haiku v2 attempt 1 (SUCCESS; 116 ticks in 10.04 s, 0.087 s
per tick, route start at 3.01 s instead of 1.08 to 1.89 s), whose start was slow too. A separate
play-mode health probe measured 0.083 s per tick over 40 waits (reported by the control thread;
not a file in this repo).

So the first ~1.5 s of some play sessions run slowly. p0.v2's spawn check counts wall-clock
time (`GROUND_WAIT` 3 s, then a 1 s settle) and reads `Humanoid.FloorMaterial`, which can still
read Air when few physics steps have run; the check then fails although the character stands on
the spawn. The runner turns any HARNESS_ERROR into a SYSTEM_ERROR version verdict that stops the
episode, so these episodes say nothing about the architect or the scene.

## Architect spend in this pilot

Sum of `cost_usd` in the stored replies (`B3-haiku/ep0/architect/replies/*.json`), n = 2 calls,
both `claude-haiku-5-5`: $0.000694 + $0.000869 = **$0.001562** (exact sum $0.00156225, equal to
B3-haiku's `cost_usd_reported`). No sonnet or opus call was made. The same two calls are rows in
`results/usage.csv` (requests `req_011CguPqfLH65CpeuKDyfZJX`, `req_011CguR55r9X2B2WLn8FeTQ2`).

## What happens next

The B3 conditions (B3-haiku, B3-sonnet, B3-opus) move to a new experiment,
`config/experiments/m2_f1a_b3.yaml` (id `m2_f1a_b3`, player p0.v3, everything else as this
pilot), run after `config/experiments/smoke_f1e_p0v3.yaml` passes (6/6 SUCCESS expected).
B0, B1 and B2 above stay this pilot's results; they are on player p0.v2 and are not pooled with
p0.v3 results.
