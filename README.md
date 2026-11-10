# worldsmith

**An AI architect fixes a broken game room so the same frozen player can finish it.**

A room is built in Roblox Studio from a bounded JSON recipe and a frozen player tries it. When the
player fails, an AI architect reads what happened and proposes a small patch to the *world*. An
independent validator rejects any patch that cheats (moving the goal, changing player physics,
adding a teleport). The room is rebuilt from scratch and the same player tries again. Every run
writes a bundle with the raw Studio calls, so each number below can be checked. It builds on my
[Autopilot](https://github.com/ramenvr/autopilot) contributions (the Roblox Studio connector,
PR #40). Full design: [`docs/SPEC.md`](docs/SPEC.md).

## Results so far

| Experiment | Room | Before | Fix | After | Source |
| --- | --- | --- | --- | --- | --- |
| M0 gate (hand fix, a control) | F1-A, bridge 6 studs over a 16-stud gap | fell 3/3 | `bridge_1.length` 6 → 16, by hand | crossed and reached the goal 3/3 | `bundles/m0/gate.json` |
| B3: Claude Haiku 5.5 as architect (player p0.v3) | F1-A | fell 3/3 | 1 call, ACCEPTED: `bridge_1.length` → 16 | 3/3 | `bundles/experiments/m2_f1a_b3/results.json` |
| B3: Claude Sonnet 5.5 | F1-A | fell 3/3 | 1 call, ACCEPTED, same patch | 3/3 | same |
| B3: Claude Opus 5.5 | F1-A | fell 3/3 | 1 call, ACCEPTED, same patch | 3/3 | same |
| B2: random valid patch (pilot, player p0.v2) | F1-A | fell 3/3 | 3 proposals (2 rejected) | 3/3 | `bundles/experiments/m2_f1a_pilot/` |

The validator rejects all 69 abuse cases in `tests/abuse/reject/` and accepts the 13 legitimate
ones in `tests/abuse/accept/` (CPU tests).

What this does not show yet: the player is a scripted walker (server-side `MoveTo` waypoints,
never jumps), not an AI agent. B3 is one episode per model, and F1-A is easy enough that a random
valid patch also fixes it, so it doesn't separate the models. Harder rooms come next.

## Build log

**Status: WS-04 / M0 gate: PASS** (`docs/M0.md`, from `bundles/m0/`). Live in Roblox Studio on
the owner's local place, with one frozen P0 controller (server-scripted `Humanoid:MoveTo`
waypoints that never jump; a substrate probe, not an Autopilot or agent result): F1-A (bridge
too short by construction) ended FELL in the gap 3/3, and its one-field fix ended BRIDGE_CROSSED
then GOAL_TOUCHED 3/3; read-back matched before each scene. N = 3 is functional-repeatability
evidence only. **WS-05:** patches (`worldsmith.patch/0.1`) apply on CPU with a base-hash check;
the M0 stage allows only `set bridge_1.length` (ADR 0006). **WS-06:** an independent validator
(`worldsmith.validator.validate`) checks schema and bounds, protection, the locked gap, difficulty
and structure, with stable violation codes ([`docs/VALIDATOR.md`](docs/VALIDATOR.md)); all 69
abuse cases in `tests/abuse/reject/` are rejected with their expected codes and the 13 legit cases
pass (CPU tests, not Studio runs). An M1 stage widens the patch ops (ADR 0007). **WS-07:** a
coarse walk/jump graph over the built geometry reports CONNECTED/DISCONNECTED as a warning only,
an approximation and never a proof (ADR 0008). **WS-08:** a failure classifier implements the
SPEC §10 taxonomy; the M0 attempts are labelled in `bundles/m0/classification.json`
([`docs/CLASSIFIER.md`](docs/CLASSIFIER.md)). **WS-09:** baseline B2, a capped random valid
sampler (at most 3 raw proposals per episode, rejects included, seeded and replayable); its
offline statistic in `bundles/b2_offline/` is validator/graph only, not a play result.
**WS-10:** the architect client (versioned prompt, strict one-object JSON, fixture replay, a
two-switch guard on live calls); one live smoke call (Haiku 5.5 on F1-A at M0, revision 1 of 3)
was ACCEPTED (`set bridge_1.length 16`, $0.000642; `bundles/architect_smoke/`): a format check,
not an episode and not a B3 result. **WS-11:** the orchestrator runs declared experiments
(B0 none, B1 hand fix, B2 random, B3 architect) with per-episode caps of 12 attempts and 3
architect calls, a new labelled player p0.v1 (the M0 controller plus position samples), a lock
that refuses config changes after the first episode, resume without repeating answered Studio
calls or paid architect calls, and P2 freeze-manifest refusal checks (no Autopilot run). The M2
pilot on F1-A (`m2_f1a_pilot`) ran B0-B2 and was closed for a harness fault; player p0.v3 fixed it
and `m2_f1a_b3` ran B3 ([`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md), ADR 0012). Ticket status is in
[`docs/TICKETS.md`](docs/TICKETS.md).

## Honest limits

- The player reads game code, so this benchmarks *patch planning* from instrumentation, not
  discovery from pixels.
- Early fixtures have known defects.
- M0's player (P0) is a server-scripted MoveTo waypoint controller that never jumps; it shows the
  patch changes the real engine outcome, nothing about an agent's skill.
- Mechanical success isn't the same as fun.

## Run the tests

CPU only: no Roblox Studio, no API keys, no paid calls. Needs Python 3.10 or newer.

```sh
make test                      # or: ./scripts/test.sh
PYTHON=python3.12 make test    # if your default python3 is older than 3.10
./scripts/test.sh -k secrets   # extra arguments go to pytest
```

`scripts/test.sh` creates `.venv/` (override with `VENV=...`), installs the package in editable
mode with its dev dependencies, and runs pytest. CI runs the same command on Python 3.10 and 3.12.

## Licence

Apache-2.0 (see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)). Dependencies, the pinned Autopilot
commit and their licences are in [`deps.md`](deps.md).
