#!/usr/bin/env python3
"""Run a declared Worldsmith experiment (WS-11, ADR 0010; owner steps in docs/EXPERIMENTS.md).

    python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml --dry-run [--out DIR]
    python scripts/run_experiment.py --config ... plan      # worst case, headroom, switches; no call
    python scripts/run_experiment.py --config ... step      # run until the next Studio call is needed
    python scripts/run_experiment.py --config ... step --architect-elsewhere   # never call a model here
    python scripts/run_experiment.py --config ... respond K RAW_FILE [--error TEXT]
    python scripts/run_experiment.py --config ... architect # make the one pending architect call (Mac)
    python scripts/run_experiment.py --config ... status
    python scripts/run_experiment.py --config config/experiments/m2_hard.yaml --part m2_hard_f1b ...
                                    # a suite (ADR 0014): every command names one part

--dry-run simulates the whole loop with a fake Studio operator and the config's fixture architects
in a temporary folder (or --out): no network, no key, no Studio, nothing paid. Its numbers are
not results. `step` is the live relay mode (ADR 0005 section 8): it replays every answered call,
makes architect calls (paid, job architect_ladder) when a B3 episode reaches one, and stops at
the first Studio call that has no answer yet, printing it; the operator (the Claude session with
the Studio MCP) makes exactly that call and stores the raw answer with `respond`, then steps
again. Before a live step that may still call a model it prints the worst case of what is left
and refuses unless both live switches are on and the job's remaining headroom covers it.

Two machines on the same files (ADR 0010 section 9): `step --architect-elsewhere` (or
WS_ARCHITECT_ELSEWHERE=1) never calls a model; where a live B3 architect call is due it stops
with exit 5 and prints the request hash, using no slot and writing no error (no switches needed).
`architect`, run on the owner's Mac, makes exactly that one call through JzapiArchitect with the
same switch and headroom checks, stores the reply by request hash and exits; it makes no Studio
call. With nothing pending, or a reply already stored for the pending request, it calls nothing.

A closed experiment (CLOSED.md in its bundle folder, ADR 0012) refuses `step`, `respond` and
`architect` with exit 4 and sends, writes and calls nothing; `status` and `plan` still print.

Exit codes: 0 done (or plan/status/dry run printed; `architect`: reply stored or nothing to do);
3 a Studio call is pending; 5 an architect call is pending (run `architect` on the Mac); 4 refused
(a switch is off, headroom too low, the config changed after the first episode, mixed players,
the experiment is closed);
2 setup problem (jzapi missing for a live B3 step); 1 any other error (`architect`: the provider
failed; the error is stored and the episode will end CONNECTOR_ERROR).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from worldsmith.architect import client as acl  # noqa: E402
from worldsmith.runner import attempts as at  # noqa: E402
from worldsmith.runner import dryrun  # noqa: E402
from worldsmith.runner import episode as epi  # noqa: E402
from worldsmith.runner import experiment as ex  # noqa: E402
from worldsmith.runner import freeze  # noqa: E402

DEFAULT_OUT = ROOT / "bundles" / "experiments"
MAC_COMMAND = "cd ~/lab/worldsmith && .venv/bin/python scripts/run_experiment.py --config {config} architect"
INSTALL_HINT = ".venv/bin/pip install -e ~/lab/JzLab   (inside the worldsmith repo)"


def _jzapi():
    """jzapi from the venv, else from the JzLab checkout beside this repo; None if neither."""
    try:
        import jzapi
        return jzapi
    except ImportError:
        jz = ROOT.parent / "JzLab"
        if (jz / "jzapi" / "__init__.py").is_file():
            sys.path.insert(0, str(jz))
            try:
                import jzapi
                return jzapi
            except ImportError:
                return None
    return None


def _price(jz):
    def price(model_id, *, prompt_tokens, max_tokens, cache_ttl):
        return jz.worst_case_usd(model_id, prompt_tokens=prompt_tokens, max_tokens=max_tokens,
                                 cache_ttl=cache_ttl, batch=False)
    return price


def _print_worst(wc: dict, *, live: bool) -> None:
    print(f"worst case (job {wc['job']}; {wc['method']}):")
    for r in wc["conditions"]:
        print(f"  {r['condition']:<10} {r['model_id']:<18} max_tokens {r['max_tokens']:>6}  "
              f"${r['per_call_usd']:.6f}/call x {r['calls_max']} calls = ${r['usd_max']:.6f}"
              + (f"  (left: {r['calls_left']} calls, ${r['usd_left']:.6f})" if live else ""))
    print(f"  experiment worst case: ${wc['total_usd']:.6f}"
          + (f"; still to spend at most ${wc['remaining_usd']:.6f}" if live else ""))


def no_retry_session(job: str, **kw):
    """jzapi session with the SDK client's retries off and the offline byte bound (as the
    architect smoke script): one HTTP request per architect call."""
    import jzapi
    from jzapi import client as jc
    secret = jzapi.load_api_key()
    return jzapi.Session(job, client=jc.make_client(secret, max_retries=0), secret=secret,
                         token_estimate="bytes", **kw)


def _switches(cfg, args):
    local = None
    try:
        local = acl.load_local_config(args.architect_config)
    except acl.ArchitectConfigError as exc:
        return False, False, str(exc), None
    live = isinstance(local, dict) and local.get("live") is True
    bpath = acl.resolve_budgets_path(args.budgets, local)
    return live, acl.budget_job_approved(cfg["architect"]["job"], bpath), None, bpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", nargs="?", default="plan", choices=["plan", "step", "respond", "architect", "status"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--config", required=True)
    ap.add_argument("--part", help="for a suite config (ADR 0014): the experiment id of the part to use")
    ap.add_argument("--dry-run", action="store_true", help="simulate everything in a temp folder; no network")
    ap.add_argument("--out", help="experiments folder (default bundles/experiments; a temp folder for --dry-run)")
    ap.add_argument("--error", help="respond: the call failed with this error text")
    ap.add_argument("--budgets", help="JzLab budgets.yaml (default as the architect client resolves it)")
    ap.add_argument("--architect-config", help="local architect config (default config/architect.yaml)")
    ap.add_argument("--architect-elsewhere", action="store_true",
                    help="step: never call a model here; stop with exit 5 where one is due (also WS_ARCHITECT_ELSEWHERE=1)")
    a = ap.parse_args(argv)
    elsewhere = a.architect_elsewhere or os.environ.get("WS_ARCHITECT_ELSEWHERE") == "1"

    try:
        cfg = ex.load_experiment(a.config, ROOT, part=a.part)
    except freeze.P2Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except ex.ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    pl = cfg["player"]
    print(f"experiment {cfg['experiment']}  config {cfg['_sha256']}")
    print(f"player {pl['version']} ({pl['tier']}, {pl['driver']}), controller {pl['controller_sha256']}, "
          f"T_attempt {pl['timing']['t_attempt']} s, N {cfg['n_attempts']}, k {cfg['pass_k']}, "
          f"max_revisions {cfg['max_revisions']}, caps {cfg['caps']['attempts_per_episode']} attempts / "
          f"{cfg['caps']['architect_calls_per_episode']} architect calls per episode")
    jz = _jzapi()

    if a.dry_run:
        out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="worldsmith-dryrun-"))
        if jz is not None:
            _print_worst(ex.worst_case(cfg, _price(jz)), live=False)
        else:
            print("worst case: not computed (jzapi is not importable; it is only needed for prices)")
        print(f"dry run: fake Studio operator + fixture architects ({', '.join(f'{k}: {v}' for k, v in cfg['dry_run']['fixtures'].items())}); "
              "no network, no key, no Studio. NOT A RESULT.")
        try:
            handoffs = []
            res = dryrun.run_dry(cfg, out, two_machine=elsewhere, on_handoff=handoffs.append)
            if elsewhere:
                print(f"two machines simulated: {len(handoffs)} architect hand-offs (exit 5 on the VM, "
                      "`architect` on the Mac)")
        except (ex.ExperimentLocked, ex.MixedPlayers, ex.ExperimentClosed) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 4
        for c in res["conditions"]:
            for e in c["episodes"]:
                print(f"  {e['episode']:<14} {e['status']:<16} final v{e['final']['version']} "
                      f"{e['final']['successes']}/{e['final']['n']} repaired={e['repaired']} "
                      f"revisions {e['revisions']['verdicts']} attempts {e['attempts_used']} "
                      f"architect calls {e['architect_calls']}")
        print(f"sample bundle: {out / cfg['experiment']}")
        return 0

    exp = ex.Experiment(cfg, Path(a.out) if a.out else DEFAULT_OUT)
    if exp.closed():
        print(f"closed: {exp.closed_path()} (step, respond and architect refuse; nothing more runs)")
        if a.what in ("step", "respond", "architect"):
            try:
                exp.check_open()
            except ex.ExperimentClosed as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 4
    if a.what == "status":
        for s in exp.status():
            print(f"  {s['condition']:<10} ep{s['episode']}  {s['state']}")
        p = exp.pending()
        print(f"pending: {p}" if p else "pending: none")
        return 0
    if a.what == "respond":
        p = exp.pending()
        if not p:
            print("error: no pending call", file=sys.stderr)
            return 1
        if p.get("architect"):
            print(f"error: an architect call is pending in {p['episode_dir']} (request "
                  f"{p['architect']['request_sha']}), not a Studio call; run on the Mac: "
                  f"{MAC_COMMAND.format(config=_rel_config(a.config, a.part))}", file=sys.stderr)
            return 1
        k, raw = int(a.args[0]), Path(a.args[1]).read_text(encoding="utf-8")
        if k != p["index"]:
            print(f"error: the pending call is {p['index']:03d} in {p['episode_dir']}, not {k:03d}", file=sys.stderr)
            return 1
        r = at.respond(exp.dir / p["episode_dir"] / "calls", k, raw, error=a.error)
        print(f"{p['episode_dir']} call {k:03d} answered ({len(raw)} chars, relay {r['relay_elapsed_s']} s)")
        return 0

    try:
        exp.check_declared()
    except (ex.ExperimentLocked, ex.MixedPlayers) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    needs_model = any(c["proposer"] == "architect" for c in cfg["_conditions"].values())
    if a.what == "step" and elsewhere:
        return _step_elsewhere(cfg, exp, a, jz)
    pend = exp.pending() if a.what == "architect" else None
    if a.what == "architect":
        if not (pend and pend.get("architect")):
            print("architect: no architect call is pending (pending.json); nothing to do")
            return 0
        pa = pend["architect"]
        stored = exp.dir / pend["episode_dir"] / "architect" / "replies" / f"{pa['request_sha']}.json"
        if stored.is_file() or stored.with_name(f"{pa['request_sha']}.error.json").is_file():
            print(f"architect: a reply for request {pa['request_sha']} is already stored; nothing to do (run step)")
            return 0
        print(f"architect: pending {pend['episode_dir']} request {pa['request_sha']} (role {pa['role']})")
    wc = None
    if needs_model:
        if jz is None:
            print(f"error: jzapi is not importable ({sys.executable}). Install it: {INSTALL_HINT}", file=sys.stderr)
            return 2
        wc = ex.worst_case(cfg, _price(jz), exp)
        _print_worst(wc, live=True)
    live, approved, cfg_err, bpath = _switches(cfg, a)
    if needs_model:
        print(f"switch budgets.yaml {cfg['architect']['job']} approved: true is {'ON' if approved else 'OFF'} ({bpath})")
        print(f"switch config/architect.yaml live: true is {'ON' if live else 'OFF'}" + (f" ({cfg_err})" if cfg_err else ""))
    headroom = None
    if wc is not None and wc["remaining_usd"] > 0 and approved:
        try:
            budget = jz.Budget(cfg["architect"]["job"], usage_csv=ROOT / "results" / "usage.csv", budgets_path=bpath)
            headroom = budget.remaining()
        except Exception as exc:
            print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 4
        print(f"job headroom: ${headroom:.6f} (cap minus results/usage.csv spend and reservations)")
    if a.what == "plan":
        for s in exp.status():
            print(f"  {s['condition']:<10} ep{s['episode']}  {s['state']}")
        return 0

    # step (live relay) or architect (the one pending call)
    if wc is not None and wc["remaining_usd"] > 0:
        if not (approved and live):
            print("refused: a B3 episode may still call a model and a live switch is OFF "
                  "(both must be ON: budgets.yaml approved: true AND config/architect.yaml live: true)", file=sys.stderr)
            return 4
        try:
            ex.check_headroom(wc["remaining_usd"], headroom)
        except ex.BudgetRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 4

    def architect_for(cond):
        return acl.JzapiArchitect(cond["role"], config_path=a.architect_config, budgets_path=a.budgets,
                                  session_factory=no_retry_session)

    if a.what == "architect":
        return _architect_one(exp, architect_for, pend)
    try:
        res = exp.run(lambda d: at.RelayClient(d / "calls"), architect_for)
    except at.Pending as p:
        _print_pending(p, exp, _rel_config(a.config, a.part) if a.part else a.config)
        return 3
    except acl.LiveCallRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except epi.PromptCapExceeded as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except ex.PreflightFailed as exc:
        print(f"stopped: preflight failed: {exc}", file=sys.stderr)
        return 4
    for c in res["conditions"]:
        print(f"  {c['condition']:<10} repaired {c['repaired']['n']}/{c['repaired']['of']}  statuses {c['statuses']}")
    print(f"results: {exp.dir / 'results.json'}")
    return 0


def _rel_config(path, part=None) -> str:
    p = Path(path).resolve()
    try:
        rel = p.relative_to(ROOT).as_posix()
    except ValueError:
        rel = str(path)
    return f"{rel} --part {part}" if part else rel


def _print_pending(p, exp, config) -> None:
    req, pend = p.request, exp.pending()
    d = exp.dir / pend["episode_dir"]
    print(f"PENDING {pend['episode_dir']} call {p.index:03d} {req['tool']} timeout_s={req['timeout_s']} "
          f"args={json.dumps(req['args'])}")
    if "code_file" in req:
        print(f"  code: {d / 'calls' / req['code_file']}  ({req['code_arg']}, sha256 {req['code_sha256']})")
    print(f"  then: python scripts/run_experiment.py --config {config} respond {p.index} <raw answer file>")


def _no_model(cond):
    raise RuntimeError("this machine never calls a model (--architect-elsewhere)")  # defer stops first


def _step_elsewhere(cfg, exp, a, jz) -> int:
    """step on the machine that may not call a model: Studio relay as usual; exit 5 where a live
    architect call is due (no slot used, nothing written but pending.json)."""
    if jz is not None:
        _print_worst(ex.worst_case(cfg, _price(jz), exp), live=True)
    print("architect calls: elsewhere (this machine never calls a model; run `architect` on the Mac)")
    try:
        res = exp.run(lambda d: at.RelayClient(d / "calls"), _no_model, architect_mode="defer")
    except at.Pending as p:
        _print_pending(p, exp, _rel_config(a.config, a.part) if a.part else a.config)
        return 3
    except ex.PreflightFailed as exc:
        print(f"stopped: preflight failed: {exc}", file=sys.stderr)
        return 4
    except epi.ArchitectPending as p:
        pend = exp.pending()
        print(f"ARCHITECT PENDING {pend['episode_dir']} request {p.request_sha} role {p.role}")
        print(f"  on the Mac: {MAC_COMMAND.format(config=_rel_config(a.config, a.part))}")
        print("  then step here again")
        return 5
    for c in res["conditions"]:
        print(f"  {c['condition']:<10} repaired {c['repaired']['n']}/{c['repaired']['of']}  statuses {c['statuses']}")
    print(f"results: {exp.dir / 'results.json'}")
    return 0


def _architect_one(exp, architect_for, pend) -> int:
    """The Mac: replay the stored files, make exactly the pending architect call, store it, stop."""
    pa = pend["architect"]
    try:
        exp.run(lambda d: at.RelayClient(d / "calls"), architect_for, architect_mode="one",
                architect_expect=pa["request_sha"])
    except epi.ArchitectCallDone as done:
        if done.error:
            print(f"architect: provider error stored for request {done.request_sha}: {done.error}", file=sys.stderr)
            print("  the episode will end CONNECTOR_ERROR at the next step (the slot is used)", file=sys.stderr)
            return 1
        print(f"architect reply stored: {pend['episode_dir']} request {done.request_sha} role {done.role} "
              f"(stop_reason {done.stop_reason}); run step on the VM")
        return 0
    except at.Pending:
        print("architect: no architect call was due before the next Studio call; nothing called")
        return 0
    except acl.LiveCallRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except epi.PromptCapExceeded as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except epi.EpisodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("architect: the experiment is complete; nothing called")
    return 0


if __name__ == "__main__":
    sys.exit(main())
