"""Read one attempt's tool result and classify it with the SPEC section 10 subset (WS-04).

Tags used here: SUCCESS, FELL, TIMEOUT (with LEVER_NOT_ACTIVATED / GATE_NOT_OPENED as extra
tags), HARNESS_ERROR, CONNECTOR_ERROR, UNCLASSIFIED. BUILD_FAILED is decided by the attempt
runner before any attempt. No hypothesis tag (BRIDGE_SHORT_HYPOTHESIS) is assigned here: SPEC
section 10 needs a geometry check and a 3/3 retry on another episode, so every FELL has cause
UNCLASSIFIED here; `runner/taxonomy.py` (WS-08) adds STUCK, the episode-level system tags and
the hypothesis rule on top of this function, over recorded bundles.

WS-08 tightened two rules (neither changes a recorded M0 outcome): a death without a
KILL_FLOOR_TOUCHED event is UNCLASSIFIED even if a GOAL_TOUCHED line follows (death-then-goal is
never SUCCESS), and a script error reported by the play tool (`error` other than nil) beside a
valid report is HARNESS_ERROR.

ADR 0011 (after the smoke_f1e_p0v1 debug) adds one rule for every player version: an attempt
whose recorded `start_pos` (the HRP at the route start, before the first MoveTo) is more than
START_POS_MAX_STUDS (2) studs in XZ from the spawn centre is HARNESS_ERROR ("moved_before_route"),
never FELL or SUCCESS: something other than the controller moved the character, so the attempt
says nothing about the scene. Every recorded M0 attempt started at x 0.00, z 6.00 (the spawn
centre), so no M0 outcome changes. Callers pass the spawn centre (`spawn_centre(scene)`).

Sources (ADR 0005): the events are the run_script_in_play_mode result's log lines, parsed by
exact prefix and nonce (`console.parse_events`). The controller's own report is printed in
chunks (`WSP|<nonce>|RESULT|i|n|<json>`) and read from the same logs (or from `value` if the
tool ever returns it). The controller also captures its run's event lines from the server log;
that copy is a cross-check only (`events_agree`).
"""

from __future__ import annotations

import json
import math
import re

from worldsmith.compiler import geometry as _geo
from worldsmith.runner.console import NONCE_RE, Event, parse_events

START_POS_MAX_STUDS = 2      # ADR 0011: XZ distance from the spawn centre above which an attempt is void

_RESULT_RE = re.compile(r"^\s*(?:\[OUTPUT\]\s+)?WSP\|(?P<nonce>[0-9a-f]{8,32})\|RESULT\|(?P<i>\d+)\|(?P<n>\d+)\|(?P<chunk>.*?)\s*$")


class ResultError(ValueError):
    """The controller's report is missing or malformed."""


def log_text(tool_result: dict) -> str:
    """Every log message of a run_script_in_play_mode result, one per line, in order."""
    msgs = []
    for entry in tool_result.get("logs") or []:
        if isinstance(entry, dict) and isinstance(entry.get("message"), str):
            msgs.extend(entry["message"].splitlines() or [""])
    value = tool_result.get("value")
    if isinstance(value, str):
        msgs.extend(value.splitlines())
    return "\n".join(msgs)


def parse_report(text: str, nonce: str) -> dict:
    """Reassemble the controller report for `nonce` from WSP|...|RESULT chunks."""
    if not NONCE_RE.fullmatch(nonce or ""):
        raise ValueError("bad nonce")
    chunks: dict[int, str] = {}
    total = None
    for line in text.splitlines():
        m = _RESULT_RE.match(line)
        if not m or m.group("nonce") != nonce:
            continue
        i, n = int(m.group("i")), int(m.group("n"))
        if total is None:
            total = n
        if n != total or not 1 <= i <= n:
            raise ResultError(f"report chunk {i}/{n} is inconsistent")
        if i in chunks:
            if chunks[i] != m.group("chunk"):
                raise ResultError(f"report chunk {i} repeated with different text")
            continue                      # the same line seen twice (logs and value)
        chunks[i] = m.group("chunk")
    if total is None:
        raise ResultError("no controller report for this nonce")
    if len(chunks) != total:
        raise ResultError(f"controller report has {len(chunks)} of {total} chunks")
    try:
        report = json.loads("".join(chunks[i] for i in range(1, total + 1)))
    except json.JSONDecodeError as e:
        raise ResultError(f"controller report is not JSON: {e}") from None
    if not isinstance(report, dict) or report.get("nonce") != nonce:
        raise ResultError("controller report nonce does not match")
    return report


def _same(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-6)
    return a == b


def gap_area(scene: dict, gap_id: str, margin: float) -> dict:
    """The gap's XZ footprint (between its two platforms' facing edges, across the wider
    platform) grown by `margin` studs on every side. Positions printed by the server lag the
    client by about 2 to 3.5 studs (ADR 0004 finding 2), so the margin must exceed that."""
    by_id = {e["id"]: e for e in scene["entities"]}
    gap = by_id[gap_id]
    src, dst = by_id[gap["from"]], by_id[gap["to"]]
    i = 0 if gap["axis"][1] == "X" else 2
    j = 2 - i
    sign = 1 if gap["axis"][0] == "+" else -1
    a = src["pos"][i] + sign * src["size"][i] / 2
    b = dst["pos"][i] - sign * dst["size"][i] / 2
    lo_i, hi_i = min(a, b), max(a, b)
    lo_j = min(p["pos"][j] - p["size"][j] / 2 for p in (src, dst))
    hi_j = max(p["pos"][j] + p["size"][j] / 2 for p in (src, dst))
    box = {"x": None, "z": None, "margin": margin, "gap": gap_id}
    box["x" if i == 0 else "z"] = [lo_i - margin, hi_i + margin]
    box["z" if i == 0 else "x"] = [lo_j - margin, hi_j + margin]
    return box


def spawn_centre(scene: dict) -> tuple[float, float]:
    """XZ centre of the scene's spawn pad (scene frame, as the controller's `start_pos`)."""
    by_id = {e["id"]: e for e in scene["entities"]}
    spawns = [e for e in scene["entities"] if e.get("type") == "Spawn"]
    if len(spawns) != 1:
        raise ValueError(f"the scene has {len(spawns)} spawns; the start_pos rule needs exactly one")
    part = _geo.entity_parts(spawns[0], by_id)[0]
    return (float(part["pos"][0]), float(part["pos"][2]))


def _xz(text):
    if not isinstance(text, str) or text == "na":
        return None
    try:
        v = [float(c) for c in text.split(",")]
    except ValueError:
        return None
    return (v[0], v[2]) if len(v) == 3 and all(math.isfinite(c) for c in v) else None


def in_area(pos, area: dict) -> bool:
    if pos is None:
        return False
    x, _, z = pos
    return area["x"][0] <= x <= area["x"][1] and area["z"][0] <= z <= area["z"][1]


def _ev_dict(e: Event) -> dict:
    return {"event": e.event, "id": e.id, "pos": list(e.pos) if e.pos else None, "t_server": e.t}


def classify_attempt(tool_result, nonce: str, expected_params: dict, area: dict,
                     *, call_error: str | None = None, spawn_xz=None) -> dict:
    """The canonical outcome of one attempt (a dict with `outcome`, `tags`, `cause`, ...).
    `spawn_xz` (the scene's spawn centre) turns on the ADR 0011 start_pos rule."""
    out = {"outcome": "UNCLASSIFIED", "tags": [], "cause": "UNCLASSIFIED", "detail": "",
           "events": [], "events_agree": None, "fell_in_area": None, "report": None,
           "tool": {"duration_s": None, "is_timeout": None, "error": None}}

    def done(outcome, detail="", tags=()):
        out["outcome"] = outcome
        out["tags"] = [outcome, *tags]
        out["detail"] = detail
        return out

    if call_error is not None:
        return done("CONNECTOR_ERROR", call_error)
    if not isinstance(tool_result, dict) or not isinstance(tool_result.get("logs"), list):
        return done("CONNECTOR_ERROR", "tool result is not a run_script_in_play_mode result")
    out["tool"] = {"duration_s": tool_result.get("duration"), "is_timeout": tool_result.get("isTimeout"),
                   "error": tool_result.get("error")}
    if tool_result.get("isTimeout") is True:
        return done("CONNECTOR_ERROR", "the play tool hit its own timeout (the server deadline should fire first)")

    text = log_text(tool_result)
    events = parse_events(text, nonce)
    out["events"] = [_ev_dict(e) for e in events]
    try:
        report = parse_report(text, nonce)
    except ResultError as e:
        return done("HARNESS_ERROR", f"{e}; tool error: {tool_result.get('error')!r}")
    out["report"] = {k: report.get(k) for k in ("status", "detail", "died", "removed", "characters_added",
                                                 "t_died", "pos_died", "jumps",
                                                 "ticks", "t_end", "t_route_start", "start_pos",
                                                 "last_supported", "scene_hash", "waypoints",
                                                 "events_dropped")}
    for k in ("samples", "samples_dropped",       # p0.v1 position samples (WS-11); absent in p0.v0
              "spawn_pos", "spawn_offset", "settle_drift", "settle_max_offset"):   # p0.v2 (ADR 0011)
        if k in report:
            out["report"][k] = report[k]
    if not _same(report.get("params"), expected_params):
        return done("HARNESS_ERROR", "controller params echo does not match the frozen route/timing")
    captured = parse_events("\n".join(x for x in report.get("events") or [] if isinstance(x, str)), nonce)
    out["events_agree"] = [tuple(e) for e in captured] == [tuple(e) for e in events]
    if report.get("status") == "harness_error":
        return done("HARNESS_ERROR", str(report.get("detail")))
    start = _xz(report.get("start_pos"))
    if spawn_xz is not None and start is not None:
        off = math.hypot(start[0] - spawn_xz[0], start[1] - spawn_xz[1])
        if off > START_POS_MAX_STUDS:
            return done("HARNESS_ERROR", f"moved_before_route: start_pos {report.get('start_pos')} is {off:.2f} studs "
                                         f"(XZ) from the spawn centre {list(spawn_xz)} (> {START_POS_MAX_STUDS}); "
                                         "the attempt says nothing about the scene")
    if report.get("jumps") != 0:
        return done("HARNESS_ERROR", f"P0 contract broken: {report.get('jumps')!r} jumps")
    if tool_result.get("error") not in (None, "", "nil"):
        return done("HARNESS_ERROR", f"the play tool reported a script error: {tool_result.get('error')!r}")

    names = [e.event for e in events]

    def first(name):
        return names.index(name) if name in names else None

    kill, goal, bridge = first("KILL_FLOOR_TOUCHED"), first("GOAL_TOUCHED"), first("BRIDGE_CROSSED")
    if kill is not None and (goal is None or kill < goal):
        ev = events[kill]
        out["fell_in_area"] = in_area(ev.pos, area)
        where = "in" if out["fell_in_area"] else "outside"
        return done("FELL", f"KILL_FLOOR_TOUCHED at {list(ev.pos) if ev.pos else 'na'}, {where} the {area['gap']} area")
    if report.get("removed") is True and goal is None:
        return done("HARNESS_ERROR", "the character was removed without dying (replaced or respawned)")
    if report.get("died") is True and kill is None:
        return done("UNCLASSIFIED", "the character died without a KILL_FLOOR_TOUCHED event"
                    + ("; a GOAL_TOUCHED line does not make it a SUCCESS" if goal is not None else ""))
    if goal is not None:
        if bridge is not None and bridge < goal and events[bridge].t <= events[goal].t:
            return done("SUCCESS", "BRIDGE_CROSSED then GOAL_TOUCHED")
        return done("UNCLASSIFIED", "GOAL_TOUCHED without BRIDGE_CROSSED before it")
    if report.get("status") == "deadline":
        extra = []
        if "LEVER_ACTIVATED" not in names:
            extra.append("LEVER_NOT_ACTIVATED")
        elif "GATE_OPENED" not in names:
            extra.append("GATE_NOT_OPENED")
        return done("TIMEOUT", f"T_attempt reached ({report.get('detail')})", extra)
    return done("UNCLASSIFIED", f"controller status {report.get('status')!r}, no goal event")
