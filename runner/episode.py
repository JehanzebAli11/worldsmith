"""One orchestrated episode (WS-11, SPEC sections 11 and 12, ADR 0010).

    load scene -> validate -> build + read-back -> N attempts -> classify
      -> if not N/N SUCCESS and proposals left: propose (B1 hand fix | B2 random | B3 architect)
         -> validate -> if accepted: apply + rebuild + N attempts, else count the rejection
      -> ... -> canonical outcome summary + bundle

Rules (declared in the experiment config, checked here):

- Revision i+1 patches the last ACCEPTED scene (the scene that was last built and played); a
  rejected proposal leaves the scene and the observations unchanged.
- One player (version, controller hash, T_attempt, N) for the whole experiment; the episode
  refuses a controller other than the experiment's.
- Hard caps per episode (`EpisodeCaps`): at most `caps.attempts_per_episode` (12) play calls and
  `caps.architect_calls_per_episode` (3) architect calls; at most `max_revisions` (3)
  proposals of any kind. Exceeding one is a bug and raises `CapExceeded`; nothing is sent.
- A version with 0 < successes < N is INCONSISTENT and the episode stops (SPEC section 9: stop and
  debug, never lower the bar). A version with a system outcome (HARNESS_ERROR, CONNECTOR_ERROR) or
  a build failure stops the episode too: no proposal is spent on a harness problem.
- An architect provider error uses its slot (WS-10) and ends the episode as CONNECTOR_ERROR.

Studio calls go through any `StudioClient`. With `attempts.RelayClient` every request and answer
is a file under `calls/`, answered calls are replayed and never re-sent, and the run stops at the
first unanswered call (`attempts.Pending`). Architect replies are stored under
`architect/replies/<request_sha>.json` the moment they arrive (`CachingArchitect`), so a resumed
episode replays them instead of paying again. The episode is deterministic given those files, so
resuming is simply running it again. Bundle files are written once; a different second write
raises `BundleConflict`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from worldsmith.architect import client as acl
from worldsmith.architect import loop as lp
from worldsmith.architect import prompt as pr
from worldsmith.baselines import random_valid as b2
from worldsmith.compiler import build, geometry
from worldsmith.grammar import scene as sc
from worldsmith.runner import attempts as at
from worldsmith.runner import p0, players, taxonomy
from worldsmith.runner.classify import gap_area, spawn_centre
from worldsmith.runner.console import NONCE_RE, new_nonce
from worldsmith.validator.validate import validate

EPISODE_SCHEMA = "worldsmith.episode/0.1"
VERSION_SCHEMA = "worldsmith.version_summary/0.1"
SUMMARY_SCHEMA = "worldsmith.episode_summary/0.1"
REPLY_SCHEMA = "worldsmith.architect_reply/0.1"
GAP_ID = "gap_1"
SYSTEM_OUTCOMES = ("HARNESS_ERROR", "CONNECTOR_ERROR")
STOP_STATUSES = ("BUILD_FAILED", "CONNECTOR_ERROR", "SYSTEM_ERROR", "INCONSISTENT")
PAIRED_FIX_NOTE = ("not evaluated inside an episode: it needs a later N/N SUCCESS on a bridge-length-only "
                   "fix (post-fix evidence, withheld from the architect; ADR 0009)")
DRY_RUN_NOTE = ("DRY RUN: Studio answers simulated by worldsmith.runner.dryrun.FakeOperator and architect "
                "replies from hand-written WS-10 fixtures. Not Roblox, not a model, not a result.")


class CapExceeded(RuntimeError):
    """An episode tried to exceed a declared cap (a bug; nothing was sent)."""


class BundleConflict(RuntimeError):
    """A bundle file already exists with different content; bundles are never overwritten."""


class EpisodeError(ValueError):
    """The episode folder does not match the experiment (config, controller, scene)."""


class PromptCapExceeded(RuntimeError):
    """An architect request is larger than the declared prompt_bytes_cap; it was not sent."""


class RunInterrupt(Exception):
    """Control flow, not a failure: stop the run here; nothing is counted or stored for it."""


class ArchitectPending(RunInterrupt):
    """`defer` mode: the next action is a live architect call, to be made on another machine
    (`run_experiment.py architect`). No slot is used and nothing is written."""

    def __init__(self, request_sha: str, role: str):
        super().__init__(f"architect call pending: request {request_sha} (role {role})")
        self.request_sha, self.role = request_sha, role


class ArchitectCallDone(RunInterrupt):
    """`one` mode: the one pending architect call was made and its reply (or provider error) is
    stored; the run stops before anything else happens."""

    def __init__(self, request_sha: str, role: str, error: str | None = None, stop_reason: str | None = None):
        super().__init__(f"architect reply stored for request {request_sha}")
        self.request_sha, self.role, self.error, self.stop_reason = request_sha, role, error, stop_reason


ARCHITECT_MODES = ("call", "defer", "one")


# --------------------------------------------------------------------------- small helpers

def _dumps(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _jl(rec) -> str:
    return json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_once(path: Path, text: str) -> None:
    """Write `text` unless the file exists with exactly that text; refuse a different one."""
    path = Path(path)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise BundleConflict(f"{path} exists with different content; bundles are never overwritten")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def request_bytes_bound(system: str, user: str, cache_system: bool) -> int:
    """jzapi's offline prompt-token bound (`jzapi.api.bytes_upper_bound`) for the params
    `JzapiArchitect` sends: UTF-8 bytes of the JSON system blocks and messages, plus 64 bytes per
    message and one more. Kept equal to jzapi's by a test."""
    block = {"type": "text", "text": system}
    if cache_system:
        block["cache_control"] = {"type": "ephemeral"}
    payload = {"system": [block], "messages": [{"role": "user", "content": user}]}
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 64 * 2


# --------------------------------------------------------------------------- caps

class EpisodeCaps:
    def __init__(self, max_attempts: int, max_architect_calls: int, max_proposals: int):
        self.max_attempts, self.max_architect_calls, self.max_proposals = max_attempts, max_architect_calls, max_proposals
        self.attempts = self.architect_calls = self.proposals = 0

    def play(self):
        if self.attempts >= self.max_attempts:
            raise CapExceeded(f"attempt {self.attempts + 1} would exceed the cap of {self.max_attempts} per episode")
        self.attempts += 1

    def proposal(self, architect: bool):
        if self.proposals >= self.max_proposals:
            raise CapExceeded(f"proposal {self.proposals + 1} would exceed max_revisions = {self.max_proposals}")
        if architect and self.architect_calls >= self.max_architect_calls:
            raise CapExceeded(f"architect call {self.architect_calls + 1} would exceed the cap of "
                              f"{self.max_architect_calls} per episode")
        self.proposals += 1
        if architect:
            self.architect_calls += 1


# --------------------------------------------------------------------------- architect replies

class CachingArchitect:
    """Wraps an architect: a reply already stored for a request hash is replayed (no call, no
    cost); otherwise the inner architect (built lazily by `factory`, so a fully cached episode
    never constructs a live client) is called once and its reply stored before it is used. A
    provider error is stored too and replayed as the same error. `LiveCallRefused` (a switch or
    the budget said no) is not stored: nothing happened."""

    def __init__(self, factory: Callable[[], Any], replies_dir, *, model_role: str, mode: str = "call",
                 expect_sha: str | None = None):
        if mode not in ARCHITECT_MODES:
            raise ValueError(f"architect mode must be one of {ARCHITECT_MODES}")
        self.mode, self.expect_sha = mode, expect_sha
        self._factory = factory
        self._inner = None
        self.dir = Path(replies_dir)
        self.model_role = model_role
        self.model_id = None
        self.provider = "cache"
        self.calls_made = 0
        self.replayed = 0

    def _path(self, sha: str, kind: str = "json") -> Path:
        return self.dir / f"{sha}.{'error.json' if kind == 'error' else 'json'}"

    def stored(self) -> int:
        return len(list(self.dir.glob("*.json"))) if self.dir.is_dir() else 0

    def propose(self, req: pr.ArchitectRequest) -> acl.RawResponse:
        ok, err = self._path(req.request_sha), self._path(req.request_sha, "error")
        if ok.exists():
            r = _load(ok)
            if r.get("condition_role") != self.model_role:
                raise EpisodeError(f"stored reply {ok.name} is for role {r.get('condition_role')!r}, "
                                   f"not {self.model_role!r}")
            self.replayed += 1
            return acl.RawResponse(text=r["response_text"], model_role=r["model_role"], model_id=r["model_id"],
                                   provider=r["provider"], stop_reason=r["stop_reason"], usage=r["usage"],
                                   cost_usd=r["cost_usd"], latency_s=r["latency_s"], request_id=r["request_id"])
        if err.exists():
            self.replayed += 1
            raise acl.ArchitectCallError(_load(err)["error"] + " (replayed from the stored error)")
        # A live call is due here. defer: hand it to another machine (nothing used or written).
        if self.mode == "defer":
            raise ArchitectPending(req.request_sha, self.model_role)
        if self.mode == "one" and self.expect_sha is not None and req.request_sha != self.expect_sha:
            raise EpisodeError(f"the request now due ({req.request_sha[:12]}…) is not the pending request "
                               f"({self.expect_sha[:12]}…); are both machines on the same files? Nothing was sent")
        if self._inner is None:
            self._inner = self._factory()
        self.model_id = getattr(self._inner, "model_id", None)
        try:
            resp = self._inner.propose(req)
        except acl.LiveCallRefused:
            raise
        except Exception as exc:
            self.calls_made += 1
            write_once(err, _dumps({"schema": REPLY_SCHEMA, "request_sha": req.request_sha,
                                    "condition_role": self.model_role, "error": f"{type(exc).__name__}: {exc}"}))
            if self.mode == "one":
                raise ArchitectCallDone(req.request_sha, self.model_role, error=f"{type(exc).__name__}: {exc}") from None
            raise
        self.calls_made += 1
        write_once(ok, _dumps({"schema": REPLY_SCHEMA, "request_sha": req.request_sha,
                               "condition_role": self.model_role, "model_role": resp.model_role, "model_id": resp.model_id, "provider": resp.provider,
                               "response_text": resp.text, "stop_reason": resp.stop_reason, "usage": resp.usage,
                               "cost_usd": resp.cost_usd, "latency_s": resp.latency_s,
                               "request_id": resp.request_id}))
        if self.mode == "one":
            raise ArchitectCallDone(req.request_sha, self.model_role, stop_reason=resp.stop_reason)
        return resp


# --------------------------------------------------------------------------- proposers

@dataclass
class Proposal:
    index: int
    source: str                    # none | hand_fix | random_valid | architect
    verdict: str                   # ACCEPTED | REJECTED | MALFORMED | ERROR
    patch: dict | None = None
    malformed: dict | None = None
    codes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    graph_walk: str | None = None
    base_hash: str | None = None
    candidate_hash: str | None = None
    model_calls: int = 0
    request_sha: str | None = None
    cost_usd: float | None = None
    error: str | None = None
    detail: dict = field(default_factory=dict)
    scene: dict | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {"index": self.index, "source": self.source, "verdict": self.verdict, "patch": self.patch,
                "malformed": self.malformed, "codes": list(self.codes), "warnings": list(self.warnings),
                "graph_walk": self.graph_walk, "base_hash": self.base_hash, "candidate_hash": self.candidate_hash,
                "model_calls": self.model_calls, "request_sha": self.request_sha, "cost_usd": self.cost_usd,
                "error": self.error, "detail": self.detail}


def _judge(scene, patch, stage):
    res = validate(scene, patch, stage=stage)
    graph = res.graph or {}
    return res, (graph.get("walk") or {}).get("result") if graph else None


class NoProposer:
    source, architect, max_revisions = "none", False, 0
    exhausted = True

    def propose(self, scene, observed):
        raise CapExceeded("B0 proposes nothing")


class HandFixProposer:
    """B1: the hand-authored fix, one patch (a positive control, SPEC section 12)."""
    source, architect, max_revisions = "hand_fix", False, 1

    def __init__(self, patch: dict, stage: str, patch_file: str):
        self.patch, self.stage, self.file, self.used = patch, stage, patch_file, 0

    @property
    def exhausted(self):
        return self.used >= self.max_revisions

    def propose(self, scene, observed):
        if self.exhausted:
            raise CapExceeded("B1 has one patch")
        self.used += 1
        res, walk = _judge(scene, self.patch, self.stage)
        return Proposal(self.used, self.source, "ACCEPTED" if res.ok else "REJECTED", patch=self.patch,
                        codes=sorted(res.codes), warnings=sorted({w.code for w in res.warnings}), graph_walk=walk,
                        base_hash=sc.canonical_hash(scene),
                        candidate_hash=sc.canonical_hash(res.scene) if res.ok else None,
                        detail={"patch_file": self.file}, scene=res.scene if res.ok else None)


class RandomProposer:
    """B2: WS-09's capped random valid sampler through its `make_b2_proposer` hook."""
    source, architect = "random_valid", False

    def __init__(self, seed: int, stage: str, max_revisions: int):
        self.inner = b2.make_b2_proposer(seed=seed, stage=stage, max_revisions=max_revisions)
        self.stage, self.max_revisions = stage, max_revisions

    @property
    def exhausted(self):
        return self.inner.remaining <= 0

    def propose(self, scene, observed):
        prop = self.inner.propose(scene)
        cand = None
        if prop.ok:
            res = validate(scene, prop.patch, stage=self.stage)
            if not res.ok:
                raise RuntimeError("B2 record and validator disagree")  # deterministic; never expected
            cand = res.scene
        return Proposal(prop.index + 1, self.source, "ACCEPTED" if prop.ok else "REJECTED", patch=prop.patch,
                        codes=list(prop.codes), warnings=list(prop.warnings), graph_walk=prop.graph_walk,
                        base_hash=prop.base_hash, candidate_hash=sc.canonical_hash(cand) if cand else None,
                        detail={"b2": prop.to_dict()}, scene=cand)


class ArchitectProposer:
    """B3: one WS-10 revision per proposal (`loop.revise`), sharing one RevisionBudget per
    episode; the earlier revisions' feedback goes into each new request."""
    source, architect = "architect", True

    def __init__(self, architect, *, stage: str, max_revisions: int, prompt: pr.Prompt, episode: str,
                 prompt_bytes_cap: int, cache_system: bool):
        self.arch, self.stage, self.prompt, self.episode = architect, stage, prompt, episode
        self.budget = lp.RevisionBudget(max_revisions)
        self.max_revisions = max_revisions
        self.cap, self.cache_system = prompt_bytes_cap, cache_system
        self.log = lp.CallLog()
        self.previous: list[dict] = []

    @property
    def exhausted(self):
        return self.budget.exhausted

    def propose(self, scene, observed):
        ctx = pr.build_context(scene, observed, stage=self.stage, revision=self.budget.used + 1,
                               max_revisions=self.budget.max_revisions, previous=self.previous)
        req = pr.make_request(ctx, self.prompt)
        nbytes = request_bytes_bound(req.system, req.user, self.cache_system)
        if nbytes > self.cap:
            raise PromptCapExceeded(f"request {req.request_sha[:12]}… bounds to {nbytes} prompt tokens > "
                                    f"declared prompt_bytes_cap {self.cap}; not sent")
        base = sc.canonical_hash(scene)
        try:
            rev = lp.revise(self.arch, scene=scene, observed=observed, stage=self.stage, budget=self.budget,
                            previous=self.previous, prompt=self.prompt, episode=self.episode, log=self.log)
        except (acl.LiveCallRefused, EpisodeError, RunInterrupt):
            raise
        except Exception as exc:  # provider error: logged by revise, slot used, episode CONNECTOR_ERROR
            rec = self.log.records[-1] if self.log.records else {}
            return Proposal(self.budget.used, self.source, "ERROR", base_hash=base, model_calls=1,
                            request_sha=req.request_sha, error=rec.get("error") or f"{type(exc).__name__}: {exc}")
        if rev.record.get("request_sha") != req.request_sha:
            raise RuntimeError("architect request hash drift")  # the loop built another request: a bug
        self.previous.append(rev.feedback())
        cand = rev.candidate_scene
        rec = rev.record
        return Proposal(rev.index, self.source, rev.verdict, patch=rev.patch, malformed=rev.malformed,
                        codes=sorted(rev.codes), warnings=list(rev.warnings),
                        graph_walk=(((rec.get("validator_result") or {}).get("graph") or {}).get("walk") or {}).get("result"),
                        base_hash=base, candidate_hash=sc.canonical_hash(cand) if cand else None, model_calls=1,
                        request_sha=req.request_sha, cost_usd=rec.get("cost_usd_if_reported"),
                        detail={"model_role": rec.get("model_role"), "model_id": rec.get("model_id"),
                                "provider": rec.get("provider"), "stop_reason": rec.get("stop_reason"),
                                "prompt_bytes_bound": nbytes},
                        scene=cand)


def make_proposer(cfg: dict, ep: dict, ep_dir: Path, architect_factory=None, *, architect_mode: str = "call",
                  architect_expect: str | None = None):
    cond = cfg["_conditions"][ep["condition"]]
    kind = cond["proposer"]
    if kind == "none":
        return NoProposer()
    if kind == "hand_fix":
        return HandFixProposer(cfg["_hand_patches"][cond["id"]], cfg["stage"], cond["patch"])
    if kind == "random_valid":
        return RandomProposer(ep["seed"], cfg["stage"], cfg["max_revisions"])
    if architect_factory is None:
        raise EpisodeError(f"condition {cond['id']} needs an architect and none was given")
    arch = CachingArchitect(architect_factory, Path(ep_dir) / "architect" / "replies", model_role=cond["role"],
                            mode=architect_mode, expect_sha=architect_expect)
    return ArchitectProposer(arch, stage=cfg["stage"], max_revisions=cfg["max_revisions"], prompt=cfg["_prompt"],
                             episode=ep["episode"], prompt_bytes_cap=cfg["architect"]["prompt_bytes_cap"],
                             cache_system=cfg["_ladder"]["cache_system"])


# --------------------------------------------------------------------------- episode folder

def max_versions(cfg: dict, cond: dict) -> int:
    return 1 + {"none": 0, "hand_fix": 1}.get(cond["proposer"], cfg["max_revisions"])


def init_episode(cfg: dict, cond_id: str, index: int, ep_dir, *, nonces=None, dry_run: bool = False) -> dict:
    """Create episode.json (nonces fixed up front for every possible scene version)."""
    d = Path(ep_dir)
    if (d / "episode.json").exists():
        raise BundleConflict(f"{d} already holds an episode")
    cond = cfg["_conditions"][cond_id]
    if not (isinstance(index, int) and 0 <= index < cond["episodes"]):
        raise EpisodeError(f"{cond_id} declares {cond['episodes']} episode(s); index {index} is outside")
    nv, n = max_versions(cfg, cond), cfg["n_attempts"]
    if nonces is None:
        nonces = [[new_nonce() for _ in range(n)] for _ in range(nv)]
    flat = [x for v in nonces for x in v]
    if len(nonces) != nv or any(len(v) != n for v in nonces) or len(set(flat)) != len(flat) \
            or not all(NONCE_RE.fullmatch(x) for x in flat):
        raise ValueError(f"need {nv} x {n} distinct valid nonces")
    pl = cfg["player"]
    ep = {
        "schema": EPISODE_SCHEMA, "experiment": cfg["experiment"], "episode": f"{cond_id}/ep{index}",
        "condition": cond_id, "proposer": cond["proposer"], "index": index,
        "seed": cond["seeds"][index] if cond["proposer"] == "random_valid" else None,
        "role": cond.get("role"), "stage": cfg["stage"], "dry_run": bool(dry_run),
        "config_sha256": cfg["_sha256"], "scene_hash": cfg["scene"]["canonical_hash"],
        "player": {"version": pl["version"], "tier": pl["tier"], "driver": pl["driver"],
                   "controller_sha256": pl["controller_sha256"], "route_sha256": pl["route"]["sha256"],
                   "t_attempt_s": pl["timing"]["t_attempt"], "freeze_manifest_sha": None},
        "n_attempts": n, "pass_k": cfg["pass_k"], "max_revisions": nv - 1, "nonces": nonces,
        "caps": dict(cfg["caps"]),
    }
    write_once(d / "episode.json", _dumps(ep))
    write_once(d / "controller.luau", cfg["_controller_text"])
    return ep


def _check_episode(cfg: dict, d: Path) -> dict:
    ep = _load(d / "episode.json")
    if ep.get("schema") != EPISODE_SCHEMA:
        raise EpisodeError(f"{d}: episode.json schema")
    if ep["config_sha256"] != cfg["_sha256"]:
        raise EpisodeError(f"{d}: created under config {ep['config_sha256'][:12]}…, not {cfg['_sha256'][:12]}…")
    ctl = (d / "controller.luau").read_text(encoding="utf-8")
    pl = ep["player"]
    if ctl != cfg["_controller_text"] or p0.controller_sha256(ctl) != pl["controller_sha256"] \
            or pl["controller_sha256"] != cfg["player"]["controller_sha256"] \
            or pl["version"] != cfg["player"]["version"] or pl["t_attempt_s"] != cfg["player"]["timing"]["t_attempt"] \
            or ep["n_attempts"] != cfg["n_attempts"]:
        raise EpisodeError(f"{d}: player differs from the experiment's (version, controller, T_attempt or N): "
                           "refusing to mix players")
    if ep["scene_hash"] != sc.canonical_hash(cfg["_scene"]):
        raise EpisodeError(f"{d}: scene hash differs from the experiment's")
    return ep


# --------------------------------------------------------------------------- one scene version

def _hypothesis(row: dict, scene: dict, area: dict) -> dict | None:
    """SPEC section 10 conditions an episode can check by itself (a and b); c (paired fix) is
    left open, so the cause stays UNCLASSIFIED (WS-08 rule)."""
    if row["outcome"] != "FELL":
        return None
    kill = next((e for e in row["events_detail"] if e["event"] == "KILL_FLOOR_TOUCHED"), None)
    a = {"ok": row.get("fell_in_area") is True and kill is not None, "kill_floor_pos": kill["pos"] if kill else None,
         "area": area}
    g = taxonomy.geometry_check(scene, GAP_ID)
    return {"tag": None, "cause": taxonomy.UNCLASSIFIED,
            "conditions": {"fell_in_gap_area": a, "geometry": g, "paired_fix": {"ok": None, "note": PAIRED_FIX_NOTE}}}


def run_version(call, cfg: dict, scene: dict, nonces: list, k: int) -> dict:
    """Build, read back and run N attempts of one scene version (the WS-04 transaction and
    attempt protocol, ADR 0005), classified with the WS-08 taxonomy. No files are written."""
    tt = cfg["tool_timeouts_s"]
    origin = tuple(cfg["origin"])
    h = sc.canonical_hash(scene)
    status, detail = "complete", ""
    build_info = {"ok": False, "build_line": None, "descendants": None, "expected_descendants": None,
                  "readback_differences": None}
    rows, raws, readback = [], {}, None
    ok, why = at._ensure_stopped(call, tt)
    if not ok:
        status, detail = "CONNECTOR_ERROR", why
    if status == "complete":
        expected = geometry.expected_tree(scene, origin=origin)
        n_exp = geometry.descendant_count(expected)
        build_info["expected_descendants"] = n_exp
        r = call("run_code", {"command": build.compile_build(scene, origin=origin)}, tt["run_code"])
        m = re.search(r"WS_BUILD_OK\|([0-9a-f]{64})\|(\d+)", r.text or "")
        if r.error or not m or m.group(1) != h or int(m.group(2)) != n_exp:
            status, detail = "BUILD_FAILED", r.error or f"no matching WS_BUILD_OK line (want {h[:12]}…|{n_exp})"
        else:
            build_info.update(build_line=m.group(0), descendants=int(m.group(2)))
    if status == "complete":
        r = call("run_code", {"command": build.compile_readback()}, tt["run_code"])
        try:
            if r.error:
                raise ValueError(r.error)
            diffs = build.compare_tree(expected, build.parse_readback(r.text))
        except ValueError as e:
            diffs = [f"read-back failed: {e}"]
        build_info["readback_differences"] = len(diffs)
        readback = "\n".join(diffs + [f"{len(expected['nodes'])} expected nodes, {len(diffs)} differences"
                                      f" (tolerance {build.TOLERANCE})"]) + "\n"
        if diffs:
            status, detail = "BUILD_FAILED", f"read-back: {len(diffs)} differences"
        else:
            build_info["ok"] = True
    if status == "complete":
        area = gap_area(scene, GAP_ID, cfg["gap_area_margin_studs"])
        spawn = spawn_centre(scene)
        params, ctl = cfg["_params"], cfg["_controller_text"]
        for i, nonce in enumerate(nonces, start=1):
            ok, why = at._ensure_stopped(call, tt)
            if not ok:
                res = taxonomy.classify_result(None, nonce, params, area, cfg["_stuck"], call_error=why)
                rows.append(_row(i, nonce, None, None, None, res, scene, area))
                status, detail = "CONNECTOR_ERROR", why
                break
            t_play = tt["run_script_in_play_mode"]
            r = call("run_script_in_play_mode",
                     {"code": p0.instantiate(ctl, nonce), "mode": "start_play", "timeout": int(t_play)}, t_play)
            tool_result, err = None, r.error
            if err is None:
                try:
                    tool_result = json.loads(r.text)
                except json.JSONDecodeError:
                    err = "the play tool's answer is not JSON"
            res = taxonomy.classify_result(tool_result, nonce, params, area, cfg["_stuck"], call_error=err,
                                           spawn_xz=spawn)
            raws[i] = r.text or ""
            rows.append(_row(i, nonce, r, len(call.calls), raws[i], res, scene, area))
            if res["outcome"] == "CONNECTOR_ERROR":
                # ADR 0011: the play tool gave no usable answer (an error, a timeout, a bare `null`);
                # Studio's state is unknown, so the episode stops here: no more attempts.
                status, detail = "CONNECTOR_ERROR", f"attempt {i}: {res['detail']}"
                break
        if status == "complete":
            ok, why = at._ensure_stopped(call, tt)
            if not ok:
                detail = f"after the attempts: {why}"
    successes = sum(r["outcome"] == "SUCCESS" for r in rows)
    n = len(nonces)
    if status != "complete":
        verdict = status
    elif any(r["outcome"] in SYSTEM_OUTCOMES for r in rows):
        verdict = "SYSTEM_ERROR"
    elif successes == n and len(rows) == n:
        verdict = "PASS"
    elif successes > 0:
        verdict = "INCONSISTENT"
    else:
        verdict = "FAIL"
    return {"version": k, "scene": scene, "scene_hash": h, "status": status, "detail": detail,
            "verdict": verdict, "build": build_info, "rows": rows, "raws": raws, "readback": readback,
            "n": n, "attempted": len(rows), "successes": successes}


def _row(i, nonce, r, call_index, raw, res, scene, area) -> dict:
    row = {"attempt": i, "nonce": nonce, "call_index": call_index,
           "t_wall": r.t_wall if r else None, "relay_elapsed_s": r.elapsed_s if r else None,
           "raw_file": f"attempts/{i}.raw.txt" if raw is not None else None,
           "raw_sha256": p0.sha256_text(raw) if raw is not None else None,
           "outcome": res["outcome"], "tags": res["tags"], "cause": res["cause"], "detail": res["detail"],
           "events": [e["event"] for e in res["events"]], "events_detail": res["events"],
           "events_agree": res["events_agree"], "fell_in_area": res["fell_in_area"], "stuck": res["stuck"],
           "report": res["report"], "tool": res["tool"]}
    row["hypothesis"] = _hypothesis(row, scene, area)
    return row


def observed_of(v: dict) -> dict:
    """The architect's view of one played version (SPEC section 7 item 6; ADR 0009 allow-list)."""
    return pr.observed_from_classification({"attempts": v["rows"], "episode_tags": []})


# --------------------------------------------------------------------------- the episode

class _Call:
    def __init__(self, client, caps: EpisodeCaps):
        self.client, self.caps, self.calls = client, caps, []

    def __call__(self, tool, args, timeout_s):
        if tool == "run_script_in_play_mode":
            self.caps.play()
        try:
            r = self.client.call(tool, args, timeout_s)
        except at.CallTimeout as e:
            r = at.CallResult(text="", error=f"timeout after {timeout_s} s ({e})")
        self.calls.append({"index": len(self.calls) + 1, "tool": tool, "timeout_s": timeout_s, "error": r.error,
                           "elapsed_s": r.elapsed_s, "t_wall": r.t_wall})
        return r


def run_episode(client, cfg: dict, ep_dir, *, architect_factory=None, proposer=None, architect_mode: str = "call",
                architect_expect: str | None = None) -> dict:
    """Run (or replay) one episode and write its bundle; returns the summary. Raises
    `attempts.Pending` at the first unanswered relay call (only the request and any architect
    reply are written then)."""
    d = Path(ep_dir)
    ep = _check_episode(cfg, d)
    cond = cfg["_conditions"][ep["condition"]]
    caps = EpisodeCaps(cfg["caps"]["attempts_per_episode"], cfg["caps"]["architect_calls_per_episode"],
                       ep["max_revisions"])
    call = _Call(client, caps)
    prop = proposer or make_proposer(cfg, ep, d, architect_factory, architect_mode=architect_mode,
                                     architect_expect=architect_expect)
    scene = cfg["_scene"]
    write_once(d / "v1" / "scene.json", sc.canonical_json(scene) + "\n")
    versions = [run_version(call, cfg, scene, ep["nonces"][0], 1)]
    proposals: list[Proposal] = []
    status, detail = "complete", ""
    while True:
        v = versions[-1]
        if v["verdict"] != "FAIL":
            if v["verdict"] in STOP_STATUSES:
                status, detail = v["verdict"], v["detail"] or f"version {v['version']}: {v['verdict']}"
            break
        if prop.exhausted:
            break
        caps.proposal(prop.architect)
        p = prop.propose(v["scene"], observed_of(v))
        p.detail = {**p.detail, "on_version": v["version"]}
        proposals.append(p)
        if p.verdict == "ERROR":
            status, detail = "CONNECTOR_ERROR", f"architect provider error: {p.error}"
            break
        if p.verdict == "ACCEPTED":
            k = len(versions) + 1
            if k > len(ep["nonces"]):
                raise CapExceeded(f"version {k} has no declared nonces (max {len(ep['nonces'])})")
            write_once(d / f"v{k}" / "scene.json", sc.canonical_json(p.scene) + "\n")
            write_once(d / f"v{k}" / "patch.json", _dumps(p.patch))
            versions.append(run_version(call, cfg, p.scene, ep["nonces"][k - 1], k))
    summary = _summary(cfg, ep, cond, status, detail, versions, proposals, caps, prop)
    _write_bundle(d, cfg, ep, summary, versions, proposals, call.calls, prop)
    return summary


def _summary(cfg, ep, cond, status, detail, versions, proposals, caps, prop) -> dict:
    final = versions[-1]
    first = versions[0]
    passed_final = final["verdict"] == "PASS"
    cost = [p.cost_usd for p in proposals if p.cost_usd is not None]
    return {
        "schema": SUMMARY_SCHEMA, "experiment": ep["experiment"], "episode": ep["episode"],
        "condition": ep["condition"], "proposer": ep["proposer"], "role": ep["role"], "seed": ep["seed"],
        "stage": ep["stage"], "dry_run": ep["dry_run"], "config_sha256": ep["config_sha256"],
        "player": dict(ep["player"]), "n": ep["n_attempts"], "pass_k": ep["pass_k"],
        "status": status, "detail": detail,
        "passed_original": first["verdict"] == "PASS",
        "passed_final": passed_final,
        "repaired": passed_final and first["verdict"] != "PASS" and final["version"] > 1 and status == "complete",
        "final": {"version": final["version"], "scene_hash": final["scene_hash"], "successes": final["successes"],
                  "n": final["n"], "verdict": final["verdict"]},
        "versions": [{"version": v["version"], "scene_hash": v["scene_hash"], "status": v["status"],
                      "verdict": v["verdict"], "successes": v["successes"], "n": v["n"], "attempted": v["attempted"],
                      "outcomes": [r["outcome"] for r in v["rows"]],
                      "tags": [r["tags"] for r in v["rows"]]} for v in versions],
        "revisions": {"used": len(proposals), "max": ep["max_revisions"],
                      "accepted": sum(p.verdict == "ACCEPTED" for p in proposals),
                      "rejected": sum(p.verdict in ("REJECTED", "MALFORMED") for p in proposals),
                      "malformed": sum(p.verdict == "MALFORMED" for p in proposals),
                      "errors": sum(p.verdict == "ERROR" for p in proposals),
                      "verdicts": [p.verdict for p in proposals]},
        "attempts_used": caps.attempts, "architect_calls": caps.architect_calls,
        "model_calls": sum(p.model_calls for p in proposals),
        "cost_usd_reported": round(sum(cost), 9) if cost else None,
        "caps": {"attempts_per_episode": caps.max_attempts, "architect_calls_per_episode": caps.max_architect_calls,
                 "max_revisions": caps.max_proposals},
        "canonical": [{"version": v["version"], "scene_hash": v["scene_hash"], "outcomes": [r["outcome"] for r in v["rows"]],
                       "successes": v["successes"], "n": v["n"]} for v in versions],
        "disclosure": (DRY_RUN_NOTE if ep["dry_run"] else
                       "P0 player " + ep["player"]["version"] + ": " + cfg["player"]["claim"]),
    }


def _trace(cfg, ep, versions) -> list[dict]:
    pl = ep["player"]
    tick = cfg["player"]["timing"]["tick"]
    base = {"run_id": ep["experiment"], "episode": ep["episode"], "config_sha256": ep["config_sha256"],
            "player": {"tier": pl["tier"], "version": pl["version"], "driver": pl["driver"],
                       "controller_sha": pl["controller_sha256"], "freeze_manifest_sha": None,
                       "t_attempt_s": pl["t_attempt_s"]},
            "poll_interval_ms": round(tick * 1000), "max_polls": int(pl["t_attempt_s"] / tick),
            "tool_timeout_s": cfg["tool_timeouts_s"]["run_script_in_play_mode"], "dry_run": ep["dry_run"]}
    out = []
    for v in versions:
        for r in v["rows"]:
            common = {**base, "scene_hash": v["scene_hash"], "scene_version": v["version"], "attempt": r["attempt"],
                      "nonce": r["nonce"], "t_wall": r["t_wall"]}
            for e in r["events_detail"]:
                out.append({**common, "event": e["event"], "id": e["id"], "t_server": e["t_server"], "pos": e["pos"],
                            "cause": None})
            rep = r["report"] or {}
            out.append({**common, "event": r["outcome"], "tags": r["tags"], "t_server": None,
                        "pos": r["events_detail"][-1]["pos"] if r["events_detail"] else None, "cause": r["cause"],
                        "detail": r["detail"], "fell_in_area": r["fell_in_area"], "stuck": r["stuck"]["result"],
                        "polls_used": rep.get("ticks"), "samples": len(rep.get("samples") or []),
                        "controller_status": rep.get("status"), "t_end_s": rep.get("t_end"),
                        "tool_duration_s": (r.get("tool") or {}).get("duration_s"),
                        "relay_elapsed_s": r["relay_elapsed_s"], "events_agree": r["events_agree"]})
    return out


def _write_bundle(d: Path, cfg, ep, summary, versions, proposals, calls, prop) -> None:
    for v in versions:
        vd = d / f"v{v['version']}"
        if v["readback"] is not None:
            write_once(vd / "readback.compare.txt", v["readback"])
        for r in v["rows"]:
            if r["raw_file"]:
                write_once(vd / r["raw_file"], v["raws"][r["attempt"]])
            write_once(vd / "attempts" / f"{r['attempt']}.json", _dumps(r))
        write_once(vd / "summary.json", _dumps({
            "schema": VERSION_SCHEMA, "episode": ep["episode"], "version": v["version"], "scene_hash": v["scene_hash"],
            "status": v["status"], "detail": v["detail"], "verdict": v["verdict"], "build": v["build"],
            "n": v["n"], "attempted": v["attempted"], "successes": v["successes"],
            "attempts": [{k: r[k] for k in ("attempt", "outcome", "tags", "cause", "fell_in_area", "events")}
                         | {"stuck": r["stuck"]["result"]} for r in v["rows"]]}))
    write_once(d / "proposals.json", _dumps([p.to_dict() for p in proposals]))
    if isinstance(prop, ArchitectProposer):
        write_once(d / "architect" / "calls.jsonl", prop.log.dumps())
    write_once(d / "calls.json", _dumps(calls))
    write_once(d / "trace.jsonl", "".join(_jl(x) + "\n" for x in _trace(cfg, ep, versions)))
    write_once(d / "summary.json", _dumps(summary))


# --------------------------------------------------------------------------- bundle check

def validate_episode_bundle(ep_dir, cfg: dict | None = None) -> list[str]:
    """Problems with a finished episode bundle ([] if complete and self-consistent)."""
    d = Path(ep_dir)
    errs = [f"missing {f}" for f in ("episode.json", "controller.luau", "summary.json", "trace.jsonl",
                                      "calls.json", "proposals.json") if not (d / f).is_file()]
    if errs:
        return errs
    ep, s = _load(d / "episode.json"), _load(d / "summary.json")
    if s.get("schema") != SUMMARY_SCHEMA:
        errs.append("summary schema")
    if cfg is not None and ep["config_sha256"] != cfg["_sha256"]:
        errs.append("episode ran under another config")
    ctl = (d / "controller.luau").read_text(encoding="utf-8")
    if p0.controller_sha256(ctl) != ep["player"]["controller_sha256"] or s["player"] != ep["player"]:
        errs.append("controller hash differs from episode.json")
    caps = ep["caps"]
    if s["attempts_used"] > caps["attempts_per_episode"]:
        errs.append(f"{s['attempts_used']} attempts > cap {caps['attempts_per_episode']}")
    if s["architect_calls"] > caps["architect_calls_per_episode"]:
        errs.append(f"{s['architect_calls']} architect calls > cap {caps['architect_calls_per_episode']}")
    if s["revisions"]["used"] > ep["max_revisions"]:
        errs.append("more proposals than max_revisions")
    props = _load(d / "proposals.json")
    if len(props) != s["revisions"]["used"]:
        errs.append("proposals.json length != revisions.used")
    if sum(v["attempted"] for v in s["versions"]) != s["attempts_used"]:
        errs.append("attempts_used != sum of attempted")
    for i, v in enumerate(s["versions"], start=1):
        vd = d / f"v{i}"
        if v["version"] != i or not (vd / "scene.json").is_file():
            errs.append(f"v{i}: missing")
            continue
        if sc.canonical_hash(sc.load_scene(vd / "scene.json")) != v["scene_hash"]:
            errs.append(f"v{i}: scene.json hash != summary")
        if i > 1:
            patch = _load(vd / "patch.json")
            if patch.get("base_hash") != s["versions"][i - 2]["scene_hash"]:
                errs.append(f"v{i}: patch base_hash is not the previous accepted version's scene")
        vs = _load(vd / "summary.json")
        if vs["successes"] != v["successes"] or [a["outcome"] for a in vs["attempts"]] != v["outcomes"]:
            errs.append(f"v{i}: version summary disagrees")
        for a in range(1, v["attempted"] + 1):
            af = vd / "attempts" / f"{a}.json"
            if not af.is_file():
                errs.append(f"v{i}: missing attempts/{a}.json")
                continue
            row = _load(af)
            if row["nonce"] != ep["nonces"][i - 1][a - 1]:
                errs.append(f"v{i} attempt {a}: nonce is not the declared one")
            if row["raw_file"]:
                raw = vd / row["raw_file"]
                if not raw.is_file() or p0.sha256_text(raw.read_text(encoding="utf-8")) != row["raw_sha256"]:
                    errs.append(f"v{i} attempt {a}: raw tool result missing or changed")
    if s["versions"][-1]["scene_hash"] != s["final"]["scene_hash"]:
        errs.append("final scene hash != last version")
    return errs


def write_once_or_replace_pending(path: Path, obj: dict) -> None:
    """pending.json is the one file that changes: the relay call now waiting (or none)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_dumps(obj), encoding="utf-8")
    os.replace(tmp, path)
