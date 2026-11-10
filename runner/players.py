"""Labelled player versions (WS-11, ADR 0010; SPEC section 9).

Different player versions are separate, labelled experiments, never mixed (SPEC section 9). Each
version is a fixed, reviewed Luau template plus the declared timing it takes:

- `p0.v0`: the M0 controller (`runner/templates/p0_controller.luau`, WS-04). Frozen with the M0
  gate (`config/m0.yaml`, controller `3743d998…`); this module renders it through
  `runner.p0.render_controller` unchanged, so `bundles/m0/gate.json` still reproduces.
- `p0.v1`: p0.v0 plus periodic position samples while alive (`sample_every`, declared 1 s), so
  STUCK is evaluable (WS-08 open question 2). A new template file and a new controller hash.

The controller hash is the SHA-256 of the rendered text with the nonce slot unfilled (as in
WS-04). Experiments declare the version, the route and the timing; the experiment loader
re-derives the hash and refuses a mismatch.
"""

from __future__ import annotations

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


PLAYER_VERSIONS = {
    "p0.v0": PlayerVersion(
        "p0.v0", "P0", "p0_controller.luau", _V0_KEYS, False,
        "server-scripted Humanoid:MoveTo waypoints via run_script_in_play_mode; never jumps",
        "the M0 controller (WS-04); frozen with the M0 gate, never changed"),
    "p0.v1": PlayerVersion(
        "p0.v1", "P0", "p0_controller_v1.luau", _V1_KEYS, True,
        "server-scripted Humanoid:MoveTo waypoints via run_script_in_play_mode; never jumps",
        "p0.v0 plus position samples while alive every sample_every s (WS-11, ADR 0010)"),
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
    text = resources.files("worldsmith.runner").joinpath("templates", pv.template).read_text(encoding="utf-8")
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
