# Architect smoke call (one Haiku call on F1-A at M0)

`scripts/architect_smoke.py` checks the WS-10 architect client and the JSON-only output contract
with **one** real call before any ladder run. It builds the request the WS-10 loop builds for
revision 1 of 3 on the recorded M0 F1-A bundle (`bundles/m0/F1-A/scene.json`, episode F1-A of
`bundles/m0/classification.json`): same prompt (`architect/prompt.md`), same inputs disclosure,
the bridge hypothesis shown as unconfirmed and the post-fix evidence withheld (ADR 0009 §2). Its
request hash equals the first request hash of the `f1a_m0_fix` fixture.

It is not an episode: nothing is built or played in Studio, and the result is not a B3 number.
The reply is Claude output: evaluation data only, never training data (ROADMAP §11.3).

## Owner commands (MacBook, Terminal)

```bash
cd ~/lab/worldsmith
make test                                   # creates .venv if missing, installs worldsmith, runs the tests
.venv/bin/pip install -e ~/lab/JzLab        # jzapi (and the anthropic SDK) into the worldsmith venv
.venv/bin/python scripts/architect_smoke.py --role haiku --dry-run
.venv/bin/python scripts/architect_smoke.py --role haiku
```

1. `make test` must pass first. If it says `.venv exists but its python does not run (made on
   another machine?)`, that `.venv` was made by the Linux VM: remove it yourself and run
   `make test` again.
2. The dry run needs no key and no network. It prints `prompt_sha`, `request_sha`,
   `context_sha`, `scene_hash`, jzapi's `worst_case_usd` for the call, and whether each live
   switch is ON or OFF. Check that both switches say ON.
3. Before the paid line: check that the key in `~/lab/JzLab/.env` is the account you mean to
   bill (jzapi reads it; nothing here prints it).
4. The last line makes exactly one call (job `architect_ladder`, cap $90). Paste its last three
   lines (`verdict:`, `usage:`, `bundle:`) back to the control thread.

## What the live run does

- Refuses (exit 3) unless **both** switches are on, and names each one that is off:
  JzLab `budgets.yaml` `architect_ladder` `approved: true`, and the git-ignored
  `config/architect.yaml` `live: true`. jzapi then re-checks approval and cap before sending.
- One HTTP request, no retries: the SDK client is built with `max_retries=0`, and jzapi bounds
  the prompt by its byte length (no `count_tokens` request). The worst case it reserves is the
  one the dry run prints. jzapi appends one row to `results/usage.csv`.
- Parses the reply strictly (ADR 0009 §4), runs the WS-06 validator if it parsed, and writes
  `bundles/architect_smoke/<UTC timestamp>-<role>/` (committable; it never holds the key):

| File | Holds |
| --- | --- |
| `request.json` | role, model ID, max_tokens, job, inputs and their hashes, prompt hash, request and context hashes, worst case |
| `prompt.system.txt`, `prompt.user.txt`, `context.json` | the rendered prompt exactly as sent, and its context object |
| `response.txt`, `response.json` | the raw reply; model ID, stop reason, request id, latency, response hash |
| `parse.json` | strict-parse verdict (`malformed` reason or the patch) |
| `validator.json` | the validator result, or `skipped` for a malformed reply |
| `usage.json` | reported usage, jzapi cost, worst case |
| `calls.jsonl` | the SPEC §11 call-log line (`worldsmith.architect_call/0.1`) |
| `summary.json` | verdict, codes, hashes, cost, provenance |

Exit codes: 0 dry run, or a reply received and recorded (any verdict: MALFORMED or REJECTED is
a finding, not a script failure); 1 the provider failed (ERROR recorded, nothing retried);
2 setup problem (jzapi missing, no key, folder already exists); 3 refused by a switch.
