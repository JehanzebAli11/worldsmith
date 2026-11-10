"""The preflight: is Studio holding the right place, alone, before a build? (ADR 0011)

The smoke_f1e_p0v1 debug (docs/notes/smoke_f1e_p0v1_debug.md) found the episode had built into
the Autopilot test place while an Autopilot bench run drove the same Studio. An experiment that
declares `preflight` (config key `preflight: {script, sha256}`) runs this step before EVERY build
(each scene version), and records it as `v<k>/preflight.json`:

1. `get_studio_mode` must answer `stop`. If not, the probe is not sent and nothing is stopped:
   another driver may own that play session (the runner never stops someone else's run).
2. A read-only `run_code` (the fixed template `runner/templates/preflight_v1.luau`, hash declared
   in the config) prints `WS_PREFLIGHT|<json>`; `evaluate` checks it.

A failed check is HARNESS_ERROR: the episode stops before the build (no attempt, no patch, no
architect call). A call that errors is CONNECTOR_ERROR. The checks (all must pass):

- studio_mode_stop: get_studio_mode is `stop` and the probe says the game is not running;
- place_marker: `Workspace:GetAttribute("ws_place") == "worldsmith-lab"`, set ONCE by the owner in
  the saved `places/worldsmith-lab.rbxl` (never by the runner: the check must be able to detect
  a wrong place). `scripts/mark_place.py` prints the payload; MARK_PLACE_LUAU is the one line;
- load_character_appearance_off: `StarterPlayer.LoadCharacterAppearance == false`;
- character_auto_loads_on: `Players.CharacterAutoLoads == true`;
- no_starter_character: no `StarterPlayer.StarterCharacter`;
- one_spawn_outside_worldsmith: exactly one SpawnLocation outside `Workspace.Worldsmith` (the
  Baseplate template's, as saved in worldsmith-lab.rbxl; places/README.md). Autopilot's place had
  a second one (`Workspace.Start`);
- no_foreign_scripts: no script (any LuaSourceContainer) in ServerScriptService,
  StarterPlayerScripts, StarterCharacterScripts or StarterGui, except Worldsmith-owned ones (name
  starting with "Worldsmith" AND attribute `ws_owned == true`; none exist today).
"""

from __future__ import annotations

import json
import re

from worldsmith.runner import p0

RECORD_SCHEMA = "worldsmith.preflight/0.1"
PROBE_SCHEMA = "worldsmith.preflight_probe/0.1"
PLACE_ATTR = "ws_place"
PLACE_MARKER = "worldsmith-lab"
MARK_PLACE_LUAU = f'workspace:SetAttribute("{PLACE_ATTR}", "{PLACE_MARKER}")'
CHECKS = ("studio_mode_stop", "place_marker", "load_character_appearance_off", "character_auto_loads_on",
          "no_starter_character", "one_spawn_outside_worldsmith", "no_foreign_scripts")
_LINE_RE = re.compile(r"^\s*(?:\[OUTPUT\]\s+)?WS_PREFLIGHT\|(?P<json>\{.*\})\s*$", re.M)
_PROBE_KEYS = {"schema", "game_name", "place_id", "is_running", "ws_place", "load_character_appearance",
               "character_auto_loads", "starter_character", "spawns_outside", "spawns_outside_list",
               "spawns_inside", "foreign_scripts", "foreign_scripts_list", "owned_scripts"}


class PreflightError(ValueError):
    """The probe's answer is missing or malformed."""


def parse_probe(text: str) -> dict:
    ms = list(_LINE_RE.finditer(text or ""))
    if len(ms) != 1:
        raise PreflightError(f"expected one WS_PREFLIGHT line, got {len(ms)}")
    try:
        probe = json.loads(ms[0].group("json"))
    except json.JSONDecodeError as e:
        raise PreflightError(f"WS_PREFLIGHT line is not JSON: {e}") from None
    if not isinstance(probe, dict) or probe.get("schema") != PROBE_SCHEMA or set(probe) != _PROBE_KEYS:
        raise PreflightError(f"WS_PREFLIGHT is not a {PROBE_SCHEMA} object")
    return probe


def evaluate(studio_mode: str, probe: dict | None) -> list[dict]:
    """One {check, ok, observed, want} per CHECKS entry. `probe` None (not sent) fails every
    probe check with observed None."""
    p = probe or {}
    have = probe is not None

    def row(name, ok, observed, want):
        return {"check": name, "ok": bool(have and ok) if name != "studio_mode_stop" else bool(ok),
                "observed": observed if (have or name == "studio_mode_stop") else None, "want": want}

    lists = lambda k: p.get(k) if isinstance(p.get(k), list) else []
    return [
        row("studio_mode_stop", studio_mode == "stop" and (not have or p.get("is_running") is False),
            {"get_studio_mode": studio_mode, "is_running": p.get("is_running")}, "stop, not running"),
        row("place_marker", p.get("ws_place") == PLACE_MARKER,
            {"ws_place": p.get("ws_place"), "game_name": p.get("game_name")}, f'{PLACE_ATTR} = "{PLACE_MARKER}"'),
        row("load_character_appearance_off", p.get("load_character_appearance") is False,
            p.get("load_character_appearance"), False),
        row("character_auto_loads_on", p.get("character_auto_loads") is True, p.get("character_auto_loads"), True),
        row("no_starter_character", p.get("starter_character") is False, p.get("starter_character"), False),
        row("one_spawn_outside_worldsmith", p.get("spawns_outside") == 1,
            {"count": p.get("spawns_outside"), "spawns": lists("spawns_outside_list"),
             "inside_worldsmith": p.get("spawns_inside")}, 1),
        row("no_foreign_scripts", p.get("foreign_scripts") == 0,
            {"count": p.get("foreign_scripts"), "scripts": lists("foreign_scripts_list"),
             "worldsmith_owned": p.get("owned_scripts")}, 0),
    ]


def run(call, script: str, timeouts: dict) -> dict:
    """The preflight record (also written to the bundle). `status`: pass | HARNESS_ERROR |
    CONNECTOR_ERROR. Read-only: get_studio_mode, then (only in `stop`) the probe."""
    rec = {"schema": RECORD_SCHEMA, "script_sha256": p0.sha256_text(script), "studio_mode": None, "probe": None,
           "probe_raw": None, "checks": [], "ok": False, "status": None, "detail": ""}
    r = call("get_studio_mode", {}, timeouts["get_studio_mode"])
    if r.error is not None:
        rec.update(status="CONNECTOR_ERROR", detail=f"preflight get_studio_mode: {r.error}")
        return rec
    mode = (r.text or "").strip()
    rec["studio_mode"] = mode
    probe = None
    if mode == "stop":
        r = call("run_code", {"command": script}, timeouts["run_code"])
        if r.error is not None:
            rec.update(status="CONNECTOR_ERROR", detail=f"preflight probe: {r.error}")
            return rec
        rec["probe_raw"] = r.text or ""
        try:
            probe = parse_probe(r.text or "")
        except PreflightError as e:
            rec.update(status="HARNESS_ERROR", detail=f"preflight probe unreadable: {e}",
                       checks=evaluate(mode, None))
            return rec
        rec["probe"] = probe
    checks = evaluate(mode, probe)
    failed = [c["check"] for c in checks if not c["ok"]]
    rec["checks"] = checks
    if failed:
        why = "" if probe is not None else f" (Studio mode {mode!r}: probe not sent, nothing stopped)"
        rec.update(status="HARNESS_ERROR", detail=f"preflight failed: {', '.join(failed)}{why}")
    else:
        rec.update(ok=True, status="pass", detail="all preflight checks passed")
    return rec


def mark_place_command() -> str:
    """The one-time run_code command the owner sends in Edit mode with places/worldsmith-lab.rbxl
    open (then File -> Save to File). It refuses in play mode and prints what it set."""
    return ('if game:GetService("RunService"):IsRunning() then error("stop play first (Edit mode only)") end\n'
            f"{MARK_PLACE_LUAU}\n"
            f'print("WS_PLACE_MARKED|" .. game.Name .. "|" .. tostring(workspace:GetAttribute("{PLACE_ATTR}")))\n')


def simulated_probe(**over) -> dict:
    """A passing probe in the shape the template prints (dry runs and tests; not Studio)."""
    p = {"schema": PROBE_SCHEMA, "game_name": "worldsmith-lab.rbxl", "place_id": 0, "is_running": False,
         "ws_place": PLACE_MARKER, "load_character_appearance": False, "character_auto_loads": True,
         "starter_character": False, "spawns_outside": 1,
         "spawns_outside_list": [{"path": "Workspace.SpawnLocation", "pos": "0.00,0.50,0.00", "neutral": True,
                                  "enabled": True}],
         "spawns_inside": 1, "foreign_scripts": 0, "foreign_scripts_list": [], "owned_scripts": 0}
    p.update(over)
    return p
