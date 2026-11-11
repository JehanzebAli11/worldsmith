"""The dry run (WS-11, ADR 0010): the full experiment loop with a simulated Studio operator and
fixture architects, no network, no key, no Studio. It exists to exercise the orchestrator end to
end (relay files, resume, caps, bundles) and to produce a sample bundle for WS-12's viewer.

NOTHING HERE IS A RESULT. `FakeOperator` answers each relay request the way the Studio MCP
answers in shape only: a build prints its `WS_BUILD_OK` line, a read-back returns the expected
tree, and a play call returns a controller report whose outcome is decided by stand-in rules on
the scene's geometry (`walk` and `stand_in_outcome`), and a preflight probe answers as the saved
worldsmith-lab place would (`preflight.simulated_probe`). It is not physics and not Roblox.

The stand-in rules (straight legs between the frozen route's waypoints, a character of radius
PLAYER_RADIUS; walls, and the gate until the lever is touched, stop a leg where they start):
- the lever is activated when a walked leg touches its zone (`compiler.geometry.LEVER_ZONE`); a
  wall in the way of the lever leg stops the character short of it (WS-14 live gate, F1-C/F1-D);
- a character that never reaches the gap (stopped by the shut gate, or by a wall) is STUCK, with
  no LEVER_ACTIVATED if the lever was never touched (the gate's recorded F1-C/F1-D outcome);
- on the gap, a route point beside the bridge deck falls at the kill positions recorded in the
  live gate_f1b attempts (F1-B: a full-length bridge off the path); a bridge that does not reach
  the far platform falls at the kill positions recorded in the M0 F1-A attempts; otherwise
  BRIDGE_CROSSED then GOAL_TOUCHED.
On F1-A and F1-E (and any scene whose lever is reachable and whose bridge is under the path) the
rules give exactly what the earlier bridge-length-only rule gave, byte for byte.

Architect replies come from the hand-written fixtures named in the config's `dry_run.fixtures`
(WS-10's F1-A M0 replies, WS-14's M1 replies in `architect/fixtures/m1/`; not model outputs).
Every bundle it writes says `dry_run: true`. A dry run rehearses the plumbing; it is never a
result (docs/EXPERIMENTS.md).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from worldsmith.architect import client as acl
from worldsmith.compiler import geometry
from worldsmith.grammar import scene as sc
from worldsmith.runner import attempts as at
from worldsmith.runner import classify
from worldsmith.runner import episode as epi
from worldsmith.runner import experiment as ex
from worldsmith.runner import preflight, taxonomy

# The KILL_FLOOR_TOUCHED positions of the three M0 F1-A attempts (bundles/m0/F1-A, docs/M0.md).
M0_FALLS = ("0.00,-14.29,49.31", "0.00,-11.06,48.52", "0.00,-10.27,48.35")
# The KILL_FLOOR_TOUCHED positions of the three live gate_f1b attempts (F1-B, bridge 10 studs
# beside the path; bundles/experiments/gate_f1b/B0/ep0/v1/attempts/{1,2,3}.json, pos_died).
GATE_F1B_FALLS = ("-0.00,-15.05,39.44", "-0.00,-11.30,39.45", "0.00,-10.04,39.53")
# Stand-in character for `walk` (not measured for this purpose): a radius that puts the stops where
# the live gate recorded them (gate_f1c/gate_f1d: lever_zone x -10.44..-10.48 against wall_ledge's
# face at x -11; past_gate z 32.45..32.48 against the shut gate's face at z 33), and the M0
# character's height (5.19 studs, bundles/m0/character/measure.json, ADR 0007).
PLAYER_RADIUS = 0.5
PLAYER_HEIGHT = 5.19
# A STUCK play: the controller gives up on each remaining waypoint and reports route_done; the live
# STUCK plays ended 47.09..47.36 s after the script start (gate_f1c, gate_f1d; n = 6).
STUCK_T_END = 47.25
_BACK_OFF = 0.01   # studs: a stopped character stands just outside what stopped it
_HASH_RE = re.compile(r'local WS_HASH = "([0-9a-f]{64})"')
_NONCE_RE = re.compile(r'local NONCE = "([0-9a-f]{8,32})"')
SIM_NOTE = "simulated by worldsmith.runner.dryrun.FakeOperator (dry run; not Roblox)"


def readback_text(scene: dict, origin) -> str:
    tree = geometry.expected_tree(scene, origin=tuple(origin))
    s = json.dumps(tree)
    n = -(-len(s) // 500)
    return "\n".join(f"WS_READBACK|{i + 1}|{n}|{s[i * 500:(i + 1) * 500]}" for i in range(n))


def bridge_reaches(scene: dict) -> bool:
    g = taxonomy.geometry_check(scene, "gap_1")
    return g["uncovered_studs"] is not None and g["uncovered_studs"] <= taxonomy.EPS


# --- stand-in walking rules (module doc; shape, not physics) ---------------------------------

def _parts(scene: dict) -> dict:
    """Boxes from the compiler's own geometry: walls, the gate, the lever zone, the bridge deck,
    and the gap's span along its axis."""
    norm = sc.normalize_scene(scene)
    by_id = {e["id"]: e for e in norm["entities"]}
    out = {"walls": [], "gate": None, "zone": None, "deck": None, "gap": None}
    for e in norm["entities"]:
        t = e["type"]
        if t == "Wall":
            out["walls"] += geometry.entity_parts(e, by_id)
        elif t == "Gate" and out["gate"] is None:
            out["gate"] = geometry.entity_parts(e, by_id)[0]
        elif t == "Lever" and out["zone"] is None:
            out["zone"] = next(b for b in geometry.entity_parts(e, by_id) if b["name"] == "zone")
        elif t == "Bridge" and out["deck"] is None:
            out["deck"] = geometry.entity_parts(e, by_id)[0]
        elif t == "Gap" and out["gap"] is None:
            src, dst = by_id[e["from"]], by_id[e["to"]]
            i, _, sign = geometry._gap_frame(e)
            a = src["pos"][i] + sign * src["size"][i] / 2
            b = dst["pos"][i] - sign * dst["size"][i] / 2
            out["gap"] = (i, min(a, b), max(a, b))
    return out


def _in_band(box: dict, feet: float) -> bool:
    lo, hi = box["pos"][1] - box["size"][1] / 2, box["pos"][1] + box["size"][1] / 2
    return lo < feet + PLAYER_HEIGHT and hi > feet


def _enter(p, q, box: dict, grow: float):
    """The fraction of the XZ segment p -> q at which it first meets `box`'s footprint grown by
    `grow` on every side; None if it never does (slab method)."""
    t0, t1 = 0.0, 1.0
    for k, axis in ((0, 0), (1, 2)):
        lo = box["pos"][axis] - box["size"][axis] / 2 - grow
        hi = box["pos"][axis] + box["size"][axis] / 2 + grow
        d = q[k] - p[k]
        if abs(d) < 1e-12:
            if p[k] < lo or p[k] > hi:
                return None
            continue
        a, b = (lo - p[k]) / d, (hi - p[k]) / d
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return None
    return t0


def walk(scene: dict, waypoints) -> dict:
    """The stand-in walk of the frozen route from the spawn centre: straight legs; a leg stops
    where a wall (or the gate, while the lever is untouched) first meets the character; touching
    the lever's zone opens the gate for the later legs. Returns {lever, legs, end}: legs is a
    list of (from, to, reached) in XZ."""
    pt = _parts(scene)
    p = classify.spawn_centre(scene)
    lever, legs = False, []
    for w in waypoints:
        feet = w["pos"][1]
        q = (w["pos"][0], w["pos"][2])
        blockers = [b for b in pt["walls"] if _in_band(b, feet)]
        if not lever and pt["gate"] is not None and _in_band(pt["gate"], feet):
            blockers.append(pt["gate"])
        hits = [t for t in (_enter(p, q, b, PLAYER_RADIUS) for b in blockers) if t is not None]
        if hits:
            length = math.dist(p, q)
            t = max(0.0, min(hits) - (_BACK_OFF / length if length else 0.0))
            end = (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
        else:
            end = q
        z = pt["zone"]
        if not lever and z is not None and _in_band(z, feet) and _enter(p, end, z, PLAYER_RADIUS) is not None:
            lever = True
        legs.append((p, end, not hits))
        p = end
    return {"lever": lever, "legs": legs, "end": p}


def _on_gap(pt: dict, legs, step: float = 0.25):
    """Points of the walked legs strictly inside the gap's span along its axis, in walking order."""
    i, lo, hi = pt["gap"]
    k = 0 if i == 0 else 1
    pts = []
    for a, b, _ in legs:
        n = max(1, int(math.dist(a, b) / step))
        for j in range(n + 1):
            x = (a[0] + (b[0] - a[0]) * j / n, a[1] + (b[1] - a[1]) * j / n)
            if lo < x[k] < hi:
                pts.append(x)
    return pts


def _over(box: dict, x) -> bool:
    return (abs(x[0] - box["pos"][0]) <= box["size"][0] / 2 + 1e-9
            and abs(x[1] - box["pos"][2]) <= box["size"][2] / 2 + 1e-9)


def stand_in_outcome(scene: dict, waypoints) -> tuple[str, str | None, bool]:
    """(outcome, fall kind, lever activated): SUCCESS, FELL ("beside" the deck or "short" of the
    far platform) or STUCK, by the module doc's rules."""
    pt = _parts(scene)
    w = walk(scene, waypoints)
    on_gap = _on_gap(pt, w["legs"]) if pt["gap"] else []
    if not on_gap:
        return "STUCK", None, w["lever"]
    deck = pt["deck"]
    i = pt["gap"][0]
    k = 0 if i == 0 else 1
    for x in on_gap:            # a point alongside the deck's length but not over it: off the bridge
        if deck is None or (abs(x[k] - deck["pos"][i]) <= deck["size"][i] / 2 and not _over(deck, x)):
            return "FELL", "beside", w["lever"]
    if not bridge_reaches(scene):
        return "FELL", "short", w["lever"]
    if not w["legs"][-1][2]:
        return "STUCK", None, w["lever"]
    return "SUCCESS", None, w["lever"]


class FakeOperator:
    """Answers relay requests for one experiment (see the module doc: shape only, not physics)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.built: dict[str, dict] = {}     # episode dir -> scene last built there
        self.plays: dict[str, int] = {}      # episode dir -> plays since the last build

    def _scene_by_hash(self, ep_dir: Path, h: str) -> dict:
        for f in sorted(ep_dir.glob("v*/scene.json")):
            s = sc.load_scene(f)
            if sc.canonical_hash(s) == h:
                return s
        raise LookupError(f"no v*/scene.json with hash {h[:12]}… in {ep_dir}")

    def answer(self, ep_dir: Path, request: dict) -> str:
        """The raw answer to one stored relay request."""
        code = None
        if "code_file" in request:
            code = (Path(ep_dir) / "calls" / request["code_file"]).read_text(encoding="utf-8")
        return self.answer_code(Path(ep_dir), request["tool"], code)

    def answer_code(self, ep_dir: Path, tool: str, code: str | None) -> str:
        key = str(ep_dir)
        if tool == "get_studio_mode":
            return "stop"
        if tool == "start_stop_play":
            return "stopped"
        if tool == "run_code" and "WS_PREFLIGHT" in code:
            return "[OUTPUT] WS_PREFLIGHT|" + json.dumps(preflight.simulated_probe())
        if tool == "run_code" and "WS_BUILD_OK" in code:
            h = _HASH_RE.search(code).group(1)
            scene = self._scene_by_hash(ep_dir, h)
            self.built[key], self.plays[key] = scene, 0
            n = geometry.descendant_count(geometry.expected_tree(scene, origin=tuple(self.cfg["origin"])))
            return f"[OUTPUT] WS_BUILD_OK|{h}|{n}"
        if tool == "run_code":
            return readback_text(self.built[key], self.cfg["origin"])
        if tool == "run_script_in_play_mode":
            nonce = _NONCE_RE.search(code).group(1)
            self.plays[key] += 1
            i = self.plays[key]
            return json.dumps(self.play(self.built[key], nonce, i, self.outcome_for(ep_dir, i)))
        raise ValueError(f"unexpected tool {tool}")

    def outcome_for(self, ep_dir: Path, i: int) -> str | None:
        """Hook for tests: force "SUCCESS", "FELL" or "STUCK" for attempt i of the current version
        (a forced SUCCESS or FELL is the earlier stand-in report: lever touched, F1-A's M0 falls)."""
        return None

    def play(self, scene: dict, nonce: str, i: int, outcome: str | None = None) -> dict:
        if outcome is None:
            outcome, fall, lever = stand_in_outcome(scene, self.cfg["_params"]["waypoints"])
            stuck_at = walk(scene, self.cfg["_params"]["waypoints"])["end"] if outcome == "STUCK" else None
        else:
            fall, lever = "short", True
            stuck_at = (0.0, 32.45) if outcome == "STUCK" else None
        lever_events = [("LEVER_ACTIVATED", "lever_1", "-8.74,4.34,24.00" if outcome == "SUCCESS" else "-8.06,4.34,23.91"),
                        ("GATE_OPENED", "gate_1", "na")] if lever else []
        falls = GATE_F1B_FALLS if fall == "beside" else M0_FALLS
        if outcome == "SUCCESS":
            events = lever_events + [("BRIDGE_CROSSED", "trig_bridge", "0.00,3.00,49.12"),
                                     ("GOAL_TOUCHED", "trig_goal", "0.00,3.00,55.94")]
            t_end, died, status = 8.25, False, "route_done"
        elif outcome == "FELL":
            events = lever_events + [("KILL_FLOOR_TOUCHED", "kill_floor", falls[(i - 1) % 3])]
            t_end, died, status = 9.25, True, "died"
        elif outcome == "STUCK":
            events = lever_events
            t_end, died, status = STUCK_T_END, False, "route_done"
        else:
            raise ValueError(f"unknown stand-in outcome {outcome!r}")
        lines = [f"WS|{nonce}|{e}|id={eid}|pos={pos}|t={1794237655.0 + k:.3f}" for k, (e, eid, pos) in enumerate(events)]
        if stuck_at is None:
            samples = [{"t": round(1.0 + 0.95 * j, 3), "pos": f"0.00,3.00,{10 + 5 * j:.2f}"} for j in range(1, 9)
                       if 1.0 + 0.95 * j < (8.4 if died else t_end)]
            last = "0.00,3.00,36.27" if fall == "beside" and died else "0.00,3.00,41.70"
        else:
            # dense samples (every 0.95 s): a straight walk to where the character stopped by 13 s,
            # then standing there to the end (shape only; STUCK by the classifier's own rule)
            x1, z1 = stuck_at
            samples = []
            for j in range(1, 49):
                t = 1.0 + 0.95 * j
                f = min(1.0, (t - 1.0) / 12.0)
                samples.append({"t": round(t, 3), "pos": f"{x1 * f:.2f},3.00,{6 + (z1 - 6) * f:.2f}"})
            last = f"{x1:.2f},3.00,{z1:.2f}"
        report = {"nonce": nonce, "params": self.cfg["_params"], "status": status, "detail": SIM_NOTE,
                  "events": lines, "waypoints": [], "died": died, "removed": False, "characters_added": 1, "jumps": 0,
                  "ticks": 160, "scene_hash": sc.canonical_hash(scene), "t_died": 8.4 if died else -1,
                  "pos_died": falls[(i - 1) % 3] if died else "na", "last_supported": last,
                  "start_pos": "0.00,3.00,6.00", "t_route_start": 1.0, "events_dropped": 0, "t_end": t_end,
                  "samples": samples, "samples_dropped": 0}
        if self.cfg["player"]["version"] in ("p0.v2", "p0.v3"):
            report.update(spawn_pos="0.00,0.50,6.00", spawn_offset=0, settle_drift=0, settle_max_offset=0)
        if self.cfg["player"]["version"] == "p0.v3":       # simulated values (shape only)
            report.update(warm_frames=10, warm_wall_s=0.17, max_dt_warm=0.017, warm_dts=[0.017] * 10,
                          ground_steps=0, ground_test="floor", ray_dist=-1, settle_steps=30, ground_test_end="floor")
        s = json.dumps(report)
        n = -(-len(s) // 800)
        out = ["[UI Workflow] dry-run operator", *lines] + \
              [f"WSP|{nonce}|RESULT|{j + 1}|{n}|{s[j * 800:(j + 1) * 800]}" for j in range(n)]
        return {"logs": [{"message": m, "ts": 1.0, "level": "output"} for m in out], "errors": [],
                "duration": t_end, "error": "nil", "isTimeout": False}


def fixture_architects(cfg: dict):
    """architect_for(condition) -> factory of the role's fixture architect."""
    def architect_for(cond: dict):
        return acl.FixtureArchitect.from_names(cfg["dry_run"]["fixtures"][cond["role"]])
    return architect_for


def run_dry(cfg: dict, out_root, *, max_steps: int = 5000, on_step=None, two_machine: bool = False,
            on_handoff=None) -> dict:
    """The whole experiment through the relay files, the fake operator answering each pending
    call, re-running (= resuming) after every answer. Returns results.json's content.

    two_machine: the "VM" steps with architect_mode defer; at each pending architect call a
    separate "Mac" run (architect_mode one, expecting that request) makes exactly that call and
    stores the reply; then the VM steps on (ADR 0010 section 9)."""
    exp = ex.Experiment(cfg, out_root, dry_run=True)
    op = FakeOperator(cfg)
    mode = "defer" if two_machine else "call"
    for _ in range(max_steps):
        try:
            return exp.run(lambda d: at.RelayClient(d / "calls"), fixture_architects(cfg), architect_mode=mode)
        except at.Pending as p:
            ep_dir = exp.dir / exp.pending()["episode_dir"]
            at.respond(ep_dir / "calls", p.index, op.answer(ep_dir, p.request))
            if on_step:
                on_step(ep_dir, p)
        except epi.ArchitectPending as p:
            mac = ex.Experiment(cfg, out_root, dry_run=True)
            try:
                mac.run(lambda d: at.RelayClient(d / "calls"), fixture_architects(cfg), architect_mode="one",
                        architect_expect=p.request_sha)
            except epi.ArchitectCallDone:
                pass
            else:
                raise RuntimeError("the Mac run made no architect call")
            if on_handoff:
                on_handoff(p.request_sha)
    raise RuntimeError(f"dry run did not finish in {max_steps} steps")
