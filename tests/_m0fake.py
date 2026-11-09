"""Helpers for the WS-04 tests: synthetic run_script_in_play_mode results and a fake Studio."""

import json

from worldsmith.compiler import build, geometry
from worldsmith.grammar.scene import canonical_hash, load_scene
from worldsmith.runner import p0
from worldsmith.runner.attempts import CallResult, CallTimeout

ORIGIN = (0, 100, 0)


def ev_line(nonce, event, id_, pos="0.00,4.00,30.00", t=1794237655.0):
    return f"WS|{nonce}|{event}|id={id_}|pos={pos}|t={t:.3f}"


EV_IDS = {"LEVER_ACTIVATED": "lever_1", "GATE_OPENED": "gate_1", "BRIDGE_CROSSED": "trig_bridge",
          "KILL_FLOOR_TOUCHED": "kill_floor", "GOAL_TOUCHED": "trig_goal"}


def play_result(nonce, params, events=(), *, status="route_done", died=False, jumps=0, removed=False,
                captured=None, chunk=50, extra_logs=(), report_nonce=None, params_override=None,
                drop_report=False, is_timeout=False):
    """events: [(EVENT, "x,y,z" or "na")] in order."""
    lines = []
    for k, (name, pos) in enumerate(events):
        lines.append(ev_line(nonce, name, EV_IDS[name], pos, 1794237655.0 + k))
    report = {"nonce": report_nonce or nonce, "params": params_override or params, "status": status,
              "detail": "", "events": list(lines if captured is None else captured), "waypoints": [],
              "died": died, "removed": removed, "characters_added": 1, "jumps": jumps, "ticks": 120, "scene_hash": "x", "t_died": -1,
              "pos_died": "na", "last_supported": "na", "start_pos": "na", "t_route_start": 1.0,
              "events_dropped": 0, "t_end": 9.5}
    s = json.dumps(report)
    n = max(1, -(-len(s) // chunk))
    rnonce = report_nonce or nonce
    out_lines = ["[UI Workflow] Plugin loaded!", *extra_logs, *lines]
    if not drop_report:
        out_lines += [f"WSP|{rnonce}|RESULT|{i + 1}|{n}|{s[i * chunk:(i + 1) * chunk]}" for i in range(n)]
    return {"logs": [{"message": m, "ts": 1.0, "level": "output"} for m in out_lines],
            "errors": [], "duration": 10.0, "error": "nil", "isTimeout": is_timeout}


FELL_EVENTS = [("LEVER_ACTIVATED", "-8.06,4.34,23.91"), ("GATE_OPENED", "na"),
               ("KILL_FLOOR_TOUCHED", "0.00,-12.44,49.20")]
SUCCESS_EVENTS = [("LEVER_ACTIVATED", "-8.74,4.34,24.00"), ("GATE_OPENED", "na"),
                  ("BRIDGE_CROSSED", "0.00,4.34,50.43"), ("GOAL_TOUCHED", "0.00,4.34,56.12")]


def readback_text(scene, origin=ORIGIN):
    """A read-back output that matches the expected tree exactly."""
    tree = geometry.expected_tree(scene, origin=origin)
    s = json.dumps(tree)
    return "\n".join(f"WS_READBACK|{i + 1}|{-(-len(s) // 500)}|{s[i * 500:(i + 1) * 500]}"
                     for i in range(-(-len(s) // 500)))


class FakeStudio:
    """Answers calls like the Studio MCP. `plays` maps attempt number -> tool result (dict),
    a CallTimeout instance, or a raw string."""

    def __init__(self, scene, nonces, params, plays, *, build_ok=True, readback=None, mode="stop"):
        self.scene, self.nonces, self.params, self.plays = scene, nonces, params, plays
        self.build_ok, self.readback, self.mode = build_ok, readback, mode
        self.log = []
        self.play_count = 0

    def call(self, tool, args, timeout_s):
        self.log.append((tool, timeout_s, args.get("timeout")))
        if tool == "get_studio_mode":
            return CallResult(text=self.mode)
        if tool == "start_stop_play":
            self.mode = "stop"
            return CallResult(text="ok")
        if tool == "run_code":
            code = args["command"]
            if "WS_BUILD_OK" in code:
                n = geometry.descendant_count(geometry.expected_tree(self.scene, origin=ORIGIN))
                h = canonical_hash(self.scene)
                return CallResult(text=f"[OUTPUT] WS_BUILD_OK|{h}|{n}" if self.build_ok else "error")
            return CallResult(text=self.readback if self.readback is not None else readback_text(self.scene))
        if tool == "run_script_in_play_mode":
            self.play_count += 1
            nonce = self.nonces[self.play_count - 1]
            assert f'local NONCE = "{nonce}"' in args["code"]
            r = self.plays[self.play_count]
            if isinstance(r, CallTimeout):
                raise r
            return CallResult(text=r if isinstance(r, str) else json.dumps(r))
        raise AssertionError(tool)
