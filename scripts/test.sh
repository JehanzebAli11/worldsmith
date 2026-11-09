#!/usr/bin/env bash
# Create or reuse a venv, install worldsmith (editable) with its dev deps, run pytest.
#   VENV=path    venv location (default: .venv in the repo)
#   PYTHON=cmd   interpreter used to create the venv (default: python3; needs >= 3.10)
# Extra arguments go to pytest, e.g. scripts/test.sh -k secrets
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${VENV:-.venv}"
PYTHON="${PYTHON:-python3}"

if [ ! -x "$VENV/bin/python" ]; then
  if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "error: $PYTHON is $("$PYTHON" -V 2>&1); worldsmith needs Python >= 3.10." >&2
    echo "       Set PYTHON=python3.12 (or similar) and retry." >&2
    exit 1
  fi
  echo "creating venv at $VENV with $("$PYTHON" -V 2>&1)"
  "$PYTHON" -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import sys' 2>/dev/null; then
  echo "error: $VENV exists but its python does not run (made on another machine?)." >&2
  echo "       Remove it or set VENV=<other path>, then retry." >&2
  exit 1
fi

# Reinstall only when pyproject.toml changed since the last install.
STAMP="$VENV/.worldsmith-installed"
if [ ! -f "$STAMP" ] || [ pyproject.toml -nt "$STAMP" ]; then
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -e ".[dev]"
  touch "$STAMP"
fi

exec "$VENV/bin/python" -m pytest "$@"
