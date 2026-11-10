"""The dry run (WS-11, ADR 0010): the full experiment loop with a simulated Studio operator and
fixture architects, no network, no key, no Studio. It exists to exercise the orchestrator end to
end (relay files, resume, caps, bundles) and to produce a sample bundle for WS-12's viewer.

NOTHING HERE IS A RESULT. `FakeOperator` answers each relay request the way the Studio MCP
answers in shape only: a build prints its `WS_BUILD_OK` line, a read-back returns the expected
tree, and a play call returns a controller report whose outcome is decided by a stand-in rule
(the bridge reaches the far platform -> BRIDGE_CROSSED then GOAL_TOUCHED; otherwise a fall into
the gap at the kill positions recorded in the M0 F1-A attempts), and a preflight probe answers
as the saved worldsmith-lab place would (`preflight.simulated_probe`). It is not physics and not
Roblox. Architect replies come from the hand-written WS-10 fixtures named in the config's
`dry_run.fixtures` (not model outputs). Every bundle it writes says `dry_run: true`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from worldsmith.architect import client as acl
from worldsmith.compiler import geometry
from worldsmith.grammar import scene as sc
from worldsmith.runner import attempts as at
from worldsmith.runner import episode as epi
from worldsmith.runner import experiment as ex
from worldsmith.runner import preflight, taxonomy

# The KILL_FLOOR_TOUCHED positions of the three M0 F1-A attempts (bundles/m0/F1-A, docs/M0.md).
M0_FALLS = ("0.00,-14.29,49.31", "0.00,-11.06,48.52", "0.00,-10.27,48.35")
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
        """Hook for tests: force "SUCCESS" or "FELL" for attempt i of the current version."""
        return None

    def play(self, scene: dict, nonce: str, i: int, outcome: str | None = None) -> dict:
        ok = bridge_reaches(scene) if outcome is None else outcome == "SUCCESS"
        if ok:
            events = [("LEVER_ACTIVATED", "lever_1", "-8.74,4.34,24.00"), ("GATE_OPENED", "gate_1", "na"),
                      ("BRIDGE_CROSSED", "trig_bridge", "0.00,3.00,49.12"), ("GOAL_TOUCHED", "trig_goal", "0.00,3.00,55.94")]
            t_end, died, status = 8.25, False, "route_done"
        else:
            events = [("LEVER_ACTIVATED", "lever_1", "-8.06,4.34,23.91"), ("GATE_OPENED", "gate_1", "na"),
                      ("KILL_FLOOR_TOUCHED", "kill_floor", M0_FALLS[(i - 1) % 3])]
            t_end, died, status = 9.25, True, "died"
        lines = [f"WS|{nonce}|{e}|id={eid}|pos={pos}|t={1794237655.0 + k:.3f}" for k, (e, eid, pos) in enumerate(events)]
        samples = [{"t": round(1.0 + 0.95 * j, 3), "pos": f"0.00,3.00,{10 + 5 * j:.2f}"} for j in range(1, 9)
                   if 1.0 + 0.95 * j < (8.4 if died else t_end)]
        report = {"nonce": nonce, "params": self.cfg["_params"], "status": status, "detail": SIM_NOTE,
                  "events": lines, "waypoints": [], "died": died, "removed": False, "characters_added": 1, "jumps": 0,
                  "ticks": 160, "scene_hash": sc.canonical_hash(scene), "t_died": 8.4 if died else -1,
                  "pos_died": M0_FALLS[(i - 1) % 3] if died else "na", "last_supported": "0.00,3.00,41.70",
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
