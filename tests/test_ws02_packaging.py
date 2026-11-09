"""WS-02: runtime deps are pinned and recorded, grammar data ships with the package,
and the scene-schema decisions are recorded in an ADR."""

import importlib.resources
import re

from _repo import REPO_ROOT


def _pyproject():
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_runtime_dependencies_are_pinned():
    text = _pyproject()
    deps = re.search(r"^dependencies = \[(.*?)\]", text, re.S | re.M).group(1)
    assert '"jsonschema==' in deps
    assert '"PyYAML==' in deps


def test_grammar_data_is_declared_as_package_data():
    text = _pyproject()
    assert "[tool.setuptools.package-data]" in text
    block = text.split("[tool.setuptools.package-data]", 1)[1]
    assert re.search(r'"worldsmith\.grammar"\s*=\s*\[[^\]]*"schema\.json"', block)
    assert re.search(r'"worldsmith\.grammar"\s*=\s*\[[^\]]*"bounds\.yaml"', block)


def test_grammar_data_resolves_through_importlib_resources():
    files = importlib.resources.files("worldsmith.grammar")
    assert files.joinpath("schema.json").is_file()
    assert files.joinpath("bounds.yaml").is_file()


def test_deps_md_records_runtime_deps_with_licences():
    deps = (REPO_ROOT / "deps.md").read_text(encoding="utf-8")
    section = deps.split("## Python runtime dependencies", 1)[1].split("\n## ", 1)[0]
    for name in ["jsonschema", "PyYAML", "referencing", "jsonschema-specifications",
                 "rpds-py", "attrs"]:
        assert name in section, name
    assert "MIT" in section
    assert "None at WS-01." not in section


def test_adr_0002_records_the_scene_schema_choices():
    adr = (REPO_ROOT / "docs" / "ADR" / "0002-scene-schema.md").read_text(encoding="utf-8")
    low = adr.lower()
    for needle in ["3 decimals", "^[a-z][a-z0-9_]{0,31}$", "bounds.yaml", "ws-06", "-0"]:
        assert needle in low, needle
