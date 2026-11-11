"""Hand-written architect fixtures: offline replay of the revision loop (WS-10).

A fixture (`architect/fixtures/<name>.json`, schema `worldsmith.architect_fixture/0.1`) is one
scenario: a base scene, the classified episode the architect observes, a stage, a prompt mode,
and the replies for each revision in order, each with its request hash and its expected
verdict. Replies are written by hand to exercise the client, parser, validator and revision
counting; they are **not model outputs** and say so in `provenance`.

The classified episode is either a WS-08 `classification.json` episode (`{"file", "episode"}`) or
one scene version of a recorded experiment episode (`{"episode_dir", "version"}`, WS-14: the M1
fixtures in `architect/fixtures/m1/` observe the live gate's bundles, which the dry run's
FakeOperator reproduces attempt for attempt, so their requests are the dry run's requests).

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
from worldsmith.grammar.scene import canonical_hash, load_scene

_KEYS = {"schema", "name", "provenance", "why", "scene", "classification", "stage", "prompt_mode",
         "max_revisions", "turns", "expect"}


M1_DIR = "m1"      # WS-14: M1 replies for the m2_hard dry run (`architect/fixtures/m1/<name>.json`)


def fixture_names() -> list[str]:
    """Every fixture: the WS-10 ones by stem, the WS-14 M1 ones as `m1/<stem>`."""
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.json")) + \
        sorted(f"{M1_DIR}/{p.stem}" for p in (FIXTURE_DIR / M1_DIR).glob("*.json"))


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
    cl = doc["classification"]
    if "episode_dir" in cl:
        # A recorded experiment episode (ADR 0010 bundle): the classified attempt rows of one
        # scene version, as the orchestrator shows them to the architect (episode.observed_of).
        vdir = REPO_ROOT / cl["episode_dir"] / f"v{cl['version']}"
        if canonical_hash(load_scene(vdir / "scene.json")) != canonical_hash(scene):
            raise ArchitectConfigError(f"{doc['name']}: {vdir}/scene.json is not the fixture's scene")
        rows = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((vdir / "attempts").glob("[0-9]*.json"), key=lambda p: int(p.stem))]
        return scene, pr.observed_from_classification({"attempts": rows, "episode_tags": []})
    cl_doc = json.loads((REPO_ROOT / cl["file"]).read_text(encoding="utf-8"))
    ep = next(e for e in cl_doc["episodes"] if e["episode"] == cl["episode"])
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
