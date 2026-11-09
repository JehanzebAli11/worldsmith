"""The navigation approximation (WS-07): SPEC section 7 item 5, ADR 0008.

    navigate(scene, mode="walk"|"jump") -> dict      one mode
    graph(scene) -> dict | None                      both modes: the validator's `graph` field
    reach(dh) -> float | None                        derived jump reach for a height change

A coarse walk graph over the top surfaces of the parts the compiler would build
(`compiler/geometry.py`), for a character approximated as a vertical cylinder. The result,
CONNECTED or DISCONNECTED, holds only UNDER THIS APPROXIMATION. It is never a physical proof of
solvability or unsolvability: actual runs decide (SPEC section 7). The validator reports it as
information and as the warning GRAPH_DISCONNECTED_WALK; it never rejects a patch.

The model (ADR 0008):

- Grid: square columns of `cell` studs over the room box (centres at half-cell offsets from
  `bounds.min`). Solids are the parts built with CanCollide on (Decor is built CanCollide off,
  so it neither blocks nor supports here; ADR 0007's conservative rules already judge it), minus
  open gates. In each column the solid intervals that contain the column centre are merged; the
  top of each merged block is a surface.
- A surface is a node when the body fits: the column is free for `body_height` above it, and no
  solid within `body_radius` of the centre reaches into the band from `step_up` to
  `body_height` above the surface. A surface whose top part is a KillFloor, or a body that would
  touch a KillFloor, is a hazard, never a node.
- Walk edges: to each of the 4 neighbouring columns, landing on the highest surface at most
  `step_up` above the current one (a lower one is a drop; Roblox has no fall damage by default).
- Jump edges (mode `jump` only): from a rim node (one with a 4-neighbour it cannot walk to at
  about its own level) to any node within `reach(dh)` horizontally and at most `jump_rise`
  higher, along a ballistic arc (measured gravity and jump height, horizontal speed at most the
  measured walk speed) whose swept body hits no solid.
- Gates: closed (solid) until the body touches their lever's touch zone from a reachable node;
  then open for good (the lever latches). Repeated to a fixpoint.
- Start: the spawn's top surface at its centre column. Goal: any node whose body overlaps the
  GOAL_TOUCHED trigger volume.

Pure and deterministic: inputs are never mutated; iteration orders are fixed.
"""

from __future__ import annotations

import bisect
import copy
import json
import math
from collections import deque
from types import MappingProxyType

from worldsmith.compiler import geometry as geo
from worldsmith.grammar import scene as sc

MODEL = "worldsmith.nav/0.1"
NOTE = ("an approximation under ADR 0008 (coarse walk graph, cylinder body, derived jump reach);"
        " never a proof of solvability or unsolvability; actual runs decide (SPEC section 7)")
MODES = ("walk", "jump")
EPS = 0.001  # the canonical precision (ADR 0002), as in ADR 0007

_MEASURE = "bundles/m0/character/measure.json (default R15, measured once in Studio, n = 1)"

# --- the config block: every parameter, with its source --------------------------------------
# measure.json holds Studio's single-precision values (196.1999969..., 7.199999809...): these are
# the same values as written in Studio.
_G = 196.2                 # measure.json gravity
_JH = 7.2                  # measure.json jump_height
_WS = 16.0                 # measure.json walk_speed


def _p(value, unit, basis, source):
    return MappingProxyType({"value": value, "unit": unit, "basis": basis, "source": source})


PARAMS = MappingProxyType({
    "cell": _p(1.0, "stud", "design",
               "grid spacing on X and Z; F1 part faces sit on whole or half studs (ADR 0008)"),
    "body_height": _p(5.19, "stud", "measured",
                      f"bbox[1] = 5.187 in {_MEASURE}, rounded up to 0.01"),
    "body_radius": _p(2.09, "stud", "derived",
                      f"half the horizontal diagonal of the measured bbox 4.000 x 1.198 (bbox[0], "
                      f"bbox[2] in {_MEASURE}) = 2.088, rounded up: a cylinder that holds the "
                      "character in any facing"),
    "step_up": _p(1.0, "stud", "assumed",
                  "not measured in M0 (P0 never stepped up or jumped); set to ADR 0007 FLOOR_BAND"),
    "walk_speed": _p(_WS, "stud/s", "measured", f"walk_speed (Humanoid.WalkSpeed) in {_MEASURE}"),
    "jump_height": _p(_JH, "stud", "measured",
                      f"jump_height (Humanoid.JumpHeight; UseJumpPower false) in {_MEASURE}"),
    "gravity": _p(_G, "stud/s^2", "measured", f"gravity (Workspace.Gravity) in {_MEASURE}"),
    "jump_rise": _p(_JH, "stud", "derived",
                    "highest landing above take-off = jump_height (ballistic apex); the one "
                    "measured jump rose 7.30 (jump.apex_rise, n = 1)"),
    "jump_reach_flat": _p(round(_WS * 2 * math.sqrt(2 * _JH / _G), 3), "stud", "derived",
                          "walk_speed x 2 sqrt(2 jump_height / gravity), centre to centre on a "
                          "level jump; the one measured jump went 8.75 (jump.horizontal, n = 1)"),
})

_CELL = PARAMS["cell"]["value"]
_H = PARAMS["body_height"]["value"]
_R = PARAMS["body_radius"]["value"]
_STEP = PARAMS["step_up"]["value"]
_RISE = PARAMS["jump_rise"]["value"]
_V0 = math.sqrt(2 * _G * _JH)                 # take-off vertical speed
_SAMPLE_XZ = 0.5                              # arc samples: at most this far apart horizontally
_SAMPLE_Y = 2.0                               # ... and vertically (well under body_height)
_N4 = ((-1, 0), (0, -1), (0, 1), (1, 0))


def reach(dh: float) -> float | None:
    """Derived horizontal reach (centre to centre) of a jump landing `dh` studs above take-off,
    at the measured walk speed; None when `dh` is above the jump rise."""
    t = _flight_time(dh)
    return None if t is None else _WS * t


def _flight_time(dh: float) -> float | None:
    disc = _V0 * _V0 - 2 * _G * dh
    if dh > _RISE + 1e-9 or disc < 0:
        return None
    return (_V0 + math.sqrt(max(disc, 0.0))) / _G


def _num(x: float):
    x = round(x, 3) + 0.0
    return int(x) if x == int(x) else x


# --- parts and grid ----------------------------------------------------------------------------

class _Part:
    __slots__ = ("eid", "name", "etype", "lo", "hi", "solid")

    def __init__(self, eid, name, etype, lo, hi, solid):
        self.eid, self.name, self.etype, self.lo, self.hi, self.solid = eid, name, etype, lo, hi, solid


def _parts(scene: dict) -> list[_Part]:
    norm = sc.normalize_scene(scene)
    by_id = {e["id"]: e for e in norm["entities"]}
    out = []
    for ent in norm["entities"]:
        try:
            boxes = geo.entity_parts(ent, by_id)
        except (KeyError, TypeError, IndexError, ValueError):
            continue  # a dangling reference: the grammar layer reports it
        for b in boxes:
            size = list(b["size"])
            if b.get("yaw") in (90, -90, 270):
                size[0], size[2] = size[2], size[0]
            lo = tuple(b["pos"][k] - size[k] / 2 for k in range(3))
            hi = tuple(b["pos"][k] + size[k] / 2 for k in range(3))
            out.append(_Part(ent["id"], b["name"], ent["type"], lo, hi, bool(b["can_collide"])))
    return out


def _hdist(x: float, z: float, p: _Part) -> float:
    dx = max(p.lo[0] - x, 0.0, x - p.hi[0])
    dz = max(p.lo[2] - z, 0.0, z - p.hi[2])
    return math.hypot(dx, dz)


class _Grid:
    """Columns, surfaces and nodes for one set of open gates (`opened` returns a copy with more
    gates open, recomputing only the columns near them)."""

    def __init__(self, scene: dict, parts: list[_Part]):
        room = scene["bounds"]
        self.x0, self.z0 = room["min"][0], room["min"][2]
        self.nx = max(1, math.ceil((room["max"][0] - self.x0) / _CELL - EPS))
        self.nz = max(1, math.ceil((room["max"][2] - self.z0) / _CELL - EPS))
        self.solids = [p for p in parts if p.solid]
        self.off: frozenset = frozenset()       # indices of open gates' parts (not solid)
        near: dict = {}                         # col -> solids within reach of the body there
        cover: dict = {}                        # col -> solids whose footprint holds the centre
        pad = _R + _CELL * 0.75   # covers any point of a column within body_radius of a part
        for idx, p in enumerate(self.solids):
            for col in self._cols(p.lo[0] - pad, p.hi[0] + pad, p.lo[2] - pad, p.hi[2] + pad):
                near.setdefault(col, []).append(idx)
            for col in self._cols(p.lo[0] - EPS - _CELL / 2, p.hi[0] + EPS - _CELL / 2,
                                  p.lo[2] - EPS - _CELL / 2, p.hi[2] + EPS - _CELL / 2):
                cover.setdefault(col, []).append(idx)
        self.near, self.cover = near, cover
        self.surf: dict = {}                    # col -> [(top, on, hazard, valid)], low to high
        for col in sorted(cover):
            self._update(col)

    def opened(self, gate_ids) -> "_Grid":
        g = copy.copy(self)
        idx = {i for i, p in enumerate(self.solids) if p.etype == "Gate" and p.eid in gate_ids}
        g.off = self.off | idx
        g.surf = dict(self.surf)
        cols = sorted({c for c, lst in self.near.items() if idx.intersection(lst)})
        for col in cols:
            g._update(col)
        return g

    def _update(self, col):
        x, z = self.centre(col)
        here = [self.solids[i] for i in self.cover.get(col, ()) if i not in self.off]
        here = [p for p in here if p.lo[0] - EPS <= x <= p.hi[0] + EPS
                and p.lo[2] - EPS <= z <= p.hi[2] + EPS]
        if not here or all(p.etype == "KillFloor" for p in here):
            self.surf.pop(col, None)   # nothing but hazards: never a node, never a landing
            return
        here.sort(key=lambda p: (p.lo[1], p.hi[1], p.eid, p.name))
        blocks = []   # [lo, hi, [parts]]
        for p in here:
            if blocks and p.lo[1] <= blocks[-1][1] + EPS:
                blocks[-1][1] = max(blocks[-1][1], p.hi[1])
                blocks[-1][2].append(p)
            else:
                blocks.append([p.lo[1], p.hi[1], [p]])
        levels = []
        for k, (_, top, ps) in enumerate(blocks):
            tops = sorted((p for p in ps if p.hi[1] >= top - EPS), key=lambda p: (p.eid, p.name))
            hazard = any(p.etype == "KillFloor" for p in tops)
            ceiling = blocks[k + 1][0] if k + 1 < len(blocks) else math.inf
            valid = (not hazard and ceiling >= top + _H - EPS and self._fits(x, z, top, col))
            levels.append((top, tops[0].eid, hazard, valid))
        self.surf[col] = levels

    def _cols(self, x_lo, x_hi, z_lo, z_hi):
        i0 = max(0, math.floor((x_lo - self.x0) / _CELL))
        i1 = min(self.nx - 1, math.floor((x_hi - self.x0) / _CELL))
        k0 = max(0, math.floor((z_lo - self.z0) / _CELL))
        k1 = min(self.nz - 1, math.floor((z_hi - self.z0) / _CELL))
        for ix in range(i0, i1 + 1):
            for iz in range(k0, k1 + 1):
                yield ix, iz

    def col_of(self, x, z):
        ix = math.floor((x - self.x0) / _CELL)
        iz = math.floor((z - self.z0) / _CELL)
        if 0 <= ix < self.nx and 0 <= iz < self.nz:
            return ix, iz
        return None

    def centre(self, col):
        return self.x0 + (col[0] + 0.5) * _CELL, self.z0 + (col[1] + 0.5) * _CELL

    def _hits(self, x, z, foot, col, low):
        """Does a body standing with its feet at `foot` at (x, z) hit a solid reaching above
        foot + low (KillFloor: above foot)?"""
        for i in self.near.get(col, ()):
            if i in self.off:
                continue
            p = self.solids[i]
            floor = foot + (EPS if p.etype == "KillFloor" else low + EPS)
            if p.hi[1] > floor and p.lo[1] < foot + _H - EPS and _hdist(x, z, p) < _R - EPS:
                return True
        return False

    def _fits(self, x, z, top, col):
        return not self._hits(x, z, top, col, _STEP)

    def arc_clear(self, a, ta, b, tb) -> bool:
        """The body's swept arc from node a (feet at ta) to node b (feet at tb) hits no solid."""
        T = _flight_time(tb - ta)
        if T is None:
            return False
        (xa, za), (xb, zb) = self.centre(a), self.centre(b)
        d = math.hypot(xb - xa, zb - za)
        apex = ta + _V0 * _V0 / (2 * _G)
        n = max(4, math.ceil(d / _SAMPLE_XZ), math.ceil((2 * apex - ta - tb) / _SAMPLE_Y))
        for k in range(1, n):
            s = k / n
            tau = T * s
            x, z = xa + (xb - xa) * s, za + (zb - za) * s
            foot = ta + _V0 * tau - 0.5 * _G * tau * tau
            col = self.col_of(x, z)
            if col is None or self._hits(x, z, foot, col, 0.0):
                return False
        return True

    def touches(self, node, part: _Part) -> bool:
        col, k = node
        top = self.surf[col][k][0]
        x, z = self.centre(col)
        return (_hdist(x, z, part) < _R - EPS and part.hi[1] > top + EPS
                and part.lo[1] < top + _H - EPS)


# --- search ------------------------------------------------------------------------------------

def _walk_target(grid: _Grid, col, top):
    """The level a body walking into `col` from feet height `top` lands on, or None."""
    levels = grid.surf.get(col)
    if not levels:
        return None
    best = None
    for k, (t, _, _, _) in enumerate(levels):
        if t <= top + _STEP + EPS:
            best = k
    if best is None or not levels[best][3]:
        return None
    return best


def _search(grid: _Grid, start, mode: str):
    """Reachable nodes from `start`: {node: (parent, via)} in discovery order."""
    parent = {start: (None, "start")}
    queue = deque([start])
    rims_done: set = set()
    while True:
        while queue:
            node = queue.popleft()
            col, k = node
            top = grid.surf[col][k][0]
            for dx, dz in _N4:
                nc = (col[0] + dx, col[1] + dz)
                j = _walk_target(grid, nc, top)
                if j is None:
                    continue
                nxt = (nc, j)
                if nxt not in parent:
                    parent[nxt] = (node, "walk" if abs(grid.surf[nc][j][0] - top) <= _STEP + EPS
                                   else "drop")
                    queue.append(nxt)
        if mode != "jump":
            break
        added = _jump_edges(grid, parent, rims_done)
        if not added:
            break
        queue.extend(added)
    return parent


def _jump_edges(grid: _Grid, parent: dict, rims_done: set) -> list:
    """Jump edges from reached rim nodes not tried yet to unreached nodes; records them in
    `parent` and returns the new nodes in a fixed order."""
    rims = []
    for node in parent:
        if node in rims_done:
            continue
        rims_done.add(node)
        col, k = node
        top = grid.surf[col][k][0]
        if _is_rim(grid, col, top):
            rims.append((node, top))
    if not rims:
        return []
    low_rim = min(top for _, top in rims)
    cands = []    # unreached nodes a rim could land on: (x, z, top, node), sorted by x
    for col in sorted(grid.surf):
        for j, (t2, _, _, ok) in enumerate(grid.surf[col]):
            if ok and (col, j) not in parent and t2 - low_rim <= _RISE + 1e-9:
                x, z = grid.centre(col)
                cands.append((x, z, t2, (col, j)))
    if not cands:
        return []
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    xs = [c[0] for c in cands]
    low = min(c[2] for c in cands)
    added = []
    for node, top in rims:
        radius = reach(min(low - top, 0.0)) or 0.0
        xa, za = grid.centre(node[0])
        for x, z, t2, tgt in cands[bisect.bisect_left(xs, xa - radius - EPS):
                                   bisect.bisect_right(xs, xa + radius + EPS)]:
            if tgt in parent or abs(z - za) > radius + EPS:
                continue
            r = reach(t2 - top)
            d = math.hypot(x - xa, z - za)
            if r is None or d > r + EPS or d < EPS:
                continue
            if grid.arc_clear(node[0], top, tgt[0], t2):
                parent[tgt] = (node, "jump")
                added.append(tgt)
    return added


def _is_rim(grid: _Grid, col, top) -> bool:
    for dx, dz in _N4:
        nc = (col[0] + dx, col[1] + dz)
        j = _walk_target(grid, nc, top)
        if j is None or abs(grid.surf[nc][j][0] - top) > _STEP + EPS:
            return True
    return False


# --- entry points ------------------------------------------------------------------------------

def _setup(scene: dict):
    """(parts, spawn part, GOAL_TOUCHED trigger parts, {gate id: lever zone part}) or None."""
    parts = _parts(scene)
    spawns = [p for p in parts if p.etype == "Spawn"]
    goals = [p for p in parts if p.etype == "Trigger" and _event(scene, p.eid) == "GOAL_TOUCHED"]
    if len(spawns) != 1 or not goals:
        return None
    ents = {e.get("id"): e for e in scene.get("entities", []) if isinstance(e, dict)}
    zones = {p.eid: p for p in parts if p.etype == "Lever" and p.name == "zone"}
    gates = {}
    for eid, e in sorted(ents.items()):
        if e.get("type") == "Gate":
            gates[eid] = zones.get(e.get("opens_on"))   # None: a gate that never opens
    return parts, spawns[0], goals, gates


def _event(scene, eid):
    for e in scene.get("entities", []):
        if isinstance(e, dict) and e.get("id") == eid:
            return e.get("event")
    return None


def _node_out(grid, node, via):
    col, k = node
    top, on, _, _ = grid.surf[col][k]
    x, z = grid.centre(col)
    return {"pos": [_num(x), _num(top), _num(z)], "on": on, "via": via}


def _navigate(scene: dict, mode: str, setup, grids: dict):
    """One mode. `grids` caches a grid per set of open gates (shared by both modes)."""
    parts, spawn, goals, gates = setup
    opened: set = set()
    while True:
        key = frozenset(opened)
        if key not in grids:
            grids[key] = grids[frozenset()].opened(key) if key else _Grid(scene, parts)
        grid = grids[key]
        start = None
        sx, sz = (spawn.lo[0] + spawn.hi[0]) / 2, (spawn.lo[2] + spawn.hi[2]) / 2
        col = grid.col_of(sx, sz)
        if col is not None:
            for k, (t, _, _, ok) in enumerate(grid.surf.get(col, ())):
                if ok and abs(t - spawn.hi[1]) <= EPS:
                    start = (col, k)
        parent = _search(grid, start, mode) if start is not None else {}
        newly = sorted(g for g, zone in gates.items()
                       if g not in opened and zone is not None
                       and any(grid.touches(n, zone) for n in parent))
        if not newly:
            break
        opened.update(newly)

    goal_nodes = [n for n in parent if any(grid.touches(n, g) for g in goals)]
    out = {
        "result": "CONNECTED" if goal_nodes else "DISCONNECTED",
        "basis": "derived" if mode == "jump" else "approximation",
        "path": None,
        "gates_opened": sorted(opened),
        "gates_closed": sorted(g for g in gates if g not in opened),
        "reached_on": sorted({grid.surf[c][k][1] for c, k in parent}),
        "nodes": len(parent),
        "closest": None,
    }
    if goal_nodes:
        path, n = [], goal_nodes[0]
        while n is not None:
            prev, via = parent[n]
            path.append(_node_out(grid, n, via))
            n = prev
        out["path"] = path[::-1]
    elif parent:
        def dist(n):
            x, z = grid.centre(n[0])
            return min(_hdist(x, z, g) for g in goals)
        best = min(parent, key=dist)   # min keeps the first (discovery order) on ties
        node = _node_out(grid, best, parent[best][1])
        out["closest"] = {"pos": node["pos"], "on": node["on"],
                          "distance_to_goal": _num(dist(best))}
    return out


def navigate(scene: dict, mode: str = "walk") -> dict | None:
    """One mode's verdict under the ADR 0008 approximation, or None when the scene has no single
    spawn or no GOAL_TOUCHED trigger. Never a proof."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    res = _both(_key(scene))
    return None if res is None else copy.deepcopy(res[MODES.index(mode)])


def graph(scene: dict) -> dict | None:
    """The validator's `graph` field: both modes, the parameters and the caveat; None when the
    scene cannot be navigated (no single spawn, no GOAL_TOUCHED trigger)."""
    res = _both(_key(scene))
    if res is None:
        return None
    return copy.deepcopy({"model": MODEL, "note": NOTE,
                          "params": {k: v["value"] for k, v in PARAMS.items()},
                          "walk": res[0], "jump": res[1]})


# A small cache keyed on the canonical scene JSON: the result depends only on that.
_CACHE: dict = {}
_CACHE_MAX = 64


def _key(scene: dict) -> str:
    return sc.canonical_json(scene)


def _both(key: str):
    """(walk, jump) for one canonical scene, or None; cached."""
    if key in _CACHE:
        return _CACHE[key]
    scene = json.loads(key)
    setup = _setup(scene)
    if setup is None:
        res = None
    else:
        grids: dict = {}
        res = (_navigate(scene, "walk", setup, grids), _navigate(scene, "jump", setup, grids))
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = res
    return res
