"""WS-01: the licence is Apache-2.0 and deps.md pins the Autopilot commit."""

import hashlib
import re

from _repo import REPO_ROOT

AUTOPILOT_SHA = "53a41f6a9c34d0434cd50f70eb528d4d74ec0e36"

# SHA-256 of the canonical Apache-2.0 text (LICENSE-2.0.txt, 202 lines, LF endings).
APACHE_2_0_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def _read(name):
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_licence_is_apache_2_0_verbatim():
    text = _read("LICENSE").replace("\r\n", "\n")
    assert text.lstrip().startswith("Apache License")
    assert "Version 2.0, January 2004" in text
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == APACHE_2_0_SHA256


def test_notice_names_the_copyright_holder():
    assert "Copyright 2026 Jehanzeb Ali" in _read("NOTICE")


def test_pyproject_declares_apache_2_0():
    assert re.search(r'^license\s*=\s*"Apache-2\.0"', _read("pyproject.toml"), re.M)


def test_deps_pins_autopilot_staging_commit():
    deps = _read("deps.md")
    assert AUTOPILOT_SHA in deps, "deps.md must record the full 40-char Autopilot SHA"
    assert "ramenvr/autopilot" in deps
    assert "staging" in deps
    assert "Apache-2.0" in deps


def test_deps_lists_context_prs_and_dev_dependencies():
    deps = _read("deps.md")
    for pr in ("#38", "#39", "#40"):
        assert pr in deps
    assert "pytest" in deps
    # Inspiration-only projects are named as NOT dependencies.
    assert "Tesseract" in deps and "Vork" in deps
