#!/usr/bin/env python3
"""Run a declared Worldsmith experiment (WS-11, ADR 0010; owner steps in docs/EXPERIMENTS.md).

    python scripts/run_experiment.py --config config/experiments/m2_f1a.yaml --dry-run [--out DIR]
    python scripts/run_experiment.py --config ... plan      # worst case, headroom, switches; no call
    python scripts/run_experiment.py --config ... step      # run until the next Studio call is needed
    python scripts/run_experiment.py --config ... respond K RAW_FILE [--error TEXT]
    python scripts/run_experiment.py --config ... status

--dry-run simulates the whole loop with a fake Studio operator and the WS-10 fixture architects
in a temporary folder (or --out): no network, no key, no Studio, nothing paid. Its numbers are
not results. `step` is the live relay mode (ADR 0005 section 8): it replays every answered call,
makes architect calls (paid, job architect_ladder) when a B3 episode reaches one, and stops at
the first Studio call that has no answer yet, printing it; the operator (the Claude session with
the Studio MCP) makes exactly that call and stores the raw answer with `respond`, then steps
again. Before a live step that may still call a model it prints the worst case of what is left
and refuses unless both live switches are on and the job's remaining headroom covers it.

Exit codes: 0 done (or plan/status/dry run printed); 3 a Studio call is pending; 4 refused (a
switch is off, headroom too low, the config changed after the first episode, mixed players);
2 setup problem (jzapi missing for a live B3 step); 1 any other error.
"""

from __future__ import annotations

import argparse
import json
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
    ap.add_argument("what", nargs="?", default="plan", choices=["plan", "step", "respond", "status"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true", help="simulate everything in a temp folder; no network")
    ap.add_argument("--out", help="experiments folder (default bundles/experiments; a temp folder for --dry-run)")
    ap.add_argument("--error", help="respond: the call failed with this error text")
    ap.add_argument("--budgets", help="JzLab budgets.yaml (default as the architect client resolves it)")
    ap.add_argument("--architect-config", help="local architect config (default config/architect.yaml)")
    a = ap.parse_args(argv)

    try:
        cfg = ex.load_experiment(a.config, ROOT)
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
            res = dryrun.run_dry(cfg, out)
        except (ex.ExperimentLocked, ex.MixedPlayers) as exc:
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

    # step (live relay)
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

    try:
        res = exp.run(lambda d: at.RelayClient(d / "calls"), architect_for)
    except at.Pending as p:
        req, pend = p.request, exp.pending()
        d = exp.dir / pend["episode_dir"]
        print(f"PENDING {pend['episode_dir']} call {p.index:03d} {req['tool']} timeout_s={req['timeout_s']} "
              f"args={json.dumps(req['args'])}")
        if "code_file" in req:
            print(f"  code: {d / 'calls' / req['code_file']}  ({req['code_arg']}, sha256 {req['code_sha256']})")
        print(f"  then: python scripts/run_experiment.py --config {a.config} respond {p.index} <raw answer file>")
        return 3
    except acl.LiveCallRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except epi.PromptCapExceeded as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    for c in res["conditions"]:
        print(f"  {c['condition']:<10} repaired {c['repaired']['n']}/{c['repaired']['of']}  statuses {c['statuses']}")
    print(f"results: {exp.dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
