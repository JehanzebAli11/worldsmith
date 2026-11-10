# Tickets

Copied from `docs/SPEC.md` §15 (the SPEC is the source; if they differ, the SPEC wins).
Fresh session per ticket: read `docs/PLAN.md`, the ticket and `docs/HANDOFF.md`; write the tests
first; implement; run the CPU tests (plus the live check if the ticket says so); append a
HANDOFF entry; stop.

| ID | Title | Depends | Acceptance | Status |
|---|---|---|---|---|
| WS-01 | Skeleton, licence notes, pin the Autopilot staging commit | — | Tests run. `deps.md` records the commit and licences. | done |
| WS-02 | Complete F1 scene schema, bounds, canonical hash, valid F1-A/F1-E files | 01 | Round-trip and hash-stability tests. Unknown keys are rejected. The fixtures validate. | done |
| WS-03 | Template compiler + Edit-mode rebuild + read-back + event templates | 02 | Luau snapshot tests. **Live:** F1-A builds and read-back matches. | done (live read-back 0 differences, 9 Nov; HANDOFF) |
| WS-04 | P0 controller (route frozen per family) + attempt runner + bounded polling | 03 | **Live:** the M0 gate in §14 (3/3 each, same controller hash). Polling and timeouts are logged. | done (M0 PASS, 9 Nov: F1-A 3/3 FELL in gap_1, fix 3/3 SUCCESS, controller `3743d998…`; docs/M0.md) |
| WS-05 | Automated patch apply (same bridge-only restriction as M0) + base-hash check | 02 | A stale base is rejected. Any other op is rejected in M0. | done (CPU, 9 Nov: `validator/patch.py`, ADR 0006; the M0 fix patch reproduces `fix.json` with version 2, hash `297b23c8…`; HANDOFF) |
| WS-06 | Validator: schema, protection, locked gap, difficulty, structure | 05 | The §16 abuse suite is all rejected and legit patches pass | done (CPU, 9 Nov: `validator/validate.py`, 28 stable codes in `docs/VALIDATOR.md`; 69 abuse cases rejected with their expected codes, 13 legit cases pass; M1 stage + `worldsmith.patch/0.2`, ADR 0007; HANDOFF) |
| WS-07 | Navigation approximation (`GRAPH_CONNECTED/DISCONNECTED`) | 06 | Fixture tests. Documented as an approximation, never a proof. | done (CPU, 9 Nov: `validator/nav.py`, ADR 0008; walk F1-A DISCONNECTED, fix and F1-E CONNECTED; jump labelled derived; warning `GRAPH_DISCONNECTED_WALK`, never a rejection; HANDOFF) |
| WS-08 | Event classifier + hypothesis rules (§10) | 04 | Recorded traces classified correctly. Unsupported cause → UNCLASSIFIED. | done (CPU, 9 Nov: `runner/taxonomy.py`, `docs/CLASSIFIER.md`; `bundles/m0/classification.json`: F1-A 3/3 FELL with cause BRIDGE_SHORT_HYPOTHESIS, evidence `bundles/m0/F1-A-fix` 3/3 SUCCESS; STUCK NOT_EVALUABLE on sparse samples; HANDOFF) |
| WS-09 | Capped random valid sampler | 06 | ≤3 raw proposals including rejects. Seeded. | done (CPU, 10 Nov: `baselines/random_valid.py`, B2 = uniform ops from the stage set, values uniform in `bounds.yaml`, every raw proposal validated once and counted; SHA-256 seed derivation, replayable records; WS-11 hook `make_b2_proposer`; offline validator/graph-only statistic (not a play result) `bundles/b2_offline/summary.json`; HANDOFF) |
| WS-10 | Architect client: JSON-only output, fixture mode, budget guard | 06,08 | Fixture replay is deterministic. A malformed output counts as a rejected revision. No live call without approval config. | done (CPU, 10 Nov: `architect/` prompt.md + prompt_redteam.md (hashed), client.py (`propose(request)`; Fixture, Jzapi, Local placeholder), loop.py (strict one-object JSON, MALFORMED counts as a rejection, 3-revision budget, SPEC §11 call log); 7 hand-written F1-A M0 fixtures replay byte-identically; live needs `budgets.yaml` approval AND git-ignored `config/architect.yaml` `live: true`; no live call made; ADR 0009; HANDOFF) |
| WS-11 | Orchestrator + bundles + P2 freeze manifest check | 04,08,09,10 | Episode caps enforced (12 attempts, 3 architect calls). P2 refuses to run on a manifest-hash mismatch or with `evolve` enabled. Resumable. | done (CPU, 10 Nov: `runner/episode.py`, `experiment.py`, `freeze.py`, `players.py`, `dryrun.py`, `scripts/run_experiment.py`, ADR 0010, `docs/EXPERIMENTS.md`; caps 12/3 enforced before sending; player p0.v1 (controller `dbc1f325…`, samples every 1 s; p0.v0 untouched); declared-before-runs lock; relay + stored-reply resume; pilot `config/experiments/m2_f1a.yaml` worst case $2.961; P2 manifest refusals, no Autopilot run; no Studio or paid call made; HANDOFF) |
| WS-12 | Static bundle viewer | 11 | Renders the sample bundle. No secrets. | todo |
| WS-13 | Release: README, limits card, failure gallery, reproduce script | 11,12 | A clean clone reproduces the CPU tests and the recorded summaries | todo |
| WS-14 | Harder F1 rooms: new broken fixtures, a pre-declared hardness test, their live gate and the experiment config | 06,07,09,11 | See the WS-14 note below. CPU parts first; live gate in Studio; no paid call until the owner approves the declared config. | todo |

**Note (10 Nov 2026, after WS-11; ADR 0011):** the first live p0.v1 smoke (`smoke_f1e_p0v1`) ran in
the Autopilot test place while an Autopilot bench run drove Studio; its bundle is closed
(CONNECTOR_ERROR) and marked CONTAMINATED. Harness fixes, CPU only: preflight before every build
(place marker `ws_place`, character settings, one outside spawn, no foreign scripts, mode stop),
player p0.v2 (`a7472c9c…`, spawn check before the route), start_pos > 2 studs → HARNESS_ERROR for
every player, a CONNECTOR_ERROR attempt stops the episode, a failed preflight stops the
experiment. New smoke `config/experiments/smoke_f1e_p0v2.yaml`; the pilot `m2_f1a.yaml` (never
ran) moved to p0.v2 + preflight. The re-run needs the owner's one-time place marker first.

**WS-14 (added 11 Nov 2026, owner go-ahead).** Why: on F1-A every model found the reference fix
on the first call, and B2 (random valid patch, M0 stage) also repaired it; F1-A cannot separate
architects (HANDOFF, m2_f1a_b3). The offline B2 statistic already shows the M1 op set is far
harder to hit by chance than M0 on the same room (`bundles/b2_offline/summary.json`: episodes
with an accepted, walk-connected patch 16/200 at M0 vs 1/200 at M1).
Acceptance, all declared before any live or paid run:
1. **Three new broken F1 fixtures** (`scenes/F1-B`, `F1-C`, `F1-D`), each with one hand-authored
   fix patch that the **M1 stage** accepts, its defect known by construction and written in a new
   ADR. At least two fixes must not be `set bridge_1.length`, and at least one must need 2 or 3 ops.
   Locked F1 rules stay (gap width, platforms, protected spawn/goal, kill floor). The player and
   its frozen route do not change (p0.v3, same 9 waypoints): if a defect cannot be shown with the
   frozen route, drop that fixture rather than change the player.
2. **Offline hardness (no play):** for each broken fixture, B2 at M1 over 200 seeded episodes x 3
   proposals (the `b2_offline` method and seeds): the share of episodes with an accepted,
   walk-connected patch is **at most 5%**; the broken scene is walk-DISCONNECTED and its hand fix
   ACCEPTED and walk-CONNECTED (ADR 0008: an approximation, never a proof). A fixture that misses
   is redesigned or dropped, never re-scored. Write `bundles/hardness/summary.json`.
3. **Live gate (Studio, p0.v3, N 3, k 3, read-back 0 differences):** each broken fixture fails
   3/3 with its expected event tag; each hand fix succeeds 3/3. Same rule as M0: an inconsistent
   result means stop and debug, never lower the bar.
4. **Experiment config** `config/experiments/m2_hard.yaml`, declared and locked before running:
   F1-A at M1 plus every fixture that passed 2 and 3; conditions B0, B1 (hand fix), B2 (M1),
   B3-haiku, B3-sonnet, B3-opus; episodes per condition and the worst-case Claude API cost
   (job `architect_ladder`, ROADMAP §9: $90 approved, $0.04 spent). No paid call until the owner
   approves that worst case.

