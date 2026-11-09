"""Synthetic run_script_in_play_mode results for the WS-08 classifier tests.

Unlike `_m0fake.play_result`, these carry a full controller report: waypoint position samples
(controller clock), start position, death time and position, so the STUCK rule can be tested.
Every result is shaped like the recorded M0 answers in `bundles/m0/*/attempts/*.raw.txt`.
"""

import json

from _m0fake import EV_IDS, ev_line

T0 = 1794239719.0


def wp(i, t, pos, *, died=False, reached=True, name=None):
    """One waypoint sample; pos is (x, y, z) or 'na'."""
    p = pos if isinstance(pos, str) else ",".join(f"{c:.2f}" for c in pos)
    return {"i": i, "name": name or f"wp{i}", "reached": reached, "died": died, "t": t, "pos": p}


def result(nonce, params, events=(), *, status="route_done", died=False, removed=False, jumps=0,
           waypoints=(), start_pos="0.00,3.20,6.00", t_route_start=1.6, t_end=9.5, t_died=-1,
           pos_died="na", error="nil", is_timeout=False, extra_logs=(), captured=None,
           event_nonce=None, chunk=800):
    """events: [(EVENT, "x,y,z" | "na")] or [(EVENT, pos, t_server)] in console order."""
    lines = []
    for k, ev in enumerate(events):
        name, pos = ev[0], ev[1]
        t = ev[2] if len(ev) > 2 else T0 + k
        lines.append(ev_line(event_nonce or nonce, name, EV_IDS[name], pos, t))
    report = {"nonce": nonce, "params": params, "status": status, "detail": "",
              "events": list(lines if captured is None else captured), "waypoints": list(waypoints),
              "died": died, "removed": removed, "characters_added": 1, "jumps": jumps, "ticks": 140,
              "scene_hash": "x", "t_died": t_died, "pos_died": pos_died, "last_supported": "na",
              "start_pos": start_pos, "t_route_start": t_route_start, "events_dropped": 0,
              "t_end": t_end}
    s = json.dumps(report)
    n = max(1, -(-len(s) // chunk))
    msgs = ["[UI Workflow] Plugin loaded!", *extra_logs, *lines]
    msgs += [f"WSP|{nonce}|RESULT|{i + 1}|{n}|{s[i * chunk:(i + 1) * chunk]}" for i in range(n)]
    return {"logs": [{"message": m, "ts": 1.0, "level": "output"} for m in msgs],
            "errors": [{"message": "[robloxstudio-mcp] /ready failed", "ts": 1.0, "level": "warning"}],
            "duration": t_end, "error": error, "isTimeout": is_timeout}


def walking(n=8, t0=1.6, dt=0.7, z0=6.0, dz=4.0):
    """Waypoint samples of a character walking +Z (dense, moving)."""
    return [wp(i, t0 + i * dt, (0.0, 3.0, z0 + i * dz)) for i in range(1, n + 1)]


def standing(t_from, t_to, dt, pos=(0.0, 3.0, 30.0), start_i=1):
    """Samples of a character that does not move, every dt seconds from t_from to t_to."""
    out, i, t = [], start_i, t_from
    while t <= t_to + 1e-9:
        out.append(wp(i, round(t, 3), pos, reached=False))
        i, t = i + 1, t + dt
    return out
