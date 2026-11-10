"""Architect prompt, inputs and request hashing (WS-10; SPEC section 7 item 6, ADR 0009).

The architect gets exactly (SPEC section 7 item 6, plus what it needs to write a legal patch):

- the current scene (canonical JSON) and its canonical hash;
- the observed event tags per attempt (`observed_from_classification`) and a short trace
  summary derived from them (`trace_summary`);
- the stage, the ops it allows, the bounds for the fields they set, the op limit, the patch
  schema(s) it accepts;
- the revision number and the validator's verdicts on the earlier revisions of the episode.

Never: controller code or hashes, routes, run nonces, raw tool files, config or file hashes,
Autopilot memory, Jev prompts or test constants (`observed_from_classification` keeps a fixed
allow-list of keys, nothing else). A hypothesis confirmed with post-fix evidence (the WS-08
`paired_fix` condition) is shown as it stood before any fix: `cause` UNCLASSIFIED and the
hypothesis `unconfirmed`, with its other conditions (ADR 0009).

The request hash (`request_sha`) is the SHA-256 of the canonical JSON of the exact system and
user texts sent; it does not include the model or max_tokens, so one recorded context has one
hash across providers. `context_sha` hashes the context object. Both are logged.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Iterable

from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt

CONTEXT_SCHEMA = "worldsmith.architect_context/0.1"
PROMPT_FILES = {"baseline": "prompt.md", "redteam": "prompt_redteam.md"}
MAX_REVISIONS_CAP = 3            # SPEC sections 6 and 12
TRACE_SUMMARY_MAX_LINES = 8
_PACKAGE = "worldsmith.architect"

# The only per-attempt keys the architect sees (SPEC section 7 item 6). Everything else in a
# classification record (nonce, raw file, hashes, stuck window details, evidence) stays out.
ATTEMPT_KEYS = ("attempt", "outcome", "tags", "cause", "events", "detail", "fell_in_area")
WITHHELD_CONDITIONS = ("paired_fix",)   # post-fix evidence: the answer, not an observation
HYPOTHESIS_TAGS = ("BRIDGE_SHORT_HYPOTHESIS",)
SLOTS = ("stage", "revision", "max_revisions", "scene_hash", "scene_json", "observed_json",
         "trace_summary", "allowed_ops_json", "bounds_json", "limits_json", "patch_schema_json",
         "previous_json")


# --------------------------------------------------------------------------- prompt files

@dataclass(frozen=True)
class Prompt:
    mode: str            # baseline | redteam
    version: str
    sha256: str          # of the whole prompt file
    system: str
    user_template: str
    file: str

    @property
    def is_baseline(self) -> bool:
        return self.mode == "baseline"


_BLOCK = {name: re.compile(rf"^<<<{name}\n(.*?)\n{name}>>>$", re.S | re.M) for name in ("SYSTEM", "USER")}


def parse_prompt(text: str, file: str = "<text>") -> Prompt:
    head = re.match(r"\A<!--\n(.*?)\n-->\n", text, re.S)
    if not head:
        raise ValueError(f"{file}: missing the <!-- id/version/mode --> header")
    meta = dict(line.split(": ", 1) for line in head.group(1).splitlines() if ": " in line)
    if meta.get("id") != "worldsmith.architect.prompt" or not meta.get("version") \
            or meta.get("mode") not in PROMPT_FILES:
        raise ValueError(f"{file}: header needs id worldsmith.architect.prompt, version and mode")
    parts = {}
    for name, rx in _BLOCK.items():
        found = rx.findall(text)
        if len(found) != 1:
            raise ValueError(f"{file}: needs exactly one <<<{name} ... {name}>>> block")
        parts[name] = found[0]
    slots = set(re.findall(r"\{\{(\w+)\}\}", parts["USER"]))
    if slots != set(SLOTS):
        raise ValueError(f"{file}: user template slots {sorted(slots)} != {sorted(SLOTS)}")
    if "{{" in parts["SYSTEM"]:
        raise ValueError(f"{file}: the system text has no slots (it is cached as is)")
    return Prompt(mode=meta["mode"], version=meta["version"],
                  sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  system=parts["SYSTEM"], user_template=parts["USER"], file=file)


def load_prompt(mode: str = "baseline") -> Prompt:
    """The `baseline` (B3) or `redteam` (ROADMAP section 4.4, never a baseline) prompt."""
    if mode not in PROMPT_FILES:
        raise ValueError(f"unknown prompt mode {mode!r}; known: {sorted(PROMPT_FILES)}")
    name = PROMPT_FILES[mode]
    data = resources.files(_PACKAGE).joinpath(name).read_bytes()
    p = parse_prompt(data.decode("utf-8"), f"architect/{name}")
    if p.mode != mode:
        raise ValueError(f"architect/{name} declares mode {p.mode!r}, expected {mode!r}")
    return p


# --------------------------------------------------------------------------- observations

def _bool(v):
    return v if isinstance(v, bool) else None


def observed_from_classification(episode: dict) -> dict:
    """The architect's view of one classified episode (a WS-08 `classification.json` episode
    or the same shape from the orchestrator): per-attempt outcome, tags, cause, events, detail,
    fell_in_area, the STUCK result, and hypothesis conditions minus withheld evidence."""
    attempts = []
    for a in episode.get("attempts") or []:
        out = {k: a[k] for k in ATTEMPT_KEYS if k in a}
        out["tags"] = list(out.get("tags") or [])
        out["events"] = list(out.get("events") or [])
        st = a.get("stuck")
        if isinstance(st, dict) and st.get("result"):
            out["stuck"] = st["result"]
        hyp = a.get("hypothesis") or {}
        conds = hyp.get("conditions") or {}
        if conds:
            withheld = [c for c in WITHHELD_CONDITIONS if c in conds]
            shown = {name: _bool((c or {}).get("ok")) for name, c in sorted(conds.items())
                     if name not in WITHHELD_CONDITIONS}
            tag = hyp.get("tag") or (hyp.get("cause") if hyp.get("cause") in HYPOTHESIS_TAGS else None) \
                or HYPOTHESIS_TAGS[0]
            if withheld:
                # Pre-fix view: the confirmation needed runs after a fix, which the architect
                # must not see. The classifier's cause without it is UNCLASSIFIED (SPEC 10).
                if out.get("cause") in HYPOTHESIS_TAGS:
                    out["cause"] = "UNCLASSIFIED"
                out["tags"] = [t for t in out["tags"] if t not in HYPOTHESIS_TAGS]
                status = "unconfirmed"
            else:
                status = "confirmed" if hyp.get("tag") else "unconfirmed"
            out["hypothesis"] = {"tag": tag, "status": status, "conditions": shown}
        attempts.append(out)
    return {"attempts": attempts,
            "episode_tags": list(episode.get("episode_tags") or []),
            "n": len(attempts),
            "successes": sum(1 for a in attempts if a.get("outcome") == "SUCCESS")}


def trace_summary(observed: dict) -> list[str]:
    """A few lines a person could read: successes/N, then one line per attempt (merged when
    attempts are identical)."""
    lines = [f"{observed.get('successes', 0)}/{observed.get('n', 0)} SUCCESS on this scene version"]
    groups: list[tuple[str, list[int]]] = []
    for a in observed.get("attempts") or []:
        bits = [a.get("outcome") or "UNKNOWN"]
        if a.get("cause") and a.get("cause") != a.get("outcome"):
            bits.append(f"cause {a['cause']}")
        if a.get("events"):
            bits.append("events " + " > ".join(a["events"]))
        if a.get("detail"):
            bits.append(a["detail"])
        text = "; ".join(bits)
        if groups and groups[-1][0] == text:
            groups[-1][1].append(a.get("attempt"))
        else:
            groups.append((text, [a.get("attempt")]))
    for text, idx in groups:
        label = "attempt" if len(idx) == 1 else "attempts"
        lines.append(f"{label} {', '.join(str(i) for i in idx)}: {text}")
    if observed.get("episode_tags"):
        lines.append("episode tags: " + ", ".join(observed["episode_tags"]))
    if len(lines) > TRACE_SUMMARY_MAX_LINES:
        lines = lines[:TRACE_SUMMARY_MAX_LINES - 1] + [f"(+{len(lines) - TRACE_SUMMARY_MAX_LINES + 1} more lines)"]
    return lines


# --------------------------------------------------------------------------- context

def allowed_ops(stage: str) -> list[dict]:
    return [{"op": r.op, "type": r.type, "id": r.id, "field": r.field} for r in pt.stage_rules(stage)]


def stage_bounds(stage: str) -> dict:
    b = sc.load_bounds()
    keys = set()
    for r in pt.stage_rules(stage):
        if r.field is not None:
            keys.add(f"{r.type}.{r.field}")
        else:
            keys.update(k for k in b if k.startswith(f"{r.type}."))
    return {k: b[k] for k in sorted(keys) if k in b}


def stage_schemas(stage: str) -> dict:
    pt.stage_rules(stage)
    return {sid: pt.load_patch_schema(sid) for sid in pt.STAGE_SCHEMAS[stage]}


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def build_context(scene: dict, observed: dict, *, stage: str, revision: int,
                  max_revisions: int = MAX_REVISIONS_CAP, previous: Iterable[dict] = ()) -> dict:
    """The full architect input for one revision. `previous` is the list of feedback dicts
    (`Revision.feedback()`) of the earlier revisions in the episode."""
    if not (_int(max_revisions) and 1 <= max_revisions <= MAX_REVISIONS_CAP):
        raise ValueError(f"max_revisions must be 1..{MAX_REVISIONS_CAP}, got {max_revisions!r}")
    if not (_int(revision) and 1 <= revision <= max_revisions):
        raise ValueError(f"revision must be 1..{max_revisions}, got {revision!r}")
    pt.stage_rules(stage)
    errs = sc.validate_scene(scene)
    if errs:
        raise ValueError(f"the scene is invalid: {'; '.join(errs[:3])}")
    prev = [json.loads(json.dumps(p, sort_keys=True)) for p in previous]
    if len(prev) != revision - 1:
        raise ValueError(f"revision {revision} needs {revision - 1} previous verdicts, got {len(prev)}")
    return {
        "schema": CONTEXT_SCHEMA,
        "stage": stage,
        "scene_hash": sc.canonical_hash(scene),
        "scene": json.loads(sc.canonical_json(scene)),
        "observed": json.loads(json.dumps(observed, sort_keys=True)),
        "trace_summary": trace_summary(observed),
        "allowed_ops": allowed_ops(stage),
        "bounds": stage_bounds(stage),
        "patch_schemas": stage_schemas(stage),
        "limits": {"max_ops": pt.MAX_OPS},
        "revision": {"this": revision, "max": max_revisions},
        "previous": prev,
    }


def _j(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)


def canonical_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def render(prompt: Prompt, ctx: dict) -> tuple[str, str]:
    """(system, user) texts for one context."""
    values = {
        "stage": ctx["stage"],
        "revision": str(ctx["revision"]["this"]),
        "max_revisions": str(ctx["revision"]["max"]),
        "scene_hash": ctx["scene_hash"],
        "scene_json": _j(ctx["scene"]),
        "observed_json": _j(ctx["observed"]),
        "trace_summary": "\n".join(f"- {l}" for l in ctx["trace_summary"]),
        "allowed_ops_json": _j(ctx["allowed_ops"]),
        "bounds_json": _j(ctx["bounds"]),
        "limits_json": _j(ctx["limits"]),
        "patch_schema_json": _j(ctx["patch_schemas"]),
        "previous_json": _j(ctx["previous"]),
    }
    user = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], prompt.user_template)
    return prompt.system, user


@dataclass(frozen=True)
class ArchitectRequest:
    """One rendered architect call: what `Architect.propose` receives."""
    context: dict = field(repr=False)
    prompt: Prompt = field(repr=False)
    system: str = field(repr=False)
    user: str = field(repr=False)
    request_sha: str
    context_sha: str


def make_request(ctx: dict, prompt: Prompt) -> ArchitectRequest:
    system, user = render(prompt, ctx)
    return ArchitectRequest(context=ctx, prompt=prompt, system=system, user=user,
                            request_sha=canonical_sha({"system": system, "user": user}),
                            context_sha=canonical_sha(ctx))
