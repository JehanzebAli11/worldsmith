"""WS-11 follow-up: two machines on the same files (ADR 0010 section 9).

The bridge VM drives `step --architect-elsewhere` (Studio relay, B0-B2) and never calls a model:
at a live B3 architect call it stops with exit 5, using no slot and writing no error. The owner's
Mac runs `architect`, which makes exactly that one call through the normal JzapiArchitect path and
stores the reply by request hash; the VM's next `step` replays it. CPU only: simulated operator,
fixture or fake-session architects, no network, nothing paid.
"""

import json
import sys

import pytest
import yaml

from _repo import REPO_ROOT
from worldsmith.architect import client as acl
from worldsmith.runner import attempts as at
from worldsmith.runner import dryrun
from worldsmith.runner import episode as epi
from worldsmith.runner import experiment as ex

CFG = REPO_ROOT / "config" / "experiments" / "m2_f1a.yaml"
M0_FIRST_REQUEST = "57f003382a7ef366af5e9073fffeea3bd485946880c74bda9032953964cf461d"
F1A = "92f866098bfb850cf4c464179887a862f305c143c8e0b38efdc7978493cddb1d"
FIX_REPLY = json.dumps({"schema": "worldsmith.patch/0.1", "base_hash": F1A,
                        "ops": [{"op": "set", "id": "bridge_1", "field": "length", "value": 16}]})

JZLAB = REPO_ROOT.parent / "JzLab"
try:
    import jzapi
except ImportError:
    if (JZLAB / "jzapi" / "__init__.py").is_file():
        sys.path.insert(0, str(JZLAB))
        import jzapi
    else:
        jzapi = None
needs_jzapi = pytest.mark.skipif(jzapi is None, reason="jzapi is not installed and JzLab is not beside worldsmith")


@pytest.fixture(scope="module")
def cfg():
    return ex.load_experiment(CFG, REPO_ROOT)


def variant(tmp_path, ids, name="two.yaml"):
    doc = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    doc["conditions"] = [c for c in doc["conditions"] if c["id"] in ids]
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


class Counting:
    def __init__(self, cfg, fail=False):
        self.cfg, self.calls, self.fail = cfg, 0, fail

    def __call__(self, cond):
        inner = acl.FixtureArchitect.from_names(self.cfg["dry_run"]["fixtures"][cond["role"]])
        outer = self

        class A:
            model_role, model_id, provider = inner.model_role, inner.model_id, inner.provider

            def propose(self, req):
                outer.calls += 1
                if outer.fail:
                    raise acl.ArchitectCallError("APIStatusError: simulated 529 overloaded")
                return inner.propose(req)
        return A()


def vm_until_architect(exp, op, counter):
    """The VM: relay steps with architect_mode defer until an architect call is pending."""
    while True:
        try:
            return exp.run(lambda d: at.RelayClient(d / "calls"), counter, architect_mode="defer")
        except at.Pending as p:
            ep_dir = exp.dir / exp.pending()["episode_dir"]
            at.respond(ep_dir / "calls", p.index, op.answer(ep_dir, p.request))


def test_two_machine_dry_run_equals_the_single_machine_run(cfg, tmp_path):
    single = dryrun.run_dry(cfg, tmp_path / "one")
    handoffs = []
    two = dryrun.run_dry(cfg, tmp_path / "two", two_machine=True, on_handoff=handoffs.append)
    assert two == single
    assert len(handoffs) == 7                           # 1 haiku + 3 sonnet + 3 opus calls
    assert handoffs[0] == M0_FIRST_REQUEST
    root = tmp_path / "two" / cfg["experiment"]
    assert not list(root.rglob("*.error.json"))
    assert len(list(root.rglob("architect/replies/*.json"))) == 7


def test_defer_stops_without_a_slot_an_error_or_a_call(cfg, tmp_path):
    c = ex.load_experiment(variant(tmp_path, {"B3-haiku"}), REPO_ROOT)
    exp, op, counter = ex.Experiment(c, tmp_path / "o", dry_run=True), dryrun.FakeOperator(c), Counting(c)
    for _ in range(2):                                  # idempotent: the same pending call both times
        with pytest.raises(epi.ArchitectPending) as e:
            vm_until_architect(exp, op, counter)
        assert e.value.request_sha == M0_FIRST_REQUEST and e.value.role == "haiku"
    assert counter.calls == 0
    ep = exp.dir / "B3-haiku" / "ep0"
    assert not (ep / "architect").exists() and not (ep / "summary.json").exists()
    p = json.loads((exp.dir / "pending.json").read_text())
    assert p["architect"] == {"request_sha": M0_FIRST_REQUEST, "role": "haiku", "condition": "B3-haiku"}
    assert exp.pending()["episode_dir"] == "B3-haiku/ep0" and p["index"] is None


def test_one_mode_makes_exactly_the_pending_call_and_no_studio_call(cfg, tmp_path):
    c = ex.load_experiment(variant(tmp_path, {"B3-haiku"}), REPO_ROOT)
    exp, op, counter = ex.Experiment(c, tmp_path / "o", dry_run=True), dryrun.FakeOperator(c), Counting(c)
    with pytest.raises(epi.ArchitectPending):
        vm_until_architect(exp, op, counter)
    calls_before = sorted(p.name for p in exp.dir.rglob("calls/*"))
    mac = ex.Experiment(c, tmp_path / "o", dry_run=True)
    with pytest.raises(epi.ArchitectCallDone) as e:
        mac.run(lambda d: at.RelayClient(d / "calls"), counter, architect_mode="one",
                architect_expect=M0_FIRST_REQUEST)
    assert e.value.request_sha == M0_FIRST_REQUEST and e.value.error is None
    assert counter.calls == 1
    assert sorted(p.name for p in exp.dir.rglob("calls/*")) == calls_before   # no Studio request written
    assert (exp.dir / "B3-haiku" / "ep0" / "architect" / "replies" / f"{M0_FIRST_REQUEST}.json").is_file()
    # again: the reply is stored, so no second call; replay runs on to the next Studio call
    with pytest.raises(at.Pending):
        mac.run(lambda d: at.RelayClient(d / "calls"), counter, architect_mode="one")
    assert counter.calls == 1
    res = vm_until_architect(exp, op, counter)          # the VM picks the reply up and finishes
    assert res["conditions"][0]["episodes"][0]["revisions"]["verdicts"] == ["ACCEPTED"]


def test_one_mode_refuses_a_request_other_than_the_pending_one(cfg, tmp_path):
    c = ex.load_experiment(variant(tmp_path, {"B3-haiku"}), REPO_ROOT)
    exp, op, counter = ex.Experiment(c, tmp_path / "o", dry_run=True), dryrun.FakeOperator(c), Counting(c)
    with pytest.raises(epi.ArchitectPending):
        vm_until_architect(exp, op, counter)
    with pytest.raises(epi.EpisodeError, match="not the pending request"):
        exp.run(lambda d: at.RelayClient(d / "calls"), counter, architect_mode="one", architect_expect="0" * 64)
    assert counter.calls == 0


def test_a_provider_error_on_the_mac_is_stored_and_the_vm_records_it(cfg, tmp_path):
    c = ex.load_experiment(variant(tmp_path, {"B3-haiku"}), REPO_ROOT)
    exp, op = ex.Experiment(c, tmp_path / "o", dry_run=True), dryrun.FakeOperator(c)
    with pytest.raises(epi.ArchitectPending):
        vm_until_architect(exp, op, Counting(c))
    failing = Counting(c, fail=True)
    with pytest.raises(epi.ArchitectCallDone) as e:
        exp.run(lambda d: at.RelayClient(d / "calls"), failing, architect_mode="one")
    assert failing.calls == 1 and "529" in e.value.error
    res = vm_until_architect(exp, op, Counting(c))
    ep = res["conditions"][0]["episodes"][0]
    assert ep["status"] == "CONNECTOR_ERROR" and ep["architect_calls"] == 1 and failing.calls == 1


# --------------------------------------------------------------------------- the CLI on two machines

def _cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_experiment", REPO_ROOT / "scripts" / "run_experiment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _switches(tmp_path, name, on):
    b = tmp_path / f"{name}-budgets.yaml"
    b.write_text(yaml.safe_dump({"total_usd": 400, "jobs": {"architect_ladder": {"cap_usd": 90, "approved": on}}}))
    a = tmp_path / f"{name}-architect.yaml"
    a.write_text(yaml.safe_dump({"schema": "worldsmith.architect_local/0.1", "live": on}))
    return ["--budgets", str(b), "--architect-config", str(a)]


class FakeSession:
    """Stands in for jzapi.Session: one reply per message(), the fix on revision 1."""
    calls = 0

    def __init__(self, job, **kw):
        pass

    def message(self, *, note, model, max_tokens, system, user, cache_system):
        FakeSession.calls += 1
        return {"text": FIX_REPLY, "model_id": model, "stop_reason": "end_turn", "request_id": "fake-1",
                "usage": {"input_tokens": 100, "output_tokens": 20}, "cost_usd": 0.0001}


def _drive(cli, cfg_path, out, tmp_path, capsys, *, elsewhere):
    """Run the experiment through the CLI; the fake operator answers each Studio call."""
    c = ex.load_experiment(cfg_path, REPO_ROOT)
    op = dryrun.FakeOperator(c)
    vm = _switches(tmp_path, "vm", on=not elsewhere)
    mac = _switches(tmp_path, "mac", on=True)
    codes = []
    for _ in range(200):
        argv = ["--config", str(cfg_path), "step", "--out", str(out), *vm] + (["--architect-elsewhere"] if elsewhere else [])
        rc = cli.main(argv)
        text = capsys.readouterr().out
        codes.append(rc)
        if rc == 0:
            return codes
        if rc == 3:
            p = json.loads((out / c["experiment"] / "pending.json").read_text())
            ep_dir = out / c["experiment"] / p["episode_dir"]
            req = json.loads((ep_dir / "calls" / f"{p['index']:03d}.request.json").read_text())
            f = tmp_path / "answer.txt"
            f.write_text(op.answer(ep_dir, req), encoding="utf-8")
            assert cli.main(["--config", str(cfg_path), "respond", str(p["index"]), str(f), "--out", str(out)]) == 0
            capsys.readouterr()
        elif rc == 5:
            assert f"ARCHITECT PENDING B3-haiku/ep0 request {M0_FIRST_REQUEST}" in text
            assert "scripts/run_experiment.py --config config/experiments/m2_f1a.yaml architect" in text \
                or "architect" in text
            assert cli.main(["--config", str(cfg_path), "respond", "1", str(tmp_path), "--out", str(out)]) == 1
            capsys.readouterr()
            assert cli.main(["--config", str(cfg_path), "architect", "--out", str(out), *mac]) == 0
            assert "architect reply stored" in capsys.readouterr().out
        else:
            raise AssertionError(f"unexpected exit {rc}: {text}")
    raise AssertionError("did not finish")


@needs_jzapi
def test_cli_two_machines_equal_one_machine(tmp_path, capsys, monkeypatch):
    cli = _cli()
    monkeypatch.setattr(cli, "no_retry_session", FakeSession)
    path = variant(tmp_path, {"B0", "B3-haiku"})
    FakeSession.calls = 0
    one = _drive(cli, path, tmp_path / "one", tmp_path, capsys, elsewhere=False)
    assert 5 not in one and FakeSession.calls == 1
    FakeSession.calls = 0
    two = _drive(cli, path, tmp_path / "two", tmp_path, capsys, elsewhere=True)
    assert two.count(5) == 1 and FakeSession.calls == 1
    name = "m2_f1a_pilot"
    assert json.loads((tmp_path / "one" / name / "results.json").read_text()) == \
        json.loads((tmp_path / "two" / name / "results.json").read_text())
    assert not list((tmp_path / "two").rglob("*.error.json"))
    # nothing pending now: `architect` does nothing and exits 0
    assert cli.main(["--config", str(path), "architect", "--out", str(tmp_path / "two"),
                     *_switches(tmp_path, "mac", on=True)]) == 0
    assert FakeSession.calls == 1


@needs_jzapi
def test_cli_architect_refuses_with_a_switch_off_and_does_not_call(tmp_path, capsys, monkeypatch):
    cli = _cli()
    monkeypatch.setattr(cli, "no_retry_session", FakeSession)
    path = variant(tmp_path, {"B3-haiku"})
    c = ex.load_experiment(path, REPO_ROOT)
    out = tmp_path / "o"
    exp = ex.Experiment(c, out)
    with pytest.raises(epi.ArchitectPending):
        vm_until_architect(exp, dryrun.FakeOperator(c), Counting(c))
    FakeSession.calls = 0
    assert cli.main(["--config", str(path), "architect", "--out", str(out), *_switches(tmp_path, "off", on=False)]) == 4
    assert FakeSession.calls == 0
    assert not (exp.dir / "B3-haiku" / "ep0" / "architect").exists()


def test_cli_step_elsewhere_needs_no_switches(tmp_path, capsys):
    path = variant(tmp_path, {"B0", "B3-haiku"})
    rc = _cli().main(["--config", str(path), "step", "--out", str(tmp_path / "o"), "--architect-elsewhere",
                      *_switches(tmp_path, "vm", on=False)])
    assert rc == 3                                       # B0's first Studio call, not a refusal
