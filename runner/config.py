"""The M0 run configuration (config/m0.yaml), declared and committed before any comparison run.

`load_config` checks every key, re-derives the route and controller hashes and the scene
hashes, and refuses the file if anything it declares does not match the repository. The
config hash is the SHA-256 of the file's bytes; it is logged with every attempt.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import yaml

from worldsmith.grammar.scene import canonical_hash, load_scene
from worldsmith.runner import p0

CONFIG_SCHEMA = "worldsmith.m0config/0.1"
OUTCOMES = ("SUCCESS", "FELL")
TOOLS = ("get_studio_mode", "run_code", "run_script_in_play_mode", "start_stop_play")
_TOP = {"schema", "timing", "n_attempts", "pass_k", "origin", "gap_area_margin_studs", "player",
        "route", "controller", "tool_timeouts_s", "scenes", "gate"}


class ConfigError(ValueError):
    """config/m0.yaml is malformed or does not match the repository."""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load_config(path, repo_root=None) -> dict:
    """The parsed config plus `_sha256`, `_path`, `_route`, `_controller_text` (derived)."""
    path = Path(path)
    root = Path(repo_root) if repo_root else path.resolve().parent.parent
    raw = path.read_bytes()
    cfg = yaml.safe_load(raw.decode("utf-8"))
    errs = []
    if not isinstance(cfg, dict):
        raise ConfigError("config is not a mapping")
    for k in sorted(set(cfg) ^ _TOP):
        errs.append(f"{'unknown' if k in cfg else 'missing'} key {k!r}")
    if errs:
        raise ConfigError("; ".join(errs))
    if cfg["schema"] != CONFIG_SCHEMA:
        errs.append(f"schema must be {CONFIG_SCHEMA}")
    errs += p0.validate_timing(cfg["timing"] if isinstance(cfg["timing"], dict) else {})
    n, k = cfg["n_attempts"], cfg["pass_k"]
    if not (n == 3 and k == 3 and isinstance(n, int) and isinstance(k, int)):
        errs.append("SPEC section 9 pre-declares n_attempts = 3 and pass_k = 3; they are never changed")
    if not (isinstance(cfg["origin"], list) and len(cfg["origin"]) == 3 and all(_num(c) for c in cfg["origin"])):
        errs.append("origin must be 3 numbers")
    m = cfg["gap_area_margin_studs"]
    if not (_num(m) and 0 <= m <= 8):
        errs.append("gap_area_margin_studs must be a number in [0, 8]")
    player = cfg["player"]
    if not (isinstance(player, dict) and player.get("tier") == "P0"
            and set(player) == {"tier", "driver", "claim"}
            and "never jumps" in str(player.get("driver"))
            and "Not an Autopilot or agent result" in str(player.get("claim"))):
        errs.append("player must be tier P0 with the driver (never jumps) and the claim limit")
    tt = cfg["tool_timeouts_s"]
    if not (isinstance(tt, dict) and set(tt) == set(TOOLS) and all(_num(v) and v > 0 for v in tt.values())):
        errs.append(f"tool_timeouts_s must give a positive number for each of {TOOLS}")
    elif _num((cfg["timing"] or {}).get("t_attempt")) and tt["run_script_in_play_mode"] <= cfg["timing"]["t_attempt"]:
        errs.append("the play tool timeout must exceed t_attempt (the server deadline fires first)")
    if errs:
        raise ConfigError("; ".join(errs))

    # Route and controller: re-derive and compare with the declared hashes.
    route_file = root / cfg["route"]["file"]
    route_sha = p0.sha256_file(route_file)
    if route_sha != cfg["route"]["sha256"]:
        errs.append(f"route sha256 {route_sha} != declared {cfg['route']['sha256']}")
    route = p0.load_route(route_file)
    text = p0.render_controller(route, cfg["timing"])
    ctl_sha = p0.controller_sha256(text)
    if ctl_sha != cfg["controller"]["sha256"]:
        errs.append(f"controller sha256 {ctl_sha} != declared {cfg['controller']['sha256']}")
    if cfg["controller"].get("template") != "runner/templates/p0_controller.luau":
        errs.append("controller.template must be runner/templates/p0_controller.luau")

    # Scenes and the gate.
    scenes = cfg["scenes"]
    if not (isinstance(scenes, dict) and set(scenes) == {"before", "after"}):
        errs.append("scenes must have exactly before and after")
    else:
        for role, s in scenes.items():
            scene = load_scene(root / s["file"])
            h = canonical_hash(scene)
            if h != s["canonical_hash"]:
                errs.append(f"scenes.{role}: hash {h} != declared {s['canonical_hash']}")
            errs += [f"scenes.{role}: route: {e}" for e in p0.validate_route(route, scene["bounds"])]
    gate = cfg["gate"]
    if not (isinstance(gate, dict) and set(gate) == {"before", "after"}
            and gate["before"] == {"expect": "FELL", "area": "gap_1"}
            and gate["after"] == {"expect": "SUCCESS", "order": ["BRIDGE_CROSSED", "GOAL_TOUCHED"]}):
        errs.append("gate must be before: FELL in gap_1, after: SUCCESS (BRIDGE_CROSSED then GOAL_TOUCHED)")
    if errs:
        raise ConfigError("; ".join(errs))
    out = dict(cfg)
    out["_sha256"] = hashlib.sha256(raw).hexdigest()
    out["_path"] = str(path)
    out["_root"] = str(root)
    out["_route"] = route
    out["_controller_text"] = text
    return out
