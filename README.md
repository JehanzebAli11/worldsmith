# worldsmith

Worldsmith is a small companion tool for [Autopilot](https://github.com/ramenvr/autopilot). It
builds one owned Roblox room from a bounded JSON recipe, and a frozen player tries to complete it.
Worldsmith records an instrumented failure event, and an architect proposes a bounded scene patch
that an independent validator must approve. Then the room is rebuilt from scratch, the same frozen
player actually retries, and the tool emits a side-by-side replay bundle. The full design is in
[`docs/SPEC.md`](docs/SPEC.md).

**Status: WS-03: scene grammar, fixtures, and the template compiler (scene JSON → one Luau
build script), read-back comparator and event templates. F1-A and F1-E have been built once in
Studio on the owner's local place and read back with 0 differences (`bundles/ws03/`). One-off
route probes ran once each; they are feasibility checks, not M0 results.** The M0 gate (WS-04)
has not run. Ticket status is in [`docs/TICKETS.md`](docs/TICKETS.md).

## Honest limits

- The player reads game code, so this benchmarks *patch planning* from instrumentation, not
  discovery from pixels.
- Early fixtures have known defects.
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
