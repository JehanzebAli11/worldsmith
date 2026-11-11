"""Shared helpers for the WS-02 scene tests."""

import copy
import json

from _repo import REPO_ROOT

SCENES_ROOT = REPO_ROOT / "scenes"

# The hand-authored F1 fixtures (SPEC section 5) and the M0 hand-authored fix (SPEC section 6).
F1A = SCENES_ROOT / "F1-A" / "scene.json"
F1A_FIX = SCENES_ROOT / "F1-A" / "fix.json"
F1E = SCENES_ROOT / "F1-E" / "scene.json"
# WS-14 broken fixtures (ADR 0013) and the scenes their hand-authored M1 patches produce.
WS14 = [SCENES_ROOT / n / f for n in ("F1-B", "F1-C", "F1-D") for f in ("scene.json", "fix.json")]
FIXTURES = [F1A, F1A_FIX, F1E, *WS14]

# SPEC section 6: the closed entity-type set.
ENTITY_TYPES = {
    "Spawn", "Platform", "Gap", "Bridge", "Ramp", "Wall",
    "Lever", "Gate", "Goal", "KillFloor", "Trigger", "Decor",
}


def raw(path):
    """The fixture as plain JSON (no worldsmith code involved)."""
    return json.loads(path.read_text(encoding="utf-8"))


def entity(scene, eid):
    for e in scene["entities"]:
        if e["id"] == eid:
            return e
    raise KeyError(eid)


def object_paths(node, path=()):
    """Every path (tuple of keys/indices) in `node` that holds a JSON object."""
    out = []
    if isinstance(node, dict):
        out.append(path)
        for k, v in node.items():
            out.extend(object_paths(v, path + (k,)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(object_paths(v, path + (i,)))
    return out


def leaf_paths(node, path=()):
    """Every path to a scalar leaf (number or string)."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(leaf_paths(v, path + (k,)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(leaf_paths(v, path + (i,)))
    else:
        out.append(path)
    return out


def get_at(node, path):
    for p in path:
        node = node[p]
    return node


def with_value(scene, path, value):
    s = copy.deepcopy(scene)
    parent = get_at(s, path[:-1])
    parent[path[-1]] = value
    return s


def bound_key(scene, path):
    """The bounds.yaml key for a numeric leaf: `<Type>.<field>` or `scene.<dotted path>`."""
    if path[0] == "entities":
        ent = scene["entities"][path[1]]
        return f"{ent['type']}.{path[2]}"
    names = [p for p in path if isinstance(p, str)]
    return "scene." + ".".join(names)
