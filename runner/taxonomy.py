"""SPEC section 10 failure taxonomy (v0) over recorded traces, and the hypothesis rule (WS-08).

Layers (each one only adds to the one below; nothing is forced into a tag):

1. Observed, per attempt: `classify.classify_attempt` (WS-04) decides SUCCESS, FELL, TIMEOUT
   (+ LEVER_NOT_ACTIVATED / GATE_NOT_OPENED), HARNESS_ERROR, CONNECTOR_ERROR or UNCLASSIFIED from
   the play tool's recorded result (ADR 0005). `classify_result` adds STUCK (`evaluate_stuck`):
   an extra tag when observed, and the outcome only when nothing else was (UNCLASSIFIED -> STUCK).
2. System, per episode: BUILD_FAILED and CONNECTOR_ERROR from the bundle's summary status
   (`load_episode`). HARNESS_ERROR and CONNECTOR_ERROR are also per-attempt outcomes.
3. Cause, per failed attempt: UNCLASSIFIED by default. The only hypothesis in v0 is
   BRIDGE_SHORT_HYPOTHESIS (`bridge_short_hypothesis`), assigned to a FELL only when ALL hold:
   (a) the KILL_FLOOR_TOUCHED position lies in the gap area (footprint + margin, ADR 0005 §6);
   (b) an independent geometry check on the bundle's scene: bridge length < gap width, and the
       bridge's far end stops short of the far platform (`geometry_check`);
   (c) a recorded episode bundle where the SAME frozen controller (and config and route) went
       k/N = 3/3 SUCCESS on a scene that differs from the fall's scene ONLY in the bridge's
       length, lengthened (`paired_fix_evidence`); linked by bundle path and file hashes.
   A SUCCESS has no cause (None).

The M0 fixture's defect is known by construction; the tag records that the three conditions
hold, which is functional evidence, not a causal diagnosis of an arbitrary room (SPEC sections
5 and 10).

STUCK rule (config/classifier.yaml; epsilon, window W and the largest usable sample gap G):
positions are the controller's own samples (route start, each waypoint's end, controller clock;
ADR 0005: there is no live position stream), used only while the character is alive.
- route phase = t_route_start .. (t_died if it died, else t_end). Shorter than W: NOT_STUCK,
  because no window fits.
- STUCK if some samples i < j have t_j - t_i >= W, every sample between them lies within
  epsilon of sample i (3D distance), and no gap between those samples exceeds G.
- NOT_STUCK if no such window exists and the samples cover the route phase densely (every gap,
  including from the last sample to the end of the phase, <= G).
- otherwise NOT_EVALUABLE (too few or too sparse samples): never a guess. With the P0
  controller, which samples only at waypoint ends, a long blocked attempt is NOT_EVALUABLE.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
from pathlib import Path

import yaml

from worldsmith.compiler import geometry as _geo
from worldsmith.grammar.scene import canonical_hash, load_scene, normalize_scene
from worldsmith.runner import attempts as _attempts
from worldsmith.runner import p0
from worldsmith.runner.classify import classify_attempt, gap_area, in_area

CLASSIFIER_CONFIG_SCHEMA = "worldsmith.classifierconfig/0.1"
CLASSIFICATION_SCHEMA = "worldsmith.classification/0.1"
OBSERVED = ("SUCCESS", "FELL", "LEVER_NOT_ACTIVATED", "GATE_NOT_OPENED", "STUCK", "TIMEOUT")
SYSTEM = ("HARNESS_ERROR", "CONNECTOR_ERROR", "BUILD_FAILED")
HYPOTHESES = ("BRIDGE_SHORT_HYPOTHESIS",)
UNCLASSIFIED = "UNCLASSIFIED"
STUCK_RESULTS = ("STUCK", "NOT_STUCK", "NOT_EVALUABLE")
GAP_ID = "gap_1"
EPS = 1e-6
DISCLOSURE = (
    "The M0 fixture's defect (a 6-stud bridge over a 16-stud gap) is known by construction, and "
    "P0 is a server-scripted MoveTo walker that never jumps, so its fall is expected. "
    "BRIDGE_SHORT_HYPOTHESIS records that SPEC section 10's three conditions hold on recorded "
    "bundles: functional evidence under one frozen controller, not a causal diagnosis of an "
    "arbitrary room, and not an Autopilot or agent result.")


class ClassifierConfigError(ValueError):
    """config/classifier.yaml is malformed."""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load_classifier_config(path) -> dict:
    """The parsed classifier config plus `_sha256` (of the file's bytes) and `_path`."""
    path = Path(path)
    raw = path.read_bytes()
    c = yaml.safe_load(raw.decode("utf-8"))
    errs = []
    if not isinstance(c, dict) or set(c) != {"schema", "stuck", "sources"}:
        raise ClassifierConfigError("config must have exactly schema, stuck and sources")
    if c["schema"] != CLASSIFIER_CONFIG_SCHEMA:
        errs.append(f"schema must be {CLASSIFIER_CONFIG_SCHEMA}")
    s, src = c["stuck"], c["sources"]
    keys = {"epsilon_studs", "window_s", "max_sample_gap_s"}
    if not (isinstance(s, dict) and set(s) == keys and all(_num(v) and v > 0 for v in s.values())):
        errs.append(f"stuck must give a positive number for each of {sorted(keys)}")
    elif s["max_sample_gap_s"] >= s["window_s"]:
        errs.append("stuck.max_sample_gap_s must be below stuck.window_s")
    if not (isinstance(src, dict) and set(src) == keys and all(isinstance(v, str) and v.strip() for v in src.values())):
        errs.append(f"sources must name a source for each of {sorted(keys)}")
    if errs:
        raise ClassifierConfigError("; ".join(errs))
    out = dict(c)
    out["_sha256"] = hashlib.sha256(raw).hexdigest()
    out["_path"] = str(path)
    return out


# --- STUCK ---------------------------------------------------------------------------------

def _pos(text):
    if not isinstance(text, str) or text == "na":
        return None
    try:
        v = tuple(float(x) for x in text.split(","))
    except ValueError:
        return None
    return v if len(v) == 3 and all(math.isfinite(x) for x in v) else None


def _t(v):
    return float(v) if _num(v) and v >= 0 else None


def position_samples(report: dict) -> list[tuple[float, tuple]]:
    """(controller time, position) samples while alive, in time order."""
    out = []
    t0, p = _t(report.get("t_route_start")), _pos(report.get("start_pos"))
    if t0 is not None and p is not None:
        out.append((t0, p))
    for w in report.get("waypoints") or []:
        if not isinstance(w, dict) or w.get("died") is True:
            continue
        t, p = _t(w.get("t")), _pos(w.get("pos"))
        if t is not None and p is not None:
            out.append((t, p))
    t_died = _t(report.get("t_died")) if report.get("died") is True else None
    if t_died is not None:
        out = [x for x in out if x[0] <= t_died]
    return sorted(out, key=lambda x: x[0])


def evaluate_stuck(report, stuck_cfg: dict) -> dict:
    """{result: STUCK | NOT_STUCK | NOT_EVALUABLE, reason, ...}; the rule is in the module doc."""
    eps, win, gap = stuck_cfg["epsilon_studs"], stuck_cfg["window_s"], stuck_cfg["max_sample_gap_s"]
    out = {"result": "NOT_EVALUABLE", "reason": "", "n_samples": 0, "max_gap_s": None,
           "route_phase_s": None, "window": None, "epsilon_studs": eps, "window_s": win,
           "max_sample_gap_s": gap}
    if not isinstance(report, dict):
        out["reason"] = "no controller report"
        return out
    t0 = _t(report.get("t_route_start"))
    died = report.get("died") is True and _t(report.get("t_died")) is not None
    t_end = _t(report.get("t_died")) if died else _t(report.get("t_end"))
    samples = position_samples(report)
    out["n_samples"] = len(samples)
    if t0 is None or t_end is None or t_end < t0:
        out["reason"] = "no route phase (route never started or no end time)"
        return out
    phase = t_end - t0
    out["route_phase_s"] = round(phase, 3)
    if phase < win:
        out["result"] = "NOT_STUCK"
        out["reason"] = f"route phase {phase:.2f} s is shorter than the window ({win} s): no window fits"
        return out
    if len(samples) < 2:
        out["reason"] = f"{len(samples)} position sample(s) over a {phase:.2f} s route phase"
        return out
    gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:])] + [max(0.0, t_end - samples[-1][0])]
    out["max_gap_s"] = round(max(gaps), 3)
    for i, (ti, pi) in enumerate(samples):
        for j in range(i + 1, len(samples)):
            tj, pj = samples[j]
            if math.dist(pj, pi) >= eps or tj - samples[j - 1][0] > gap:
                break
            if tj - ti >= win:
                out["result"] = "STUCK"
                out["window"] = [round(ti, 3), round(tj, 3)]
                out["reason"] = (f"moved < {eps} studs from {ti:.2f} s to {tj:.2f} s "
                                 f"({j - i + 1} samples, no gap > {gap} s)")
                return out
    if max(gaps) <= gap:
        out["result"] = "NOT_STUCK"
        out["reason"] = f"dense samples (max gap {max(gaps):.2f} s) and no {win} s window under {eps} studs"
    else:
        out["reason"] = (f"samples too sparse to judge: max gap {max(gaps):.2f} s > {gap} s "
                         "(the P0 controller samples only at waypoint ends)")
    return out


# --- observed tags for one recorded attempt ------------------------------------------------

def classify_result(tool_result, nonce: str, params: dict, area: dict, stuck_cfg: dict,
                    *, call_error: str | None = None) -> dict:
    """WS-04's observed outcome plus STUCK, and the default cause. No hypothesis here: that
    needs the episode and other bundles (`bridge_short_hypothesis`)."""
    base = classify_attempt(tool_result, nonce, params, area, call_error=call_error)
    rep = None
    if base["report"] is not None and isinstance(tool_result, dict):
        rep = base["report"]
    st = evaluate_stuck(rep, stuck_cfg)
    outcome, tags = base["outcome"], list(base["tags"])
    if st["result"] == "STUCK" and outcome not in SYSTEM:
        if outcome == UNCLASSIFIED:
            outcome, tags = "STUCK", ["STUCK", *tags[1:]]
        else:
            tags.insert(1, "STUCK")
    return {"outcome": outcome, "tags": tags, "cause": None if outcome == "SUCCESS" else UNCLASSIFIED,
            "detail": base["detail"], "events": base["events"], "events_agree": base["events_agree"],
            "fell_in_area": base["fell_in_area"], "stuck": st, "hypothesis": None,
            "report": base["report"], "tool": base["tool"]}


# --- recorded episodes -----------------------------------------------------------------------

@dataclasses.dataclass
class EpisodeRecord:
    dir: Path
    rel: str
    episode: str
    role: str
    run_id: str
    scene: dict
    scene_hash: str
    controller_sha256: str
    config_sha256: str
    route_sha256: str
    n: int
    attempted: int
    status: str
    build_ok: bool
    readback_differences: int | None
    nonces: list
    attempts: list
    episode_tags: list
    files: dict
    problems: list


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel(path: Path, root) -> str:
    p = Path(path).resolve()
    try:
        return p.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def load_episode(episode_dir, cfg: dict, ccfg: dict, root) -> EpisodeRecord:
    """Re-read one episode bundle and re-classify every attempt from its raw tool result.

    Anything that makes the bundle untrustworthy is listed in `problems` (bundle schema, scene
    hash, a config other than `cfg`, a re-classification that disagrees with the summary); a
    record with problems is never used as evidence."""
    d = Path(episode_dir)
    ep = json.loads((d / "episode.json").read_text(encoding="utf-8"))
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    problems = list(_attempts.validate_bundle(d))
    scene = load_scene(d / "scene.json")
    if canonical_hash(scene) != ep["scene_hash"]:
        problems.append("scene.json does not match episode.json scene_hash")
    files = {f: _sha(d / f) for f in ("episode.json", "scene.json", "controller.luau", "summary.json",
                                      "trace.jsonl", "calls.json") if (d / f).is_file()}
    build = s.get("build") or {}
    status = s.get("status")
    episode_tags = [status] if status in ("BUILD_FAILED", "CONNECTOR_ERROR") else []
    out_attempts = []
    if ep["config_sha256"] != cfg["_sha256"]:
        problems.append(f"ran under config {ep['config_sha256'][:12]}…, not {cfg['_sha256'][:12]}…; "
                        "not re-classified")
    else:
        params = p0.expected_params(cfg["_route"], cfg["timing"])
        area = gap_area(scene, GAP_ID, cfg["gap_area_margin_studs"])
        calls = json.loads((d / "calls.json").read_text(encoding="utf-8")) if (d / "calls.json").is_file() else []
        for i, summ in enumerate(s.get("attempts") or [], start=1):
            af = d / "attempts" / f"{i}.json"
            if not af.is_file():
                problems.append(f"attempt {i}: no attempts/{i}.json")
                continue
            rec = json.loads(af.read_text(encoding="utf-8"))
            nonce = ep["nonces"][i - 1]
            ci = rec.get("call_index")
            raw_rel = rec.get("raw_file")
            tool_result, call_error = None, None
            if ci is None:
                call_error = rec.get("detail") or "no play call"
            else:
                call_error = calls[ci - 1].get("error") if 1 <= ci <= len(calls) else "call index out of range"
                raw_path = d / (raw_rel or "")
                if call_error is None and raw_rel and raw_path.is_file():
                    files[raw_rel] = _sha(raw_path)
                    try:
                        tool_result = json.loads(raw_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        call_error = "the play tool's answer is not JSON"
                elif call_error is None:
                    problems.append(f"attempt {i}: raw tool result missing")
                    continue
            res = classify_result(tool_result, nonce, params, area, ccfg["stuck"], call_error=call_error)
            if res["outcome"] != summ["outcome"] and not (res["outcome"] == "STUCK" and summ["outcome"] == UNCLASSIFIED):
                problems.append(f"attempt {i}: re-classified {res['outcome']}, summary says {summ['outcome']}")
            out_attempts.append({"attempt": i, "nonce": nonce, "raw_file": raw_rel if ci is not None else None,
                                 "raw_sha256": files.get(raw_rel) if ci is not None else None, **res})
    return EpisodeRecord(
        dir=d, rel=_rel(d, root), episode=ep["episode"], role=ep["role"], run_id=ep["run_id"],
        scene=scene, scene_hash=ep["scene_hash"], controller_sha256=ep["controller_sha256"],
        config_sha256=ep["config_sha256"], route_sha256=ep["route_sha256"], n=ep["n_attempts"],
        attempted=s.get("attempted", 0), status=status, build_ok=build.get("ok") is True,
        readback_differences=build.get("readback_differences"), nonces=list(ep["nonces"]),
        attempts=out_attempts, episode_tags=episode_tags, files=dict(sorted(files.items())),
        problems=problems)


# --- geometry and scene change ---------------------------------------------------------------

def geometry_check(scene: dict, gap_id: str) -> dict:
    """Bridge length < gap width, read from the scene (not the trace): the declared gap width,
    the width measured between the two platforms' facing edges (they must agree), and how far
    the built bridge's far end stops short of the far platform."""
    out = {"ok": False, "reason": "", "gap": gap_id, "bridge": None, "bridge_length": None,
           "gap_width_declared": None, "gap_width_measured": None, "uncovered_studs": None,
           "scene_hash": canonical_hash(scene)}
    by_id = {e["id"]: e for e in scene.get("entities", [])}
    gap = by_id.get(gap_id)
    if not gap or gap.get("type") != "Gap":
        out["reason"] = f"no Gap {gap_id!r} in the scene"
        return out
    bridges = [e for e in scene["entities"] if e.get("type") == "Bridge" and e.get("spans") == gap_id]
    if len(bridges) != 1:
        out["reason"] = f"{len(bridges)} bridges span {gap_id}; the rule needs exactly one"
        return out
    br = bridges[0]
    src, dst = by_id[gap["from"]], by_id[gap["to"]]
    i, sign = (0 if gap["axis"][1] == "X" else 2), (1 if gap["axis"][0] == "+" else -1)
    src_edge = src["pos"][i] + sign * src["size"][i] / 2
    dst_edge = dst["pos"][i] - sign * dst["size"][i] / 2
    measured = (dst_edge - src_edge) * sign
    part = _geo.entity_parts(br, by_id)[0]
    far_end = part["pos"][i] + sign * part["size"][i] / 2
    uncovered = (dst_edge - far_end) * sign
    out.update(bridge=br["id"], bridge_length=br["length"], gap_width_declared=gap["width"],
               gap_width_measured=_geo._r(measured), uncovered_studs=_geo._r(uncovered))
    if abs(measured - gap["width"]) > EPS:
        out["reason"] = f"declared gap width {gap['width']} != measured {measured:g} between the platforms"
    elif not br["length"] < gap["width"]:
        out["reason"] = f"bridge length {br['length']} is not < gap width {gap['width']}"
    elif not uncovered > EPS:
        out["reason"] = "the built bridge reaches the far platform"
    else:
        out["ok"] = True
        out["reason"] = (f"bridge {br['length']} < gap {gap['width']} (measured {measured:g}); "
                         f"{uncovered:g} studs uncovered")
    return out


def scene_change(a: dict, b: dict) -> list[dict]:
    """Field-level differences between two scenes (normalised): top-level keys with entity None,
    then entity fields (an added or removed entity is field None)."""
    na, nb = normalize_scene(a), normalize_scene(b)
    out = []
    for k in sorted(set(na) | set(nb)):
        if k == "entities":
            continue
        if na.get(k) != nb.get(k):
            out.append({"entity": None, "field": k, "before": na.get(k), "after": nb.get(k)})
    ea = {e["id"]: e for e in na.get("entities", [])}
    eb = {e["id"]: e for e in nb.get("entities", [])}
    for eid in sorted(set(ea) | set(eb)):
        if eid not in ea or eid not in eb:
            out.append({"entity": eid, "field": None, "before": ea.get(eid), "after": eb.get(eid)})
            continue
        for f in sorted(set(ea[eid]) | set(eb[eid])):
            if ea[eid].get(f) != eb[eid].get(f):
                out.append({"entity": eid, "field": f, "before": ea[eid].get(f), "after": eb[eid].get(f)})
    return out


# --- the hypothesis --------------------------------------------------------------------------

def _bridge_id(scene: dict, gap_id: str):
    ids = [e["id"] for e in scene.get("entities", []) if e.get("type") == "Bridge" and e.get("spans") == gap_id]
    return ids[0] if len(ids) == 1 else None


def paired_fix_evidence(ep: EpisodeRecord, candidates, cfg: dict, gap_id: str = GAP_ID) -> dict:
    """Recorded episodes that confirm condition (c) for `ep`'s scene; every other candidate is
    listed with its reasons."""
    k_req, n_req = cfg["pass_k"], cfg["n_attempts"]
    bridge = _bridge_id(ep.scene, gap_id)
    evidence, rejected = [], []
    for c in candidates:
        reasons = []
        if Path(c.dir).resolve() == Path(ep.dir).resolve():
            reasons.append("the same episode as the fall")
        if c.problems:
            reasons.append(f"bundle problems: {c.problems}")
        if c.controller_sha256 != ep.controller_sha256:
            reasons.append(f"controller {c.controller_sha256[:12]}… != {ep.controller_sha256[:12]}… (not the same frozen player)")
        if c.config_sha256 != ep.config_sha256:
            reasons.append("different config hash")
        if c.route_sha256 != ep.route_sha256:
            reasons.append("different route hash")
        if c.status != "complete" or not c.build_ok or c.readback_differences != 0:
            reasons.append(f"episode not clean (status {c.status}, build ok {c.build_ok}, "
                           f"read-back differences {c.readback_differences})")
        outcomes = [a["outcome"] for a in c.attempts]
        hits = outcomes.count("SUCCESS")
        if not (c.n == n_req and len(outcomes) == n_req and hits >= k_req):
            reasons.append(f"not {k_req}/{n_req} SUCCESS: {hits}/{c.n} ({len(outcomes)} attempts)")
        change = scene_change(ep.scene, c.scene)
        real = [x for x in change if not (x["entity"] is None and x["field"] == "version")]
        if bridge is None:
            reasons.append(f"no single bridge spans {gap_id} in the fall's scene")
        elif not (len(real) == 1 and real[0]["entity"] == bridge and real[0]["field"] == "length"):
            reasons.append(f"scene change is not {bridge}.length only: "
                           f"{[(x['entity'], x['field']) for x in real]}")
        elif not (_num(real[0]["after"]) and _num(real[0]["before"]) and real[0]["after"] > real[0]["before"]):
            reasons.append(f"{bridge}.length was not lengthened ({real[0]['before']} -> {real[0]['after']})")
        if reasons:
            rejected.append({"bundle": c.rel, "reasons": reasons})
            continue
        evidence.append({
            "bundle": c.rel, "episode": c.episode, "run_id": c.run_id, "scene_hash": c.scene_hash,
            "controller_sha256": c.controller_sha256, "config_sha256": c.config_sha256,
            "route_sha256": c.route_sha256, "k": k_req, "n": c.n, "outcomes": outcomes,
            "nonces": list(c.nonces), "scene_change": change, "files": dict(c.files),
            "fix_covers_gap": geometry_check(c.scene, gap_id)["uncovered_studs"] is not None
            and geometry_check(c.scene, gap_id)["uncovered_studs"] <= EPS,
        })
    return {"ok": bool(evidence), "evidence": evidence, "rejected": rejected}


def bridge_short_hypothesis(ep: EpisodeRecord, attempt: dict, candidates, cfg: dict,
                            gap_id: str = GAP_ID) -> dict:
    """{tag, cause, conditions} for one attempt of `ep`. A non-FELL gets no hypothesis (cause
    None for SUCCESS, UNCLASSIFIED otherwise)."""
    if attempt["outcome"] != "FELL":
        return {"tag": None, "cause": None if attempt["outcome"] == "SUCCESS" else UNCLASSIFIED,
                "conditions": None}
    kill = next((e for e in attempt["events"] if e["event"] == "KILL_FLOOR_TOUCHED"), None)
    area = gap_area(ep.scene, gap_id, cfg["gap_area_margin_studs"])
    in_gap = attempt.get("fell_in_area") is True and kill is not None and in_area(kill["pos"], area)
    a = {"ok": in_gap, "kill_floor_pos": kill["pos"] if kill else None, "area": area,
         "source": "KILL_FLOOR_TOUCHED position (server, lags 2-3.5 studs) in the gap footprint + "
                   "gap_area_margin_studs (config/m0.yaml, ADR 0005 section 6)"}
    b = geometry_check(ep.scene, gap_id)
    b["source"] = f"{ep.rel}/scene.json (scene hash {ep.scene_hash})"
    c = paired_fix_evidence(ep, candidates, cfg, gap_id)
    ok = a["ok"] and b["ok"] and c["ok"]
    tag = "BRIDGE_SHORT_HYPOTHESIS" if ok else None
    return {"tag": tag, "cause": tag or UNCLASSIFIED,
            "conditions": {"fell_in_gap_area": a, "geometry": b, "paired_fix": c}}


# --- classification documents ---------------------------------------------------------------

M0_EPISODES = ("F1-A", "F1-A-fix")
M0_CANDIDATES = ("F1-A", "F1-A-fix", "smoke-F1-E", "smoke2-F1-E")


def classify_episodes(episodes, candidates, cfg: dict, ccfg: dict) -> dict:
    """The classification of every attempt of `episodes` (EpisodeRecords); `candidates` are the
    recorded episodes searched for paired-fix evidence. Deterministic: no clock, no paths
    outside the records."""
    counts: dict[str, int] = {}
    eps_out = []
    for ep in episodes:
        rows = []
        for a in ep.attempts:
            h = bridge_short_hypothesis(ep, a, candidates, cfg)
            tags = list(a["tags"]) + ([h["tag"]] if h["tag"] else [])
            for t in tags:
                counts[t] = counts.get(t, 0) + 1
            rows.append({"attempt": a["attempt"], "nonce": a["nonce"], "raw_file": a["raw_file"],
                         "raw_sha256": a["raw_sha256"], "outcome": a["outcome"], "tags": tags,
                         "cause": h["cause"], "detail": a["detail"],
                         "events": [e["event"] for e in a["events"]], "fell_in_area": a["fell_in_area"],
                         "stuck": a["stuck"], "hypothesis": h["conditions"] and h})
        for t in ep.episode_tags:
            counts[t] = counts.get(t, 0) + 1
        eps_out.append({"episode": ep.episode, "bundle": ep.rel, "role": ep.role, "run_id": ep.run_id,
                        "scene_hash": ep.scene_hash, "controller_sha256": ep.controller_sha256,
                        "config_sha256": ep.config_sha256, "status": ep.status,
                        "episode_tags": list(ep.episode_tags), "problems": list(ep.problems),
                        "files": dict(ep.files), "attempts": rows})
    return {
        "schema": CLASSIFICATION_SCHEMA,
        "taxonomy": "SPEC section 10 (v0)",
        "classifier": {"module": "worldsmith/runner/taxonomy.py", "config_file": "config/classifier.yaml",
                       "config_sha256": ccfg["_sha256"], "stuck": dict(ccfg["stuck"]),
                       "m0_config_sha256": cfg["_sha256"]},
        "disclosure": DISCLOSURE,
        "candidates": [c.rel for c in candidates],
        "episodes": eps_out,
        "counts": dict(sorted(counts.items())),
    }


def classify_m0(root, cfg: dict, ccfg: dict) -> dict:
    """bundles/m0/classification.json: the six M0 gate attempts; evidence searched in every
    recorded M0 episode bundle."""
    root = Path(root)
    m0 = root / "bundles" / "m0"
    recs = {n: load_episode(m0 / n, cfg, ccfg, root) for n in M0_CANDIDATES}
    return classify_episodes([recs[n] for n in M0_EPISODES], [recs[n] for n in M0_CANDIDATES], cfg, ccfg)


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"
