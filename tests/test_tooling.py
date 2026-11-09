"""WS-01: the CPU test runner and CI exist and run the same thing."""

import os

from _repo import REPO_ROOT


def test_make_test_target_runs_the_script():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\ntest:" in "\n" + makefile
    assert "scripts/test.sh" in makefile


def test_test_script_is_executable_and_uses_pytest():
    script = REPO_ROOT / "scripts" / "test.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK)
    assert "pytest" in script.read_text(encoding="utf-8")


def test_ci_runs_cpu_tests_on_both_pythons_without_secrets():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ubuntu-latest" in ci
    assert '"3.10"' in ci and '"3.12"' in ci
    assert "push" in ci and "pull_request" in ci
    assert "make test" in ci
    assert "secrets." not in ci
