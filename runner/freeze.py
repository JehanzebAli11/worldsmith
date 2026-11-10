"""P2 freeze manifest: schema and refusal checks (WS-11, SPEC section 9, ADR 0010).

P2 is the frozen live Autopilot player. Before any P2 attempt the runner must prove the player is
the declared one: a freeze manifest lists SHA-256 hashes of the exported harness, its options,
the controllers, the prompt, the kit and an initial memory snapshot, plus the LLM and Jev model
versions, with `evolve` disabled, memory reset to the snapshot before every attempt,
`AUTOPILOT_SHARE=0` and a declared wall-clock cap (SPEC sections 9 and 12).

This module is code and tests only: WS-11 makes no Autopilot run. `check_manifest` returns every
refusal it finds (never just the first); `require_p2` raises `P2Refused` if there is any. The
experiment config declares the manifest file and its hash; a P2 experiment is refused on any
refusal, and refused anyway while no P2 runner exists.

The manifest hash is the SHA-256 of its canonical JSON (sorted keys, compact separators, UTF-8),
so whitespace edits do not change it and any field edit does.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

FREEZE_SCHEMA = "worldsmith.p2freeze/0.1"
COMPONENTS = ("harness", "options", "controllers", "prompt", "kit", "memory_snapshot")
REFUSAL_CODES = (
    "MANIFEST_MISSING", "MANIFEST_INVALID", "MANIFEST_HASH_MISMATCH", "COMPONENT_MISSING",
    "COMPONENT_HASH_MISMATCH", "MEMORY_SNAPSHOT_MISSING", "EVOLVE_ENABLED", "MEMORY_RESET_DISABLED",
    "AUTOPILOT_SHARE_NOT_ZERO", "WALL_CLOCK_CAP_MISSING",
)
_TOP = {"schema", "autopilot", "components", "models", "evolve", "memory_reset_before_each_attempt",
        "autopilot_share", "wall_clock_cap_s"}
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX40 = re.compile(r"[0-9a-f]{40}")


class P2Refused(RuntimeError):
    """The P2 player is not provably frozen; `refusals` lists why."""

    def __init__(self, refusals: list[dict]):
        self.refusals = list(refusals)
        self.codes = sorted({r["code"] for r in self.refusals})
        super().__init__("P2 refused: " + "; ".join(f"{r['code']}: {r['detail']}" for r in self.refusals))


def canonical(manifest: dict) -> str:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(canonical(manifest).encode("utf-8")).hexdigest()


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _r(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def check_manifest(manifest_path, *, declared_sha256: str | None, root, runtime: dict | None = None,
                   env: dict | None = None) -> list[dict]:
    """Every reason to refuse P2 ([] if none). `root` resolves the component paths; `runtime`
    is what the harness reports at start (`{"evolve": bool}`), `env` the environment the run
    would use (`AUTOPILOT_SHARE` must be "0")."""
    out: list[dict] = []
    p = Path(manifest_path)
    if not p.is_file():
        return [_r("MANIFEST_MISSING", f"no freeze manifest at {p}")]
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [_r("MANIFEST_INVALID", f"not JSON: {exc}")]
    if not isinstance(m, dict):
        return [_r("MANIFEST_INVALID", "not a JSON object")]
    keys = set(m)
    if keys != _TOP:
        out.append(_r("MANIFEST_INVALID", f"keys differ: missing {sorted(_TOP - keys)}, unknown {sorted(keys - _TOP)}"))
    if m.get("schema") != FREEZE_SCHEMA:
        out.append(_r("MANIFEST_INVALID", f"schema must be {FREEZE_SCHEMA}"))
    sha = manifest_sha256(m)
    if not (isinstance(declared_sha256, str) and _HEX64.fullmatch(declared_sha256)):
        out.append(_r("MANIFEST_HASH_MISMATCH", "the experiment declares no manifest sha256"))
    elif sha != declared_sha256:
        out.append(_r("MANIFEST_HASH_MISMATCH", f"manifest sha256 {sha} != declared {declared_sha256}"))
    ap = m.get("autopilot")
    if not (isinstance(ap, dict) and isinstance(ap.get("commit"), str) and _HEX40.fullmatch(ap["commit"])):
        out.append(_r("MANIFEST_INVALID", "autopilot.commit must be a 40-hex commit"))
    models = m.get("models")
    if not (isinstance(models, dict) and all(isinstance(models.get(k), str) and models[k] for k in ("llm", "jev"))):
        out.append(_r("MANIFEST_INVALID", "models.llm and models.jev must name pinned versions"))
    comps = m.get("components") if isinstance(m.get("components"), dict) else {}
    for name in COMPONENTS:
        c = comps.get(name)
        missing_code = "MEMORY_SNAPSHOT_MISSING" if name == "memory_snapshot" else "COMPONENT_MISSING"
        if not (isinstance(c, dict) and isinstance(c.get("path"), str) and isinstance(c.get("sha256"), str)
                and _HEX64.fullmatch(c["sha256"])):
            out.append(_r(missing_code, f"components.{name} needs path and sha256"))
            continue
        f = Path(root) / c["path"]
        if not f.is_file():
            out.append(_r(missing_code, f"components.{name}: {c['path']} does not exist"))
            continue
        got = _sha_file(f)
        if got != c["sha256"]:
            out.append(_r("COMPONENT_HASH_MISMATCH", f"components.{name}: {c['path']} sha256 {got} != {c['sha256']}"))
    for name in sorted(set(comps) - set(COMPONENTS)):
        out.append(_r("MANIFEST_INVALID", f"unknown component {name!r}"))
    if m.get("evolve") is not False:
        out.append(_r("EVOLVE_ENABLED", f"manifest evolve is {m.get('evolve')!r}; it must be false"))
    if runtime is not None and runtime.get("evolve") is not False:
        out.append(_r("EVOLVE_ENABLED", f"the harness reports evolve {runtime.get('evolve')!r}"))
    if m.get("memory_reset_before_each_attempt") is not True:
        out.append(_r("MEMORY_RESET_DISABLED", "memory must be reset to the snapshot before each attempt"))
    if m.get("autopilot_share") != 0 or isinstance(m.get("autopilot_share"), bool):
        out.append(_r("AUTOPILOT_SHARE_NOT_ZERO", f"manifest autopilot_share is {m.get('autopilot_share')!r}"))
    if env is not None and env.get("AUTOPILOT_SHARE") != "0":
        out.append(_r("AUTOPILOT_SHARE_NOT_ZERO", f"environment AUTOPILOT_SHARE is {env.get('AUTOPILOT_SHARE')!r}"))
    cap = m.get("wall_clock_cap_s")
    if not (isinstance(cap, (int, float)) and not isinstance(cap, bool) and 0 < cap <= 7200):
        out.append(_r("WALL_CLOCK_CAP_MISSING", f"wall_clock_cap_s must be a number in (0, 7200], got {cap!r}"))
    return out


def require_p2(manifest_path, *, declared_sha256, root, runtime=None, env=None) -> str:
    """The manifest hash if P2 may start; raises P2Refused otherwise."""
    refusals = check_manifest(manifest_path, declared_sha256=declared_sha256, root=root, runtime=runtime, env=env)
    if refusals:
        raise P2Refused(refusals)
    return declared_sha256
