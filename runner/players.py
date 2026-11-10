"""Labelled player versions (WS-11, ADR 0010; SPEC section 9).

Different player versions are separate, labelled experiments, never mixed (SPEC section 9). Each
version is a fixed, reviewed Luau template plus the declared timing it takes:

- `p0.v0`: the M0 controller (`runner/templates/p0_controller.luau`, WS-04). Frozen with the M0
  gate (`config/m0.yaml`, controller `3743d998…`); this module renders it through
  `runner.p0.render_controller` unchanged, so `bundles/m0/gate.json` still reproduces.
- `p0.v1`: p0.v0 plus periodic position samples while alive (`sample_every`, declared 1 s), so
  STUCK is evaluable (WS-08 open question 2). A new template file and a new controller hash.
- `p0.v2`: p0.v1 plus a spawn check before the route (ADR 0011): grounded within 1 stud (XZ) of
  the spawn centre and staying there through the settle, else harness_error "moved_before_route"
  with `spawn_offset` and `settle_drift` reported. Same timing keys as p0.v1 (samples every 1 s).
- `p0.v3`: p0.v2 with only the spawn check changed (ADR 0012): it counts physics steps
  (RunService.Heartbeat), not wall-clock ticks: a warm-up of steady steps ("not_warm"), grounded
  by FloorMaterial or a downward ray ("not_grounded"), then a settle of SETTLE_STEPS steps within
  1 stud (XZ) of the spawn centre ("moved_before_route"). Same timing keys as p0.v2.

Every template file's SHA-256 is pinned here (`template_sha256`); `load_template` refuses a
template whose bytes differ, so a frozen player cannot change under a declared experiment.

The controller hash is the SHA-256 of the rendered text with the nonce slot unfilled (as in
WS-04). Experiments declare the version, the route and the timing; the experiment loader
re-derives the hash and refuses a mismatch.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from worldsmith.compiler.templates import TemplateError, render
from worldsmith.runner import p0

_V0_KEYS = p0.TIMING_KEYS
_V1_KEYS = p0.TIMING_KEYS + ("sample_every",)
SAMPLE_EVERY_LIMITS = (0.25, 5)


@dataclass(frozen=True)
class PlayerVersion:
    id: str
    tier: str
    template: str             # file under runner/templates/
    timing_keys: tuple
    samples: bool             # the report carries periodic position samples
    driver: str
    note: str
    template_sha256: str      # SHA-256 of the template file's bytes (pinned; load_template checks it)


PLAYER_VERSIONS = {
    "p0.v0": PlayerVersion(
        "p0.v0", "P0", "p0_controller.luau", _V0_KEYS, False,
        "server-scripted Humanoid:MoveTo waypoints via run_script_in_play_mode; never jumps",
        "the M0 controller (WS-04); frozen with the M0 gate, never changed",
        "c246c3f6773adc23db2aed23a8f21f7676a41821cbe03f23de6ed4468d00dd12"),
    "p0.v1": PlayerVersion(
        "p0.v1", "P0", "p0_controller_v1.luau", _V1_KEYS, True,
        "server-scripted Humanoid:MoveTo waypoints via run_script_in_play_mode; never jumps",
        "p0.v0 plus position samples while alive every sample_every s (WS-11, ADR 0010)",
        "91748051fea911731353e2dcef5cac0b9f288a929908080cde0bd7f731cc145e"),
    "p0.v2": PlayerVersion(
        "p0.v2", "P0", "p0_controller_v2.luau", _V1_KEYS, True,
        "server-scripted Humanoid:MoveTo waypoints via run_script_in_play_mode; never jumps",
        "p0.v1 plus a spawn check before the route: harness_error moved_before_route if the character "
        "is not grounded within 1 stud (XZ) of the spawn centre through the settle (ADR 0011)",
        "7eeff15ee5222a1027c5eebe2704737ef88bbf24be336380312566c5f0f5ff09"),
    "p0.v3": PlayerVersion(
        "p0.v3", "P0", "p0_controller_v3.luau", _V1_KEYS, True,
        "server-scripted Humanoid:MoveTo waypoints via run_script_in_play_mode; never jumps",
        "p0.v2 with the spawn check counting physics steps (RunService.Heartbeat), not wall time: warm-up "
        "(not_warm), grounded by FloorMaterial or a downward ray (not_grounded), settle within 1 stud "
        "(moved_before_route) (ADR 0012)",
        "58ca4bce6abd0a4315194a05544bfea05fad9575449fb0c7d254f7d3540c66d6"),
}


class PlayerError(ValueError):
    """Unknown player version, or timing/route it cannot take."""


def get(version: str) -> PlayerVersion:
    if version not in PLAYER_VERSIONS:
        raise PlayerError(f"unknown player version {version!r}; known: {sorted(PLAYER_VERSIONS)}")
    return PLAYER_VERSIONS[version]


@lru_cache(maxsize=None)
def load_template(version: str) -> str:
    pv = get(version)
    raw = resources.files("worldsmith.runner").joinpath("templates", pv.template).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != pv.template_sha256:
        raise TemplateError(f"{pv.template}: sha256 {got} != pinned {pv.template_sha256} (a frozen player "
                            "template changed; a change is a new player version)")
    text = raw.decode("utf-8")
    if not text.isascii():
        raise TemplateError(f"{pv.template} is not ASCII")
    return text


def validate_timing(version: str, timing) -> list[str]:
    pv = get(version)
    if not isinstance(timing, dict):
        return ["timing must be a mapping"]
    errs = [f"unknown timing key {k!r} for {version}" for k in sorted(set(timing) - set(pv.timing_keys))]
    errs += p0.validate_timing({k: timing.get(k) for k in p0.TIMING_KEYS})
    if "sample_every" in pv.timing_keys:
        v = timing.get("sample_every")
        lo, hi = SAMPLE_EVERY_LIMITS
        if not (p0._finite(v) and lo <= v <= hi):
            errs.append(f"sample_every must be a number in [{lo}, {hi}], got {v!r}")
        elif p0._finite(timing.get("tick")) and v - 2 * timing["tick"] <= 0:
            errs.append("sample_every must exceed 2 * tick")
    return errs


def render_controller(version: str, route: dict, timing: dict) -> str:
    """The controller text of `version` with the nonce slot unfilled; its SHA-256 is the
    controller hash. p0.v0 goes through `p0.render_controller` exactly (the M0 path)."""
    if version == "p0.v0":
        return p0.render_controller(route, timing)
    pv = get(version)
    errs = p0.validate_route(route) + validate_timing(version, timing)
    if errs:
        raise p0.RouteError("; ".join(errs))
    tpl = load_template(version)
    if tpl.count(p0.NONCE_SLOT) != 1 or tpl.count(p0._WAYPOINTS_MARKER) != 1 or p0._NONCE_SENTINEL in tpl:
        raise TemplateError(f"{pv.template} must hold the nonce slot and the waypoint marker once")
    text = tpl.replace(p0.NONCE_SLOT, p0._NONCE_SENTINEL)
    text = render(text, {k: timing[k] for k in pv.timing_keys})
    text = text.replace(p0._WAYPOINTS_MARKER, p0._waypoint_lines(route))
    return text.replace(p0._NONCE_SENTINEL, p0.NONCE_SLOT)


def controller_sha256(text: str) -> str:
    return p0.controller_sha256(text)


def expected_params(version: str, route: dict, timing: dict) -> dict:
    """What the controller echoes back as `params`."""
    pv = get(version)
    return {**{k: timing[k] for k in pv.timing_keys},
            "waypoints": [{"name": w["name"], "pos": list(w["pos"])} for w in route["waypoints"]]}
