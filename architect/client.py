"""Architect clients (WS-10): one provider-agnostic interface, `propose(request) -> RawResponse`.

- `FixtureArchitect` replays hand-written responses from `architect/fixtures/*.json`, keyed by
  request hash. Offline and deterministic; an unknown request raises `FixtureMiss`.
- `JzapiArchitect` calls the Claude API through JzLab's `jzapi` (job `architect_ladder`, role
  haiku / sonnet / opus, pinned IDs in `config/architect_ladder.yaml`). It refuses to construct
  (`LiveCallRefused`) unless BOTH JzLab `budgets.yaml` has the job `approved: true` AND the
  git-ignored local `config/architect.yaml` says `live: true`. jzapi checks the approval and
  cap again before every request. No temperature / top_p / top_k (API 2026-10-21 rejects them)
  and no thinking parameter: Claude samples and thinks at the API default (ADR 0009).
- `LocalArchitect` is a placeholder for B4 (local Gemma 4 31B on an OpenAI-compatible
  endpoint, ROADMAP section 4.3): it refuses without an endpoint in the local config and does
  not implement `propose` yet.

Rule (ROADMAP section 11.3): Claude outputs are evaluation and inference only, never training
data. Nothing here reads, prints or stores an API key; jzapi owns the key.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

import yaml

from worldsmith.architect.prompt import ArchitectRequest

REPO_ROOT = Path(__file__).resolve().parent.parent
LADDER_CONFIG = REPO_ROOT / "config" / "architect_ladder.yaml"
LOCAL_CONFIG = REPO_ROOT / "config" / "architect.yaml"           # git-ignored, per machine
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
LADDER_SCHEMA = "worldsmith.architect_ladder/0.1"
LOCAL_SCHEMA = "worldsmith.architect_local/0.1"
FIXTURE_SCHEMA = "worldsmith.architect_fixture/0.1"
# ROADMAP section 4.3: the B3 ladder, pinned IDs (they match jzapi.ROLES).
PINNED_MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
DEFAULT_JOB = "architect_ladder"


class ArchitectConfigError(ValueError):
    """A config file is malformed, or a role is unknown."""


class LiveCallRefused(RuntimeError):
    """A live architect may not be constructed: approval or local config missing."""


class ArchitectCallError(RuntimeError):
    """The provider failed to return a response (transport, API or batch error)."""


class FixtureMiss(LookupError):
    """FixtureArchitect has no recorded response for this request hash."""


class FixtureConflict(ValueError):
    """Two loaded fixtures record different responses for one request hash."""


@dataclass(frozen=True)
class RawResponse:
    text: str
    model_role: str
    model_id: str
    provider: str                       # fixture | jzapi | local
    stop_reason: str | None = None
    usage: dict | None = None           # as the provider reported it, else None
    cost_usd: float | None = None       # jzapi's computed cost, else None
    latency_s: float | None = None      # wall clock around the call; None when not measured
    request_id: str | None = None


class Architect(Protocol):
    model_role: str
    model_id: str
    provider: str

    def propose(self, req: ArchitectRequest) -> RawResponse: ...


# --------------------------------------------------------------------------- fixtures

def fixture_path(name_or_path) -> Path:
    p = Path(name_or_path)
    if p.suffix == ".json" and p.exists():
        return p
    return FIXTURE_DIR / f"{name_or_path}.json"


class FixtureArchitect:
    """Replays recorded responses keyed by request hash. Never touches the network."""
    model_role = "fixture"
    provider = "fixture"

    def __init__(self, turns: dict[str, dict], name: str):
        self._turns = turns
        self.model_id = f"fixture:{name}"

    @classmethod
    def from_names(cls, names: Iterable) -> "FixtureArchitect":
        names = list(names)
        turns: dict[str, dict] = {}
        for n in names:
            doc = json.loads(fixture_path(n).read_text(encoding="utf-8"))
            if doc.get("schema") != FIXTURE_SCHEMA:
                raise ArchitectConfigError(f"{n}: schema must be {FIXTURE_SCHEMA}")
            for t in doc["turns"]:
                sha = t["request_sha"]
                rec = {"response_text": t["response_text"], "stop_reason": t.get("stop_reason", "end_turn")}
                if sha in turns and turns[sha] != rec:
                    raise FixtureConflict(f"request {sha[:12]}… has different recorded responses ({n})")
                turns[sha] = rec
        label = "+".join(Path(str(n)).stem for n in names)
        return cls(turns, label)

    def propose(self, req: ArchitectRequest) -> RawResponse:
        t = self._turns.get(req.request_sha)
        if t is None:
            raise FixtureMiss(f"no recorded response for request {req.request_sha} (revision "
                              f"{req.context['revision']['this']}); re-run scripts/architect_fixtures.py "
                              f"if the prompt or inputs changed on purpose")
        return RawResponse(text=t["response_text"], model_role=self.model_role, model_id=self.model_id,
                           provider=self.provider, stop_reason=t["stop_reason"])


# --------------------------------------------------------------------------- configs

def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def load_ladder_config(path=None) -> dict:
    """`config/architect_ladder.yaml`, checked strictly: pinned IDs, max_tokens per role."""
    path = Path(path) if path else LADDER_CONFIG
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ArchitectConfigError(f"{path}: {exc}") from None
    errs = []
    if not isinstance(cfg, dict):
        raise ArchitectConfigError(f"{path}: not a mapping")
    keys = {"schema", "job", "max_revisions", "cache_system", "sampling", "roles"}
    if set(cfg) != keys:
        errs.append(f"keys must be {sorted(keys)}, got {sorted(cfg)}")
    if cfg.get("schema") != LADDER_SCHEMA:
        errs.append(f"schema must be {LADDER_SCHEMA}")
    if not isinstance(cfg.get("job"), str):
        errs.append("job must be a string")
    if cfg.get("max_revisions") != 3 or not _int(cfg.get("max_revisions")):
        errs.append("max_revisions must be 3 (SPEC section 12)")
    if not isinstance(cfg.get("cache_system"), bool):
        errs.append("cache_system must be true or false")
    if cfg.get("sampling") != "api_default":
        errs.append("sampling must be api_default (temperature/top_p/top_k are not sent)")
    roles = cfg.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(PINNED_MODELS):
        errs.append(f"roles must be exactly {sorted(PINNED_MODELS)}")
    else:
        for role, r in roles.items():
            if not isinstance(r, dict) or set(r) != {"model_id", "max_tokens"}:
                errs.append(f"roles.{role} must have exactly model_id and max_tokens")
                continue
            if r["model_id"] != PINNED_MODELS[role]:
                errs.append(f"roles.{role}.model_id must be the pinned {PINNED_MODELS[role]}")
            if not (_int(r["max_tokens"]) and 1 <= r["max_tokens"] <= 128000):
                errs.append(f"roles.{role}.max_tokens must be an integer 1..128000")
    if errs:
        raise ArchitectConfigError(f"{path}: " + "; ".join(errs))
    return cfg


def load_local_config(path=None) -> dict | None:
    """The git-ignored per-machine `config/architect.yaml`, or None if it does not exist."""
    path = Path(path) if path else LOCAL_CONFIG
    if not path.is_file():
        return None
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ArchitectConfigError(f"{path}: {exc}") from None
    if not isinstance(cfg, dict) or cfg.get("schema") != LOCAL_SCHEMA:
        raise ArchitectConfigError(f"{path}: must be a mapping with schema {LOCAL_SCHEMA}")
    unknown = set(cfg) - {"schema", "live", "local", "budgets_file"}
    if unknown:
        raise ArchitectConfigError(f"{path}: unknown keys {sorted(unknown)}")
    return cfg


def resolve_budgets_path(explicit=None, local_cfg: dict | None = None) -> Path:
    """budgets.yaml: explicit path, else $JZ_BUDGETS_FILE, else the local config's
    `budgets_file`, else JzLab beside this repo (`../JzLab/budgets.yaml`)."""
    if explicit:
        return Path(explicit)
    if os.environ.get("JZ_BUDGETS_FILE"):
        return Path(os.environ["JZ_BUDGETS_FILE"]).expanduser()
    if local_cfg and local_cfg.get("budgets_file"):
        return Path(str(local_cfg["budgets_file"])).expanduser()
    return REPO_ROOT.parent / "JzLab" / "budgets.yaml"


def budget_job_approved(job: str, budgets_path) -> bool:
    """True only if budgets.yaml lists `job` with `approved: true` (a YAML boolean)."""
    try:
        data = yaml.safe_load(Path(budgets_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    job_cfg = (data.get("jobs") or {}).get(job) if isinstance(data, dict) else None
    return isinstance(job_cfg, dict) and job_cfg.get("approved") is True


# --------------------------------------------------------------------------- Claude via jzapi

def _attr(obj, k, default=None):
    return obj.get(k, default) if isinstance(obj, dict) else getattr(obj, k, default)


def _default_session_factory(job: str, **kw):
    import jzapi  # lazy: only a live, approved architect needs JzLab installed
    return jzapi.Session(job, **kw)


class JzapiArchitect:
    """B3: a Claude model through jzapi. Construct only after the owner's approval."""
    provider = "jzapi"

    def __init__(self, role: str, *, config_path=None, ladder_path=None, budgets_path=None,
                 job: str | None = None, session_factory: Callable[..., Any] | None = None):
        ladder = load_ladder_config(ladder_path)
        if role not in ladder["roles"]:
            raise ArchitectConfigError(f"unknown role {role!r}; known: {sorted(ladder['roles'])}")
        local = load_local_config(config_path)
        where = Path(config_path) if config_path else LOCAL_CONFIG
        if local is None:
            raise LiveCallRefused(f"no local architect config at {where}: copy "
                                  f"config/architect.example.yaml there and set live: true "
                                  f"(owner decision, recorded in JzLab state/DECISIONS.md)")
        if local.get("live") is not True:
            raise LiveCallRefused(f"{where} does not set live: true (it says {local.get('live')!r})")
        self.job = job or ladder["job"]
        self.budgets_path = resolve_budgets_path(budgets_path, local)
        if not budget_job_approved(self.job, self.budgets_path):
            raise LiveCallRefused(f"job {self.job!r} is not approved in {self.budgets_path} "
                                  f"(needs approved: true, set only after the owner's yes)")
        self.model_role = role
        self.model_id = ladder["roles"][role]["model_id"]
        self.max_tokens = ladder["roles"][role]["max_tokens"]
        self.cache_system = ladder["cache_system"]
        factory = session_factory or _default_session_factory
        self._session = factory(self.job, repo_dir=REPO_ROOT, budgets_path=self.budgets_path)

    def _params(self, req: ArchitectRequest) -> dict:
        return {"model": self.model_id, "max_tokens": self.max_tokens, "system": req.system,
                "user": req.user, "cache_system": self.cache_system}

    def propose(self, req: ArchitectRequest) -> RawResponse:
        params = self._params(req)
        t0 = time.monotonic()
        try:
            r = self._session.message(note=f"worldsmith architect {self.model_role} "
                                           f"{req.request_sha[:12]}", **params)
        except Exception as exc:  # jzapi scrubs the key from its errors
            if type(exc).__name__ in ("JobNotApproved", "BudgetExceeded", "BudgetError"):
                raise LiveCallRefused(str(exc)) from None
            raise ArchitectCallError(f"{type(exc).__name__}: {exc}") from None
        latency = time.monotonic() - t0
        return RawResponse(text=_attr(r, "text") or "", model_role=self.model_role,
                           model_id=_attr(r, "model_id") or self.model_id, provider=self.provider,
                           stop_reason=_attr(r, "stop_reason"), usage=_attr(r, "usage"),
                           cost_usd=_attr(r, "cost_usd"), latency_s=latency,
                           request_id=_attr(r, "request_id"))

    # Batches: for many independent first revisions (e.g. the 40 held-out rooms). Later
    # revisions depend on earlier verdicts, so they stay sequential.
    def custom_id(self, req: ArchitectRequest) -> str:
        return f"{self.model_role}-{req.request_sha[:56]}"[:64]

    def batch_request(self, req: ArchitectRequest) -> dict:
        import jzapi
        return jzapi.request(self.custom_id(req), **self._params(req))

    def response_from_batch_item(self, item, req: ArchitectRequest) -> RawResponse:
        if _attr(item, "custom_id") != self.custom_id(req):
            raise ArchitectCallError(f"batch item {_attr(item, 'custom_id')!r} is not for this request")
        if _attr(item, "type") != "succeeded":
            raise ArchitectCallError(f"batch item {_attr(item, 'custom_id')}: {_attr(item, 'type')} "
                                     f"{_attr(item, 'error') or ''}".strip())
        return RawResponse(text=_attr(item, "text") or "", model_role=self.model_role,
                           model_id=_attr(item, "model_id") or self.model_id, provider=self.provider,
                           stop_reason=_attr(item, "stop_reason"), usage=_attr(item, "usage"),
                           cost_usd=_attr(item, "cost_usd"), latency_s=None,
                           request_id=_attr(item, "custom_id"))


# --------------------------------------------------------------------------- B4 placeholder

class LocalArchitect:
    """B4 placeholder: local Gemma 4 31B behind an OpenAI-compatible endpoint (ROADMAP 4.3).
    Interface only; refuses without `local.endpoint` and `local.model` in the local config."""
    provider = "local"
    model_role = "local"

    def __init__(self, *, config_path=None):
        local = load_local_config(config_path)
        where = Path(config_path) if config_path else LOCAL_CONFIG
        sect = (local or {}).get("local") or {}
        endpoint, model = sect.get("endpoint"), sect.get("model")
        if not (isinstance(endpoint, str) and endpoint.startswith(("http://", "https://"))
                and isinstance(model, str) and model):
            raise LiveCallRefused(f"no local architect endpoint and model in {where} (local.endpoint, local.model)")
        self.endpoint = endpoint
        self.model_id = model

    def propose(self, req: ArchitectRequest) -> RawResponse:
        raise NotImplementedError("B4 (local Gemma 4 31B) is planned; WS-10 ships only its interface")
