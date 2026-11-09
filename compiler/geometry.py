"""Scene geometry and the expected Workspace.Worldsmith tree (WS-03).

One source of truth for where every built part goes: the compiler fills the Luau templates from
`entity_parts`, and the read-back comparator checks Studio against `expected_tree`. Positions are
box centres in the scene frame (studs, Y up); the build adds the origin, the read-back subtracts
it. Conventions (ADR 0002, ADR 0003):

- Spawn and Goal sit on the top centre of their platform.
- A Bridge starts at the gap's `from` edge, runs `length` along the gap axis, its top flush with
  the `from` platform top; `lateral_offset` shifts it across the axis from that platform's
  centreline.
- Trigger `landing` (BRIDGE_CROSSED): the first LANDING_DEPTH studs of the gap's `to` platform
  from its gap-facing edge, across its full width, from its top up by `height`.
- Trigger `whole` (GOAL_TOUCHED): the goal pad's footprint, from the pad's top up by `height`.
- A Gap is not built.

Fixed sizes that are not scene fields (SPAWN_SIZE, the lever parts) are template constants,
reviewed with the templates; they are not patchable.
"""

from __future__ import annotations

from worldsmith.grammar.scene import canonical_hash, normalize_scene

LANDING_DEPTH = 2.0                 # ADR 0003
SPAWN_SIZE = (6.0, 0.2, 6.0)
LEVER_BASE = (2.0, 1.0, 2.0)
LEVER_HANDLE = (0.5, 3.0, 0.5)
LEVER_ZONE = (6.0, 5.0, 6.0)        # touch volume around the lever (CanCollide off)
SCHEMA = "worldsmith.scene/0.1"
RAMP_YAW = {"+Z": 0, "-Z": 180, "+X": 90, "-X": -90}

_AXIS = {"X": 0, "Z": 2}


def _r(v: float) -> float | int:
    """Round to the canonical 3 decimals; whole numbers become ints (JSON-stable)."""
    x = round(float(v), 3) + 0.0
    return int(x) if x == int(x) else x


def _vec(v) -> list:
    return [_r(c) for c in v]


def _top(p: dict) -> float:
    return p["pos"][1] + p["size"][1] / 2


def _box(name, pos, size, *, cls="Part", yaw=0, can_collide=True):
    return {"name": name, "class": cls, "pos": _vec(pos), "size": _vec(size), "yaw": yaw,
            "can_collide": can_collide}


def _gap_frame(gap: dict):
    """(axis index, across index, sign) for a gap axis like '+Z'."""
    i = _AXIS[gap["axis"][1]]
    return i, 2 - i, (1 if gap["axis"][0] == "+" else -1)


def entity_parts(ent: dict, by_id: dict) -> list[dict]:
    """The parts (boxes) one entity builds, in build order. [] for a Gap."""
    t = ent["type"]
    if t in ("Platform", "Wall", "KillFloor", "Decor", "Gate"):
        return [_box(ent["id"], ent["pos"], ent["size"],
                     can_collide=t != "Decor")]
    if t == "Spawn":
        p = by_id[ent["on"]]
        return [_box(ent["id"], [p["pos"][0], _top(p) + SPAWN_SIZE[1] / 2, p["pos"][2]],
                     SPAWN_SIZE, cls="SpawnLocation")]
    if t == "Goal":
        p = by_id[ent["on"]]
        return [_box(ent["id"], [p["pos"][0], _top(p) + ent["size"][1] / 2, p["pos"][2]],
                     ent["size"])]
    if t == "Bridge":
        gap = by_id[ent["spans"]]
        src = by_id[gap["from"]]
        i, j, sign = _gap_frame(gap)
        edge = src["pos"][i] + sign * src["size"][i] / 2
        pos, size = [0.0, 0.0, 0.0], [0.0, ent["thickness"], 0.0]
        pos[i] = edge + sign * ent["length"] / 2
        pos[j] = src["pos"][j] + ent["lateral_offset"]
        pos[1] = _top(src) - ent["thickness"] / 2
        size[i], size[j] = ent["length"], ent["width"]
        return [_box(ent["id"], pos, size)]
    if t == "Trigger":
        host = by_id[ent["on"]]
        h = ent["height"]
        if ent["region"] == "landing":
            dst = by_id[host["to"]]
            i, j, sign = _gap_frame(host)
            edge = dst["pos"][i] - sign * dst["size"][i] / 2      # the gap-facing edge
            pos, size = [0.0, 0.0, 0.0], [0.0, h, 0.0]
            pos[i] = edge + sign * LANDING_DEPTH / 2
            pos[j] = dst["pos"][j]
            size[i], size[j] = LANDING_DEPTH, dst["size"][j]
            pos[1] = _top(dst) + h / 2
        else:  # whole: the goal pad's footprint
            pad = entity_parts(host, by_id)[0]
            top = pad["pos"][1] + pad["size"][1] / 2
            pos = [pad["pos"][0], top + h / 2, pad["pos"][2]]
            size = [pad["size"][0], h, pad["size"][2]]
        return [_box(ent["id"], pos, size, can_collide=False)]
    if t == "Lever":
        x, y, z = ent["pos"]
        return [
            _box("base", [x, y + LEVER_BASE[1] / 2, z], LEVER_BASE),
            _box("handle", [x, y + LEVER_BASE[1] + LEVER_HANDLE[1] / 2, z], LEVER_HANDLE),
            _box("zone", [x, y + LEVER_ZONE[1] / 2, z], LEVER_ZONE, can_collide=False),
        ]
    if t == "Ramp":
        yaw = RAMP_YAW[ent["rises_towards"]]
        sx, sy, sz = ent["size"]
        local = [sx, sy, sz] if yaw in (0, 180) else [sz, sy, sx]
        return [_box(ent["id"], ent["pos"], local, cls="WedgePart", yaw=yaw)]
    if t == "Gap":
        return []
    raise ValueError(f"unknown entity type {t!r}")


def _attrs(ent: dict) -> dict:
    a = {"ws_id": ent["id"], "ws_type": ent["type"]}
    t = ent["type"]
    if t == "Lever":
        a.update(ws_gate=ent["controls"], ws_active=False)
    elif t == "Gate":
        a.update(ws_lever=ent["opens_on"], ws_open=False)
    elif t == "Trigger":
        a.update(ws_event=ent["event"], ws_region=ent["region"], ws_host=ent["on"])
    elif t == "Decor":
        a.update(ws_palette=ent["palette"])
    return a


# Event scripts each entity type carries (name of the fixed source in templates/events/).
EVENT_SCRIPT = {"Lever": "lever", "Gate": "gate", "Trigger": "trigger", "KillFloor": "kill_floor"}


def _part_node(path, box, attrs):
    return {"path": path, "class": box["class"], "size": box["size"], "pos": box["pos"],
            "rot": [0, box["yaw"], 0], "anchored": True, "can_collide": box["can_collide"],
            "attrs": attrs}


def _script_node(path, cls, source_name):
    from worldsmith.compiler.templates import load_template
    return {"path": path, "class": cls,
            "source_len": len(load_template("events/" + source_name).encode("utf-8")),
            "attrs": {}}


def build_order(scene: dict) -> list[dict]:
    """Entities in the order the build script creates them: sorted by id (canonical order)."""
    return normalize_scene(scene)["entities"]


def expected_tree(scene: dict, origin=(0, 0, 0)) -> dict:
    """The tree the read-back script should report after building `scene` (scene frame)."""
    norm = normalize_scene(scene)
    by_id = {e["id"]: e for e in norm["entities"]}
    nodes = [_script_node("ws_lib", "ModuleScript", "ws_lib")]
    for ent in norm["entities"]:
        t, eid = ent["type"], ent["id"]
        parts = entity_parts(ent, by_id)
        if not parts:
            continue
        if t == "Lever":
            nodes.append({"path": eid, "class": "Model", "attrs": _attrs(ent)})
            for box in parts:
                nodes.append(_part_node(f"{eid}/{box['name']}", box, {}))
            nodes.append({"path": f"{eid}/handle/prompt", "class": "ProximityPrompt", "attrs": {}})
        else:
            nodes.append(_part_node(eid, parts[0], _attrs(ent)))
        if t in EVENT_SCRIPT:
            nodes.append(_script_node(f"{eid}/ws_event", "Script", EVENT_SCRIPT[t]))
    nodes.sort(key=lambda n: n["path"])
    return {
        "roots": 1,
        "root": {"class": "Folder", "attrs": {
            "ws_schema": SCHEMA, "ws_scene_hash": canonical_hash(scene),
            "ws_version": scene["version"], "ws_origin": _vec(origin)}},
        "nodes": nodes,
    }


def descendant_count(tree: dict) -> int:
    """What `#root:GetDescendants()` should print after the build."""
    return len(tree["nodes"])
