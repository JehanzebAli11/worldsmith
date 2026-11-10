"""scripts/architect_smoke.py: one architect call on F1-A at M0, as the WS-10 loop would make it.

The script builds the request from the recorded M0 F1-A bundle, prints the prompt hash, the
request hash and jzapi's worst-case cost, and (without --dry-run) makes exactly one call through
`JzapiArchitect` (job architect_ladder), parses it strictly, validates it and writes
`bundles/architect_smoke/<UTC>-<role>/`.

Offline only: no network, no real key, no real config. The live path runs against jzapi's own
fake client (`jzapi.tests.fakes`) in a temporary repo, so nothing is billed or logged for real.
jzapi comes from the venv (`pip install -e ~/lab/JzLab`) or the JzLab checkout beside this repo;
the tests that need it skip when neither is there.
"""

import importlib.util
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from _repo import REPO_ROOT
from worldsmith.architect import client as cl
from worldsmith.architect import prompt as pr

SCRIPT = REPO_ROOT / "scripts" / "architect_smoke.py"
JZLAB = REPO_ROOT.parent / "JzLab"
FIX_FIXTURE = REPO_ROOT / "architect" / "fixtures" / "f1a_m0_fix.json"
F1A_HASH = "92f866098bfb850cf4c464179887a862f305c143c8e0b38efdc7978493cddb1d"
KEY_PREFIX = "sk-" + "ant-"                       # split so the repo secret scan never matches here
FAKE_KEY = KEY_PREFIX + "test-FAKEKEY-a11-0123456789abcdef-never-real"
NOW = datetime(2026, 11, 10, 9, 30, 0, tzinfo=timezone.utc)
STAMP_DIR = "20261110T093000Z-haiku"

try:
    import jzapi  # noqa: F401
except ImportError:
    if (JZLAB / "jzapi" / "__init__.py").is_file():
        sys.path.insert(0, str(JZLAB))
        import jzapi  # noqa: F401
    else:
        jzapi = None

needs_jzapi = pytest.mark.skipif(jzapi is None, reason="jzapi is not installed and JzLab is not beside worldsmith")


def load_script():
    spec = importlib.util.spec_from_file_location("architect_smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smoke():
    return load_script()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No network and no real key in any test here."""
    def refuse(*a, **k):
        raise RuntimeError("network is disabled in the architect smoke tests")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.delenv("JZ_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JZ_BUDGETS_FILE", raising=False)
    monkeypatch.delenv("JZ_REPO_DIR", raising=False)
    monkeypatch.setenv("JZ_ENV_FILE", str(tmp_path / "no-such.env"))


def budgets(tmp_path, approved=True):
    p = tmp_path / "budgets.yaml"
    p.write_text(yaml.safe_dump({"total_usd": 400, "jobs": {
        "architect_ladder": {"cap_usd": 90, "approved": approved, "spend_row": "Architect ladder"}}}))
    return p


def local_cfg(tmp_path, live=True):
    p = tmp_path / "architect.yaml"
    p.write_text(yaml.safe_dump({"schema": "worldsmith.architect_local/0.1", "live": live,
                                 "local": {"endpoint": None, "model": None}}))
    return p


def patch_text(value=16):
    return json.dumps({"schema": "worldsmith.patch/0.1", "base_hash": F1A_HASH,
                       "ops": [{"op": "set", "id": "bridge_1", "field": "length", "value": value}]})


def args(tmp_path, *extra, role="haiku", live=True, approved=True):
    return ["--role", role, "--config", str(local_cfg(tmp_path, live)),
            "--budgets", str(budgets(tmp_path, approved)), "--out-root", str(tmp_path / "out"), *extra]


class FakeFactory:
    """jzapi.Session over jzapi's fake SDK client, in a temporary repo (usage.csv lands there)."""

    def __init__(self, tmp_path, *, text=None, stop_reason="end_turn", raise_on_create=None, secret=False):
        from jzapi.tests.fakes import FakeClient, message
        self.client = FakeClient()
        msg = message(text=patch_text() if text is None else text, model="claude-haiku-5-5", inp=1200, out=900)
        msg.stop_reason = stop_reason
        self.client.next_message = msg
        self.client.raise_on_create = raise_on_create
        self.repo = tmp_path / "fake-repo"
        (self.repo / ".git").mkdir(parents=True, exist_ok=True)
        self.secret = secret
        self.made = []

    def __call__(self, job, **kw):
        import jzapi as jz
        kw = dict(kw, repo_dir=self.repo)
        if self.secret:
            kw["secret"] = jz.load_api_key()
        s = jz.Session(job, client=self.client, token_estimate="bytes", **kw)
        self.made.append((job, kw, s))
        return s


def run(smoke, argv, capsys, **kw):
    code = smoke.main(argv, now=lambda: NOW, **kw)
    return code, capsys.readouterr()


def bundle_files(root):
    return sorted(p for p in root.rglob("*") if p.is_file())


# --------------------------------------------------------------------------- dry run

@needs_jzapi
def test_dry_run_prints_hashes_and_worst_case_and_writes_nothing(smoke, tmp_path, capsys):
    code, out = run(smoke, args(tmp_path, "--dry-run"), capsys)
    assert code == 0, out.err
    p = pr.load_prompt("baseline")
    fixture_first = json.loads(FIX_FIXTURE.read_text())["turns"][0]["request_sha"]
    assert f"prompt_sha: {p.sha256}" in out.out
    assert f"request_sha: {fixture_first}" in out.out
    assert "worst_case_usd: " in out.out
    assert "dry run: no call made" in out.out
    assert not (tmp_path / "out").exists()


@needs_jzapi
def test_dry_run_needs_no_key_and_no_network_even_with_both_switches_off(smoke, tmp_path, capsys):
    code, out = run(smoke, args(tmp_path, "--dry-run", live=False, approved=False), capsys)
    assert code == 0
    assert "OFF" in out.out and "dry run: no call made" in out.out


def test_request_is_the_ws10_loop_request_for_f1a_at_m0(smoke):
    built = smoke.build_request("haiku")
    req = built.request
    # The same request the WS-10 loop builds for revision 1 of 3 (the fix fixture's first turn).
    assert req.request_sha == json.loads(FIX_FIXTURE.read_text())["turns"][0]["request_sha"]
    assert req.context["stage"] == "M0" and req.context["scene_hash"] == F1A_HASH
    assert req.context["revision"] == {"this": 1, "max": 3} and req.context["previous"] == []
    assert req.prompt.mode == "baseline" and req.prompt.sha256 == pr.load_prompt("baseline").sha256
    # Inputs disclosure: the hypothesis is shown unconfirmed, the post-fix evidence withheld.
    att = req.context["observed"]["attempts"][0]
    assert att["cause"] == "UNCLASSIFIED" and att["hypothesis"]["status"] == "unconfirmed"
    assert "paired_fix" not in req.user and "F1-A-fix" not in req.user
    assert built.inputs["scene"]["path"] == "bundles/m0/F1-A/scene.json"
    assert built.inputs["classification"] == {**built.inputs["classification"],
                                              "path": "bundles/m0/classification.json", "episode": "F1-A"}


@pytest.mark.parametrize("role", ["haiku", "sonnet", "opus"])
def test_params_are_exactly_what_jzapi_architect_sends(smoke, tmp_path, role):
    class S:
        def message(self, **kw):
            raise AssertionError("no call here")
    arch = cl.JzapiArchitect(role, config_path=local_cfg(tmp_path), budgets_path=budgets(tmp_path),
                             session_factory=lambda *a, **k: S())
    built = smoke.build_request(role)
    assert built.params == arch._params(built.request)
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(built.params)


@needs_jzapi
@pytest.mark.parametrize("role", ["haiku", "sonnet", "opus"])
def test_worst_case_is_what_jzapi_reserves_for_the_call(smoke, tmp_path, role):
    import jzapi as jz
    built = smoke.build_request(role)
    wc = smoke.worst_case(built.params)
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    s = jz.Session("architect_ladder", repo_dir=repo, budgets_path=budgets(tmp_path), token_estimate="bytes",
                   client=object())
    params = jz.build_params(model=built.params["model"], max_tokens=built.params["max_tokens"],
                             system=built.params["system"], user=built.params["user"],
                             cache_system=built.params["cache_system"])
    assert wc["usd"] == pytest.approx(s.worst_case(params, batch=False))
    assert wc["usd"] > 0 and wc["prompt_tokens_upper_bound"] > 0


def test_missing_jzapi_is_a_clear_error(smoke, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "jzapi", None)
    code, out = run(smoke, args(tmp_path, "--dry-run"), capsys)
    assert code == 2
    assert "request_sha: " in out.out                  # the hashes still print
    assert "pip install -e ~/lab/JzLab" in out.err


def test_unknown_role_is_refused(smoke, tmp_path):
    with pytest.raises(SystemExit) as e:
        smoke.main(["--role", "gpt", "--dry-run"])
    assert e.value.code == 2


# --------------------------------------------------------------------------- the two switches

@pytest.mark.parametrize("live,approved,off", [
    (False, True, ["live"]), (True, False, ["approved"]), (False, False, ["live", "approved"])])
def test_live_refuses_unless_both_switches_are_on_and_names_the_off_one(smoke, tmp_path, capsys, live, approved, off):
    made = []
    code, out = run(smoke, args(tmp_path, live=live, approved=approved), capsys,
                    session_factory=lambda *a, **k: made.append(1))
    assert code == 3
    if "live" in off:
        assert "config/architect.yaml" in out.err or "architect.yaml" in out.err
        assert "live: true is OFF" in out.err
    if "approved" in off:
        assert "architect_ladder approved: true is OFF" in out.err
    if off == ["live"]:
        assert "approved: true is OFF" not in out.err
    if off == ["approved"]:
        assert "live: true is OFF" not in out.err
    assert made == [] and not (tmp_path / "out").exists()


def test_live_refuses_without_a_local_config(smoke, tmp_path, capsys):
    made = []
    argv = ["--role", "haiku", "--config", str(tmp_path / "missing.yaml"), "--budgets", str(budgets(tmp_path)),
            "--out-root", str(tmp_path / "out")]
    code, out = run(smoke, argv, capsys, session_factory=lambda *a, **k: made.append(1))
    assert code == 3 and "live: true is OFF" in out.err and made == []


# --------------------------------------------------------------------------- one live call (fake client)

@needs_jzapi
def test_one_call_writes_the_bundle(smoke, tmp_path, capsys):
    f = FakeFactory(tmp_path)
    code, out = run(smoke, args(tmp_path), capsys, session_factory=f)
    assert code == 0, out.err
    assert f.client.n("create") == 1 and f.client.n("count_tokens") == 0   # one request, bytes estimate
    d = tmp_path / "out" / STAMP_DIR
    names = {p.name for p in d.iterdir()}
    assert {"request.json", "prompt.system.txt", "prompt.user.txt", "context.json", "response.txt",
            "response.json", "parse.json", "validator.json", "usage.json", "calls.jsonl",
            "summary.json"} <= names
    req = json.loads((d / "request.json").read_text())
    built = smoke.build_request("haiku")
    assert req["request_sha"] == built.request.request_sha
    assert req["prompt"]["sha256"] == pr.load_prompt("baseline").sha256
    assert req["model_id"] == "claude-haiku-5-5" and req["job"] == "architect_ladder"
    assert (d / "prompt.user.txt").read_text() == built.request.user
    assert (d / "prompt.system.txt").read_text() == built.request.system
    assert (d / "response.txt").read_text() == patch_text()
    assert json.loads((d / "parse.json").read_text())["malformed"] is None
    val = json.loads((d / "validator.json").read_text())
    assert val["ok"] is True
    (line,) = (d / "calls.jsonl").read_text().splitlines()
    rec = json.loads(line)
    for k in ("model_role", "request_sha", "response_sha", "patch", "validator_result",
              "usage_if_reported", "latency_s"):
        assert k in rec
    assert rec["verdict"] == "ACCEPTED" and rec["request_sha"] == built.request.request_sha
    assert rec["provider"] == "jzapi" and rec["model_role"] == "haiku"
    usage = json.loads((d / "usage.json").read_text())
    assert usage["usage"]["output_tokens"] == 900 and usage["cost_usd"] > 0
    assert usage["worst_case_usd"] >= usage["cost_usd"]
    rows = (f.repo / "results" / "usage.csv").read_text().splitlines()
    assert len(rows) == 2                                        # header + one call
    summ = json.loads((d / "summary.json").read_text())
    assert summ["verdict"] == "ACCEPTED" and "evaluation" in summ["provenance"]
    assert "verdict: ACCEPTED" in out.out


@needs_jzapi
@pytest.mark.parametrize("text,stop,reason", [
    ("```json\n" + patch_text() + "\n```", "end_turn", "CODE_FENCE"),
    ("Here is the patch: " + patch_text(), "end_turn", "NOT_JSON"),
    (patch_text(), "max_tokens", "TRUNCATED"),
])
def test_a_malformed_reply_is_recorded_and_never_validated(smoke, tmp_path, capsys, text, stop, reason):
    f = FakeFactory(tmp_path, text=text, stop_reason=stop)
    code, out = run(smoke, args(tmp_path), capsys, session_factory=f)
    assert code == 0 and f.client.n("create") == 1
    d = tmp_path / "out" / STAMP_DIR
    assert json.loads((d / "parse.json").read_text())["malformed"]["reason"] == reason
    val = json.loads((d / "validator.json").read_text())
    assert val["ok"] is None and "never" in val["skipped"]
    rec = json.loads((d / "calls.jsonl").read_text())
    assert rec["verdict"] == "MALFORMED" and rec["validator_result"] is None
    assert f"verdict: MALFORMED ({reason})" in out.out


@needs_jzapi
def test_a_rejected_patch_records_validator_codes(smoke, tmp_path, capsys):
    f = FakeFactory(tmp_path, text=patch_text(65))
    code, out = run(smoke, args(tmp_path), capsys, session_factory=f)
    assert code == 0
    d = tmp_path / "out" / STAMP_DIR
    val = json.loads((d / "validator.json").read_text())
    assert val["ok"] is False and "OUT_OF_BOUNDS" in {v["code"] for v in val["violations"]}
    assert "verdict: REJECTED" in out.out and "OUT_OF_BOUNDS" in out.out


@needs_jzapi
def test_a_provider_error_is_logged_once_and_never_retried(smoke, tmp_path, capsys):
    from jzapi.tests.fakes import StatusError
    f = FakeFactory(tmp_path, raise_on_create=StatusError(529, "overloaded"))
    code, out = run(smoke, args(tmp_path), capsys, session_factory=f)
    assert code == 1
    assert f.client.n("create") == 1
    d = tmp_path / "out" / STAMP_DIR
    rec = json.loads((d / "calls.jsonl").read_text())
    assert rec["verdict"] == "ERROR" and "overloaded" in rec["error"]
    assert json.loads((d / "summary.json").read_text())["verdict"] == "ERROR"
    assert not (d / "response.txt").exists()


@needs_jzapi
def test_an_existing_bundle_folder_is_never_overwritten(smoke, tmp_path, capsys):
    f = FakeFactory(tmp_path)
    assert run(smoke, args(tmp_path), capsys, session_factory=f)[0] == 0
    code, out = run(smoke, args(tmp_path), capsys, session_factory=f)
    assert code == 2 and "exists" in out.err
    assert f.client.n("create") == 1


@needs_jzapi
def test_missing_key_stops_before_any_folder_or_call(smoke, tmp_path, capsys):
    f = FakeFactory(tmp_path, secret=True)              # load_api_key with no key anywhere
    code, out = run(smoke, args(tmp_path), capsys, session_factory=f)
    assert code == 2 and "JZ_ANTHROPIC_API_KEY" in out.err
    assert f.client.n("create") == 0 and not (tmp_path / "out").exists()


@needs_jzapi
def test_no_key_material_in_any_written_file_or_output(smoke, tmp_path, capsys, monkeypatch):
    env = tmp_path / "test.env"
    env.write_text(f"JZ_ANTHROPIC_API_KEY={FAKE_KEY}\n")
    monkeypatch.setenv("JZ_ENV_FILE", str(env))
    f = FakeFactory(tmp_path, secret=True)
    code, out = run(smoke, args(tmp_path), capsys, session_factory=f)
    assert code == 0, out.err
    files = bundle_files(tmp_path / "out") + [f.repo / "results" / "usage.csv"]
    assert len(files) >= 11
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert KEY_PREFIX not in text and FAKE_KEY not in text, p
        assert "JZ_ANTHROPIC_API_KEY" not in text, p
    assert KEY_PREFIX not in out.out + out.err


@needs_jzapi
def test_default_live_session_never_retries_and_estimates_by_bytes(smoke, tmp_path, monkeypatch):
    import jzapi as jz
    import jzapi.client as jc
    seen = {}

    def fake_make_client(secret=None, **kw):
        seen.update(kw, secret=secret)
        return object()
    monkeypatch.setattr(jc, "make_client", fake_make_client)
    monkeypatch.setattr(jz, "load_api_key", lambda *a, **k: jz.keys.Secret("x" * 20))
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    s = smoke.no_retry_session("architect_ladder", repo_dir=repo, budgets_path=budgets(tmp_path))
    assert seen["max_retries"] == 0 and seen["secret"] is not None
    assert s.token_estimate == "bytes" and s.job == "architect_ladder"


# --------------------------------------------------------------------------- repo hygiene

def test_smoke_bundles_are_committable_and_local_config_stays_ignored():
    probe = "bundles/architect_smoke/20261110T093000Z-haiku/request.json"
    assert subprocess.run(["git", "check-ignore", "-q", probe], cwd=REPO_ROOT).returncode == 1
    assert subprocess.run(["git", "check-ignore", "-q", "config/architect.yaml"], cwd=REPO_ROOT).returncode == 0


def test_owner_doc_lists_the_exact_commands():
    doc = (REPO_ROOT / "docs" / "ARCHITECT_SMOKE.md").read_text(encoding="utf-8")
    for cmd in ("make test", ".venv/bin/pip install -e ~/lab/JzLab",
                ".venv/bin/python scripts/architect_smoke.py --role haiku --dry-run",
                ".venv/bin/python scripts/architect_smoke.py --role haiku\n"):
        assert cmd in doc, cmd


@needs_jzapi
def test_cli_dry_run_in_a_subprocess(tmp_path):
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("JZ_ANTHROPIC_API_KEY", "JZ_BUDGETS_FILE")}
    env["JZ_ENV_FILE"] = str(tmp_path / "none.env")
    if JZLAB.is_dir():
        env["PYTHONPATH"] = str(JZLAB) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    r = subprocess.run([sys.executable, str(SCRIPT), "--role", "haiku", "--dry-run",
                        "--config", str(local_cfg(tmp_path)), "--budgets", str(budgets(tmp_path)),
                        "--out-root", str(tmp_path / "out")],
                       cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "request_sha: " in r.stdout and "worst_case_usd: " in r.stdout
    assert not (tmp_path / "out").exists()
