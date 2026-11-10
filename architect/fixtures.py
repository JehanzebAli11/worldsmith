"""Hand-written architect fixtures: offline replay of the revision loop (WS-10).

A fixture (`architect/fixtures/<name>.json`, schema `worldsmith.architect_fixture/0.1`) is one
scenario: a base scene, the classified episode the architect observes, a stage, a prompt mode,
and the replies for each revision in order, each with its request hash and its expected
verdict. Replies are written by hand to exercise the client, parser, validator and revision
counting; they are **not model outputs** and say so in `provenance`.

`replay(name)` runs `propose_until_valid` with a `FixtureArchitect` (replies looked up by
request hash, so a changed prompt or input is a loud `FixtureMiss`, never a silent mismatch).
`compute_request_shas(name)` replays by turn order instead, to refresh the hashes after a
deliberate prompt change: `python scripts/architect_fixtures.py` (`--check` only verifies).
"""

from __future__ import annotations

import json
from pathlib import Path

from worldsmith.architect import loop as lp
from worldsmith.architect import prompt as pr
from worldsmith.architect.client import (FIXTURE_DIR, FIXTURE_SCHEMA, REPO_ROOT, ArchitectConfigError,
                                         FixtureArchitect, RawResponse, fixture_path)
from worldsmith.grammar.scene import load_scene

_KEYS = {"schema", "name", "provenance", "why", "scene", "classification", "stage", "prompt_mode",
         "max_revisions", "turns", "expect"}


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))


def load_fixture(name_or_path) -> dict:
    path = fixture_path(name_or_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != FIXTURE_SCHEMA or set(doc) - _KEYS or doc.get("name") != path.stem:
        raise ArchitectConfigError(f"{path}: needs schema {FIXTURE_SCHEMA}, name {path.stem!r}, "
                                   f"keys within {sorted(_KEYS)}")
    if not doc.get("turns"):
        raise ArchitectConfigError(f"{path}: no turns")
    return doc


def _inputs(doc: dict):
    scene = load_scene(REPO_ROOT / doc["scene"])
    cl_doc = json.loads((REPO_ROOT / doc["classification"]["file"]).read_text(encoding="utf-8"))
    ep = next(e for e in cl_doc["episodes"] if e["episode"] == doc["classification"]["episode"])
    return scene, pr.observed_from_classification(ep)


def _run(doc: dict, architect, log: lp.CallLog | None = None) -> lp.LoopResult:
    scene, observed = _inputs(doc)
    return lp.propose_until_valid(architect, scene=scene, observed=observed, stage=doc["stage"],
                                  budget=lp.RevisionBudget(doc.get("max_revisions", 3)),
                                  prompt=pr.load_prompt(doc["prompt_mode"]), episode=doc["name"],
                                  log=log)


def replay(name_or_path) -> tuple[lp.LoopResult, lp.CallLog]:
    doc = load_fixture(name_or_path)
    log = lp.CallLog()
    res = _run(doc, FixtureArchitect.from_names([fixture_path(name_or_path)]), log)
    return res, log


class _InOrder:
    """Replies by turn order (only to compute request hashes)."""
    model_role = "fixture"
    provider = "fixture"

    def __init__(self, doc):
        self.model_id = f"fixture:{doc['name']}"
        self.turns = list(doc["turns"])
        self.shas = []

    def propose(self, req):
        self.shas.append(req.request_sha)
        t = self.turns.pop(0)
        return RawResponse(text=t["response_text"], model_role=self.model_role, model_id=self.model_id,
                           provider=self.provider, stop_reason=t.get("stop_reason", "end_turn"))


def compute_request_shas(name_or_path) -> list[str]:
    doc = load_fixture(name_or_path)
    arch = _InOrder(doc)
    _run(doc, arch)
    if arch.turns:
        raise ArchitectConfigError(f"{doc['name']}: {len(arch.turns)} turn(s) never used; the loop "
                                   f"stopped earlier (accepted or out of revisions)")
    return arch.shas
