"""WS-02: canonical JSON, canonical_hash stability and sensitivity, round trip, HASHES.md."""

import copy
import json
import random
import re
import subprocess
import sys

import pytest

from _repo import REPO_ROOT
from _scenes import F1A, FIXTURES, SCENES_ROOT, entity, get_at, leaf_paths, raw, with_value
from worldsmith.grammar import scene as sc

_IDS = lambda p: f"{p.parent.name}/{p.name}"  # noqa: E731


# --- canonical JSON format ---------------------------------------------------

def test_float_precision_is_three_decimals():
    assert sc.FLOAT_DECIMALS == 3


@pytest.mark.parametrize("value,text", [
    (7, "7"), (7.0, "7"), (7.0000001, "7"), (6.9999999, "7"), (7.25, "7.25"),
    (7.2504, "7.25"), (0.1 + 0.2, "0.3"), (-0.0, "0"), (-0.0001, "0"), (-1.5, "-1.5"),
    (256, "256"), (0.001, "0.001"), (0.0004, "0"), (123.456789, "123.457"),
])
def test_number_formatting(value, text):
    assert sc.canonical_json({"v": value}) == '{"v":%s}' % text


def test_canonical_json_has_sorted_keys_and_no_whitespace():
    out = sc.canonical_json({"b": [1, 2.5], "a": {"d": "x", "c": 1}})
    assert out == '{"a":{"c":1,"d":"x"},"b":[1,2.5]}'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_refuses_non_finite(bad):
    with pytest.raises(ValueError):
        sc.canonical_json({"v": bad})


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_canonical_json_of_fixture_is_compact_ascii(path):
    out = sc.canonical_json(raw(path))
    assert out.isascii()
    assert " " not in out and "\n" not in out
    assert out == json.dumps(json.loads(out), sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_canonical_hash_is_sha256_hex(path):
    h = sc.canonical_hash(raw(path))
    assert re.fullmatch(r"[0-9a-f]{64}", h)
    import hashlib
    assert h == hashlib.sha256(sc.canonical_json(raw(path)).encode("utf-8")).hexdigest()


# --- round trip ---------------------------------------------------------------

@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_round_trip_load_canonical_load(path, tmp_path):
    first = sc.load_scene(path)
    text = sc.canonical_json(first)
    out = tmp_path / "canon.json"
    out.write_text(text, encoding="utf-8")
    second = sc.load_scene(out)
    assert second == sc.normalize_scene(first)
    assert sc.canonical_json(second) == text
    assert sc.canonical_hash(second) == sc.canonical_hash(first)


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_normalize_is_idempotent(path):
    once = sc.normalize_scene(raw(path))
    assert sc.normalize_scene(once) == once


# --- hash stability -----------------------------------------------------------

def _reverse_keys(node):
    if isinstance(node, dict):
        return {k: _reverse_keys(node[k]) for k in reversed(list(node))}
    if isinstance(node, list):
        return [_reverse_keys(v) for v in node]
    return node


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_hash_stable_across_key_order(path):
    scene = raw(path)
    assert sc.canonical_hash(_reverse_keys(scene)) == sc.canonical_hash(scene)


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_hash_stable_across_whitespace(path):
    scene = raw(path)
    for text in (json.dumps(scene), json.dumps(scene, indent=4),
                 json.dumps(scene, separators=(",", ":"))):
        assert sc.canonical_hash(sc.parse_scene_text(text)) == sc.canonical_hash(scene)


@pytest.mark.parametrize("path", FIXTURES, ids=_IDS)
def test_hash_stable_across_entity_and_protected_order(path):
    scene = raw(path)
    shuffled = copy.deepcopy(scene)
    rng = random.Random(1)
    rng.shuffle(shuffled["entities"])
    rng.shuffle(shuffled["protected"])
    assert shuffled != scene
    assert sc.canonical_hash(shuffled) == sc.canonical_hash(scene)


@pytest.mark.parametrize("text", ["7", "7.0", "7.000", "7.0000001", "6.9999999", "7e0", "70e-1"])
def test_hash_stable_across_float_formatting(text):
    base = F1A.read_text(encoding="utf-8")
    old = '"thickness": 1,'
    assert base.count(old) == 1, "fixture layout changed; update this test"
    ref = sc.canonical_hash(sc.parse_scene_text(base.replace(old, '"thickness": 7,')))
    variant = sc.parse_scene_text(base.replace(old, f'"thickness": {text},'))
    assert sc.canonical_hash(variant) == ref


@pytest.mark.parametrize("value,same", [(6, True), (6.0004, True), (6.001, False), (6.5, False)])
def test_hash_resolution_is_the_chosen_precision(value, same):
    scene = raw(F1A)
    entity(scene, "bridge_1")["length"] = 6
    ref = sc.canonical_hash(scene)
    entity(scene, "bridge_1")["length"] = value
    assert (sc.canonical_hash(scene) == ref) is same


def _mutated(value):
    if isinstance(value, (int, float)):
        return value + 1
    return value + "x" if not value.endswith("x") else value[:-1] + "y"


@pytest.mark.parametrize("path", [p for p in leaf_paths(raw(F1A))],
                         ids=lambda p: "/".join(map(str, p)))
def test_hash_changes_when_any_value_changes(path):
    scene = raw(F1A)
    changed = with_value(scene, path, _mutated(get_at(scene, path)))
    assert sc.canonical_hash(changed) != sc.canonical_hash(scene)


def test_hash_changes_when_an_entity_is_added_or_removed():
    scene = raw(F1A)
    h = sc.canonical_hash(scene)
    fewer = copy.deepcopy(scene)
    fewer["entities"] = [e for e in fewer["entities"] if e["id"] != "decor_1"]
    assert sc.canonical_hash(fewer) != h


# --- HASHES.md ----------------------------------------------------------------

def test_hashes_md_is_generated_and_up_to_date():
    md = (SCENES_ROOT / "HASHES.md").read_text(encoding="utf-8")
    assert md == sc.render_hashes_md(SCENES_ROOT)


def test_hashes_md_lists_every_fixture_with_its_hash():
    md = (SCENES_ROOT / "HASHES.md").read_text(encoding="utf-8")
    for path in FIXTURES:
        rel = path.relative_to(SCENES_ROOT).as_posix()
        assert rel in md
        assert sc.canonical_hash(sc.load_scene(path)) in md


def test_hash_script_check_mode_passes():
    script = REPO_ROOT / "scripts" / "scene_hashes.py"
    res = subprocess.run([sys.executable, str(script), "--check"],
                         capture_output=True, text=True, cwd=REPO_ROOT)
    assert res.returncode == 0, res.stdout + res.stderr


def test_hash_script_check_mode_detects_a_stale_file(tmp_path):
    import shutil
    root = tmp_path / "scenes"
    shutil.copytree(SCENES_ROOT, root)
    (root / "HASHES.md").write_text("stale\n", encoding="utf-8")
    script = REPO_ROOT / "scripts" / "scene_hashes.py"
    res = subprocess.run([sys.executable, str(script), "--check", "--root", str(root)],
                         capture_output=True, text=True)
    assert res.returncode != 0
