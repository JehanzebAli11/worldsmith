# ADR 0001: Flat package layout

- **Status:** accepted (WS-01, 9 Nov 2026)
- **Context:** SPEC §11 puts `grammar/`, `validator/`, `compiler/`, `runner/`, `architect/`,
  `baselines/`, `scenes/`, `bundles/`, `viewer/`, `tests/` and `docs/` at the top of the
  repository. The code also has to be an installable Python package named `worldsmith`
  (Python >= 3.10).

## Decision

**Flat layout.** The SPEC §11 folders stay exactly where the SPEC puts them, at the repository root.
The repository root *is* the `worldsmith` package:

- `__init__.py` at the root defines `worldsmith` (and `__version__`).
- The code folders are its subpackages: `worldsmith.grammar`, `worldsmith.validator`,
  `worldsmith.compiler`, `worldsmith.runner`, `worldsmith.architect`, `worldsmith.baselines`.
- `pyproject.toml` maps this with `[tool.setuptools.package-dir] "worldsmith" = "."` and an
  explicit `packages` list. Data folders (`scenes/`, `bundles/`, `viewer/`, `tests/`, `docs/`) are
  not packages and are not shipped in the wheel.
- Tests run against an editable install (`pip install -e ".[dev]"`, done by `scripts/test.sh`);
  `tests/test_skeleton.py` checks that each `worldsmith.<sub>` resolves to this repo's folder.

## Alternatives considered

- **src layout** (`src/worldsmith/grammar/...`): the PyPA default and it prevents accidental
  imports from the working tree, but it moves the SPEC §11 folders under `src/worldsmith/`, so the
  tree no longer matches the SPEC that every ticket refers to.
- **Plain flat layout with top-level packages** (`import validator`): matches the SPEC tree, but
  installs generic names (`validator`, `runner`) as top-level modules, which can collide with other
  packages, and gives no `worldsmith` import name.

## Consequences

- Any new `.py` file placed at the repository root becomes a `worldsmith.<name>` module. Keep the
  root for the package `__init__.py` only; scripts go in `scripts/`.
- A new code folder must be added to the `packages` list in `pyproject.toml`.
- Non-Python files a subpackage needs at runtime (for example `grammar/templates/*.luau`,
  `grammar/bounds.yaml` in WS-02/WS-03) must be declared as package data when they are added.
- `tests/` has no `__init__.py`, on purpose: pytest imports the test modules by file name, so they
  cannot shadow the installed package.
