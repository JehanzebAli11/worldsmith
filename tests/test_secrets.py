"""WS-01 / SPEC section 16 "Secrets": no key material or .env file in the repo.

Scans every file git would commit: tracked files plus untracked files that are
not ignored. The patterns are assembled from pieces so this file does not
match itself.
"""

import re
import subprocess
from pathlib import PurePosixPath

import pytest

from _repo import REPO_ROOT

_KEY_PREFIX = "sk-" + "ant-"
_ENV_NAME = "ANTHROPIC" + "_API_KEY"

SECRET_PATTERNS = [
    # Anthropic key prefix followed by key-like characters.
    re.compile(re.escape(_KEY_PREFIX) + r"[A-Za-z0-9_\-]{8,}"),
    # ANTHROPIC_API_KEY (or JZ_ANTHROPIC_API_KEY, etc.) assigned a value.
    re.compile(r"\w*" + _ENV_NAME + r"""\s*[:=]\s*["']?[A-Za-z0-9_\-]{8,}"""),
]


def _candidate_files():
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # not a git checkout
        pytest.skip(f"secret scan needs a git checkout: {exc}")
    return [p for p in out.decode("utf-8").split("\0") if p]


def _is_env_file(path):
    name = PurePosixPath(path).name
    return name == ".env" or (name.startswith(".env.") and name != ".env.example")


def test_no_env_file_is_tracked():
    offenders = [p for p in _candidate_files() if _is_env_file(p)]
    assert not offenders, f".env files must never be committed: {offenders}"


def test_no_secret_strings_in_repo_files():
    offenders = []
    for rel in _candidate_files():
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{rel}: {pattern.pattern[:30]}...")
    assert not offenders, "possible secrets found:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "sample",
    [
        _KEY_PREFIX + "api03-ABCDEFGHIJKLMNOP",
        _ENV_NAME + "=abcdefgh12345678",
        "JZ_" + _ENV_NAME + ' = "abcdefgh12345678"',
    ],
)
def test_scanner_catches_fake_secrets(sample):
    # Fake values only; proves the patterns are not dead.
    assert any(p.search(sample) for p in SECRET_PATTERNS)


@pytest.mark.parametrize("sample", [_ENV_NAME, "load " + _ENV_NAME + " from env", _ENV_NAME + "="])
def test_scanner_allows_names_without_values(sample):
    assert not any(p.search(sample) for p in SECRET_PATTERNS)


@pytest.mark.parametrize(
    "path,expected",
    [(".env", True), ("sub/.env", True), (".env.local", True), (".env.example", False), ("env.md", False)],
)
def test_env_file_rule(path, expected):
    assert _is_env_file(path) is expected


def test_gitignore_covers_env_and_venv():
    rules = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rule in (".env", ".env.*", "!.env.example", ".venv/", "__pycache__/"):
        assert rule in rules, f".gitignore is missing {rule}"
