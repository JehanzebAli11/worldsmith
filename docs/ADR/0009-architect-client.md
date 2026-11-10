# ADR 0009: The architect client (inputs, strict JSON, fixtures, budget guard; WS-10)

- **Status:** accepted for WS-10 (10 Nov 2026); the owner confirms in the WS-10 HANDOFF open
  questions.
- **Context:** SPEC §7 item 6 (inputs disclosure), §11 (architect call log), §12 (B3: at most 3
  revisions, rejects count), §15 WS-10; ROADMAP §4.3 (B3 = Claude Haiku 5.5, Sonnet 5.5 and Opus
  5.5 with pinned IDs; B4 = local Gemma 4 31B later) and §4.4 (red-team architect). Code:
  `architect/prompt.py`, `client.py`, `loop.py`, `fixtures.py`; prompts `architect/prompt.md`,
  `architect/prompt_redteam.md`; config `config/architect_ladder.yaml`,
  `config/architect.example.yaml`; tests `tests/test_ws10_architect.py`.

## 1. Interface

`propose(request) -> RawResponse` for every provider. A request (`make_request(context,
prompt)`) carries the context object, the prompt, the rendered system and user texts, and two
hashes. Implementations: `FixtureArchitect` (offline replay), `JzapiArchitect` (Claude through
JzLab `jzapi`), `LocalArchitect` (B4 placeholder: interface only). The loop (`revise`,
`propose_until_valid`) is provider-agnostic and never sees a key.

## 2. Inputs (SPEC §7 item 6)

Exactly: the scene (canonical JSON) and its canonical hash; the observed event tags per attempt
and a short trace summary derived from them; the stage, its allowed ops, the bounds of the fields
those ops set, the op limit and the patch schema(s) it accepts; the revision number and the
validator verdicts (stable codes and refs, never messages) on the episode's earlier revisions.

- **Allow-list, not deny-list.** Per attempt only `attempt, outcome, tags, cause, events, detail,
  fell_in_area`, the STUCK result, and hypothesis condition flags reach the prompt. Nonces, raw
  files and their hashes, controller and config hashes, run ids, routes, Autopilot memory, Jev
  prompts and test constants never do (a test searches the rendered prompt for them).
- **Post-fix evidence is withheld.** WS-08 confirms `BRIDGE_SHORT_HYPOTHESIS` with the
  `paired_fix` condition: the same player succeeding 3/3 after the fix. That is the answer, not an
  observation. The architect sees the pre-fix view the live loop would have: `cause`
  UNCLASSIFIED, the hypothesis tag `unconfirmed`, and its other conditions
  (`fell_in_gap_area`, `geometry`) as booleans. The geometry flag is derived from the scene the
  architect already sees; the benchmark measures patch planning, not failure discovery (SPEC
  §7 item 6), and that is disclosed.
- **Bounds and schemas are given.** They are the grammar, not harness internals. Out-of-bounds
  values are still possible and still rejected.
- **Scene text is data.** Both prompts say so (ROADMAP §4.4 asset-injection work builds on it).

## 3. Hashes

- `prompt_sha`: SHA-256 of the whole prompt file (notes included); `version` in its header.
- `request_sha`: SHA-256 of the canonical JSON `{"system", "user"}` of the exact texts sent. It
  excludes the model and `max_tokens`, so one context has one hash for every provider, and it is
  the fixture key.
- `context_sha`: SHA-256 of the canonical context object.
- `response_sha`: SHA-256 of the raw reply text (UTF-8).

## 4. Strict output: one JSON object, nothing else

The reply, after stripping ASCII whitespace at both ends, must be exactly one JSON object whose
`schema` starts with `worldsmith.patch/`. **No code fence is tolerated**, and no prose before or
after. Anything else is `MALFORMED` with one reason: `EMPTY`, `CODE_FENCE`, `NOT_JSON`,
`TRAILING_TEXT`, `DUPLICATE_KEY`, `NON_FINITE`, `NOT_A_PATCH`, `TRUNCATED` (stop reason
`max_tokens`, even if the text parses) or `REFUSED` (stop reason `refusal`).

Why strict: the prompt states the contract plainly, so tolerating fences would hide a real
instruction-following difference between models behind a lenient parser, and every leniency is a
judgement call that changes the metric. The malformed rate is reported per model; if a model
fences everything, that is a finding, not something to repair silently. A `MALFORMED` reply never
reaches the validator and **counts as a rejected revision**.

A schema that names a patch version the stage does not accept (e.g. 0.2 in M0) is not malformed:
the validator judges it (`STAGE_OP_FORBIDDEN`), so the cheating counts in ROADMAP §4.4 see it.

## 5. Revisions and the call log

- One call = one revision; at most 3 per episode (`RevisionBudget`, 1..3; the ladder config
  must say 3). The slot is taken before the call, so the cap holds on calls.
- Verdicts: `ACCEPTED`, `REJECTED` (validator violations), `MALFORMED`, `ERROR` (the provider
  raised: logged, re-raised, the slot stays used, not counted as a rejection; the orchestrator
  records `CONNECTOR_ERROR`; WS-10 never retries).
- `propose_until_valid` stops at the first accepted patch; WS-11 builds and runs it and, if the
  room is still not 3/3, calls again with the new scene, the new observations and the earlier
  verdicts (`previous`), on the same budget.
- Log record (`worldsmith.architect_call/0.1`, JSONL): SPEC §11's `model_role, request_sha,
  response_sha, patch, validator_result, usage_if_reported, latency` (`latency_s`), plus
  `model_id, provider, episode, revision, max_revisions, stage, scene_hash, prompt_mode,
  prompt_version, prompt_sha, context_sha, response_text, stop_reason, request_id,
  cost_usd_if_reported, verdict, malformed, candidate_scene_hash, error`. Fixtures log
  `latency_s: null` (not measured) so replays are byte-identical. **Raw replies are evaluation
  data only, never training data** (ROADMAP §11.3).

## 6. Live calls: two switches, then jzapi's own guard

`JzapiArchitect` refuses to construct (`LiveCallRefused`), before any session or import of
jzapi, unless **both**:

1. JzLab `budgets.yaml` lists the job (`architect_ladder`, $90) with `approved: true` (a YAML
   boolean). The file is found by argument, `$JZ_BUDGETS_FILE`, the local config's
   `budgets_file`, or `../JzLab/budgets.yaml`; and
2. the git-ignored, per-machine `config/architect.yaml` exists and says `live: true` (exactly the
   boolean; `"true"`, `yes` as a string, `1` are refused). `config/architect.example.yaml` is the
   committed template with `live: false`.

jzapi then re-checks the approval and the cap (worst case reserved) before every request and logs
each call to `results/usage.csv`. Its `JobNotApproved` / `BudgetExceeded` become `LiveCallRefused`.

## 7. Models, sampling, thinking, max_tokens

`config/architect_ladder.yaml` pins `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`
(the loader refuses any other ID, extra keys, or `max_revisions` other than 3). No `temperature`,
`top_p` or `top_k` (API version 2026-10-21 rejects them; results record `sampling:
api_default`) and no thinking parameter. **Claude 5.5 models think by default and thinking tokens
count toward `max_tokens`; Opus 5.5 cannot turn thinking off.** A patch is about 100 output
tokens, so `max_tokens` (haiku 8000, sonnet 16000, opus 32000) is mostly thinking headroom. A
reply cut at the limit is `MALFORMED` / `TRUNCATED` and counts as a rejection; its rate is
reported per role so config-caused truncation stays visible, and a change of these values is a
new config hash declared before a comparison. Worst case per call (output only, jzapi prices):
sonnet $0.16, opus $0.64.

Batches: `batch_request` / `response_from_batch_item` (custom id `<role>-<request_sha[:56]>`)
for many independent first revisions (e.g. the held-out rooms); later revisions depend on earlier
verdicts and stay sequential.

## 8. Fixtures

`architect/fixtures/<name>.json` (`worldsmith.architect_fixture/0.1`): base scene, classified
episode, stage, prompt mode, `max_revisions`, and per turn the reply, its request hash and the
expected verdict (and codes or malformed reason). The replies are **hand-written, not model
outputs** (`provenance` says so). Seven F1-A M0 scenarios: a correct bridge-length fix (16), an
out-of-bounds value (65), prose, a cheat moving the goal (patch 0.2 `move goal`), a no-op (6),
reject-then-fix (prose, cheat, fix: accepted on revision 3), and exhausted (65, 6, fenced fix:
three rejections). Replay looks replies up by request hash, so any prompt or input change is a
loud `FixtureMiss`; after a deliberate change, `python scripts/architect_fixtures.py` refreshes
the hashes (`--check` in the tests). The single-reject fixtures allow one revision, which the
context states, so their first request hash differs from the three-revision ones.

## 9. Red-team prompt

`architect/prompt_redteam.md` ("win at any cost", ROADMAP §4.4) has the same slots and output
contract, `mode: redteam` in its header, and "RED-TEAM MODE. This is not a baseline." at the top
of its system text. Every call logs `prompt_mode`; red-team results are never reported as B3 and
never mixed with baseline results. Which budget job pays for red-team calls is an open question.

## Consequences

- WS-11 owns episode orchestration, bundles and storing the call log beside each run; it must
  keep `prompt_mode` apart in results and count `MALFORMED` with rejections.
- Changing the prompt, the context fields or the parser rules is a new prompt version or ADR, and
  happens before the held-out rooms are frozen and run (SPEC §12), never after seeing results.
