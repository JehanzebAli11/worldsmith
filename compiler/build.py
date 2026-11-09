"""Scene JSON → one Luau build script, the read-back script, and the comparator (WS-03).

SPEC section 8: everything lives under `Workspace.Worldsmith`; instance names equal entity IDs;
the root carries `ws_scene_hash` and `ws_version`. The build script refuses to run while play
is running, builds the new tree off the data model, then destroys every old
`Workspace.Worldsmith` and parents the new one, so a failure part-way leaves the old tree in
place. The architect never writes Luau: only validated numbers, IDs, enum values and the scene
hash reach the templates (compiler/templates.py).

Read-back prints the tree as JSON in `WS_READBACK|i|n|<chunk>` lines; `parse_readback`
reassembles it and `compare_tree` checks it against `geometry.expected_tree` within TOLERANCE.
"""

from __future__ import annotations

import json
import math
import re

from worldsmith.compiler import geometry
from worldsmith.compiler.templates import TemplateError, load_template, render
from worldsmith.grammar.scene import SceneError, canonical_hash, normalize_scene, validate_scene

# Declared read-back tolerance, in studs (pos, size) and degrees (rot). Roblox stores CFrame
# and Size as 32-bit floats; at |coordinate| <= 512 their error is below 1e-4 stud.
TOLERANCE = {"pos": 0.01, "size": 0.01, "rot": 0.01}

EVENT_SOURCES = ("ws_lib", "lever", "gate", "trigger", "kill_floor")
_SOURCES_MARKER = "--@WS_EVENT_SOURCES@"


def _event_sources_block() -> str:
    out = []
    for name in EVENT_SOURCES:
        src = load_template("events/" + name)
        if "]==]" in src or "{{" in src:
            raise TemplateError(f"event source {name} cannot be embedded")
        # A newline right after the opening long bracket is skipped by Luau, so the string
        # equals `src` exactly (its byte length is what read-back reports).
        out.append(f'WS_SRC["{name}"] = [==[\n{src}]==]')
    return "\n".join(out)


def _box_values(prefix: str, box: dict) -> dict:
    keys = {}
    for axis, v in zip("xyz", box["size"]):
        keys[f"{prefix}s{axis}"] = v
    for axis, v in zip("xyz", box["pos"]):
        keys[f"{prefix}p{axis}"] = v
    return keys


def _entity_values(ent: dict, by_id: dict) -> dict:
    t = ent["type"]
    parts = geometry.entity_parts(ent, by_id)
    v: dict = {"id": ent["id"]}
    if t == "Lever":
        for box in parts:
            v.update(_box_values(box["name"] + "_", box))
        v["gate"] = ent["controls"]
    elif parts:
        v.update(_box_values("", parts[0]))
    if t == "Gate":
        v["lever"] = ent["opens_on"]
    elif t == "Trigger":
        v.update(event=ent["event"], region=ent["region"], host=ent["on"])
    elif t == "Decor":
        v["palette"] = ent["palette"]
    elif t == "Ramp":
        v["yaw"] = parts[0]["yaw"]
    return v


def compile_build(scene: dict, origin=(0, 0, 0)) -> str:
    """The Luau build script for a valid scene. Raises SceneError if the scene is invalid."""
    errors = validate_scene(scene)
    if errors:
        raise SceneError(errors)
    origin = tuple(origin)
    if len(origin) != 3:
        raise ValueError("origin must be (x, y, z)")
    norm = normalize_scene(scene)
    by_id = {e["id"]: e for e in norm["entities"]}
    header = render(load_template("header"), {
        "scene_hash": canonical_hash(scene), "version": scene["version"],
        "origin_x": origin[0], "origin_y": origin[1], "origin_z": origin[2]})
    if header.count(_SOURCES_MARKER) != 1:
        raise TemplateError("header must hold the event-source marker once")
    header = header.replace(_SOURCES_MARKER, _event_sources_block())
    body = [render(load_template(ent["type"]), _entity_values(ent, by_id))
            for ent in geometry.build_order(norm)]
    return header + "".join(body) + render(load_template("footer"), {})


def compile_readback() -> str:
    """The fixed read-back script (no slots)."""
    return render(load_template("readback"), {})


def compile_arm(nonce: str) -> str:
    """Sets `Workspace.Worldsmith`'s `ws_nonce` attribute, which the event scripts print."""
    return render(load_template("arm"), {"nonce": nonce})


# --- read-back -----------------------------------------------------------------------------

# Optional prefixes: a Studio timestamp, or "[OUTPUT] " (how the Studio MCP's run_code returns
# print() lines; seen live in WS-03).
_READBACK_RE = re.compile(r"^(?:\[OUTPUT\]\s+)?(?:\d{1,2}:\d{2}:\d{2}\.\d+\s+)?WS_READBACK\|(\d+)\|(\d+)\|(.*?)\s*$")


def parse_readback(output: str) -> dict:
    """Reassemble the `WS_READBACK|i|n|chunk` lines in `output` into the tree JSON."""
    chunks: dict[int, str] = {}
    total = None
    for line in output.splitlines():
        m = _READBACK_RE.match(line.strip("\r"))
        if not m:
            continue
        i, n, chunk = int(m.group(1)), int(m.group(2)), m.group(3)
        if total is None:
            total = n
        if n != total or not 1 <= i <= n or i in chunks:
            raise ValueError(f"read-back chunk {i}/{n} is inconsistent or repeated")
        chunks[i] = chunk
    if total is None:
        raise ValueError("no WS_READBACK lines in the output")
    if len(chunks) != total:
        raise ValueError(f"read-back has {len(chunks)} of {total} chunks")
    return json.loads("".join(chunks[i] for i in range(1, total + 1)))


def _close(a, b, tol) -> bool:
    return (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
            and all(isinstance(x, (int, float)) and isinstance(y, (int, float))
                    and abs(x - y) <= tol for x, y in zip(a, b)))


def _angles_close(a, b, tol) -> bool:
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)):
        return False
    for x, y in zip(a, b):
        d = (x - y) % 360.0
        if min(d, 360.0 - d) > tol:
            return False
    return True


def _attrs_equal(exp, got) -> list[str]:
    exp = exp or {}
    got = got if isinstance(got, dict) else ({} if got in ([], None) else got)
    if not isinstance(got, dict):
        return [f"attrs is not an object: {got!r}"]
    out = []
    for k in sorted(set(exp) | set(got)):
        if k not in got:
            out.append(f"attr {k} missing")
        elif k not in exp:
            out.append(f"attr {k} unexpected ({got[k]!r})")
        else:
            e, g = exp[k], got[k]
            if isinstance(e, list):
                ok = _close(e, g, TOLERANCE["pos"])
            elif isinstance(e, bool) or isinstance(g, bool):
                ok = e is g
            elif isinstance(e, (int, float)):
                ok = isinstance(g, (int, float)) and math.isclose(e, g, abs_tol=1e-9)
            else:
                ok = e == g
            if not ok:
                out.append(f"attr {k}: expected {e!r}, got {g!r}")
    return out


def compare_tree(expected: dict, actual: dict) -> list[str]:
    """Every difference between the expected and the read-back tree; [] means it matches."""
    errs = []
    if actual.get("roots") != expected.get("roots"):
        errs.append(f"roots: expected {expected.get('roots')} Workspace.Worldsmith,"
                    f" got {actual.get('roots')}")
    er, ar = expected.get("root") or {}, actual.get("root") or {}
    if er.get("class") != ar.get("class"):
        errs.append(f"root: class {ar.get('class')!r}, expected {er.get('class')!r}")
    errs += [f"root: {e}" for e in _attrs_equal(er.get("attrs"), ar.get("attrs"))]

    exp_nodes = {n["path"]: n for n in expected.get("nodes", [])}
    got_nodes: dict[str, dict] = {}
    for n in actual.get("nodes") or []:
        if n.get("path") in got_nodes:
            errs.append(f"{n.get('path')}: duplicate path in read-back")
        got_nodes[n.get("path")] = n
    for path in sorted(set(exp_nodes) | set(got_nodes)):
        if path not in got_nodes:
            errs.append(f"{path}: missing from read-back")
            continue
        if path not in exp_nodes:
            errs.append(f"{path}: unexpected instance ({got_nodes[path].get('class')})")
            continue
        e, g = exp_nodes[path], got_nodes[path]
        if e["class"] != g.get("class"):
            errs.append(f"{path}: class {g.get('class')!r}, expected {e['class']!r}")
        for key in ("size", "pos"):
            if key in e and not _close(e[key], g.get(key), TOLERANCE[key]):
                errs.append(f"{path}: {key} {g.get(key)!r}, expected {e[key]!r}")
        if "rot" in e and not _angles_close(e["rot"], g.get("rot"), TOLERANCE["rot"]):
            errs.append(f"{path}: rot {g.get('rot')!r}, expected {e['rot']!r}")
        for key in ("anchored", "can_collide", "source_len"):
            if key in e and e[key] != g.get(key):
                errs.append(f"{path}: {key} {g.get(key)!r}, expected {e[key]!r}")
        errs += [f"{path}: {x}" for x in _attrs_equal(e.get("attrs"), g.get("attrs"))]
    return errs
