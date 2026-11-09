"""WS-03: template compiler, expected tree, read-back comparator (SPEC sections 6 and 8).

CPU only. The live half (build F1-A in Studio and compare its read-back) is recorded in
docs/HANDOFF.md and bundles/ws03/, not here.
"""

import copy
import json
import math
import os
import re
import subprocess
import sys

import pytest

from _repo import REPO_ROOT
from _scenes import F1A, F1A_FIX, F1E, FIXTURES, ENTITY_TYPES, entity, raw
from worldsmith.compiler import build, geometry, templates
from worldsmith.grammar.scene import SceneError, canonical_hash

SNAP = REPO_ROOT / "tests" / "snapshots" / "ws03"
TEMPLATES = REPO_ROOT / "grammar" / "templates"
EVENT_SCRIPTS = ["ws_lib", "lever", "gate", "trigger", "kill_floor"]
EVENTS = ["LEVER_ACTIVATED", "GATE_OPENED", "BRIDGE_CROSSED", "KILL_FLOOR_TOUCHED", "GOAL_TOUCHED"]


def _ids(paths):
    return [p.parent.name + "/" + p.name for p in paths]


def _snap_name(path):
    return f"{path.parent.name}_{path.stem}.build.luau"


# --- template inventory and the no-free-text rule ------------------------------------------

def test_one_template_per_entity_type_plus_fixed_parts():
    for t in ENTITY_TYPES:
        assert (TEMPLATES / f"{t}.luau").is_file(), t
    for name in ["header", "footer", "readback", "arm"]:
        assert (TEMPLATES / f"{name}.luau").is_file(), name
    for name in EVENT_SCRIPTS:
        assert (TEMPLATES / "events" / f"{name}.luau").is_file(), name


def _all_templates():
    return sorted(TEMPLATES.rglob("*.luau"))


@pytest.mark.parametrize("path", _all_templates(), ids=lambda p: p.relative_to(TEMPLATES).as_posix())
def test_template_is_ascii_and_every_slot_is_typed(path):
    text = path.read_text(encoding="utf-8")
    assert text.isascii()
    assert "\t" not in text and "\r" not in text
    slots = templates.placeholders(text)
    # Every {{...}} is a typed slot; nothing else uses braces-braces.
    assert text.count("{{") == text.count("}}") == len(slots)
    for kind, name in slots:
        assert kind in templates.SLOT_KINDS, (kind, name)
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name)
        if kind == "enum":
            assert name in templates.ENUMS, name
    assert "str" not in {k for k, _ in slots} and "text" not in {k for k, _ in slots}


def test_slot_kinds_are_closed_and_hold_no_free_text():
    assert templates.SLOT_KINDS == {"num", "int", "id", "enum", "hash", "nonce"}
    for name, allowed in templates.ENUMS.items():
        for v in allowed:
            assert re.fullmatch(r"[A-Za-z0-9_+-]{1,32}", v), (name, v)


@pytest.mark.parametrize("name", EVENT_SCRIPTS + ["readback"])
def test_fixed_scripts_have_no_slots(name):
    path = TEMPLATES / "events" / f"{name}.luau" if name != "readback" else TEMPLATES / "readback.luau"
    assert templates.placeholders(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("value", [
    'a"); game:Destroy() --', "plat a", "Plat_a", "a\nb", "", "x" * 33, "a.b", "a]==]", 7, None, True,
])
def test_id_slot_rejects_anything_but_an_id(value):
    with pytest.raises(templates.TemplateError):
        templates.render("local x = {{id:id}}", {"id": value})


@pytest.mark.parametrize("value", ["CRATE", "crate ", "teleport", 'lamp"', 1])
def test_enum_slot_rejects_values_outside_its_set(value):
    with pytest.raises(templates.TemplateError):
        templates.render("local p = {{enum:palette}}", {"palette": value})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "1", "1+1", None, [1]])
def test_num_slot_rejects_non_numbers(value):
    with pytest.raises(templates.TemplateError):
        templates.render("local n = {{num:n}}", {"n": value})


@pytest.mark.parametrize("value", [1.5, True, "1", 10**9])
def test_int_slot_rejects_non_ints(value):
    with pytest.raises(templates.TemplateError):
        templates.render("local n = {{int:n}}", {"n": value})


@pytest.mark.parametrize("kind,value", [
    ("hash", "abc"), ("hash", "G" * 64), ("hash", "a" * 63 + "\n"),
    ("nonce", "short"), ("nonce", "ABCDEF0123"), ("nonce", "0" * 33), ("nonce", "0123456789|x"),
])
def test_hash_and_nonce_slots_are_strict_hex(kind, value):
    with pytest.raises(templates.TemplateError):
        templates.render("local v = {{%s:v}}" % kind, {"v": value})


def test_render_requires_every_slot_and_no_extras():
    with pytest.raises(templates.TemplateError):
        templates.render("{{num:a}} {{num:b}}", {"a": 1})
    with pytest.raises(templates.TemplateError):
        templates.render("{{num:a}}", {"a": 1, "b": 2})
    with pytest.raises(templates.TemplateError):
        templates.render("{{str:a}}", {"a": "x"})


@pytest.mark.parametrize("value,out", [
    (7, "7"), (7.0, "7"), (-0.0, "0"), (1.23456, "1.235"), (-2.5, "-2.5"), (0.0004, "0"),
    (100.1, "100.1"),
])
def test_numbers_render_as_fixed_point(value, out):
    assert templates.render("{{num:n}}", {"n": value}) == out


def test_ids_enums_hashes_render_as_quoted_literals():
    h = "ab" * 32
    got = templates.render("{{id:i}} {{enum:palette}} {{hash:h}} {{nonce:n}} {{int:k}}",
                           {"i": "gate_1", "palette": "lamp", "h": h, "n": "0123abcd", "k": 3})
    assert got == f'"gate_1" "lamp" "{h}" "0123abcd" 3'


# --- compile: snapshots and determinism ----------------------------------------------------

@pytest.mark.parametrize("path", FIXTURES, ids=_ids(FIXTURES))
def test_build_script_matches_reviewed_snapshot(path):
    got = build.compile_build(raw(path))
    snap = SNAP / _snap_name(path)
    if os.environ.get("WS_UPDATE_SNAPSHOTS") == "1":
        snap.write_text(got, encoding="utf-8")
    assert snap.is_file(), f"missing snapshot {snap}; run WS_UPDATE_SNAPSHOTS=1 make test and review it"
    assert got == snap.read_text(encoding="utf-8"), (
        f"{snap.name} changed; review the diff, then WS_UPDATE_SNAPSHOTS=1 make test")


@pytest.mark.parametrize("path", FIXTURES, ids=_ids(FIXTURES))
def test_build_script_shape(path):
    scene = raw(path)
    lua = build.compile_build(scene)
    assert lua.isascii()
    assert f'local WS_HASH = "{canonical_hash(scene)}"' in lua
    assert f"local WS_VERSION = {scene['version']}" in lua
    assert "\"RunService\"):IsRunning()" in lua.split("local WS_HASH", 1)[0]   # refuses play mode first
    assert 'root.Name = "Worldsmith"' in lua
    assert lua.index("old:Destroy()") < lua.index("root.Parent = workspace")  # swap at the end
    assert lua.rstrip().endswith('print("WS_BUILD_OK|" .. WS_HASH .. "|" .. tostring(#root:GetDescendants()))')
    for e in scene["entities"]:
        if e["type"] != "Gap":
            assert f'"{e["id"]}"' in lua, e["id"]


@pytest.mark.parametrize("path", FIXTURES, ids=_ids(FIXTURES))
def test_compile_is_deterministic_and_order_independent(path):
    scene = raw(path)
    a = build.compile_build(scene)
    shuffled = copy.deepcopy(scene)
    shuffled["entities"].reverse()
    shuffled["protected"].reverse()
    assert build.compile_build(shuffled) == a
    assert build.compile_build(copy.deepcopy(scene)) == a


def test_compile_is_identical_across_processes():
    code = ("import json,sys;from worldsmith.compiler import build;"
            "print(build.compile_build(json.load(open(sys.argv[1]))), end='')")
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.add(subprocess.run([sys.executable, "-c", code, str(F1A)], env=env, check=True,
                                capture_output=True, text=True).stdout)
    assert len(outs) == 1
    assert outs.pop() == build.compile_build(raw(F1A))


def test_compile_refuses_an_invalid_scene():
    scene = raw(F1A)
    entity(scene, "decor_1")["id"] = 'x"); workspace:ClearAllChildren() --'
    with pytest.raises(SceneError):
        build.compile_build(scene)
    scene = raw(F1A)
    entity(scene, "bridge_1")["length"] = 1e9
    with pytest.raises(SceneError):
        build.compile_build(scene)


def test_origin_is_a_number_only_slot():
    lua = build.compile_build(raw(F1A), origin=(0, 100, 0))
    assert "local WS_ORIGIN = Vector3.new(0, 100, 0)" in lua
    with pytest.raises(templates.TemplateError):
        build.compile_build(raw(F1A), origin=(0, "100", 0))
    with pytest.raises(ValueError):
        build.compile_build(raw(F1A), origin=(0, 100))


def test_event_sources_are_embedded_verbatim_once():
    lua = build.compile_build(raw(F1A))
    for name in EVENT_SCRIPTS:
        src = (TEMPLATES / "events" / f"{name}.luau").read_text(encoding="utf-8")
        assert "]==]" not in src
        assert f'WS_SRC["{name}"] = [==[\n{src}]==]' in lua
    assert "--@WS_EVENT_SOURCES@" not in lua


def test_fix_build_differs_only_in_bridge_and_hash_lines():
    a = build.compile_build(raw(F1A)).splitlines()
    f = build.compile_build(raw(F1A_FIX)).splitlines()
    assert len(a) == len(f)
    diff = [(x, y) for x, y in zip(a, f) if x != y]
    assert diff and all("WS_HASH" in x or '"bridge_1"' in x for x, _ in diff), diff


# --- expected tree (geometry conventions, ADR 0002 and 0003) -------------------------------

def _nodes(tree):
    return {n["path"]: n for n in tree["nodes"]}


def test_expected_tree_f1a_geometry():
    scene = raw(F1A)
    tree = geometry.expected_tree(scene)
    n = _nodes(tree)
    assert tree["roots"] == 1
    assert tree["root"] == {"class": "Folder", "attrs": {
        "ws_schema": "worldsmith.scene/0.1", "ws_scene_hash": canonical_hash(scene),
        "ws_version": 1, "ws_origin": [0, 0, 0]}}
    # Bridge: anchored at the corridor's far edge (z 36), top flush with its top (y 0).
    assert (n["bridge_1"]["pos"], n["bridge_1"]["size"]) == ([0, -0.5, 39], [6, 1, 6])
    # BRIDGE_CROSSED: first 2 studs of plat_b (z 52..54), full plat_b width, 6 high (ADR 0003).
    assert (n["trig_bridge"]["pos"], n["trig_bridge"]["size"]) == ([0, 3, 53], [16, 6, 2])
    assert n["trig_bridge"]["can_collide"] is False
    assert n["trig_bridge"]["attrs"]["ws_event"] == "BRIDGE_CROSSED"
    # Goal pad on plat_b's top centre; its trigger from the pad's top up by 6.
    assert (n["goal"]["pos"], n["goal"]["size"]) == ([0, 0.5, 60], [4, 1, 4])
    assert (n["trig_goal"]["pos"], n["trig_goal"]["size"]) == ([0, 4, 60], [4, 6, 4])
    assert n["spawn"]["class"] == "SpawnLocation"
    assert n["spawn"]["pos"] == [0, 0.1, 6]
    assert n["gate_1"]["can_collide"] is True and n["gate_1"]["attrs"]["ws_lever"] == "lever_1"
    assert n["lever_1"]["class"] == "Model" and n["lever_1"]["attrs"]["ws_active"] is False
    assert {"lever_1/base", "lever_1/handle", "lever_1/zone", "lever_1/handle/prompt",
            "lever_1/ws_event", "gate_1/ws_event", "trig_bridge/ws_event", "trig_goal/ws_event",
            "kill_floor/ws_event", "ws_lib"} <= set(n)
    assert n["lever_1/zone"]["can_collide"] is False
    assert "gap_1" not in n                                  # gaps are not built
    for e in scene["entities"]:
        if e["type"] != "Gap":
            assert n[e["id"]]["attrs"]["ws_id"] == e["id"]
            assert n[e["id"]]["attrs"]["ws_type"] == e["type"]


def test_expected_tree_fix_moves_only_the_bridge():
    a, f = _nodes(geometry.expected_tree(raw(F1A))), _nodes(geometry.expected_tree(raw(F1A_FIX)))
    assert (f["bridge_1"]["pos"], f["bridge_1"]["size"]) == ([0, -0.5, 44], [6, 1, 16])
    assert [p for p in a if a[p] != f[p]] == ["bridge_1"]


def test_expected_descendant_count_matches_nodes():
    tree = geometry.expected_tree(raw(F1A))
    assert geometry.descendant_count(tree) == len(tree["nodes"])


def test_script_source_lengths_are_byte_lengths():
    n = _nodes(geometry.expected_tree(raw(F1A)))
    lib = (TEMPLATES / "events" / "ws_lib.luau").read_bytes()
    assert n["ws_lib"]["source_len"] == len(lib)


def _synthetic(axis):
    """Two platforms joined along `axis`, for bridge and landing geometry."""
    s = raw(F1E)
    sign = 1 if axis[0] == "+" else -1
    a = entity(s, "corridor")
    b = entity(s, "plat_b")
    if axis[1] == "Z":
        a["pos"], a["size"] = [0, -1, 0], [10, 2, 20]
        b["pos"], b["size"] = [0, -1, sign * 30], [16, 2, 20]
    else:
        a["pos"], a["size"] = [0, -1, 0], [20, 2, 10]
        b["pos"], b["size"] = [sign * 30, -1, 0], [20, 2, 16]
    entity(s, "gap_1").update({"axis": axis, "width": 10})
    entity(s, "bridge_1").update({"length": 4, "width": 6, "lateral_offset": 1})
    return s


@pytest.mark.parametrize("axis,bridge,landing", [
    ("+Z", ([1, -0.5, 12], [6, 1, 4]), ([0, 3, 21], [16, 6, 2])),
    ("-Z", ([1, -0.5, -12], [6, 1, 4]), ([0, 3, -21], [16, 6, 2])),
    ("+X", ([12, -0.5, 1], [4, 1, 6]), ([21, 3, 0], [2, 6, 16])),
    ("-X", ([-12, -0.5, 1], [4, 1, 6]), ([-21, 3, 0], [2, 6, 16])),
])
def test_bridge_and_landing_follow_the_gap_axis(axis, bridge, landing):
    n = _nodes(geometry.expected_tree(_synthetic(axis)))
    assert (n["bridge_1"]["pos"], n["bridge_1"]["size"]) == tuple(bridge)
    assert (n["trig_bridge"]["pos"], n["trig_bridge"]["size"]) == tuple(landing)


@pytest.mark.parametrize("towards,yaw,size", [
    ("+Z", 0, [4, 2, 6]), ("-Z", 180, [4, 2, 6]), ("+X", 90, [6, 2, 4]), ("-X", -90, [6, 2, 4]),
])
def test_ramp_yaw_and_local_size(towards, yaw, size):
    s = raw(F1E)
    s["entities"].append({"id": "ramp_1", "type": "Ramp", "pos": [10, 1, 10], "size": [4, 2, 6],
                          "rises_towards": towards})
    n = _nodes(geometry.expected_tree(s))
    assert n["ramp_1"]["class"] == "WedgePart"
    assert n["ramp_1"]["rot"] == [0, yaw, 0]
    assert n["ramp_1"]["size"] == size
    assert "ramp_1" in build.compile_build(s)


# --- read-back parsing and comparison ------------------------------------------------------

def _readback_output(tree, chunk=50, noise=True):
    text = json.dumps(tree)
    parts = [text[i:i + chunk] for i in range(0, len(text), chunk)]
    lines = []
    for i, p in enumerate(parts, 1):
        if noise:
            lines.append("12:00:00.000  some plugin says hi")
        lines.append(f"WS_READBACK|{i}|{len(parts)}|{p}")
    return "\n".join(lines)


def test_parse_readback_reassembles_chunks_among_foreign_lines():
    tree = geometry.expected_tree(raw(F1A))
    assert build.parse_readback(_readback_output(tree)) == tree


def test_parse_readback_rejects_missing_or_duplicate_chunks():
    tree = geometry.expected_tree(raw(F1A))
    lines = _readback_output(tree, noise=False).splitlines()
    with pytest.raises(ValueError):
        build.parse_readback("\n".join(lines[:-1]))
    with pytest.raises(ValueError):
        build.parse_readback("\n".join(lines + lines[:1]))
    with pytest.raises(ValueError):
        build.parse_readback("nothing here")


@pytest.mark.parametrize("prefix", ["10:11:12.345  ", "[OUTPUT] "])
def test_parse_readback_accepts_studio_line_prefixes(prefix):
    # "[OUTPUT] " is how the Studio MCP's run_code returns print() lines (seen live, WS-03).
    tree = {"roots": 0, "root": None, "nodes": []}
    out = "\n".join(prefix + l for l in _readback_output(tree, noise=False).splitlines())
    assert build.parse_readback(out) == tree


def test_live_f1a_readback_matches(tmp_path):
    """The raw read-back recorded live in WS-03 (bundles/ws03, git-ignored) if present."""
    raw_path = REPO_ROOT / "bundles" / "ws03" / "F1-A.readback.raw.txt"
    if not raw_path.is_file():
        pytest.skip("no live read-back recorded in this checkout")
    got = build.parse_readback(raw_path.read_text(encoding="utf-8"))
    assert build.compare_tree(geometry.expected_tree(raw(F1A), origin=(0, 100, 0)), got) == []


def test_compare_identical_is_clean():
    tree = geometry.expected_tree(raw(F1A))
    assert build.compare_tree(tree, copy.deepcopy(tree)) == []


def test_compare_treats_luau_empty_tables_and_float_noise_as_equal():
    exp = geometry.expected_tree(raw(F1A))
    got = copy.deepcopy(exp)
    for node in got["nodes"]:
        if node["attrs"] == {}:
            node["attrs"] = []          # JSONEncode writes an empty table as []
        if "pos" in node:
            node["pos"] = [v + 0.004 for v in node["pos"]]
        if "rot" in node:
            node["rot"] = [-0.0 if v == 0 else v for v in node["rot"]]
    assert build.compare_tree(exp, got) == []


def test_tolerance_is_declared():
    assert build.TOLERANCE == {"pos": 0.01, "size": 0.01, "rot": 0.01}


@pytest.mark.parametrize("mutate,needle", [
    (lambda t: _nodes(t)["bridge_1"]["size"].__setitem__(2, 6.05), "bridge_1"),
    (lambda t: _nodes(t)["goal"]["pos"].__setitem__(1, 0.6), "goal"),
    (lambda t: _nodes(t)["gate_1"].__setitem__("can_collide", False), "gate_1"),
    (lambda t: _nodes(t)["gate_1"].__setitem__("class", "WedgePart"), "gate_1"),
    (lambda t: _nodes(t)["lever_1"]["attrs"].__setitem__("ws_gate", "gate_2"), "lever_1"),
    (lambda t: t["nodes"].remove(_nodes(t)["decor_1"]), "decor_1"),
    (lambda t: t["nodes"].append({"path": "teleport", "class": "Part", "attrs": {}}), "teleport"),
    (lambda t: t["root"]["attrs"].__setitem__("ws_scene_hash", "0" * 64), "ws_scene_hash"),
    (lambda t: t.__setitem__("roots", 2), "roots"),
    (lambda t: _nodes(t)["ws_lib"].__setitem__("source_len", 1), "ws_lib"),
    (lambda t: _nodes(t)["ramp_x"] if False else _nodes(t)["plat_a"]["rot"].__setitem__(1, 90), "plat_a"),
])
def test_compare_detects_injected_mismatches(mutate, needle):
    exp = geometry.expected_tree(raw(F1A))
    got = copy.deepcopy(exp)
    mutate(got)
    errs = build.compare_tree(exp, got)
    assert errs and any(needle in e for e in errs), errs


def test_compare_flags_duplicate_paths():
    exp = geometry.expected_tree(raw(F1A))
    got = copy.deepcopy(exp)
    got["nodes"].append(copy.deepcopy(_nodes(got)["decor_1"]))
    assert any("duplicate" in e for e in build.compare_tree(exp, got))


def test_compare_angles_modulo_360():
    exp = geometry.expected_tree(raw(F1A))
    got = copy.deepcopy(exp)
    _nodes(exp)["plat_a"]["rot"] = [0, 180, 0]
    _nodes(got)["plat_a"]["rot"] = [0, -180, 0]
    assert build.compare_tree(exp, got) == []


# --- arm (run nonce) -----------------------------------------------------------------------

def test_arm_sets_a_validated_nonce():
    lua = build.compile_arm("0123456789abcdef")
    assert 'root:SetAttribute("ws_nonce", "0123456789abcdef")' in lua
    for bad in ("", "XYZ", "0123456789abcdef\nprint(1)", "0123"):
        with pytest.raises(templates.TemplateError):
            build.compile_arm(bad)


def test_readback_script_is_the_fixed_template():
    assert build.compile_readback() == (TEMPLATES / "readback.luau").read_text(encoding="utf-8")


# --- event templates (SPEC section 8) ------------------------------------------------------

def test_every_spec_event_is_emitted_by_exactly_one_event_script():
    sources = {n: (TEMPLATES / "events" / f"{n}.luau").read_text(encoding="utf-8")
               for n in EVENT_SCRIPTS}
    lib = sources.pop("ws_lib")
    assert 'string.format("WS|%s|%s|id=%s|pos=%s|t=%.3f"' in lib
    for ev in EVENTS:
        assert ev in lib                                       # the library's allow-list
    literal = {ev: [n for n, s in sources.items() if f'"{ev}"' in s] for ev in EVENTS}
    assert literal["LEVER_ACTIVATED"] == ["lever"]
    assert literal["GATE_OPENED"] == ["gate"]
    assert literal["KILL_FLOOR_TOUCHED"] == ["kill_floor"]
    # Trigger volumes print the event stored in their ws_event attribute (an enum).
    assert 'GetAttribute("ws_event")' in sources["trigger"]


def test_event_scripts_are_server_scripts_in_the_tree():
    n = _nodes(geometry.expected_tree(raw(F1A)))
    for path in ["lever_1/ws_event", "gate_1/ws_event", "trig_bridge/ws_event",
                 "trig_goal/ws_event", "kill_floor/ws_event"]:
        assert n[path]["class"] == "Script"
    assert n["ws_lib"]["class"] == "ModuleScript"


def test_lever_prints_before_it_sets_the_attribute():
    src = (TEMPLATES / "events" / "lever.luau").read_text(encoding="utf-8")
    assert src.index('lib.emit("LEVER_ACTIVATED"') < src.index('SetAttribute("ws_active", true)')
