"""WS-01: the package imports and every SPEC section 11 folder exists."""

import importlib

import pytest

from _repo import REPO_ROOT

# SPEC section 11 code folders; each one is a subpackage of `worldsmith`.
SUBPACKAGES = ["grammar", "validator", "compiler", "runner", "architect", "baselines"]

# Every folder named in SPEC section 11 (plus the nested data folders it names).
SPEC_FOLDERS = [
    "grammar",
    "grammar/templates",
    "validator",
    "compiler",
    "runner",
    "architect",
    "architect/fixtures",
    "baselines",
    "baselines/authored",
    "scenes",
    "bundles",
    "viewer",
    "tests",
    "docs",
    "docs/ADR",
]

SPEC_DOC_FILES = ["docs/PLAN.md", "docs/TICKETS.md", "docs/HANDOFF.md", "docs/SPEC.md"]


def test_package_imports():
    pkg = importlib.import_module("worldsmith")
    assert isinstance(pkg.__version__, str) and pkg.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports_from_this_repo(name):
    mod = importlib.import_module(f"worldsmith.{name}")
    # The installed subpackage must be this repo's folder (editable install),
    # not a stale copy somewhere else.
    assert mod.__file__ is not None
    assert (REPO_ROOT / name / "__init__.py").samefile(mod.__file__)


@pytest.mark.parametrize("folder", SPEC_FOLDERS)
def test_spec_folder_exists(folder):
    assert (REPO_ROOT / folder).is_dir(), f"missing SPEC section 11 folder: {folder}/"


@pytest.mark.parametrize("path", SPEC_DOC_FILES)
def test_spec_doc_file_exists(path):
    assert (REPO_ROOT / path).is_file(), f"missing {path}"


def test_layout_decision_recorded():
    adr = REPO_ROOT / "docs" / "ADR" / "0001-layout.md"
    assert adr.is_file()
    assert "flat" in adr.read_text(encoding="utf-8").lower()
