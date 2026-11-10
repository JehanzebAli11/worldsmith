"""Strict parsing, the revision budget and the offline revision loop (WS-10; ADR 0009).

One architect call is one revision (SPEC sections 6 and 12): at most 3 per episode, and every
reply counts, accepted or not. A reply is

- `MALFORMED` when it is not exactly one JSON object holding a patch (`parse_response`); the
  validator never sees it, and it counts as a rejected revision;
- `REJECTED` when the validator (`worldsmith.validator.validate`) reports violations;
- `ACCEPTED` when the validator accepts it (the orchestrator, WS-11, then builds and runs it).

A provider failure (no reply) is logged as `ERROR` and re-raised: the call still used its slot
(the cap is on calls), it is not a rejection, and the orchestrator records the episode as
`CONNECTOR_ERROR` (SPEC section 10). WS-10 never retries.

Every call is logged as SPEC section 11 asks: `{model_role, model_id, request_sha,
response_sha, patch, validator_result, usage_if_reported, latency}` plus the episode, revision,
prompt mode/version/hash, context hash, raw reply, stop reason, verdict and malformed reason.
The raw reply is evaluation data only, never training data (ROADMAP section 11.3).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from worldsmith.architect import prompt as pr
from worldsmith.architect.client import Architect, RawResponse
from worldsmith.grammar.scene import canonical_hash
from worldsmith.validator.validate import validate

CALL_SCHEMA = "worldsmith.architect_call/0.1"
MALFORMED_REASONS = ("EMPTY", "CODE_FENCE", "NOT_JSON", "TRAILING_TEXT", "DUPLICATE_KEY",
                     "NON_FINITE", "NOT_A_PATCH", "TRUNCATED", "REFUSED")
VERDICTS = ("ACCEPTED", "REJECTED", "MALFORMED", "ERROR")
_WS = " \t\r\n"


# --------------------------------------------------------------------------- parsing

class _Reject(ValueError):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise _Reject("DUPLICATE_KEY", f"duplicate key {k!r}")
        out[k] = v
    return out


def _const(name):
    raise _Reject("NON_FINITE", f"{name} is not allowed")


def _float(text):
    v = float(text)
    if not math.isfinite(v):
        raise _Reject("NON_FINITE", f"{text} is not finite")
    return v


_DECODER = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_const, parse_float=_float)


@dataclass(frozen=True)
class Parsed:
    patch: dict | None
    malformed: dict | None          # {"reason", "detail"} or None


def _bad(reason, detail):
    return Parsed(None, {"reason": reason, "detail": detail})


def parse_response(text: str, stop_reason: str | None = None) -> Parsed:
    """Exactly one JSON object holding a patch, with only whitespace around it. No prose and no
    code fence (ADR 0009). A reply cut at max_tokens is TRUNCATED even if it parses."""
    if stop_reason == "max_tokens":
        return _bad("TRUNCATED", "stop_reason max_tokens: the reply was cut off")
    if stop_reason == "refusal":
        return _bad("REFUSED", "stop_reason refusal")
    s = (text or "").strip(_WS)
    if not s:
        return _bad("EMPTY", "no text")
    if s.startswith("```"):
        return _bad("CODE_FENCE", "the reply is wrapped in a code fence")
    if not s.startswith("{"):
        return _bad("NOT_JSON", f"the reply starts with {s[:24]!r}, not a JSON object")
    try:
        obj, end = _DECODER.raw_decode(s)
    except _Reject as exc:
        return _bad(exc.reason, str(exc))
    except ValueError as exc:
        return _bad("NOT_JSON", str(exc)[:120])
    if s[end:].strip(_WS):
        return _bad("TRAILING_TEXT", f"text after the JSON object: {s[end:end + 24].strip(_WS)!r}")
    schema = obj.get("schema")
    if not (isinstance(schema, str) and schema.startswith("worldsmith.patch/")):
        return _bad("NOT_A_PATCH", f"schema is {schema!r}, not a worldsmith.patch/* id")
    return Parsed(obj, None)


# --------------------------------------------------------------------------- budget

class BudgetExhausted(RuntimeError):
    """The episode has used all its architect revisions."""


class RevisionBudget:
    """At most `max_revisions` (1..3) architect calls in one episode; every call counts."""

    def __init__(self, max_revisions: int = pr.MAX_REVISIONS_CAP):
        if not (isinstance(max_revisions, int) and not isinstance(max_revisions, bool)
                and 1 <= max_revisions <= pr.MAX_REVISIONS_CAP):
            raise ValueError(f"max_revisions must be an integer 1..{pr.MAX_REVISIONS_CAP}")
        self.max_revisions = max_revisions
        self.used = 0
        self.rejected = 0

    @property
    def remaining(self) -> int:
        return self.max_revisions - self.used

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_revisions

    def consume(self) -> int:
        """Take the next revision slot; returns its 1-based index."""
        if self.exhausted:
            raise BudgetExhausted(f"all {self.max_revisions} architect revisions are used")
        self.used += 1
        return self.used


# --------------------------------------------------------------------------- log

def _dumps(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class CallLog:
    """Architect call records, in memory and (optionally) appended to a JSONL file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.records: list[dict] = []

    def append(self, rec: dict) -> None:
        self.records.append(rec)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(_dumps(rec) + "\n")

    def dumps(self) -> str:
        return "".join(_dumps(r) + "\n" for r in self.records)


# --------------------------------------------------------------------------- revisions

@dataclass(frozen=True)
class Revision:
    index: int
    verdict: str
    patch: dict | None
    malformed: dict | None
    violations: tuple = ()          # ({"code", "ref"}, ...)
    warnings: tuple = ()            # (code, ...)
    candidate_scene: dict | None = field(default=None, repr=False, compare=False)
    record: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def codes(self) -> set[str]:
        return {v["code"] for v in self.violations}

    @property
    def rejected(self) -> bool:
        return self.verdict in ("REJECTED", "MALFORMED")

    def feedback(self) -> dict:
        """What the next revision's context shows about this one (stable codes only)."""
        return {"revision": self.index, "verdict": self.verdict,
                "malformed_reason": self.malformed["reason"] if self.malformed else None,
                "patch": self.patch, "violations": [dict(v) for v in self.violations],
                "warnings": list(self.warnings)}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(req, resp: RawResponse | None, *, architect, episode, index, budget) -> dict:
    p = req.prompt
    return {
        "schema": CALL_SCHEMA, "episode": episode, "revision": index,
        "max_revisions": budget.max_revisions, "stage": req.context["stage"],
        "scene_hash": req.context["scene_hash"],
        "provider": resp.provider if resp else getattr(architect, "provider", None),
        "model_role": resp.model_role if resp else getattr(architect, "model_role", None),
        "model_id": resp.model_id if resp else getattr(architect, "model_id", None),
        "prompt_mode": p.mode, "prompt_version": p.version, "prompt_sha": p.sha256,
        "request_sha": req.request_sha, "context_sha": req.context_sha,
        "response_sha": _sha(resp.text) if resp else None,
        "response_text": resp.text if resp else None,
        "stop_reason": resp.stop_reason if resp else None,
        "request_id": resp.request_id if resp else None,
        "usage_if_reported": resp.usage if resp else None,
        "cost_usd_if_reported": resp.cost_usd if resp else None,
        "latency_s": resp.latency_s if resp else None,
        "verdict": None, "malformed": None, "patch": None, "validator_result": None,
        "candidate_scene_hash": None, "error": None,
    }


def revise(architect: Architect, *, scene: dict, observed: dict, stage: str,
           budget: RevisionBudget, previous: Iterable[dict] = (), prompt: pr.Prompt | None = None,
           episode: str | None = None, log: CallLog | None = None) -> Revision:
    """One architect revision: context -> propose -> parse -> validate -> log."""
    if budget.exhausted:
        raise BudgetExhausted(f"all {budget.max_revisions} architect revisions are used")
    prompt = prompt or pr.load_prompt("baseline")
    ctx = pr.build_context(scene, observed, stage=stage, revision=budget.used + 1,
                           max_revisions=budget.max_revisions, previous=list(previous))
    req = pr.make_request(ctx, prompt)
    index = budget.consume()
    try:
        resp = architect.propose(req)
    except Exception as exc:
        rec = _record(req, None, architect=architect, episode=episode, index=index, budget=budget)
        rec.update(verdict="ERROR", error=f"{type(exc).__name__}: {exc}")
        if log is not None:
            log.append(rec)
        raise
    rec = _record(req, resp, architect=architect, episode=episode, index=index, budget=budget)
    parsed = parse_response(resp.text, resp.stop_reason)
    if parsed.malformed:
        budget.rejected += 1
        rec.update(verdict="MALFORMED", malformed=parsed.malformed)
        rev = Revision(index, "MALFORMED", None, parsed.malformed, record=rec)
    else:
        result = validate(scene, parsed.patch, stage=stage)
        verdict = "ACCEPTED" if result.ok else "REJECTED"
        if not result.ok:
            budget.rejected += 1
        cand = result.scene if result.ok else None
        rec.update(verdict=verdict, patch=parsed.patch, validator_result=result.to_dict(),
                   candidate_scene_hash=canonical_hash(cand) if cand is not None else None)
        rev = Revision(index, verdict, parsed.patch, None,
                       violations=tuple({"code": v.code, "ref": v.ref} for v in result.violations),
                       warnings=tuple(w.code for w in result.warnings), candidate_scene=cand, record=rec)
    if log is not None:
        log.append(rec)
    return rev


@dataclass(frozen=True)
class LoopResult:
    revisions: tuple
    accepted: Revision | None
    exhausted: bool
    used: int
    rejected: int


def propose_until_valid(architect: Architect, *, scene: dict, observed: dict, stage: str,
                        budget: RevisionBudget | None = None, prompt: pr.Prompt | None = None,
                        episode: str | None = None, log: CallLog | None = None,
                        previous: Iterable[dict] = ()) -> LoopResult:
    """Revise on one scene version until the validator accepts a patch or the budget is used.
    `previous` carries earlier revisions of the episode (on older scene versions)."""
    budget = budget or RevisionBudget()
    prev = list(previous)
    if len(prev) != budget.used:
        raise ValueError(f"previous has {len(prev)} verdicts but the budget has used {budget.used}")
    revs = []
    while not budget.exhausted:
        rev = revise(architect, scene=scene, observed=observed, stage=stage, budget=budget,
                     previous=prev, prompt=prompt, episode=episode, log=log)
        revs.append(rev)
        prev.append(rev.feedback())
        if rev.verdict == "ACCEPTED":
            return LoopResult(tuple(revs), rev, False, budget.used, budget.rejected)
    return LoopResult(tuple(revs), None, True, budget.used, budget.rejected)
