# Experiments (WS-11): how to run the M2 experiments on F1-A

The orchestrator (`scripts/run_experiment.py`, ADR 0010) runs a **declared** experiment: one
config file fixes the scene, the stage, one player version, N = 3, k = 3, the revision budget,
the caps and the conditions with their episode counts, before any run. After the first episode
bundle exists the runner refuses a changed config (a change is a new experiment id).

## Now: `smoke_f1e_p0v3`, then `m2_f1a_b3` (ADR 0012)

**Status 10 Nov 2026, 22:45 PKT:** `smoke_f1e_p0v3b` PASS (6/6). `m2_f1a_b3` complete: B3-haiku,
B3-sonnet and B3-opus each FELL ×3 on v1, proposed `bridge_1.length 16` (1 call, ACCEPTED), and
v2 passed SUCCESS ×3; $0.037481 in total. Details and caveats in HANDOFF (same date).

**The pilot `m2_f1a_pilot` is closed** (10 Nov 2026, owner decision A;
`bundles/experiments/m2_f1a_pilot/CLOSED.md`). B0, B1 and B2 are its valid results; B3-haiku and
B3-sonnet ended SYSTEM_ERROR because p0.v2's wall-clock spawn check failed in slow play starts
(5 to 6 controller ticks in 1.33 to 2.40 s); B3-opus was not run. `step`, `respond` and
`architect` refuse a closed experiment (exit 4, nothing sent), so its pending call 010 stays
unanswered. Do not answer it.

Run, in this order, with the same owner commands as below (only the `--config` path changes):

1. `config/experiments/smoke_f1e_p0v3.yaml` (`smoke_f1e_p0v3`): F1-E passing control, B0,
   player **p0.v3** (spawn check counts physics steps: warm-up, grounded, settle; ADR 0012),
   preflight on, 2 episodes = 6 attempts. **6/6 SUCCESS expected.** Any HARNESS_ERROR
   (`not_warm`, `not_grounded`, `moved_before_route`) means stop and debug; nothing paid.
2. `config/experiments/m2_f1a_b3.yaml` (`m2_f1a_b3`): F1-A, M0, p0.v3, preflight on, B3-haiku,
   B3-sonnet, B3-opus, one episode each; every other field as the pilot (ladder, baseline prompt
   `df1b7a12…`, caps, N 3, k 3, max_revisions 3, job `architect_ladder`). Worst case
   **$2.961000** (9 calls, the pilot's table below). Only after step 1 passed.

```bash
cd ~/lab/worldsmith
.venv/bin/python scripts/run_experiment.py --config config/experiments/smoke_f1e_p0v3.yaml --dry-run
.venv/bin/python scripts/run_experiment.py --config config/experiments/smoke_f1e_p0v3.yaml step
# ... step / respond as in section 2 below; then the same with m2_f1a_b3.yaml
```

Studio for these runs (in addition to the owner checklist below):

- **One Studio driver.** Only this relay session drives Studio: no Autopilot bench run, no other
  plugin play session, no second Claude session with the Studio MCP.
- **Studio restarted fresh, in the foreground.** Quit Roblox Studio, reopen
  `places/worldsmith-lab.rbxl`, stay in Edit mode, and keep the Studio window visible and in the
  foreground for the whole run (not minimised, not behind a full-screen app, display awake).
  macOS throttles background apps, which slows play sessions (the pilot's flake was a slow start).
- **Tip: Prevent App Nap.** In Finder → Applications → Roblox Studio → Get Info (⌘I), tick
  "Prevent App Nap" if the box is shown; and keep the Mac awake during a run (e.g. `caffeinate -d`
  in a Terminal, or Energy settings).

p0.v3 results are not pooled with the pilot's p0.v2 results.

## The pilot (CLOSED): `config/experiments/m2_f1a.yaml` (experiment `m2_f1a_pilot`)

| Item | Value |
| --- | --- |
| Scene, stage | F1-A (`92f86609…`), stage M0 (only `set bridge_1.length`) |
| Player | **p0.v2** (P0, server-scripted MoveTo, never jumps), controller `a7472c9c…`, the M0 route `b9514a4b…`, T_attempt 60 s, samples every 1 s, spawn check before the route (ADR 0011). Not the M0 controller (p0.v0); its results are not compared with M0's. (Moved from p0.v1 before any run.) |
| Preflight | `runner/templates/preflight_v1.luau` (`29ffe3d7…`) before every build (ADR 0011) |
| N, k | 3, 3/3; 0 < successes < 3 stops the episode (INCONSISTENT) |
| Revisions, caps | 3 proposals per episode (rejects count); ≤ 12 attempts and ≤ 3 architect calls per episode |
| Conditions (run in this order, 1 episode each) | B0 none · B1 hand fix `scenes/F1-A/patches/m0_fix.json` · B2 random valid, seed 0 · B3-haiku · B3-sonnet · B3-opus (baseline prompt `df1b7a12…`) |
| Budget | jzapi job `architect_ladder` (cap $90); worst case of the whole pilot **$2.961000** (below) |
| Bundles | `bundles/experiments/m2_f1a_pilot/<condition>/ep0/` (committable) |

A pilot of one episode per condition is a functional check of the loop on one hand-authored
fixture, not a comparison: n = 1 per condition, and F1-A's defect is known by construction.

### Worst case (printed by `--dry-run` and `plan`; no network)

Per call: `jzapi.worst_case_usd(model, prompt_tokens = 24000 (prompt_bytes_cap), max_tokens,
cache write 5m)`, every call billed as if it used all of max_tokens:

| Condition | Model | max_tokens | Per call | Calls (max) | Worst case |
| --- | --- | --- | --- | --- | --- |
| B3-haiku | claude-haiku-5-5 | 8000 | $0.007000 | 3 | $0.021000 |
| B3-sonnet | claude-sonnet-5-5 | 16000 | $0.220000 | 3 | $0.660000 |
| B3-opus | claude-opus-5-5 | 32000 | $0.760000 | 3 | $2.280000 |
| **Pilot** | | | | **9** | **$2.961000** |

A request larger than the cap is refused unsent, so this is a ceiling. The live smoke call cost
$0.000642 (`bundles/architect_smoke/`); real spend depends on how much each model thinks.

## Owner checklist before ANY live run (ADR 0011)

The first live smoke (`smoke_f1e_p0v1`) ran in the Autopilot test place while an Autopilot bench
run drove the same Studio; its bundle is CONTAMINATED and not evidence. Before every live run:

1. **One-time:** mark the place. Open `places/worldsmith-lab.rbxl` (File → Open from File), stay in
   Edit mode, run `workspace:SetAttribute("ws_place", "worldsmith-lab")` in the Command Bar (or
   send the payload from `python scripts/mark_place.py` with `run_code`), then File → Save to File
   over `places/worldsmith-lab.rbxl`. Only in that place, never in another.
2. **Open the right place:** `places/worldsmith-lab.rbxl`, and close any other place (the Autopilot
   test place in particular). Don't save a Worldsmith build into another place.
3. **One driver:** no Autopilot bench run, noise check or any other plugin-driven play session
   while a Worldsmith episode runs. Finish or stop those first; don't start one until `step`
   exits 0. Optionally disable the Autopilot plugin during Worldsmith runs (it attaches to every
   play session).
4. **Edit mode, stopped**, before the first `step`. The preflight never stops a play session it
   finds; it fails the episode as HARNESS_ERROR instead.

The preflight (before every build) checks the marker, `LoadCharacterAppearance` false,
`CharacterAutoLoads` true, no StarterCharacter, exactly one SpawnLocation outside
`Workspace.Worldsmith`, no foreign scripts in ServerScriptService / StarterPlayerScripts /
StarterCharacterScripts / StarterGui, and Studio mode `stop`. A failure is recorded in
`v<k>/preflight.json` with what it saw, and `step` stops with exit 4: no further episode of that
experiment starts, now or later. Fix the place (or stop the other driver) and declare a new
experiment id.

`config/experiments/smoke_f1e_p0v2.yaml` (F1-E, B0, N = 3, p0.v2, preflight on) ran before the
pilot. For p0.v3 the smoke is `smoke_f1e_p0v3.yaml` (6 attempts, 6/6 SUCCESS expected; see "Now"
above): the same `step` / `respond` loop as below with that config; anything else means stop and
debug.

## Owner commands

The commands are unchanged for every experiment; the examples use the pilot's
`config/experiments/m2_f1a.yaml`. For the current runs put `config/experiments/smoke_f1e_p0v3.yaml`,
then `config/experiments/m2_f1a_b3.yaml`, in its place. (On the closed pilot, `step`, `respond` and
`architect` now exit 4.)

### 1. On the Mac, Terminal (no Studio, nothing paid)

```bash
cd ~/lab/worldsmith
make test                                   # CPU tests; .venv as in docs/ARCHITECT_SMOKE.md
.venv/bin/pip install -e ~/lab/JzLab        # once; jzapi for prices, headroom and B3 calls
.venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml --dry-run
.venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml plan
```

- The dry run simulates everything in a temp folder (fake Studio operator, WS-10 fixture
  architects) and prints the worst case and a sample bundle path. It is not a result.
- `plan` prints the worst case, what is left, both live switches (JzLab `budgets.yaml`
  `architect_ladder approved: true`; the git-ignored `config/architect.yaml` `live: true`) and the
  job's headroom. Check both say ON and the headroom covers the worst case. Check the active
  login/key is the account you mean to bill.

### 2. The live pilot: a Claude Code session on the Mac with the Studio MCP connected

Roblox Studio open on `places/worldsmith-lab.rbxl` (settings in `places/README.md`), in Edit
mode, the Studio MCP (`studio-rust-mcp-server`) connected to that session, one Studio run at a
time. The session is the relay operator (ADR 0005 §8):

```bash
cd ~/lab/worldsmith
.venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml step
#   PENDING B0/ep0 call 001 get_studio_mode timeout_s=15 args={}
#   (for run_code / run_script_in_play_mode it also prints the code file and its SHA-256)
# -> make exactly that MCP call (the code file's text unchanged, the printed args and timeout),
#    save the tool's raw text answer to a file, e.g. /tmp/ws_answer.txt, then:
.venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml respond 1 /tmp/ws_answer.txt
.venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml step
# ... repeat step / respond until step exits 0 and prints the results path
.venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml status
```

- If a call itself fails (the tool errors or times out), store that instead:
  `respond K /tmp/ws_answer.txt --error "timeout after 90 s"` (an empty file is fine).
- `step` makes the architect calls (paid) itself when a B3 episode reaches one; they are stored
  under `<episode>/architect/replies/` before use and never repeated on a later `step`.
- `step` refuses (exit 4) unless both switches are ON and the headroom covers what is left, as
  long as a B3 episode may still call a model; it also refuses if the config changed after the
  first episode. Exit 3 means a Studio call is waiting.
- Stopping at any point is safe: run `step` again later; answered calls are replayed, not re-sent.
  A Ctrl-C during an architect call may repeat that one call (ADR 0010 §5).
- At most 57 play calls and about 230 Studio calls in all [count from the caps; the preflight adds
  2 per scene version, at most 19 versions]; M0's relay took about a minute per play call.
- The first two calls of every scene version are the preflight (`get_studio_mode`, then a
  `run_code` whose code file starts `-- Worldsmith preflight probe v1`). Make them like any other
  call. If Studio is not in `stop`, answer the mode truthfully: the episode ends HARNESS_ERROR
  and nothing is stopped.

### 2b. Two machines on the same files (the control thread on the bridge VM, architect calls on the Mac)

The bridge VM's egress proxy blocks keyed Anthropic calls, so a model call made there would fail
and be stored as a provider ERROR that uses a slot. So the VM never calls a model:

- **VM (control thread, with the Studio MCP):** the same `step` / `respond` loop as in step 2, with
  `--architect-elsewhere` on every `step` (or `WS_ARCHITECT_ELSEWHERE=1` in the environment):

  ```bash
  $VENV/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml step --architect-elsewhere
  ```

  (`$VENV` is the VM's own venv outside the repo, e.g. `$HOME/venv-ws11`; never the Mac's
  `.venv`. `respond` works as in step 2.) Where a live B3 architect call is due, `step` makes
  no call and exits **5**:

  ```
  ARCHITECT PENDING B3-haiku/ep0 request 57f00338…461d role haiku
    on the Mac: cd ~/lab/worldsmith && .venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml architect
    then step here again
  ```

  No slot is used and nothing is written except `pending.json` (which names the request).
  `respond` refuses while an architect call is pending. This `step` needs no live switches.
- **Mac (owner, Terminal), once per exit 5:**

  ```bash
  cd ~/lab/worldsmith && .venv/bin/python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml architect
  ```

  It replays the stored files, makes exactly the pending call through `JzapiArchitect` (both
  switches and the headroom check as for `step`; exit 4 if refused, with no call), stores the
  reply under `<episode>/architect/replies/<request_sha>.json`, and exits 0 (`architect reply
  stored: …`). It makes no Studio call. If nothing is pending, or a reply for the pending request
  is already stored, it calls nothing and exits 0. If the request it rebuilds is not the pending
  one (the two machines are not on the same files), it sends nothing and exits 1. A provider
  error is stored (exit 1); the episode then ends CONNECTOR_ERROR at the next `step`.
- **VM again:** `step --architect-elsewhere` replays the stored reply and continues. The results are
  identical to the single-machine path (tested end to end in the simulator, and through the CLI
  with a fake jzapi session). Run one machine at a time; the pilot has at most 9 hand-offs.
- `--dry-run --architect-elsewhere` simulates this alternation (7 hand-offs with the pilot's
  fixtures; same results as the single-machine dry run).

Exit codes of `run_experiment.py`: 0 done / nothing to do / reply stored; 3 a Studio call is
pending (`respond`); **5 an architect call is pending (`architect` on the Mac)**; 4 refused (or
stopped by a failed preflight);
2 setup problem (jzapi missing); 1 other error (for `architect`: provider error stored, or a
request other than the pending one).

### 3. After the run

Commit `bundles/experiments/m2_f1a_pilot/` and `results/usage.csv`. `results.json` has, per
condition, repaired n/of, the final successes/N, the revision verdicts, attempts used and
architect calls; each episode's `summary.json` and `trace.jsonl` hold the detail. Numbers
quoted anywhere come from those files, with n.
