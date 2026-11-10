#!/usr/bin/env python3
"""One architect call on F1-A at stage M0: a smoke test of the WS-10 client and output format.

    python scripts/architect_smoke.py --role haiku --dry-run   # hashes + worst case; no key, no network
    python scripts/architect_smoke.py --role haiku             # exactly ONE paid call (owner's Mac only)

Builds the architect request for revision 1 of 3 from the recorded M0 F1-A bundle
(`bundles/m0/F1-A/scene.json`, episode F1-A of `bundles/m0/classification.json`) exactly as the
WS-10 loop does (same prompt, same inputs disclosure, hypothesis shown as unconfirmed), prints
the prompt hash, the request hash and jzapi's worst-case cost, and without --dry-run makes one
call through `JzapiArchitect` (job architect_ladder), parses the reply strictly, runs the WS-06
validator and writes `bundles/architect_smoke/<UTC timestamp>-<role>/`.

This is not an episode: nothing is built or played in Studio, and the result is not a B3
number. No retries: the SDK client is made with max_retries=0 and jzapi estimates the prompt by
its byte length, so the call is one HTTP request. Live calls refuse unless BOTH switches are on
(JzLab budgets.yaml architect_ladder approved: true AND the git-ignored config/architect.yaml
live: true); jzapi then re-checks approval and cap. The key never passes through this script's
output or files. Claude replies are evaluation data only, never training data (ROADMAP 11.3).

Exit codes: 0 dry run done, or one reply received and recorded (any verdict); 1 the provider
failed (ERROR recorded); 2 setup problem (jzapi missing, no key, folder exists); 3 refused (a
switch is off).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from worldsmith.architect import client as cl
from worldsmith.architect import loop as lp
from worldsmith.architect import prompt as pr
from worldsmith.grammar import scene as sc

ROOT = Path(__file__).resolve().parent.parent
SCENE = "bundles/m0/F1-A/scene.json"
CLASSIFICATION = "bundles/m0/classification.json"
EPISODE = "F1-A"
STAGE = "M0"
PROMPT_MODE = "baseline"
MAX_REVISIONS = 3                       # the loop's budget; this is revision 1 of 3
OUT_ROOT = ROOT / "bundles" / "architect_smoke"
SCHEMA = "worldsmith.architect_smoke/0.1"
INSTALL_HINT = ".venv/bin/pip install -e ~/lab/JzLab   (inside the worldsmith repo; see docs/ARCHITECT_SMOKE.md)"
PROVENANCE = ("One architect call made by scripts/architect_smoke.py: a smoke test of the WS-10 client and "
              "output format on the recorded M0 F1-A bundle. Not an episode (nothing built or played) and "
              "not a B3 result. The reply is Claude output: evaluation and inference data only, never "
              "training data (ROADMAP 11.3).")


@dataclass(frozen=True)
class Built:
    role: str
    model_id: str
    max_tokens: int
    job: str
    request: pr.ArchitectRequest
    params: dict
    inputs: dict


def _sha_file(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def build_request(role: str) -> Built:
    """The WS-10 loop's first request for F1-A at M0, and the params JzapiArchitect would send."""
    ladder = cl.load_ladder_config()
    if role not in ladder["roles"]:
        raise cl.ArchitectConfigError(f"unknown role {role!r}; known: {sorted(ladder['roles'])}")
    scene = sc.load_scene(ROOT / SCENE)
    doc = json.loads((ROOT / CLASSIFICATION).read_text(encoding="utf-8"))
    episode = next(e for e in doc["episodes"] if e["episode"] == EPISODE)
    observed = pr.observed_from_classification(episode)
    ctx = pr.build_context(scene, observed, stage=STAGE, revision=1, max_revisions=MAX_REVISIONS, previous=[])
    req = pr.make_request(ctx, pr.load_prompt(PROMPT_MODE))
    r = ladder["roles"][role]
    params = {"model": r["model_id"], "max_tokens": r["max_tokens"], "system": req.system,
              "user": req.user, "cache_system": ladder["cache_system"]}
    inputs = {"scene": {"path": SCENE, "sha256": _sha_file(SCENE), "canonical_hash": sc.canonical_hash(scene)},
              "classification": {"path": CLASSIFICATION, "sha256": _sha_file(CLASSIFICATION), "episode": EPISODE}}
    return Built(role, r["model_id"], r["max_tokens"], ladder["job"], req, params, inputs)


def worst_case(params: dict) -> dict:
    """jzapi's worst case for this request, offline: prompt tokens bounded by the prompt's UTF-8
    bytes (jzapi `token_estimate="bytes"`, the estimate the live session uses too)."""
    import jzapi
    from jzapi.api import bytes_upper_bound, cache_ttl_of
    p = jzapi.build_params(model=params["model"], max_tokens=params["max_tokens"], system=params["system"],
                           user=params["user"], cache_system=params["cache_system"])
    pt = bytes_upper_bound(p)
    usd = jzapi.worst_case_usd(p["model"], prompt_tokens=pt, max_tokens=p["max_tokens"],
                               cache_ttl=cache_ttl_of(p), batch=False)
    return {"usd": usd, "prompt_tokens_upper_bound": pt, "max_tokens": p["max_tokens"],
            "method": "jzapi.worst_case_usd, prompt tokens <= UTF-8 bytes (token_estimate=bytes), "
                      "cache-write input rate, max_tokens of output",
            "price_source": jzapi.PRICE_SOURCE, "price_checked": jzapi.PRICE_CHECKED}


def switches(config_path, budgets_path, job: str) -> dict:
    """Both live switches, as JzapiArchitect checks them; never raises."""
    where = Path(config_path) if config_path else cl.LOCAL_CONFIG
    try:
        local = cl.load_local_config(config_path)
        local_err = None
    except cl.ArchitectConfigError as exc:
        local, local_err = None, str(exc)
    live = isinstance(local, dict) and local.get("live") is True
    if local_err:
        live_why = local_err
    elif local is None:
        live_why = f"{where} does not exist (copy config/architect.example.yaml and set live: true)"
    else:
        live_why = f"{where} says live: {local.get('live')!r}"
    bpath = cl.resolve_budgets_path(budgets_path, local if isinstance(local, dict) else None)
    approved = cl.budget_job_approved(job, bpath)
    return {"live": live, "live_why": live_why, "config": str(where),
            "approved": approved, "budgets": str(bpath), "job": job}


def no_retry_session(job: str, **kw):
    """The live jzapi session: SDK client with max_retries=0 (one HTTP request, no retries) and
    the offline byte bound for the prompt (no count_tokens request)."""
    import jzapi
    from jzapi import client as jc
    secret = jzapi.load_api_key()
    return jzapi.Session(job, client=jc.make_client(secret, max_retries=0), secret=secret,
                         token_estimate="bytes", **kw)


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def main(argv=None, *, session_factory=None, now=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--role", required=True, choices=sorted(cl.PINNED_MODELS), help="ladder role (haiku for the smoke)")
    ap.add_argument("--dry-run", action="store_true", help="print hashes and worst case only; no key, no network")
    ap.add_argument("--config", help="local architect config (default config/architect.yaml)")
    ap.add_argument("--budgets", help="JzLab budgets.yaml (default as JzapiArchitect resolves it)")
    ap.add_argument("--out-root", help="where the bundle folder goes (default bundles/architect_smoke)")
    a = ap.parse_args(argv)

    built = build_request(a.role)
    req = built.request
    print(f"architect smoke: {EPISODE} at {STAGE}, revision 1 of {MAX_REVISIONS} "
          f"({SCENE}, {CLASSIFICATION} episode {EPISODE})")
    print(f"role: {built.role} ({built.model_id}), max_tokens {built.max_tokens}, "
          f"cache_system {str(built.params['cache_system']).lower()}, job {built.job}")
    print(f"prompt: {req.prompt.mode} v{req.prompt.version} ({req.prompt.file})")
    print(f"prompt_sha: {req.prompt.sha256}")
    print(f"request_sha: {req.request_sha}")
    print(f"context_sha: {req.context_sha}")
    print(f"scene_hash: {req.context['scene_hash']}")
    try:
        wc = worst_case(built.params)
    except ImportError:
        _err(f"error: jzapi is not importable in this Python ({sys.executable}). Install it: {INSTALL_HINT}")
        return 2
    print(f"worst_case_usd: {wc['usd']:.6f} (jzapi: <= {wc['prompt_tokens_upper_bound']} prompt tokens "
          f"by bytes + {wc['max_tokens']} output tokens)")
    sw = switches(a.config, a.budgets, built.job)
    print(f"switch budgets.yaml {sw['job']} approved: true is {'ON' if sw['approved'] else 'OFF'} ({sw['budgets']})")
    print(f"switch config/architect.yaml live: true is {'ON' if sw['live'] else 'OFF'} ({sw['config']})")
    if a.dry_run:
        print("dry run: no call made")
        return 0

    if not (sw["approved"] and sw["live"]):
        if not sw["approved"]:
            _err(f"refused: budgets.yaml {sw['job']} approved: true is OFF in {sw['budgets']} "
                 f"(set only after the owner's yes, recorded in JzLab state/DECISIONS.md)")
        if not sw["live"]:
            _err(f"refused: config/architect.yaml live: true is OFF: {sw['live_why']}")
        return 3

    try:
        arch = cl.JzapiArchitect(a.role, config_path=a.config, budgets_path=a.budgets,
                                 session_factory=session_factory or no_retry_session)
    except cl.LiveCallRefused as exc:
        _err(f"refused: {exc}")
        return 3
    except ImportError:
        _err(f"error: jzapi is not importable. Install it: {INSTALL_HINT}")
        return 2
    except Exception as exc:  # e.g. jzapi.MissingKey: names only the variable and the file checked
        _err(f"error: could not start the jzapi session: {type(exc).__name__}: {exc}")
        return 2

    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y%m%dT%H%M%SZ")
    out = Path(a.out_root) if a.out_root else OUT_ROOT
    d = out / f"{stamp}-{a.role}"
    try:
        d.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        _err(f"error: {d} exists; not overwriting it (wait a second and re-run)")
        return 2

    _dump(d / "request.json", {
        "schema": SCHEMA, "created_utc": stamp, "role": built.role, "model_id": built.model_id,
        "max_tokens": built.max_tokens, "cache_system": built.params["cache_system"], "job": built.job,
        "sampling": "api_default", "stage": STAGE, "episode": EPISODE, "revision": 1,
        "max_revisions": MAX_REVISIONS, "inputs": built.inputs,
        "prompt": {"mode": req.prompt.mode, "version": req.prompt.version, "sha256": req.prompt.sha256,
                   "file": req.prompt.file},
        "request_sha": req.request_sha, "context_sha": req.context_sha,
        "scene_hash": req.context["scene_hash"], "worst_case": wc,
        "files": {"system": "prompt.system.txt", "user": "prompt.user.txt", "context": "context.json"}})
    (d / "prompt.system.txt").write_text(req.system, encoding="utf-8")
    (d / "prompt.user.txt").write_text(req.user, encoding="utf-8")
    _dump(d / "context.json", req.context)

    scene = sc.load_scene(ROOT / SCENE)
    observed = req.context["observed"]
    log = lp.CallLog(d / "calls.jsonl")
    budget = lp.RevisionBudget(MAX_REVISIONS)
    episode = f"architect_smoke/{d.name}"
    try:
        rev = lp.revise(arch, scene=scene, observed=observed, stage=STAGE, budget=budget,
                        previous=[], prompt=req.prompt, episode=episode, log=log)
    except Exception as exc:
        rec = log.records[-1] if log.records else {"error": f"{type(exc).__name__}: {exc}"}
        _dump(d / "summary.json", {"schema": SCHEMA, "verdict": "ERROR", "error": rec.get("error"),
                                   "calls": len(log.records), "provenance": PROVENANCE})
        _err(f"verdict: ERROR (no reply): {rec.get('error')}")
        _err(f"bundle: {d}")
        return 1

    rec = rev.record
    if rec["request_sha"] != req.request_sha:          # the loop rebuilt a different request: a bug
        raise AssertionError(f"request hash drift: {rec['request_sha']} != {req.request_sha}")
    (d / "response.txt").write_text(rec["response_text"] or "", encoding="utf-8")
    _dump(d / "response.json", {k: rec[k] for k in ("model_id", "stop_reason", "request_id", "latency_s",
                                                     "response_sha", "provider", "model_role")})
    _dump(d / "parse.json", {"strict": "ADR 0009 section 4", "ok": rev.malformed is None,
                             "malformed": rev.malformed, "patch": rev.patch})
    if rev.malformed:
        _dump(d / "validator.json", {"ok": None, "skipped": "MALFORMED: a malformed reply never reaches "
                                     "the validator (ADR 0009 section 4)"})
    else:
        _dump(d / "validator.json", {**rec["validator_result"], "candidate_scene_hash": rec["candidate_scene_hash"]})
    _dump(d / "usage.json", {"job": built.job, "usage": rec["usage_if_reported"],
                             "cost_usd": rec["cost_usd_if_reported"], "worst_case_usd": wc["usd"],
                             "usage_csv": "results/usage.csv (one row, written by jzapi)"})
    codes = sorted(rev.codes)
    _dump(d / "summary.json", {
        "schema": SCHEMA, "verdict": rev.verdict, "malformed_reason": rev.malformed["reason"] if rev.malformed else None,
        "violation_codes": codes, "warning_codes": list(rev.warnings), "calls": len(log.records),
        "model_id": rec["model_id"], "request_sha": req.request_sha, "response_sha": rec["response_sha"],
        "cost_usd": rec["cost_usd_if_reported"], "provenance": PROVENANCE})

    detail = f" ({rev.malformed['reason']})" if rev.malformed else (f" ({', '.join(codes)})" if codes else "")
    print(f"verdict: {rev.verdict}{detail}")
    u = rec["usage_if_reported"] or {}
    cost = rec["cost_usd_if_reported"]
    print(f"usage: input {u.get('input_tokens')}, output {u.get('output_tokens')}, cache write "
          f"{u.get('cache_creation_input_tokens')}, cache read {u.get('cache_read_input_tokens')}; "
          f"cost_usd {cost if cost is None else f'{cost:.6f}'}; stop_reason {rec['stop_reason']}")
    print(f"bundle: {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
