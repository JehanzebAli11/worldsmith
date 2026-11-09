"""The P0 controller: a fixed, reviewed Luau template plus a frozen route (WS-04, ADR 0004/0005).

P0 is server-scripted `Humanoid:MoveTo` waypoints; it never jumps (SPEC section 9 as amended by
the owner on 9 Nov 2026). The template `runner/templates/p0_controller.luau` takes only:

- the run nonce (`{{nonce:nonce}}`, filled per attempt by `instantiate`);
- the declared timing numbers (`t_attempt`, `t_waypoint`, `settle`, `tick`, from config/m0.yaml);
- the frozen route's waypoint names and numbers (config/p0_route_F1.json), validated here and
  written with the compiler's number formatter.

`render_controller` returns the controller text with the nonce slot left in place; its SHA-256
is **the controller hash**. It has no scene input, so the same hash runs before and after a
patch. `instantiate` fills the nonce with the strict slot renderer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path

from worldsmith.compiler.templates import TemplateError, format_number, render

NONCE_SLOT = "{{nonce:nonce}}"
_NONCE_SENTINEL = "WS_NONCE_SENTINEL_9f3c"
_WAYPOINTS_MARKER = "--@WS_WAYPOINTS@"
ROUTE_SCHEMA = "worldsmith.p0route/0.1"
MAX_WAYPOINTS = 16
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
TIMING_KEYS = ("t_attempt", "t_waypoint", "settle", "tick")
# Hard limits for the declared timing (seconds); the gate's values must sit inside them.
TIMING_LIMITS = {"t_attempt": (5, 300), "t_waypoint": (1, 60), "settle": (0, 10), "tick": (0.01, 1)}


class RouteError(ValueError):
    """The route file or a timing value is not acceptable."""


@lru_cache(maxsize=None)
def load_template() -> str:
    text = resources.files("worldsmith.runner").joinpath("templates", "p0_controller.luau") \
        .read_text(encoding="utf-8")
    if not text.isascii():
        raise TemplateError("controller template is not ASCII")
    return text


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_route(route: dict, bounds: dict | None = None) -> list[str]:
    """Problems with a route dict ([] if fine). `bounds` = a scene's {"min": [..], "max": [..]}."""
    errs = []
    if not isinstance(route, dict):
        return ["route is not an object"]
    allowed = {"schema", "family", "frame", "chosen_from", "waypoints"}
    for k in sorted(set(route) - allowed):
        errs.append(f"unknown key {k!r}")
    if route.get("schema") != ROUTE_SCHEMA:
        errs.append(f"schema must be {ROUTE_SCHEMA}")
    if route.get("frame") != "scene":
        errs.append("frame must be 'scene'")
    if not isinstance(route.get("family"), str) or not re.fullmatch(r"F\d+", route.get("family", "")):
        errs.append("family must look like F1")
    wps = route.get("waypoints")
    if not isinstance(wps, list) or not 1 <= len(wps) <= MAX_WAYPOINTS:
        return errs + [f"waypoints must be a list of 1..{MAX_WAYPOINTS}"]
    names = set()
    for i, w in enumerate(wps):
        if not isinstance(w, dict) or set(w) != {"name", "pos"}:
            errs.append(f"waypoint {i}: needs exactly name and pos")
            continue
        if not isinstance(w["name"], str) or not _NAME_RE.fullmatch(w["name"]):
            errs.append(f"waypoint {i}: bad name {w['name']!r}")
        elif w["name"] in names:
            errs.append(f"waypoint {i}: duplicate name {w['name']!r}")
        names.add(w.get("name"))
        pos = w["pos"]
        if not isinstance(pos, list) or len(pos) != 3 or not all(_finite(c) for c in pos):
            errs.append(f"waypoint {i}: pos must be 3 finite numbers")
            continue
        if bounds is not None:
            for c, lo, hi in zip(pos, bounds["min"], bounds["max"]):
                if not lo <= c <= hi:
                    errs.append(f"waypoint {i} ({w['name']}): {pos} outside the scene bounds")
                    break
    return errs


def load_route(path) -> dict:
    route = json.loads(Path(path).read_text(encoding="utf-8"))
    errs = validate_route(route)
    if errs:
        raise RouteError("; ".join(errs))
    return route


def validate_timing(timing: dict) -> list[str]:
    errs = []
    for k in sorted(set(timing) - set(TIMING_KEYS)):
        errs.append(f"unknown timing key {k!r}")
    for k in TIMING_KEYS:
        v = timing.get(k)
        lo, hi = TIMING_LIMITS[k]
        if not _finite(v) or not lo <= v <= hi:
            errs.append(f"{k} must be a number in [{lo}, {hi}], got {v!r}")
    return errs


def _waypoint_lines(route: dict) -> str:
    lines = []
    for w in route["waypoints"]:
        x, y, z = (format_number(c) for c in w["pos"])
        lines.append(f'    {{ "{w["name"]}", {x}, {y}, {z} }},')
    return "\n".join(lines)


def render_controller(route: dict, timing: dict) -> str:
    """The controller text with the nonce slot unfilled. Its SHA-256 is the controller hash."""
    errs = validate_route(route) + validate_timing(timing)
    if errs:
        raise RouteError("; ".join(errs))
    tpl = load_template()
    if tpl.count(NONCE_SLOT) != 1 or tpl.count(_WAYPOINTS_MARKER) != 1 or _NONCE_SENTINEL in tpl:
        raise TemplateError("controller template must hold the nonce slot and the waypoint marker once")
    text = tpl.replace(NONCE_SLOT, _NONCE_SENTINEL)
    text = render(text, {k: timing[k] for k in TIMING_KEYS})
    text = text.replace(_WAYPOINTS_MARKER, _waypoint_lines(route))
    return text.replace(_NONCE_SENTINEL, NONCE_SLOT)


def controller_sha256(controller_text: str) -> str:
    if controller_text.count(NONCE_SLOT) != 1:
        raise TemplateError("controller text must hold the unfilled nonce slot once")
    return sha256_text(controller_text)


def instantiate(controller_text: str, nonce: str) -> str:
    """The script sent for one attempt: the controller with its nonce filled (strict)."""
    if controller_text.count(NONCE_SLOT) != 1:
        raise TemplateError("controller text must hold the unfilled nonce slot once")
    return render(controller_text, {"nonce": nonce})


def expected_params(route: dict, timing: dict) -> dict:
    """What the controller echoes back as `params` (the runner checks it arrived intact)."""
    return {**{k: timing[k] for k in TIMING_KEYS},
            "waypoints": [{"name": w["name"], "pos": list(w["pos"])} for w in route["waypoints"]]}
