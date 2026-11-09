#!/usr/bin/env python3
"""M0 attempt runner, relay mode (WS-04; see worldsmith/runner/attempts.py and ADR 0005).

    python scripts/m0.py init    --role before|after|smoke --episode NAME [--scene FILE --n N]
    python scripts/m0.py step    bundles/m0/NAME     # replay answered calls; print the next one
    python scripts/m0.py respond bundles/m0/NAME K RAW_FILE [--error TEXT]
    python scripts/m0.py gate    bundles/m0/BEFORE bundles/m0/AFTER   # writes bundles/m0/gate.json
    python scripts/m0.py check   bundles/m0/NAME     # bundle schema check

`step` exits 0 when the episode is complete (its bundle written), 3 when a call is pending.
"""

import argparse
import json
import sys
from pathlib import Path

from worldsmith.runner import attempts
from worldsmith.runner.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["init", "step", "respond", "gate", "check"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--config", default=str(ROOT / "config" / "m0.yaml"))
    ap.add_argument("--role")
    ap.add_argument("--episode")
    ap.add_argument("--scene")
    ap.add_argument("--n", type=int)
    ap.add_argument("--error")
    a = ap.parse_args(argv)
    if a.what == "check":
        errs = attempts.validate_bundle(a.args[0])
        print("\n".join(errs) or "bundle ok")
        return 1 if errs else 0
    if a.what == "respond":
        d, k, raw = Path(a.args[0]), int(a.args[1]), Path(a.args[2]).read_text(encoding="utf-8")
        r = attempts.respond(d / "calls", k, raw, error=a.error)
        print(f"call {k:03d} answered ({len(raw)} chars, relay {r['relay_elapsed_s']} s)")
        return 0
    cfg = load_config(a.config, ROOT)
    print(f"config {cfg['_sha256']}  controller {cfg['controller']['sha256']}")
    if a.what == "init":
        d = ROOT / "bundles" / "m0" / a.episode
        ep = attempts.init_episode(cfg, a.role, d, a.episode, scene_path=a.scene, n_attempts=a.n)
        print(f"episode {ep['episode']} ({ep['role']}) scene {ep['scene_hash']} nonces {ep['nonces']}")
        return 0
    if a.what == "step":
        d = Path(a.args[0])
        try:
            s = attempts.run_episode(attempts.RelayClient(d / "calls"), cfg, d)
        except attempts.Pending as p:
            req = p.request
            print(f"PENDING {p.index:03d} {req['tool']} timeout_s={req['timeout_s']} args={json.dumps(req['args'])}")
            if "code_file" in req:
                print(f"  code: {d / 'calls' / req['code_file']}  ({req['code_arg']}, sha256 {req['code_sha256']})")
            return 3
        print(json.dumps({k: s[k] for k in ("episode", "status", "detail", "successes", "n", "counts")}))
        for x in s["attempts"]:
            print(f"  attempt {x['attempt']}: {x['outcome']} events={x['events']} in_area={x['fell_in_area']}")
        return 0
    g = attempts.evaluate_gate(cfg, a.args[0], a.args[1])
    out = ROOT / "bundles" / "m0" / "gate.json"
    out.write_text(json.dumps(g, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(g, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
