"""Experiment configs, the declared-before-runs lock, budgets and the run order (WS-11, ADR 0010).

An experiment (`config/experiments/*.yaml`, schema `worldsmith.experiment/0.1`) fixes, before any
run: the scene and stage, ONE player (version, route, timing, controller hash), N, k, the revision
budget and the per-episode caps, the classifier config, the architect prompt and ladder, and the
conditions in run order with their episode counts (B2 seeds declared up front, one per episode).
`load_experiment` re-derives every hash it declares and refuses any mismatch. Optional:
`preflight: {script, sha256}` (ADR 0011), the read-only probe run before every build.

Declared before runs: when the first episode bundle of an experiment is created, the runner
writes `<out>/<experiment>/experiment.lock.json` with the config's SHA-256 (of its bytes) and a
copy of the config. Afterwards it refuses to run if the config's hash differs (a change is a new
experiment id), or if any episode folder there was made by another config or player.

Budget: B3 episodes go through the jzapi job named in the config. `worst_case` is the most the
experiment (or what is left of it) can cost: per architect call, the jzapi worst case of a
request at the declared `prompt_bytes_cap` and the role's max_tokens (every prompt token at the
cache-write rate, every output token billed), times max_revisions, times the episodes. The CLI
prints it before starting and refuses if the job's remaining headroom is below it. Requests over
the cap are refused unsent (`episode.PromptCapExceeded`), so the bound holds.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Callable

import yaml

from worldsmith.architect import client as acl
from worldsmith.architect import prompt as pr
from worldsmith.compiler.templates import TemplateError
from worldsmith.grammar import scene as sc
from worldsmith.runner import attempts as at
from worldsmith.runner import episode as epi
from worldsmith.runner import freeze, p0, players, taxonomy
from worldsmith.validator import patch as pt

EXPERIMENT_SCHEMA = "worldsmith.experiment/0.1"
LOCK_SCHEMA = "worldsmith.experiment_lock/0.1"
RESULTS_SCHEMA = "worldsmith.experiment_results/0.1"
PROPOSERS = ("none", "hand_fix", "random_valid", "architect")
TOOLS = ("get_studio_mode", "run_code", "run_script_in_play_mode", "start_stop_play")
SPEC_MAX_ATTEMPTS = 12          # SPEC section 12: N x (1 + 3)
SPEC_MAX_ARCHITECT_CALLS = 3
_TOP = {"schema", "experiment", "stage", "scene", "player", "n_attempts", "pass_k", "max_revisions", "caps",
        "origin", "gap_area_margin_studs", "tool_timeouts_s", "classifier", "architect", "conditions", "dry_run"}
_OPTIONAL = {"preflight"}       # ADR 0011: absent in configs declared before it (they run without it)
_ID = re.compile(r"[a-z0-9][a-z0-9_]{0,47}")
_COND_ID = re.compile(r"B[0-9]([a-z0-9-]{0,24})?")
_HEX64 = re.compile(r"[0-9a-f]{64}")
CLOSED_FILE = "CLOSED.md"       # ADR 0012: an experiment closed by the owner decision; step/respond/architect refuse


class ExperimentError(ValueError):
    """The experiment config is malformed or does not match the repository."""


class ExperimentLocked(ExperimentError):
    """The config changed after the experiment's first episode bundle was written."""


class ExperimentClosed(ExperimentError):
    """The experiment's bundle folder holds CLOSED.md (closed by the owner): nothing more is run,
    answered or paid for; its remaining conditions move to a new experiment id."""


class MixedPlayers(ExperimentError):
    """An episode folder in the experiment was made by another config or player."""


class PreflightFailed(RuntimeError):
    """An episode ended HARNESS_ERROR at its preflight (ADR 0011): wrong place, another driver or
    wrong settings. Its bundle is written; no further episode is started."""


class BudgetRefused(RuntimeError):
    """The job's remaining headroom is below the experiment's worst case."""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file_and_sha(root: Path, spec, key_file: str, key_sha: str, what: str, errs: list) -> Path | None:
    if not isinstance(spec, dict) or not isinstance(spec.get(key_file), str):
        errs.append(f"{what}: needs {key_file}")
        return None
    f = root / spec[key_file]
    if not f.is_file():
        errs.append(f"{what}: {spec[key_file]} does not exist")
        return None
    got = _sha_file(f)
    if spec.get(key_sha) != got:
        errs.append(f"{what}: sha256 {got} != declared {spec.get(key_sha)}")
    return f


def load_experiment(path, repo_root=None, *, env=None) -> dict:
    """The parsed config plus derived keys (`_sha256`, `_path`, `_root`, `_scene`, `_route`,
    `_controller_text`, `_params`, `_stuck`, `_prompt`, `_ladder`, `_conditions`,
    `_hand_patches`). Raises ExperimentError listing every problem."""
    path = Path(path)
    root = Path(repo_root) if repo_root else path.resolve().parent.parent.parent
    raw = path.read_bytes()
    cfg = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(cfg, dict):
        raise ExperimentError("config is not a mapping")
    errs = [f"{'unknown' if k in cfg else 'missing'} key {k!r}" for k in sorted((set(cfg) - _OPTIONAL) ^ _TOP)]
    if errs:
        raise ExperimentError("; ".join(errs))
    if cfg["schema"] != EXPERIMENT_SCHEMA:
        errs.append(f"schema must be {EXPERIMENT_SCHEMA}")
    if not (isinstance(cfg["experiment"], str) and _ID.fullmatch(cfg["experiment"])):
        errs.append("experiment must be a short lower-case id")
    stage = cfg["stage"]
    if stage not in pt.STAGES:
        errs.append(f"stage must be one of {sorted(pt.STAGES)}")

    # scene
    scene = None
    s = cfg["scene"]
    if isinstance(s, dict) and isinstance(s.get("file"), str) and (root / s["file"]).is_file():
        scene = sc.load_scene(root / s["file"])
        if sc.canonical_hash(scene) != s.get("canonical_hash"):
            errs.append(f"scene: canonical hash {sc.canonical_hash(scene)} != declared {s.get('canonical_hash')}")
    else:
        errs.append("scene.file must name an existing scene")

    # N, k, revisions, caps (SPEC sections 9 and 12)
    n, k, R = cfg["n_attempts"], cfg["pass_k"], cfg["max_revisions"]
    if not (_int(n) and _int(k) and n == 3 and k == 3):
        errs.append("SPEC section 9 pre-declares n_attempts = 3 and pass_k = 3; they are never changed")
    if not (_int(R) and 1 <= R <= 3):
        errs.append("max_revisions must be an integer 1..3 (SPEC sections 6 and 12)")
    caps = cfg["caps"]
    if not (isinstance(caps, dict) and set(caps) == {"attempts_per_episode", "architect_calls_per_episode"}):
        errs.append("caps must give attempts_per_episode and architect_calls_per_episode")
    elif _int(n) and _int(R):
        if caps["attempts_per_episode"] != n * (1 + R) or not caps["attempts_per_episode"] <= SPEC_MAX_ATTEMPTS:
            errs.append(f"caps.attempts_per_episode must equal N x (1 + max_revisions) = {n * (1 + R)} "
                        f"and be <= {SPEC_MAX_ATTEMPTS}")
        if caps["architect_calls_per_episode"] != R or R > SPEC_MAX_ARCHITECT_CALLS:
            errs.append(f"caps.architect_calls_per_episode must equal max_revisions ({R}) and be <= "
                        f"{SPEC_MAX_ARCHITECT_CALLS}")

    # player
    pl = cfg["player"]
    route = text = params = None
    if not isinstance(pl, dict):
        errs.append("player must be a mapping")
    elif pl.get("tier") == "P2":
        refusals = freeze.check_manifest(root / str((pl.get("freeze_manifest") or {}).get("file", "")),
                                         declared_sha256=(pl.get("freeze_manifest") or {}).get("sha256"),
                                         root=root, env=env)
        raise freeze.P2Refused(refusals or [{"code": "P2_NOT_IMPLEMENTED",
                                             "detail": "the manifest checks pass, but WS-11 ships no P2 runner "
                                                       "(no Autopilot runs); P2 needs its own ticket and approval"}])
    else:
        keys = {"version", "tier", "driver", "claim", "template", "route", "timing", "controller_sha256"}
        if set(pl) != keys:
            errs.append(f"player keys must be {sorted(keys)}")
        else:
            try:
                pv = players.get(pl["version"])
            except players.PlayerError as exc:
                errs.append(str(exc))
                pv = None
            if pv:
                if pl["tier"] != pv.tier:
                    errs.append(f"player {pv.id} is tier {pv.tier}, not {pl['tier']}")
                if pl["template"] != f"runner/templates/{pv.template}":
                    errs.append(f"player.template must be runner/templates/{pv.template}")
                if "never jumps" not in str(pl["driver"]) or "Not an Autopilot or agent result" not in str(pl["claim"]):
                    errs.append("player.driver must say it never jumps and player.claim must keep the claim limit")
                errs += players.validate_timing(pv.id, pl["timing"])
                rf = _file_and_sha(root, pl["route"], "file", "sha256", "player.route", errs)
                if rf is not None and not errs:
                    route = p0.load_route(rf)
                    if scene is not None:
                        errs += [f"player.route: {e}" for e in p0.validate_route(route, scene["bounds"])]
                    try:
                        text = players.render_controller(pv.id, route, pl["timing"])
                    except TemplateError as exc:
                        errs.append(f"player.template: {exc}")
                    else:
                        got = players.controller_sha256(text)
                        if got != pl["controller_sha256"]:
                            errs.append(f"player.controller_sha256: rendered {got} != declared {pl['controller_sha256']}")
                        params = players.expected_params(pv.id, route, pl["timing"])

    # Studio and classification
    if not (isinstance(cfg["origin"], list) and len(cfg["origin"]) == 3 and all(_num(c) for c in cfg["origin"])):
        errs.append("origin must be 3 numbers")
    m = cfg["gap_area_margin_studs"]
    if not (_num(m) and 0 <= m <= 8):
        errs.append("gap_area_margin_studs must be a number in [0, 8]")
    tt = cfg["tool_timeouts_s"]
    if not (isinstance(tt, dict) and set(tt) == set(TOOLS) and all(_num(v) and v > 0 for v in tt.values())):
        errs.append(f"tool_timeouts_s must give a positive number for each of {TOOLS}")
    elif isinstance(pl, dict) and isinstance(pl.get("timing"), dict) and _num(pl["timing"].get("t_attempt")) \
            and tt["run_script_in_play_mode"] <= pl["timing"]["t_attempt"]:
        errs.append("the play tool timeout must exceed t_attempt")
    stuck = None
    cf = _file_and_sha(root, cfg["classifier"], "file", "sha256", "classifier", errs)
    if cf is not None:
        stuck = taxonomy.load_classifier_config(cf)["stuck"]

    # preflight (ADR 0011): a fixed read-only probe before every build, hash declared
    preflight_text = None
    if "preflight" in cfg:
        pfc = cfg["preflight"]
        if not (isinstance(pfc, dict) and set(pfc) == {"script", "sha256"}):
            errs.append("preflight must give exactly script and sha256")
        else:
            pf_file = _file_and_sha(root, pfc, "script", "sha256", "preflight", errs)
            if pf_file is not None:
                if not pfc["script"].startswith("runner/templates/preflight_"):
                    errs.append("preflight.script must be a runner/templates/preflight_*.luau template")
                preflight_text = pf_file.read_text(encoding="utf-8")
                if not preflight_text.isascii():
                    errs.append("preflight: the script is not ASCII")

    # architect
    a = cfg["architect"]
    ladder = prompt = None
    akeys = {"ladder", "ladder_sha256", "job", "prompt", "prompt_sha256", "prompt_bytes_cap"}
    if not (isinstance(a, dict) and set(a) == akeys):
        errs.append(f"architect keys must be {sorted(akeys)}")
    else:
        lf = _file_and_sha(root, a, "ladder", "ladder_sha256", "architect.ladder", errs)
        if lf is not None:
            ladder = acl.load_ladder_config(lf)
            if ladder["job"] != a["job"]:
                errs.append(f"architect.job {a['job']!r} != the ladder's job {ladder['job']!r}")
            if ladder["max_revisions"] < (R if _int(R) else 0):
                errs.append("the ladder allows fewer revisions than max_revisions")
        if a["prompt"] != "baseline":
            errs.append("architect.prompt must be baseline (red-team runs are never a baseline condition)")
        else:
            prompt = pr.load_prompt("baseline")
            if prompt.sha256 != a["prompt_sha256"]:
                errs.append(f"architect.prompt_sha256: {prompt.sha256} != declared {a['prompt_sha256']}")
        if not (_int(a["prompt_bytes_cap"]) and 1000 <= a["prompt_bytes_cap"] <= 100_000):
            errs.append("architect.prompt_bytes_cap must be an integer 1000..100000")

    # conditions
    conds: dict[str, dict] = {}
    patches: dict[str, dict] = {}
    roles_used = set()
    if not (isinstance(cfg["conditions"], list) and cfg["conditions"]):
        errs.append("conditions must be a non-empty list")
    else:
        for c in cfg["conditions"]:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not _COND_ID.fullmatch(c["id"]):
                errs.append(f"condition {c!r}: needs an id like B0, B2 or B3-haiku")
                continue
            cid = c["id"]
            if cid in conds:
                errs.append(f"condition {cid}: duplicate id")
            kind = c.get("proposer")
            base = {"id", "proposer", "episodes"}
            extra = {"none": set(), "hand_fix": {"patch", "patch_sha256"}, "random_valid": {"seeds"},
                     "architect": {"role"}}.get(kind)
            if extra is None:
                errs.append(f"condition {cid}: proposer must be one of {PROPOSERS}")
                continue
            if set(c) != base | extra:
                errs.append(f"condition {cid}: keys must be {sorted(base | extra)}")
                continue
            if not (_int(c["episodes"]) and 1 <= c["episodes"] <= 100):
                errs.append(f"condition {cid}: episodes must be an integer 1..100")
                continue
            if kind == "random_valid":
                seeds = c["seeds"]
                if not (isinstance(seeds, list) and len(seeds) == c["episodes"] and all(_int(x) and x >= 0 for x in seeds)
                        and len(set(seeds)) == len(seeds)):
                    errs.append(f"condition {cid}: seeds must be {c['episodes']} distinct non-negative integers, "
                                "one per episode, declared up front")
            if kind == "architect":
                if ladder is not None and c["role"] not in ladder["roles"]:
                    errs.append(f"condition {cid}: role {c['role']!r} is not in the ladder")
                roles_used.add(c["role"])
            if kind == "hand_fix":
                f = _file_and_sha(root, c, "patch", "patch_sha256", f"condition {cid} patch", errs)
                if f is not None:
                    patch = pt.load_patch(f)
                    if scene is not None and patch.get("base_hash") != sc.canonical_hash(scene):
                        errs.append(f"condition {cid}: the hand patch's base_hash is not the experiment scene's")
                    patches[cid] = patch
            conds[cid] = dict(c)

    # dry run fixtures
    dr = cfg["dry_run"]
    if not (isinstance(dr, dict) and set(dr) == {"fixtures"} and isinstance(dr["fixtures"], dict)):
        errs.append("dry_run must have exactly fixtures: {role: [fixture names]}")
    else:
        for role in sorted(roles_used):
            names = dr["fixtures"].get(role)
            if not (isinstance(names, list) and names and all(acl.fixture_path(x).is_file() for x in names)):
                errs.append(f"dry_run.fixtures.{role} must list existing architect fixtures")

    if errs:
        raise ExperimentError("; ".join(errs))
    out = dict(cfg)
    out.update(_sha256=hashlib.sha256(raw).hexdigest(), _path=str(path), _root=str(root), _raw=raw.decode("utf-8"),
               _scene=scene, _route=route, _controller_text=text, _params=params, _stuck=stuck, _prompt=prompt,
               _ladder=ladder, _conditions=conds, _hand_patches=patches, _preflight=preflight_text)
    return out


# --------------------------------------------------------------------------- budget

def per_call_worst_case(cfg: dict, role: str, price: Callable[..., float]) -> float:
    """`price(model_id, prompt_tokens=, max_tokens=, cache_ttl=)` is jzapi.worst_case_usd."""
    r = cfg["_ladder"]["roles"][role]
    return price(r["model_id"], prompt_tokens=cfg["architect"]["prompt_bytes_cap"], max_tokens=r["max_tokens"],
                 cache_ttl="5m" if cfg["_ladder"]["cache_system"] else None)


def worst_case(cfg: dict, price: Callable[..., float], exp: "Experiment | None" = None) -> dict:
    """Worst-case USD of the whole experiment and (with `exp`) of what is left: calls already
    stored in an episode's replies are not paid again; finished episodes cost nothing more."""
    R = cfg["max_revisions"]
    rows, total, left = [], 0.0, 0.0
    for cid, c in cfg["_conditions"].items():
        if c["proposer"] != "architect":
            continue
        per = per_call_worst_case(cfg, c["role"], price)
        calls = c["episodes"] * R
        calls_left = calls
        if exp is not None:
            calls_left = 0
            for i in range(c["episodes"]):
                d = exp.episode_dir(cid, i)
                if (d / "summary.json").is_file():
                    continue
                stored = len(list((d / "architect" / "replies").glob("*.json"))) if (d / "architect" / "replies").is_dir() else 0
                calls_left += max(0, R - stored)
        rows.append({"condition": cid, "role": c["role"], "model_id": cfg["_ladder"]["roles"][c["role"]]["model_id"],
                     "max_tokens": cfg["_ladder"]["roles"][c["role"]]["max_tokens"], "per_call_usd": per,
                     "episodes": c["episodes"], "calls_max": calls, "usd_max": per * calls,
                     "calls_left": calls_left, "usd_left": per * calls_left})
        total += per * calls
        left += per * calls_left
    return {"job": cfg["architect"]["job"], "prompt_tokens_cap": cfg["architect"]["prompt_bytes_cap"],
            "conditions": rows, "total_usd": total, "remaining_usd": left,
            "method": "per call: jzapi.worst_case_usd(model, prompt_tokens = prompt_bytes_cap, max_tokens, cache "
                      "write 5m); x max_revisions x episodes; requests over the cap are refused unsent"}


def check_headroom(worst_usd: float, headroom_usd: float) -> None:
    if headroom_usd + 1e-12 < worst_usd:
        raise BudgetRefused(f"worst case ${worst_usd:.4f} is above the job's remaining headroom ${headroom_usd:.4f}")


# --------------------------------------------------------------------------- the experiment folder

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stop_on_failed_preflight(d: Path, summ: dict, cid: str, i: int) -> None:
    """ADR 0011: after an episode that failed its preflight, no further episode of the experiment
    starts (now or on a later step): the place or the driver was wrong, so the experiment is spent."""
    pre = d / f"v{summ['final']['version']}" / "preflight.json"
    if summ["status"] == "HARNESS_ERROR" and pre.is_file() and json.loads(pre.read_text(encoding="utf-8"))["ok"] is False:
        raise PreflightFailed(f"{cid}/ep{i}: {summ['detail']} (see {pre}); no further episode of this experiment "
                              "starts. Fix the place (docs/EXPERIMENTS.md owner checklist) and declare a new "
                              "experiment id")


class Experiment:
    """`<out_root>/<experiment>/`: lock, config copy, controller, one folder per episode
    (`<condition>/ep<i>/`), `pending.json` (the relay call waiting for an answer) and
    `results.json` (written when every episode is complete)."""

    def __init__(self, cfg: dict, out_root, *, dry_run: bool = False):
        self.cfg = cfg
        self.dir = Path(out_root) / cfg["experiment"]
        self.dry_run = dry_run

    def plan(self) -> list[tuple[str, int]]:
        return [(cid, i) for cid, c in self.cfg["_conditions"].items() for i in range(c["episodes"])]

    def episode_dir(self, cid: str, i: int) -> Path:
        return self.dir / cid / f"ep{i}"

    def lock_path(self) -> Path:
        return self.dir / "experiment.lock.json"

    def closed_path(self) -> Path:
        return self.dir / CLOSED_FILE

    def closed(self) -> bool:
        return self.closed_path().is_file()

    def check_open(self) -> None:
        """Refuse a closed experiment (ADR 0012): no Studio request, answer or architect call."""
        if self.closed():
            raise ExperimentClosed(
                f"{self.cfg['experiment']} is closed ({self.closed_path()}): nothing is run, answered or "
                "called for it; its remaining conditions move to a new experiment id")

    def check_declared(self) -> None:
        """Refuse a changed config after the first episode, and any mixed episode folder."""
        lp_ = self.lock_path()
        if lp_.is_file():
            lock = json.loads(lp_.read_text(encoding="utf-8"))
            if lock["config_sha256"] != self.cfg["_sha256"]:
                raise ExperimentLocked(
                    f"{self.cfg['experiment']}: the config's hash is {self.cfg['_sha256'][:12]}…, but the "
                    f"experiment was declared with {lock['config_sha256'][:12]}… (lock written {lock['created']}); "
                    "a changed config is a new experiment id, never an edit")
            if lock.get("dry_run") != self.dry_run:
                raise ExperimentLocked(f"{self.dir} is a {'dry-run' if lock.get('dry_run') else 'live'} experiment folder")
        for f in sorted(self.dir.glob("*/ep*/episode.json")) if self.dir.is_dir() else []:
            ep = json.loads(f.read_text(encoding="utf-8"))
            pl, want = ep.get("player") or {}, self.cfg["player"]
            if ep.get("config_sha256") != self.cfg["_sha256"] or pl.get("controller_sha256") != want["controller_sha256"] \
                    or pl.get("version") != want["version"] or pl.get("tier") != want["tier"] \
                    or pl.get("t_attempt_s") != want["timing"]["t_attempt"] or ep.get("n_attempts") != self.cfg["n_attempts"]:
                raise MixedPlayers(f"{f.parent}: made by another config or player; refusing to mix")

    def _lock(self) -> None:
        if self.lock_path().is_file():
            return
        epi.write_once(self.dir / "config.yaml", self.cfg["_raw"])
        epi.write_once(self.dir / "controller.luau", self.cfg["_controller_text"])
        epi.write_once(self.lock_path(), epi._dumps({
            "schema": LOCK_SCHEMA, "experiment": self.cfg["experiment"], "config_sha256": self.cfg["_sha256"],
            "config_file": Path(self.cfg["_path"]).name, "created": _now(), "dry_run": self.dry_run,
            "player": {"version": self.cfg["player"]["version"], "controller_sha256": self.cfg["player"]["controller_sha256"],
                       "t_attempt_s": self.cfg["player"]["timing"]["t_attempt"]},
            "n_attempts": self.cfg["n_attempts"],
            "rule": "declared before runs: the runner refuses this folder if the config's hash differs"}))

    def status(self) -> list[dict]:
        out = []
        for cid, i in self.plan():
            d = self.episode_dir(cid, i)
            state = "done" if (d / "summary.json").is_file() else "started" if (d / "episode.json").is_file() else "todo"
            out.append({"condition": cid, "episode": i, "state": state, "dir": str(d)})
        return out

    def run(self, client_for: Callable[[Path], object], architect_for: Callable[[dict], Callable] | None = None,
            *, nonces_for: Callable[[str, int], list] | None = None, architect_mode: str = "call",
            architect_expect: str | None = None) -> dict:
        """Run every episode in declared order (resuming the first unfinished one). Raises
        `attempts.Pending` at the first unanswered relay call (and records it in pending.json).
        A closed experiment (CLOSED.md) raises `ExperimentClosed` before anything is read or sent.

        `architect_mode` (ADR 0010 section 9): `call` makes live architect calls inline; `defer`
        raises `episode.ArchitectPending` where a live call is due (no slot used, nothing written;
        pending.json names the request); `one` makes exactly one live call (it must be
        `architect_expect` when given), stores the reply and raises `episode.ArchitectCallDone`."""
        self.check_open()
        self.check_declared()
        for cid, i in self.plan():
            d = self.episode_dir(cid, i)
            if (d / "summary.json").is_file():
                errs = epi.validate_episode_bundle(d, self.cfg)
                if errs:
                    raise epi.EpisodeError(f"{d}: finished bundle has problems: {errs}")
                _stop_on_failed_preflight(d, json.loads((d / "summary.json").read_text(encoding="utf-8")), cid, i)
                continue
            if not (d / "episode.json").is_file():
                self._lock()
                epi.init_episode(self.cfg, cid, i, d, dry_run=self.dry_run,
                                 nonces=nonces_for(cid, i) if nonces_for else None)
            cond = self.cfg["_conditions"][cid]
            factory = (lambda c=cond: architect_for(c)) if (architect_for and cond["proposer"] == "architect") else None
            try:
                summ = epi.run_episode(client_for(d), self.cfg, d, architect_factory=factory,
                                       architect_mode=architect_mode, architect_expect=architect_expect)
            except at.Pending as p:
                epi.write_once_or_replace_pending(self.dir / "pending.json", {
                    "episode_dir": str(d.relative_to(self.dir)), "index": p.index, "tool": p.request["tool"]})
                raise
            except epi.ArchitectPending as p:
                epi.write_once_or_replace_pending(self.dir / "pending.json", {
                    "episode_dir": str(d.relative_to(self.dir)), "index": None, "tool": None,
                    "architect": {"request_sha": p.request_sha, "role": p.role, "condition": cid}})
                raise
            except epi.ArchitectCallDone as p:
                epi.write_once_or_replace_pending(self.dir / "pending.json", {
                    "episode_dir": None, "index": None, "tool": None,
                    "note": f"architect reply stored for {cid} request {p.request_sha}; run step"})
                raise
            _stop_on_failed_preflight(d, summ, cid, i)
        res = self.results()
        epi.write_once_or_replace_pending(self.dir / "pending.json", {"episode_dir": None, "index": None, "tool": None})
        epi.write_once(self.dir / "results.json", epi._dumps(res))
        return res

    def pending(self) -> dict | None:
        f = self.dir / "pending.json"
        if not f.is_file():
            return None
        p = json.loads(f.read_text(encoding="utf-8"))
        return p if p.get("episode_dir") else None

    def results(self) -> dict:
        conds = []
        for cid, c in self.cfg["_conditions"].items():
            eps = []
            for i in range(c["episodes"]):
                f = self.episode_dir(cid, i) / "summary.json"
                if f.is_file():
                    eps.append(json.loads(f.read_text(encoding="utf-8")))
            done = len(eps)
            conds.append({
                "condition": cid, "proposer": c["proposer"], "role": c.get("role"), "seeds": c.get("seeds"),
                "episodes_planned": c["episodes"], "episodes_done": done,
                "repaired": {"n": sum(e["repaired"] for e in eps), "of": done},
                "passed_final": {"n": sum(e["passed_final"] for e in eps), "of": done},
                "statuses": [e["status"] for e in eps],
                "episodes": [{k: e[k] for k in ("episode", "status", "passed_original", "passed_final", "repaired",
                                                 "final", "revisions", "attempts_used", "architect_calls",
                                                 "model_calls", "cost_usd_reported", "canonical")} for e in eps]})
        return {"schema": RESULTS_SCHEMA, "experiment": self.cfg["experiment"], "config_sha256": self.cfg["_sha256"],
                "dry_run": self.dry_run, "stage": self.cfg["stage"], "scene_hash": self.cfg["scene"]["canonical_hash"],
                "player": {"version": self.cfg["player"]["version"], "tier": self.cfg["player"]["tier"],
                           "controller_sha256": self.cfg["player"]["controller_sha256"],
                           "t_attempt_s": self.cfg["player"]["timing"]["t_attempt"], "driver": self.cfg["player"]["driver"]},
                "n": self.cfg["n_attempts"], "pass_k": self.cfg["pass_k"], "max_revisions": self.cfg["max_revisions"],
                "conditions": conds,
                "disclosure": epi.DRY_RUN_NOTE if self.dry_run else self.cfg["player"]["claim"]}
