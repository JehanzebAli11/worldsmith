# worldsmith

Worldsmith is a small companion tool for [Autopilot](https://github.com/ramenvr/autopilot). It
builds one owned Roblox room from a bounded JSON recipe, and a frozen player tries to complete it.
Worldsmith records an instrumented failure event, and an architect proposes a bounded scene patch
that an independent validator must approve. Then the room is rebuilt from scratch, the same frozen
player actually retries, and the tool emits a side-by-side replay bundle. The full design is in
[`docs/SPEC.md`](docs/SPEC.md).

**Status: WS-04 / M0 gate: PASS** (`docs/M0.md`, from `bundles/m0/`). Live in Roblox Studio on
the owner's local place, with one frozen P0 controller (server-scripted `Humanoid:MoveTo`
waypoints that never jump; a substrate probe, not an Autopilot or agent result): F1-A (bridge
too short by construction) ended FELL in the gap 3/3, and its one-field fix ended BRIDGE_CROSSED
then GOAL_TOUCHED 3/3; read-back matched before each scene. N = 3 is functional-repeatability
evidence only. No architect, validator or patch engine exists yet (WS-05+). Ticket status is in [`docs/TICKETS.md`](docs/TICKETS.md).

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
