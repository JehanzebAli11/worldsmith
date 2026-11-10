"""The capped random valid sampler: baseline B2 (WS-09, SPEC section 12).

    propose_episode(scene, stage, seed, budget=3) -> [Proposal]    one offline episode
    make_b2_proposer(seed=, stage=, max_revisions=3) -> B2Proposer  the WS-11 harness hook
    sample_patch(scene, stage, rng, proposal=0) -> patch             sampling only
    replay_patch(record) -> patch                                    re-derive a recorded patch

**Budget.** At most `MAX_PROPOSALS` (3) raw proposals per episode, validator rejects included
(SPEC section 12: no unlimited resampling; its failures are reported). A budget outside 1..3 is a
caller error. The orchestrator's equal-budget hook (`B2Proposer`) takes the same
`max_revisions=3` as the architect (SPEC section 6) and raises `BudgetExhausted` past it.

**No peeking.** Sampling never calls the validator. Every raw proposal is then validated exactly
once (`worldsmith.validator.validate.validate`) and is returned, and counts, whether it is
accepted or rejected. B2 makes zero model calls (SPEC section 12 reports call counts; it does not
equalise them).

**What is sampled** (from the patch grammar and the stage, never from validator outcomes):

1. Op count `k`: M0 allows one op (its only rule is `set bridge_1.length`, and the patch grammar
   allows one op per target), so `k = 1`; a stage with an `add` rule (M1) draws
   `k = rng.randint(1, 3)` (SPEC section 6: at most 3 ops per patch).
2. Each op: a rule drawn uniformly (`rng.randrange`) from the stage's rules in
   `validator.patch.STAGES` order, leaving out `set`/`move` rules already used in this patch (one
   op per target is a grammar rule, `OP_CONFLICT`); `add` rules may repeat.
3. Values: each number uniform over the canonical 0.001-stud grid of its `grammar/bounds.yaml`
   range, inclusive (`rng.randint(round(min*1000), round(max*1000)) / 1000`); a vector draws its
   three components in x, y, z order. `set` uses `<Type>.<field>`; `move` uses `<Type>.pos`;
   an added Wall draws `pos` then `size` (`Wall.pos`, `Wall.size`); an added Decor draws its
   `palette` uniformly from the scene schema's enum (schema order), then `pos`, then `size`.
   The bounds are the grammar's, not the room's: most M1 positions land outside the F1 room
   and are rejected (`OUTSIDE_ROOM`). That is the sampler the SPEC defines; it is reported, not
   tuned.
4. Added ids: `b2_<type>_<proposal>_<op>` (e.g. `b2_wall_1_0`), unique within an episode.
5. Patch schema: the last one the stage accepts (`worldsmith.patch/0.1` in M0, `0.2` in M1);
   `base_hash` is the canonical hash of the scene the proposal is made against.

**Seeding** (deterministic across processes and `PYTHONHASHSEED`; no set or dict-hash order is
used). Proposal `i` of episode `e` under seed `s` uses `random.Random(derive_seed(s, i, episode=e))`
where `derive_seed` is the first 8 bytes (big-endian) of
`SHA-256("worldsmith.b2/0.1|seed=<s>|episode=<e>|proposal=<i>")`. Each record carries that string,
the integer seed and the SHA-256 of the generator's initial state (`getstate()`, compact JSON), so
a proposal is replayed from its record alone (`replay_patch`), independently of earlier draws.

Nothing here plays the room: a record holds the validator verdict (ok, codes, warnings) and the
WS-07 navigation approximation (`graph_walk`, `graph_jump`: CONNECTED / DISCONNECTED, or None when
the graph was not computed). An accepted patch is one that could be tried; only actual runs decide
whether it repairs anything (SPEC section 7).
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from worldsmith.grammar import scene as sc
from worldsmith.validator import patch as pt
from worldsmith.validator.validate import validate

SAMPLER_ID = "worldsmith.b2/0.1"
MAX_PROPOSALS = 3          # SPEC section 12: at most 3 raw proposals per episode, rejects included
_GRID = 1000               # canonical precision: 3 decimals (ADR 0002)
# The fields sampled for an added entity, in draw order. A stage that adds another type fails
# closed (NotImplementedError) until it is listed here.
_ADD_FIELDS = {
    "Wall": ("pos", "size"),
    "Decor": ("palette", "pos", "size"),
}


class BudgetExhausted(RuntimeError):
    """The proposer has used all of its revisions; B2 never resamples past its budget."""


# --- seeding -------------------------------------------------------------------------------

def derivation(seed: int, proposal: int, *, episode: int = 0) -> str:
    """The documented string behind proposal `proposal` of episode `episode` under `seed`."""
    for name, v in (("seed", seed), ("proposal", proposal), ("episode", episode)):
        if not isinstance(v, int) or isinstance(v, bool):
            raise TypeError(f"{name} must be an int, not {type(v).__name__}")
    return f"{SAMPLER_ID}|seed={seed}|episode={episode}|proposal={proposal}"


def derive_seed(seed: int, proposal: int, *, episode: int = 0) -> int:
    """First 8 bytes (big-endian) of SHA-256 of `derivation(...)`."""
    text = derivation(seed, proposal, episode=episode)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def proposal_rng(seed: int, proposal: int, *, episode: int = 0) -> random.Random:
    """A fresh generator for one proposal."""
    return random.Random(derive_seed(seed, proposal, episode=episode))


def _state_sha256(rng: random.Random) -> str:
    return hashlib.sha256(json.dumps(rng.getstate(), separators=(",", ":")).encode()).hexdigest()


# --- sampling ------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _decor_palette() -> tuple[str, ...]:
    return tuple(sc.load_schema()["$defs"]["Decor"]["properties"]["palette"]["enum"])


def _uniform(rng: random.Random, key: str) -> int | float:
    b = sc.load_bounds().get(key)
    if b is None:
        raise NotImplementedError(f"no bound declared for {key}: cannot sample it (fail closed)")
    n = rng.randint(round(b["min"] * _GRID), round(b["max"] * _GRID))
    return n // _GRID if n % _GRID == 0 else n / _GRID


def _vector(rng: random.Random, key: str) -> list:
    return [_uniform(rng, key) for _ in range(3)]


def _op_cap(rules: tuple) -> int:
    if any(r.op == "add" for r in rules):
        return pt.MAX_OPS
    return min(pt.MAX_OPS, len(rules))


def _sample_op(rng: random.Random, rule: pt.OpRule, proposal: int, k: int) -> dict:
    if rule.op == "set":
        return {"op": "set", "id": rule.id, "field": rule.field,
                "value": _uniform(rng, f"{rule.type}.{rule.field}")}
    if rule.op == "move":
        return {"op": "move", "id": rule.id, "pos": _vector(rng, f"{rule.type}.pos")}
    if rule.op == "add":
        fields = _ADD_FIELDS.get(rule.type)
        if fields is None:
            raise NotImplementedError(f"the sampler has no field list for an added {rule.type}")
        ent: dict[str, Any] = {"id": f"b2_{rule.type.lower()}_{proposal}_{k}", "type": rule.type}
        for f in fields:
            if f == "palette":
                pal = _decor_palette()
                ent[f] = pal[rng.randrange(len(pal))]
            else:
                ent[f] = _vector(rng, f"{rule.type}.{f}")
        return {"op": "add", "entity": ent}
    raise NotImplementedError(f"the sampler cannot draw a {rule.op!r} op")


def _sample(base_hash: str, stage: str, rng: random.Random, proposal: int) -> dict:
    rules = pt.stage_rules(stage)  # ValueError for an unknown stage
    k_ops = rng.randint(1, _op_cap(rules))
    used: list[pt.OpRule] = []
    ops = []
    for k in range(k_ops):
        avail = [r for r in rules if r.op == "add" or r not in used]
        rule = avail[rng.randrange(len(avail))]
        used.append(rule)
        ops.append(_sample_op(rng, rule, proposal, k))
    return {"schema": pt.STAGE_SCHEMAS[stage][-1], "base_hash": base_hash, "ops": ops}


def sample_patch(scene: dict, stage: str, rng: random.Random, *, proposal: int = 0) -> dict:
    """One raw patch for `scene` at `stage`, drawn from `rng`. Never consults the validator;
    never mutates `scene`. `proposal` only names added entities."""
    return _sample(sc.canonical_hash(scene), stage, rng, proposal)


# --- proposals -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Proposal:
    """One raw proposal and its validator verdict; `to_dict()` is the replayable record."""
    stage: str
    seed: int
    episode: int
    index: int
    rng_derivation: str
    rng_seed: int
    rng_state_sha256: str
    patch: dict
    patch_hash: str
    base_hash: str
    ok: bool
    codes: tuple[str, ...]
    warnings: tuple[str, ...]
    graph_walk: str | None
    graph_jump: str | None

    def to_dict(self) -> dict:
        return {
            "sampler": SAMPLER_ID, "stage": self.stage, "seed": self.seed,
            "episode": self.episode, "index": self.index,
            "rng": {"derivation": self.rng_derivation, "seed": self.rng_seed,
                    "state_sha256": self.rng_state_sha256},
            "patch": json.loads(json.dumps(self.patch)), "patch_hash": self.patch_hash,
            "base_hash": self.base_hash, "ok": self.ok, "codes": list(self.codes),
            "warnings": list(self.warnings), "graph_walk": self.graph_walk,
            "graph_jump": self.graph_jump,
        }


def propose(scene: dict, stage: str, seed: int, proposal: int, *, episode: int = 0) -> Proposal:
    """Draw proposal `proposal` (0-based) for `scene`, then validate it once. Rejects are
    returned like accepted ones; the caller counts both against the budget."""
    pt.stage_rules(stage)
    text = derivation(seed, proposal, episode=episode)
    rseed = derive_seed(seed, proposal, episode=episode)
    rng = random.Random(rseed)
    state = _state_sha256(rng)
    patch = sample_patch(scene, stage, rng, proposal=proposal)
    res = validate(scene, patch, stage=stage)
    graph = res.graph or {}
    return Proposal(
        stage=stage, seed=seed, episode=episode, index=proposal, rng_derivation=text,
        rng_seed=rseed, rng_state_sha256=state, patch=patch, patch_hash=pt.patch_hash(patch),
        base_hash=patch["base_hash"], ok=res.ok, codes=tuple(sorted(res.codes)),
        warnings=tuple(sorted({w.code for w in res.warnings})),
        graph_walk=graph.get("walk", {}).get("result") if graph else None,
        graph_jump=graph.get("jump", {}).get("result") if graph else None)


def _check_budget(budget: Any, name: str = "budget") -> int:
    if not isinstance(budget, int) or isinstance(budget, bool):
        raise TypeError(f"{name} must be an int, not {type(budget).__name__}")
    if not 1 <= budget <= MAX_PROPOSALS:
        raise ValueError(f"{name} {budget} is outside 1..{MAX_PROPOSALS} (SPEC section 12: at most"
                         f" {MAX_PROPOSALS} raw proposals per episode, rejects included)")
    return budget


def propose_episode(scene: dict, stage: str, seed: int, budget: int = MAX_PROPOSALS, *,
                    episode: int = 0) -> list[Proposal]:
    """`budget` raw proposals against the same `scene` (offline: nothing is played, so the scene
    does not change between them). Every proposal is returned, accepted or rejected."""
    _check_budget(budget)
    pt.stage_rules(stage)
    return [propose(scene, stage, seed, i, episode=episode) for i in range(budget)]


def replay_patch(record: dict | Proposal) -> dict:
    """Re-derive a recorded patch from its record alone (stage, seed, episode, index, base_hash)
    and check the record's RNG fields agree. Raises ValueError on any mismatch."""
    d = record.to_dict() if isinstance(record, Proposal) else record
    text = derivation(d["seed"], d["index"], episode=d["episode"])
    rng = proposal_rng(d["seed"], d["index"], episode=d["episode"])
    if (d["rng"]["derivation"], d["rng"]["seed"], d["rng"]["state_sha256"]) != \
            (text, derive_seed(d["seed"], d["index"], episode=d["episode"]), _state_sha256(rng)):
        raise ValueError("the record's RNG fields do not match its seed, episode and index")
    return _sample(d["base_hash"], d["stage"], rng, d["index"])


# --- the equal-budget harness hook (WS-11) ---------------------------------------------------

class B2Proposer:
    """What the orchestrator calls once per revision, with the architect's `max_revisions`.

    `propose(scene)` draws the next raw proposal against the scene the orchestrator passes (it
    decides which scene a revision patches), validates it, records it and counts it, accepted or
    rejected. Past `max_revisions` it raises BudgetExhausted. Zero model calls.
    """

    def __init__(self, *, seed: int, stage: str, max_revisions: int = MAX_PROPOSALS,
                 episode: int = 0):
        _check_budget(max_revisions, "max_revisions")
        pt.stage_rules(stage)
        derivation(seed, 0, episode=episode)  # type checks
        self.seed, self.stage, self.episode = seed, stage, episode
        self.max_revisions = max_revisions
        self.model_calls = 0
        self._records: list[Proposal] = []

    @property
    def used(self) -> int:
        return len(self._records)

    @property
    def remaining(self) -> int:
        return self.max_revisions - self.used

    @property
    def records(self) -> tuple[Proposal, ...]:
        return tuple(self._records)

    def propose(self, scene: dict) -> Proposal:
        if self.used >= self.max_revisions:
            raise BudgetExhausted(f"B2 used its {self.max_revisions} revisions (rejects count)")
        rec = propose(scene, self.stage, self.seed, self.used, episode=self.episode)
        self._records.append(rec)
        return rec


def make_b2_proposer(*, seed: int, stage: str, max_revisions: int = MAX_PROPOSALS,
                     episode: int = 0) -> B2Proposer:
    """The WS-11 hook: a B2 proposer with the same revision budget as the architect."""
    return B2Proposer(seed=seed, stage=stage, max_revisions=max_revisions, episode=episode)


# --- the offline statistic (validator and graph only; never a play result) -------------------

def _share(n: int, of: int) -> dict:
    return {"n": n, "of": of, "share": round(n / of, 4) if of else None}


def stage_summary(records: list[dict], budget: int) -> dict:
    """Counts over one stage's records (dicts from `Proposal.to_dict`)."""
    n = len(records)
    acc = [r for r in records if r["ok"]]
    conn = [r for r in records if r["graph_walk"] == "CONNECTED"]
    both = [r for r in acc if r["graph_walk"] == "CONNECTED"]
    episodes: dict[tuple, list] = {}
    for r in records:
        episodes.setdefault((r["seed"], r["episode"]), []).append(r)
    codes: dict[str, int] = {}
    ops_n: dict[str, int] = {}
    ops_kind: dict[str, int] = {}
    for r in records:
        for c in r["codes"]:
            codes[c] = codes.get(c, 0) + 1
        ops = r["patch"]["ops"]
        ops_n[str(len(ops))] = ops_n.get(str(len(ops)), 0) + 1
        for op in ops:
            kind, oid, field = pt.op_target(op)
            name = f"add {op['entity']['type']}" if kind == "add" else \
                f"move {oid}" if kind == "move" else f"{kind} {oid}.{field}"
            ops_kind[name] = ops_kind.get(name, 0) + 1
    return {
        "proposals": n,
        "episodes": len(episodes),
        "budget_per_episode": budget,
        "accepted": _share(len(acc), n),
        "rejected": n - len(acc),
        "walk_connected": _share(len(conn), n),
        "walk_connected_given_accepted": _share(len(both), len(acc)),
        "accepted_and_walk_connected": _share(len(both), n),
        "graph_not_computed": sum(r["graph_walk"] is None for r in records),
        "episodes_with_accepted": _share(sum(any(r["ok"] for r in v) for v in episodes.values()),
                                         len(episodes)),
        "episodes_with_accepted_walk_connected": _share(
            sum(any(r["ok"] and r["graph_walk"] == "CONNECTED" for r in v)
                for v in episodes.values()), len(episodes)),
        "rejection_codes": dict(sorted(codes.items())),
        "ops_per_patch": dict(sorted(ops_n.items())),
        "ops_by_kind": dict(sorted(ops_kind.items())),
    }
