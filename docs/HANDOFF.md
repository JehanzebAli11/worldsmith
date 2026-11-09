# Handoff

One entry per session, newest last: **Done** · **Verified** · **Next** · **Open questions**.

## 2026-11-09 18:41 PKT · WS-01

**Done**
- Committed `docs/SPEC.md` unchanged (the owner's build plan).
- Licence Apache-2.0 (owner decision, 9 Nov): `LICENSE` is the canonical Apache-2.0 text,
  copied from `../autopilot/LICENSE` (apache.org was blocked by the network proxy). Verified as
  canonical: SHA-256 `cfc7749b…3d30`, 202 lines, and lines 1–177 identical to the
  `LICENSE.APACHE` shipped in the PyPI sdist of `packaging` 24.2 (the rest is the standard
  appendix). `NOTICE`: Copyright 2026 Jehanzeb Ali. `pyproject.toml`: `license = "Apache-2.0"`.
- `deps.md`: Autopilot pinned to `ramenvr/autopilot` `staging` @
  `53a41f6a9c34d0434cd50f70eb528d4d74ec0e36` (PR #40 merge; verified by the control thread on
  9 Nov), PRs #38 and #39 as context, external dependency only (nothing vendored), dev
  dependencies with versions and licences, Mirage Tesseract and Vork listed as not dependencies.
- Skeleton per SPEC §11 as the installable package `worldsmith` (flat layout, root `__init__.py`,
  subpackages `grammar`, `validator`, `compiler`, `runner`, `architect`, `baselines`; data folders
  with `.gitkeep`). Choice and trade-offs in `docs/ADR/0001-layout.md`.
- CPU runner: `make test` → `scripts/test.sh` (venv at `$VENV`, default `.venv`; editable install
  with `.[dev]`; pytest). CI: `.github/workflows/ci.yml`, `make test` on ubuntu-latest, Python
  3.10 and 3.12, on push and pull_request, no secrets.
- `.gitignore`, `README.md` (summary, honest limits, status, how to test), `docs/TICKETS.md`
  (SPEC §15 table + Status), `docs/PLAN.md` (pointer to SPEC; SPEC §11/§15 name a PLAN.md).
- Tests (written first; at commit `d19dd29` they ran 33 failed, 16 passed): package and
  subpackage imports, every §11 folder, licence text hash, NOTICE, deps pin, secret scan over all
  files git would commit (`sk-ant-…`-style keys, `*ANTHROPIC_API_KEY=` with a value, any tracked
  `.env`/`.env.*` except `.env.example`), `.gitignore` rules, Makefile/script/CI shape.

**Verified** (Linux VM on the Mac bridge, Python 3.10.12, venv outside the repo)
- `VENV=$HOME/tmp/wsvenv make test` in the working tree → `49 passed in 0.07s`
- Fresh `git clone` of the repo + fresh venv, `VENV=… make test` → `49 passed in 0.03s`
  (HANDOFF.md copied in, since it is committed after this run)
- Not verified: Python 3.12 (not installed here; CI covers it once pushed), macOS run of
  `make test`, and the CI workflow itself (nothing pushed).

**Next**
- WS-02: complete F1 scene schema, `grammar/bounds.yaml`, canonical hash, valid F1-A/F1-E files
  (round-trip and hash-stability tests; unknown keys rejected). It will add the first runtime
  dependencies (likely a JSON Schema validator and a YAML parser): record them in `deps.md`.

**Open questions**
- `evolve` (PR #39): listed in `deps.md` only as context with no authorship claim. Confirm the
  credit line before any public text mentions it (ROADMAP §12).
- On the Mac, `python3` from the Xcode command-line tools is 3.9; `scripts/test.sh` refuses it
  with a clear message. Use `PYTHON=python3.12 make test` if needed.
