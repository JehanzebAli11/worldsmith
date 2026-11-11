# ADR 0014: Experiment suites (one declared file, one part per scene) and `m2_hard`

- **Status:** **accepted** (owner, 11 Nov 2026, after the live gate of ADR 0013 passed): the suite
  format, `prompt_bytes_cap` 32000, and B0/B1 at 1 episode per part (below). `m2_hard` is declared,
  **not run**. The owner approves its worst case before any paid call.
- **Context:** ADR 0010 (orchestrator: one experiment = one scene, declared before runs, locked to
  its config's hash), ADR 0013 (the WS-14 fixtures). WS-14 asks for one declared config,
  `config/experiments/m2_hard.yaml`, over F1-A at M1 plus every fixture that passed the hardness
  test, with conditions B0, B1, B2, B3-haiku, B3-sonnet and B3-opus.
- **Code:** `runner/experiment.py` (`SUITE_SCHEMA`, `suite_parts`, `load_suite`,
  `load_experiment(..., part=)`, `suite_worst_case`), `scripts/run_experiment.py` (`--part`);
  tests `tests/test_ws14_m2_hard.py`.

## Decision

A **suite** (`schema: worldsmith.experiment_suite/0.1`) has exactly `suite` (an id), `common`
(every experiment field except `schema`, `experiment`, `scene`, `conditions`) and `parts` (each
exactly `experiment`, `scene`, `conditions`). Part ids start with `<suite>_`, are unique, and no two
parts share a scene file. `load_experiment(path, part=<id>)` builds
`{schema: worldsmith.experiment/0.1, experiment: <id>, **common, scene, conditions}` and runs the
unchanged ADR 0010 checks on it; `_sha256` (and the lock) is the hash of the **whole suite file**,
so editing any part, or `common`, after any part has started is refused like any changed config.
A suite without `part`, an unknown part, or `part` on a single experiment is an
`ExperimentError`. Each part runs as its own experiment folder
(`bundles/experiments/<part id>/`), with the same `step` / `respond` / `architect` / `status` loop;
every CLI command takes `--part <id>`, and the commands it prints for the owner include it.

The runner, the episode, the bundle format and every existing config are unchanged (the single-
experiment path is the same function body; all earlier tests pass).

## `m2_hard`

| Item | Value |
| --- | --- |
| Parts (run in this order) | `m2_hard_f1a` (F1-A, `92f86609…`), `m2_hard_f1b` (F1-B, `2b54bbe7…`), `m2_hard_f1c` (F1-C, `e916fde2…`), `m2_hard_f1d` (F1-D, `16d226d1…`) |
| Stage | M1 (ADR 0007) |
| Player | p0.v3, controller `cd9a7a0a…`, route `b9514a4b…`, T_attempt 60 s (as `m2_f1a_b3`) |
| N, k, revisions, caps | 3, 3/3, 3, 12 attempts and 3 architect calls per episode |
| Conditions and episodes | B0 none, **1** · B1 hand fix (F1-A: `m0_fix.json`; others: `m1_fix.json`), **1** · B2 random valid, seeds 0, 1, 2, 3 · B3-haiku, 3 · B3-sonnet, 3 · B3-opus, 3 (baseline prompt `df1b7a12…`) |
| Architect | ladder `14219e3e…`, job `architect_ladder`, **prompt_bytes_cap 32000** |
| Dry-run fixtures | the WS-14 M1 replies, `architect/fixtures/m1/` (one per part per role) |
| Everything else | equal to `m2_f1a_b3.yaml` (tested field by field) |

**Why the prompt cap rises (24000 to 32000).** M1 requests carry more allowed ops, bounds and
patch schemas than M0: a first revision at M1 bounds to about 19 000 bytes against 11 713 for the
recorded M0 calls (`m2_f1a_b3` proposals.json; the M1 figure was computed while writing this, with three
synthetic STUCK attempts). A pessimistic third revision (two rejected 3-op
revisions with six violations each, three STUCK attempts) bounds to more than 24 000 on F1-D
(tested). A request over the cap is refused unsent, which stops the run, so the cap must cover it.

**Episodes per condition: 3 for B2 and B3; 1 for B0 and B1** (owner, 11 Nov 2026; first declared
with 3 each and edited before any part ran, so no folder or lock existed). B0 and B1 rooms are
deterministic (the original scene, one fixed hand patch, no proposer choice), the live gate already
played each broken scene and each hand-fixed room 3/3 (`bundles/experiments/gate_*`), and one
episode each saves Studio time. B0 and B1 make no architect call, so the worst case is unchanged.
Worst case by ADR 0010 §6 (per call, the jzapi worst case of a
request at `prompt_bytes_cap` and the role's `max_tokens`, cache write 5m; × max_revisions 3 ×
episodes):

| Role | max_tokens | Per call | Calls per part | Per part | 4 parts |
| --- | --- | --- | --- | --- | --- |
| haiku | 8000 | $0.008 | 9 | $0.072 | $0.288 |
| sonnet | 16000 | $0.240 | 9 | $2.160 | $8.640 |
| opus | 32000 | $0.800 | 9 | $7.200 | $28.800 |
| **Total** | | | **27** | **$9.432** | **$37.728 (108 calls)** |

That is 42 % of the job's headroom ($90 cap, $0.04 spent: about $89.96), so 3 episodes fit
comfortably; the actual spend on F1-A was $0.037481 for 3 calls (HANDOFF 10 Nov). The per-call
figures use per-token rates derived from the repo's recorded jzapi figures (haiku $0.007000,
sonnet $0.220000, opus $0.760000 per call at 24 000 prompt tokens, ADR 0010 §6; the ladder's output
rates sonnet $10/M, opus $20/M; haiku $0.005464 at 11 715 tokens, HANDOFF WS-10), because jzapi is
not installed on the machine that wrote this. Before approval, `plan --part <id>` on the Mac prints
jzapi's own figure for each part; if it differs, jzapi's figure is the one to approve.

**Studio time (a count from the caps, not a measurement).** At most N = 3 play calls per B0
episode and 12 (the attempt cap) per other episode: per part 3 (B0) + 12 (B1) + 4 × 36 (B2, B3) =
159, 636 in all (189 and 756 with 3 B0/B1 episodes), plus builds and preflights. As declared, B2's
seeds 0, 1, 2 propose only rejected patches on every part (36 of 36, the hardness record and the
dry run), so B2 will play v1 only: 3 plays per episode, 9 per part. M0's
relay took about a minute per play call (docs/EXPERIMENTS.md), and a STUCK attempt in F1-C or F1-D
runs about 46 to 48 s in play (ADR 0013), so a full run is many hours of relayed Studio time.

**Dry run.** `--dry-run` rehearses every condition of every part (owner decision 11 Nov 2026,
HANDOFF open question 5). FakeOperator decides plays by stand-in rules on the scene's geometry
(lever zone reachable past walls, the gate shut until then, the route over the bridge deck), which
reproduce the live gate's outcomes and the architect's view of every attempt on the six gate
scenes and leave F1-A/F1-E byte-identical; the B3 replies are hand-written M1 fixtures
(`architect/fixtures/m1/`) whose observations are the live gate's bundles (F1-B/C/D) or M0's F1-A
classification. What that proves and does not prove: docs/EXPERIMENTS.md. Not a result.

## Alternatives considered

- **Four config files** (`m2_hard_f1a.yaml` …): no runner change, but four hashes to declare and
  approve, and nothing ties them together; the ticket asks for one declared config.
- **A multi-scene experiment** (`scenes:` list inside one experiment): one lock and one results
  file, but the episode folder layout, results, status, dry run and worst case would all change;
  a larger runner change for the same declaration.
- **Keep prompt_bytes_cap 24000:** the cheaper worst case ($35.532), but a third revision could be
  refused unsent and stop the run mid-experiment.

## Consequences

- A suite's parts can run, stop and resume independently; results stay per part
  (`bundles/experiments/<part>/results.json`), never pooled across scenes without saying so.
- After any part starts, the suite file is frozen for every part.
- Before m2_hard runs: each fixture's live gate (ADR 0013; **passed** 11 Nov 2026) and the
  owner's approval of the worst case after `plan --part` prints jzapi's figure on the Mac.
