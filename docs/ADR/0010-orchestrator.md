# ADR 0010: The orchestrator, player p0.v1, declared-before-runs and resume (WS-11)

- **Status:** accepted for WS-11 (10 Nov 2026); the owner confirms in the WS-11 HANDOFF open
  questions. Amended by ADR 0011 (10 Nov 2026): a CONNECTOR_ERROR attempt now ends the
  episode at once (§1), the optional preflight runs before every build, player p0.v2, and the
  start_pos rule in the classifier. CPU only: no Studio call, no paid call and no Autopilot run was made in WS-11.
- **Context:** SPEC §9 (frozen players, N = 3, k = 3, P2 freeze manifest), §10 (taxonomy), §11
  (loop, trace, bundles), §12 (B0–B3, caps: ≤ 12 attempts and ≤ 3 architect calls per episode),
  §15 WS-11, §16 (freeze, bounds, cancellation); ADR 0005 (relay mode), 0006 (patches), 0009
  (architect client); WS-08's open question 2 (position samples as a new labelled player).
- **Code:** `runner/episode.py` (one episode), `runner/experiment.py` (config, lock, budget, run
  order), `runner/players.py` and `runner/templates/p0_controller_v1.luau` (player versions),
  `runner/freeze.py` (P2 manifest), `runner/dryrun.py` (simulated operator),
  `scripts/run_experiment.py` (CLI), `config/experiments/m2_f1a.yaml` (the pilot); tests
  `tests/test_ws11_orchestrator.py`, `tests/_ws11sim.py`.

## 1. One episode

```
v1 = the experiment's scene -> build + read-back -> N attempts -> classify
while the last version is 0/N (FAIL) and the proposer has slots left:
    propose on the LAST ACCEPTED scene (B1 hand fix | B2 random | B3 architect)
    -> validator -> ACCEPTED: apply, rebuild from scratch, N attempts (next version)
                    REJECTED / MALFORMED: count it; same scene, same observations
-> canonical outcome summary + bundle
```

- **Revision i+1 patches the last accepted scene**, the one last built and played (WS-09 open
  question 2). A rejected proposal changes nothing but the count and the next request's
  `previous` verdicts.
- **Proposers.** B0: none (0 slots). B1: the hand-authored patch, one slot (a positive control,
  not an equal-budget competitor; SPEC §12). B2: WS-09's `make_b2_proposer(seed, stage,
  max_revisions)`; zero model calls; its records go into `proposals.json`. B3: one WS-10
  `loop.revise` per proposal with one shared `RevisionBudget(max_revisions)` per episode and the
  earlier revisions' feedback in every request. B2 and B3 have the same slots (3).
- **Stops without spending a proposal:** `0 < successes < N` is **INCONSISTENT** (SPEC §9: stop
  and debug, never lower the bar; no rerun); any HARNESS_ERROR or CONNECTOR_ERROR attempt is
  **SYSTEM_ERROR**; a build or read-back failure is **BUILD_FAILED**; an architect provider error
  (no reply) uses its slot (WS-10) and ends the episode as **CONNECTOR_ERROR**.
- **Classification:** WS-08's `taxonomy.classify_result` per attempt (observed tags + STUCK). A
  FELL carries SPEC §10's conditions a and b (in the gap area, bridge shorter than the gap) and
  leaves c (a later 3/3 bridge-length-only fix) open, so the cause stays UNCLASSIFIED, exactly
  as the architect saw M0: the first B3 request of the dry run hashes to `57f00338…`, the request
  of the live architect smoke call.
- **Metrics in `summary.json`:** `repaired` (original not 3/3, final version 3/3 after at least
  one accepted patch, status complete), `passed_original`, `passed_final`, the final version's
  successes/N, revisions used/accepted/rejected/malformed/errors, attempts used, architect calls,
  model calls, reported cost, and `canonical` (per version: scene hash, outcomes, successes/N),
  which is what a reproducibility check compares.

## 2. Caps (SPEC §12)

Declared in the config (`caps.attempts_per_episode` = N × (1 + max_revisions) = 12,
`caps.architect_calls_per_episode` = 3; the loader refuses other values) and enforced by
`EpisodeCaps` before anything is sent: the 13th play call, the 4th architect call or a 4th
proposal of any kind raises `CapExceeded`. The proposers' own budgets stop first; the caps are the
backstop (a test drives a proposer that ignores its budget into the cap). Nonces for every
possible version (1 + max_revisions) × N are fixed in `episode.json` up front.

## 3. Player p0.v1 (a new labelled player, never a silent change)

`p0.v1` = the M0 controller `p0.v0` plus position samples while alive, from the route start:
one sample at the first watch tick at least `sample_every − 2 × tick` (0.9 s) after the previous
one, so the 50 ms tick's overshoot keeps consecutive samples within `sample_every` (1 s) under
normal frame pacing. Up to 128 samples (`samples_dropped` counts the rest). The template diff
removes only four v0 lines (header, params, the watch body) and adds no MoveTo, Jump, CFrame or
LoadCharacter (tested). Controller hash `dbc1f325ce64bda8b046bc4f08a18050cced76a123b843af5937b85b65754c6d`.

`p0.v0` is untouched: same template file, same renderer (`runner.p0.render_controller`), hash
`3743d998…`; `bundles/m0/gate.json` and `bundles/m0/classification.json` still reproduce.
The taxonomy reads `samples` when a report has them (`position_samples`), so the WS-08 STUCK
rule (ε 2 studs, window 10 s, max gap 1 s) becomes evaluable on long runs; with gaps over 1 s it
stays NOT_EVALUABLE, never a guess.

An experiment declares one player (`player.version`, template, route, timing, controller hash);
every episode records it, and the runner refuses an episode folder whose version, controller
hash, T_attempt or N differs (`MixedPlayers`, `EpisodeError`). Comparing p0.v1 results with the
M0 (p0.v0) results is a comparison across players and is not made.

## 4. Declared before runs

The first time an episode of an experiment is created, the runner writes
`<out>/<experiment>/experiment.lock.json` (config SHA-256 of its bytes, creation time, player,
N), `config.yaml` (a byte copy) and `controller.luau`. From then on it refuses (`ExperimentLocked`,
CLI exit 4) if the config's hash differs: a change is a new experiment id. A dry-run folder and a
live folder are never mixed. Bundle files are written once (`write_once`); a different second
write raises `BundleConflict`. The only file that changes is `pending.json` (the relay call now
waiting).

## 5. Resume

Studio calls go through `attempts.RelayClient` (ADR 0005 §8): every request and answer is a file
under `<episode>/calls/`, answered calls are replayed and never re-sent, a stored request that
differs from the runner's call stops the run. Architect replies are written to
`<episode>/architect/replies/<request_sha>.json` the moment they arrive (`CachingArchitect`);
replays read them and pay nothing. Provider errors are stored too and replay as the same error.
The episode is deterministic given those files, so resuming is running `step` again; finished
episodes are checked (`validate_episode_bundle`) and skipped. A test interrupts the pilot dry run
inside a B3 episode, restarts with a new `Experiment`, and checks every request was paid once
(7 calls for 7 stored replies), no stored relay file changed, and the results equal an
uninterrupted run.

Known gap: a Ctrl-C **during** an architect call (after the request left, before the reply was
stored) loses that reply; the resumed run makes the call again, which may bill twice (at most
one call's worst case). A switch refusal (`LiveCallRefused`) is never stored: nothing happened.

## 6. Budget

B3 calls go through jzapi job `architect_ladder` (cap $90; approval and live switch as in ADR
0009). Before a live `step` the CLI prints the worst case of the experiment and of what is left,
and refuses unless both switches are on and the job's remaining headroom (`jzapi.Budget.remaining`:
cap − `results/usage.csv` spend − reservations) covers what is left. Worst case per call =
`jzapi.worst_case_usd(model, prompt_tokens = prompt_bytes_cap, max_tokens, cache write 5m)`; ×
max_revisions × episodes. The bound holds because a request whose jzapi byte bound exceeds
`prompt_bytes_cap` (24 000) is refused unsent (`PromptCapExceeded`); the dry run's largest request
bounds to 12 994. Live calls use the smoke script's session: SDK `max_retries=0`, prompt bounded
by bytes (one HTTP request per call). The pilot's worst case, at jzapi's price table (checked
2026-11-09): haiku $0.007000/call, sonnet $0.220000/call, opus $0.760000/call; × 3 calls each =
**$2.961000** for the whole pilot. These are ceilings, not estimates of spend.

## 7. P2 freeze manifest (code and tests only)

`runner/freeze.py`, schema `worldsmith.p2freeze/0.1`: the Autopilot commit; SHA-256 of the
harness, options, controllers, prompt, kit and initial memory snapshot (paths relative to the
repo); the LLM and Jev model versions; `evolve: false`; `memory_reset_before_each_attempt: true`;
`autopilot_share: 0`; `wall_clock_cap_s`. The manifest hash is the SHA-256 of its canonical JSON.
`check_manifest` lists every refusal: MANIFEST_MISSING, MANIFEST_INVALID, MANIFEST_HASH_MISMATCH
(declared vs computed), COMPONENT_MISSING, COMPONENT_HASH_MISMATCH (a file on disk differs),
MEMORY_SNAPSHOT_MISSING, EVOLVE_ENABLED (manifest or the harness's runtime report),
MEMORY_RESET_DISABLED, AUTOPILOT_SHARE_NOT_ZERO (manifest or environment), WALL_CLOCK_CAP_MISSING.
An experiment with `player.tier: P2` is refused with those codes, and with `P2_NOT_IMPLEMENTED`
when the manifest passes: WS-11 ships no P2 runner, and P2 needs its own ticket and approval.

## 8. Dry run

`scripts/run_experiment.py --config … --dry-run` runs the whole experiment through the relay
files with `dryrun.FakeOperator` answering each request and the WS-10 fixtures standing in for
each role (pilot: haiku `f1a_m0_fix`, sonnet `f1a_m0_reject_then_fix`, opus `f1a_m0_exhausted`),
in a temp folder; no network (a test blocks sockets), no key, no Studio. The operator's play
answers follow a stand-in rule (bridge reaches the far platform → BRIDGE_CROSSED then
GOAL_TOUCHED; else a fall at the M0 F1-A kill positions): shape, not physics. Every file says
`dry_run: true` and the disclosure "DRY RUN … Not Roblox, not a model, not a result." Its outcome
counts are not results and are not reported as such; the sample bundle is WS-12's input.

## 9. Two machines (added after review, 10 Nov 2026)

The control thread drives the Studio relay from the bridge VM, whose proxy blocks keyed Anthropic
calls; architect calls must run on the owner's Mac. Both machines see the same files.
`CachingArchitect` takes a mode (`Experiment.run(architect_mode=…)`):

- `call` (default): live calls inline, as before.
- `defer` (`step --architect-elsewhere`): where a live call is due (no stored reply or error for
  the request hash), raise `ArchitectPending` before the provider is touched. Nothing is stored
  and no slot is used (the in-memory budget is discarded with the run); `pending.json` records
  `{episode_dir, architect: {request_sha, role, condition}}`; the CLI exits 5.
- `one` (`architect`, on the Mac): replay up to the first live call, check it is the request
  named in `pending.json` (else `EpisodeError`, nothing sent), make it through `JzapiArchitect`
  with the usual switch and headroom checks, store the reply (or provider error) and raise
  `ArchitectCallDone` at once, so no Studio request is written. With a stored reply or nothing
  pending, it calls nothing.

Control-flow exceptions derive from `RunInterrupt`; `ArchitectProposer` re-raises them (never an
ERROR verdict). Determinism makes the hand-off safe: the VM's next replay rebuilds the same
request, finds the stored reply and continues; the dry run's two-machine mode equals the
single-machine results (7 hand-offs for the pilot fixtures).

## Consequences

- Live runs are relay runs: the operator session makes each Studio call by hand-off; the pilot
  needs at most 57 play calls and about 190 Studio calls in all [count from the caps, not a
  measurement].
- Per-call timeouts for `run_code`, `get_studio_mode` and `start_stop_play` remain unenforceable
  in relay mode (ADR 0005 §7).
- The hypothesis tag BRIDGE_SHORT_HYPOTHESIS is not assigned inside an episode; a post-run
  classification over the bundles (WS-08's rule) can add it.
