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
| WS-04 | P0 controller (route frozen per family) + attempt runner + bounded polling | 03 | **Live:** the M0 gate in §14 (3/3 each, same controller hash). Polling and timeouts are logged. | todo |
| WS-05 | Automated patch apply (same bridge-only restriction as M0) + base-hash check | 02 | A stale base is rejected. Any other op is rejected in M0. | todo |
| WS-06 | Validator: schema, protection, locked gap, difficulty, structure | 05 | The §16 abuse suite is all rejected and legit patches pass | todo |
| WS-07 | Navigation approximation (`GRAPH_CONNECTED/DISCONNECTED`) | 06 | Fixture tests. Documented as an approximation, never a proof. | todo |
| WS-08 | Event classifier + hypothesis rules (§10) | 04 | Recorded traces classified correctly. Unsupported cause → UNCLASSIFIED. | todo |
| WS-09 | Capped random valid sampler | 06 | ≤3 raw proposals including rejects. Seeded. | todo |
| WS-10 | Architect client: JSON-only output, fixture mode, budget guard | 06,08 | Fixture replay is deterministic. A malformed output counts as a rejected revision. No live call without approval config. | todo |
| WS-11 | Orchestrator + bundles + P2 freeze manifest check | 04,08,09,10 | Episode caps enforced (12 attempts, 3 architect calls). P2 refuses to run on a manifest-hash mismatch or with `evolve` enabled. Resumable. | todo |
| WS-12 | Static bundle viewer | 11 | Renders the sample bundle. No secrets. | todo |
| WS-13 | Release: README, limits card, failure gallery, reproduce script | 11,12 | A clean clone reproduces the CPU tests and the recorded summaries | todo |
