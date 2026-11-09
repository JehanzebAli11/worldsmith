"""The M0 attempt runner (WS-04): build, read back, N attempts, classify, bundle, gate.

One episode = one scene version: (1) make sure Studio is stopped (Edit mode); (2) delete and
rebuild `Workspace.Worldsmith` from the full scene with `run_code`; (3) read the tree back and
compare it with the expected tree; then N attempts, each one `run_script_in_play_mode` call
(it starts play, runs the P0 controller as a server script and stops play: stop -> start ->
stop), with a fresh nonce per attempt. The controller enforces `T_attempt` itself (server-side
deadline) and every MCP call carries its own declared timeout (config `tool_timeouts_s`); a
call that times out is a CONNECTOR_ERROR. There is no console polling (ADR 0005).

`run_episode` is deterministic given the call results, so it runs against any `StudioClient`:
a fake in the tests, or `RelayClient`, which stores every request and response under the
bundle's `calls/` folder. In relay mode the MCP calls are made by the operator (the Claude
session holding the Studio MCP tools): `scripts/m0.py step` stops at the first unanswered call
(`Pending`), the operator makes exactly that call with the stored code and timeout, saves the
raw answer with `scripts/m0.py respond`, and steps again. Answered calls are replayed, never
re-sent.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from worldsmith.compiler import build, geometry
from worldsmith.grammar.scene import canonical_hash, load_scene
from worldsmith.runner import p0
from worldsmith.runner.classify import classify_attempt, gap_area
from worldsmith.runner.console import NONCE_RE, new_nonce

EPISODE_SCHEMA = "worldsmith.m0episode/0.1"
SUMMARY_SCHEMA = "worldsmith.m0summary/0.1"
GATE_SCHEMA = "worldsmith.m0gate/0.1"
ROLES = ("before", "after", "smoke")


class Pending(Exception):
    """The relay has no answer yet for call `index`."""

    def __init__(self, index: int, request: dict):
        super().__init__(f"call {index:03d} ({request['tool']}) is waiting for its answer")
        self.index = index
        self.request = request


class RelayError(RuntimeError):
    """A stored request does not match the call the runner makes now (non-deterministic run)."""


class CallTimeout(Exception):
    """A client gave up on a call after its timeout."""


class GateError(ValueError):
    """The two episodes cannot be compared under the declared gate."""


@dataclass
class CallResult:
    text: str
    error: str | None = None          # e.g. "timeout after 30 s"; None if the call answered
    elapsed_s: float | None = None    # what the client measured (relay: an upper bound)
    t_wall: str | None = None         # ISO time the answer was recorded


class StudioClient(Protocol):
    def call(self, tool: str, args: dict, timeout_s: float) -> CallResult: ...


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- relay client -------------------------------------------------------------------------

_CODE_ARGS = {"run_code": "command", "run_script_in_play_mode": "code"}


class RelayClient:
    """Requests and answers as files in `calls/` (NNN.request.json, NNN.code.luau,
    NNN.response.json). Deterministic replay: a stored request must equal the new one."""

    def __init__(self, calls_dir):
        self.dir = Path(calls_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.k = 0

    def call(self, tool: str, args: dict, timeout_s: float) -> CallResult:
        self.k += 1
        stem = f"{self.k:03d}"
        args = dict(args)
        code_key = _CODE_ARGS.get(tool)
        req = {"index": self.k, "tool": tool, "timeout_s": timeout_s}
        if code_key:
            code = args.pop(code_key)
            req["code_file"] = f"{stem}.code.luau"
            req["code_arg"] = code_key
            req["code_sha256"] = p0.sha256_text(code)
        req["args"] = args
        req_path = self.dir / f"{stem}.request.json"
        if req_path.exists():
            stored = _load(req_path)
            stored.pop("t_request", None)
            if stored != req:
                raise RelayError(f"call {stem}: stored request differs from the runner's call")
        else:
            if code_key:
                (self.dir / req["code_file"]).write_text(code, encoding="utf-8")
            _dump(req_path, {**req, "t_request": _now()})
        resp_path = self.dir / f"{stem}.response.json"
        if not resp_path.exists():
            raise Pending(self.k, req)
        resp = _load(resp_path)
        return CallResult(text=resp["text"], error=resp.get("error"),
                          elapsed_s=resp.get("relay_elapsed_s"), t_wall=resp.get("t_response"))


def respond(calls_dir, index: int, raw_text: str, error: str | None = None) -> dict:
    """Store the raw answer to call `index` (refuses to overwrite an answer)."""
    d = Path(calls_dir)
    stem = f"{index:03d}"
    req_path, resp_path = d / f"{stem}.request.json", d / f"{stem}.response.json"
    if not req_path.exists():
        raise RelayError(f"no request {stem}")
    if resp_path.exists():
        raise RelayError(f"call {stem} already has an answer; answers are never replaced")
    req = _load(req_path)
    t_resp = _now()
    t0 = _dt.datetime.strptime(req["t_request"], "%Y-%m-%dT%H:%M:%S.%fZ")
    t1 = _dt.datetime.strptime(t_resp, "%Y-%m-%dT%H:%M:%S.%fZ")
    resp = {"index": index, "tool": req["tool"], "text": raw_text, "error": error,
            "t_response": t_resp, "relay_elapsed_s": round((t1 - t0).total_seconds(), 3),
            "relay_elapsed_note": "request written -> answer saved; includes the operator's relay time"}
    _dump(resp_path, resp)
    return resp


# --- episode ------------------------------------------------------------------------------

def init_episode(cfg: dict, role: str, episode_dir, episode_id: str, scene_path=None,
                 n_attempts: int | None = None, nonces=None) -> dict:
    """Create the bundle folder: episode.json (nonces fixed up front), scene.json, controller."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    root = Path(cfg["_root"])
    d = Path(episode_dir)
    if (d / "episode.json").exists():
        raise FileExistsError(f"{d} already holds an episode; bundles are never overwritten")
    if role in ("before", "after"):
        decl = cfg["scenes"][role]
        if scene_path is not None and Path(scene_path).resolve() != (root / decl["file"]).resolve():
            raise GateError(f"the {role} scene is fixed by the config: {decl['file']}")
        scene_path = root / decl["file"]
        if n_attempts not in (None, cfg["n_attempts"]):
            raise GateError(f"gate episodes run exactly n_attempts = {cfg['n_attempts']}")
        n_attempts = cfg["n_attempts"]
    elif scene_path is None or n_attempts is None:
        raise ValueError("a smoke episode needs a scene and n_attempts")
    scene = load_scene(scene_path)
    h = canonical_hash(scene)
    if role in ("before", "after") and h != cfg["scenes"][role]["canonical_hash"]:
        raise GateError(f"{role} scene hash {h} != declared")
    errs = p0.validate_route(cfg["_route"], scene["bounds"])
    if errs:
        raise p0.RouteError("; ".join(errs))
    nonces = list(nonces) if nonces is not None else [new_nonce() for _ in range(n_attempts)]
    if len(nonces) != n_attempts or len(set(nonces)) != n_attempts or not all(NONCE_RE.fullmatch(x) for x in nonces):
        raise ValueError("need n_attempts distinct valid nonces")
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(scene_path, d / "scene.json")
    ctl = cfg["_controller_text"]
    (d / "controller.luau").write_text(ctl, encoding="utf-8")
    ep = {
        "schema": EPISODE_SCHEMA, "episode": episode_id, "role": role, "run_id": f"m0-{_now()[:19].replace(':', '')}",
        "created": _now(), "scene_file": str(Path(scene_path).resolve().relative_to(root.resolve())),
        "scene_hash": h, "scene_version": scene["version"], "n_attempts": n_attempts, "nonces": nonces,
        "config_file": str(Path(cfg["_path"]).resolve().relative_to(root.resolve())),
        "config_sha256": cfg["_sha256"], "controller_sha256": p0.controller_sha256(ctl),
        "route_sha256": cfg["route"]["sha256"], "origin": cfg["origin"],
        "player": dict(cfg["player"]), "t_attempt_s": cfg["timing"]["t_attempt"],
        "tool_timeouts_s": dict(cfg["tool_timeouts_s"]),
    }
    _dump(d / "episode.json", ep)
    return ep


def _ensure_stopped(call, tt) -> tuple[bool, str]:
    r = call("get_studio_mode", {}, tt["get_studio_mode"])
    if r.error is None and r.text.strip() == "stop":
        return True, "stop"
    first = r.error or r.text.strip()
    r2 = call("start_stop_play", {"mode": "stop"}, tt["start_stop_play"])
    r3 = call("get_studio_mode", {}, tt["get_studio_mode"])
    if r3.error is None and r3.text.strip() == "stop":
        return True, f"was {first!r}; stopped"
    return False, f"not stopped: {first!r}, stop -> {r2.error or r2.text.strip()!r}, then {r3.error or r3.text.strip()!r}"


def run_episode(client: StudioClient, cfg: dict, episode_dir) -> dict:
    """Run (or replay) the episode in `episode_dir` and write its bundle. Returns the summary.
    Raises Pending (relay) when a call has no answer yet; nothing is written then except the
    request."""
    d = Path(episode_dir)
    ep = _load(d / "episode.json")
    if ep["config_sha256"] != cfg["_sha256"]:
        raise GateError("the config changed since this episode was created")
    ctl = (d / "controller.luau").read_text(encoding="utf-8")
    if p0.controller_sha256(ctl) != ep["controller_sha256"] or ctl != cfg["_controller_text"] \
            or ep["controller_sha256"] != cfg["controller"]["sha256"]:
        raise GateError("controller hash mismatch: refusing to run a different controller")
    scene = load_scene(d / "scene.json")
    if canonical_hash(scene) != ep["scene_hash"]:
        raise GateError("bundle scene.json does not match episode.json")
    tt = cfg["tool_timeouts_s"]
    origin = tuple(cfg["origin"])
    calls: list[dict] = []

    def call(tool, args, timeout_s):
        try:
            r = client.call(tool, args, timeout_s)
        except CallTimeout as e:
            r = CallResult(text="", error=f"timeout after {timeout_s} s ({e})")
        calls.append({"index": len(calls) + 1, "tool": tool, "timeout_s": timeout_s, "error": r.error,
                      "elapsed_s": r.elapsed_s, "t_wall": r.t_wall})
        return r

    status, detail = "complete", ""
    build_info = {"ok": False, "build_line": None, "descendants": None, "expected_descendants": None,
                  "readback_differences": None}
    attempts: list[dict] = []
    ok, why = _ensure_stopped(call, tt)
    if not ok:
        status, detail = "CONNECTOR_ERROR", why
    if status == "complete":
        expected = geometry.expected_tree(scene, origin=origin)
        n_exp = geometry.descendant_count(expected)
        build_info["expected_descendants"] = n_exp
        r = call("run_code", {"command": build.compile_build(scene, origin=origin)}, tt["run_code"])
        m = re.search(r"WS_BUILD_OK\|([0-9a-f]{64})\|(\d+)", r.text or "")
        if r.error or not m or m.group(1) != ep["scene_hash"] or int(m.group(2)) != n_exp:
            status, detail = "BUILD_FAILED", r.error or f"no matching WS_BUILD_OK line (want {ep['scene_hash'][:12]}…|{n_exp})"
        else:
            build_info["build_line"] = m.group(0)
            build_info["descendants"] = int(m.group(2))
    if status == "complete":
        r = call("run_code", {"command": build.compile_readback()}, tt["run_code"])
        try:
            if r.error:
                raise ValueError(r.error)
            diffs = build.compare_tree(expected, build.parse_readback(r.text))
        except ValueError as e:
            diffs = [f"read-back failed: {e}"]
        build_info["readback_differences"] = len(diffs)
        (d / "readback.compare.txt").write_text(
            "\n".join(diffs + [f"{len(expected['nodes'])} expected nodes, {len(diffs)} differences"
                               f" (tolerance {build.TOLERANCE})"]) + "\n", encoding="utf-8")
        if diffs:
            status, detail = "BUILD_FAILED", f"read-back: {len(diffs)} differences"
        else:
            build_info["ok"] = True
    if status == "complete":
        area = gap_area(scene, "gap_1", cfg["gap_area_margin_studs"])
        params = p0.expected_params(cfg["_route"], cfg["timing"])
        for i, nonce in enumerate(ep["nonces"], start=1):
            ok, why = _ensure_stopped(call, tt)
            if not ok:
                attempts.append({"attempt": i, "nonce": nonce, "call_index": None,
                                 **classify_attempt(None, nonce, params, area, call_error=why)})
                status, detail = "CONNECTOR_ERROR", why
                break
            t_play = tt["run_script_in_play_mode"]
            r = call("run_script_in_play_mode",
                     {"code": p0.instantiate(ctl, nonce), "mode": "start_play", "timeout": int(t_play)}, t_play)
            tool_result, err = None, r.error
            if err is None:
                try:
                    tool_result = json.loads(r.text)
                except json.JSONDecodeError:
                    err = "the play tool's answer is not JSON"
            res = classify_attempt(tool_result, nonce, params, area, call_error=err)
            (d / "attempts").mkdir(exist_ok=True)
            (d / "attempts" / f"{i}.raw.txt").write_text(r.text or "", encoding="utf-8")
            attempts.append({"attempt": i, "nonce": nonce, "call_index": len(calls), "t_wall": r.t_wall,
                             "relay_elapsed_s": r.elapsed_s, "raw_file": f"attempts/{i}.raw.txt",
                             "raw_sha256": p0.sha256_text(r.text or ""), **res})
        if status == "complete":
            ok, why = _ensure_stopped(call, tt)
            if not ok:
                detail = f"after the attempts: {why}"
    summary = _summary(ep, cfg, status, detail, build_info, attempts)
    _write_bundle(d, ep, cfg, summary, attempts, calls)
    return summary


def _summary(ep, cfg, status, detail, build_info, attempts) -> dict:
    counts: dict[str, int] = {}
    for a in attempts:
        counts[a["outcome"]] = counts.get(a["outcome"], 0) + 1
    return {
        "schema": SUMMARY_SCHEMA, "episode": ep["episode"], "role": ep["role"],
        "scene_hash": ep["scene_hash"], "scene_version": ep["scene_version"],
        "config_sha256": ep["config_sha256"], "controller_sha256": ep["controller_sha256"],
        "route_sha256": ep["route_sha256"], "player_tier": "P0", "t_attempt_s": ep["t_attempt_s"],
        "status": status, "detail": detail, "build": build_info,
        "n": ep["n_attempts"], "attempted": len(attempts),
        "successes": counts.get("SUCCESS", 0), "counts": dict(sorted(counts.items())),
        "attempts": [{"attempt": a["attempt"], "outcome": a["outcome"], "tags": a["tags"],
                      "cause": a["cause"], "fell_in_area": a["fell_in_area"],
                      "events": [e["event"] for e in a["events"]]} for a in attempts],
    }


def _trace_lines(ep, cfg, attempts) -> list[dict]:
    lines = []
    tick = cfg["timing"]["tick"]
    base = {"run_id": ep["run_id"], "episode": ep["episode"], "scene_hash": ep["scene_hash"],
            "scene_version": ep["scene_version"], "config_sha256": ep["config_sha256"],
            "player": {"tier": "P0", "driver": cfg["player"]["driver"],
                       "controller_sha": ep["controller_sha256"], "freeze_manifest_sha": None,
                       "t_attempt_s": ep["t_attempt_s"]},
            "poll_interval_ms": round(tick * 1000), "max_polls": int(ep["t_attempt_s"] / tick),
            "tool_timeout_s": cfg["tool_timeouts_s"]["run_script_in_play_mode"]}
    for a in attempts:
        rep = a.get("report") or {}
        common = {**base, "attempt": a["attempt"], "nonce": a["nonce"], "t_wall": a.get("t_wall")}
        for e in a["events"]:
            lines.append({**common, "event": e["event"], "id": e["id"], "t_server": e["t_server"],
                          "pos": e["pos"], "cause": None})
        last = a["events"][-1]["pos"] if a["events"] else None
        lines.append({**common, "event": a["outcome"], "tags": a["tags"], "t_server": None,
                      "pos": last, "cause": a["cause"], "detail": a["detail"],
                      "fell_in_area": a["fell_in_area"], "polls_used": rep.get("ticks"),
                      "controller_status": rep.get("status"), "t_end_s": rep.get("t_end"),
                      "tool_duration_s": (a.get("tool") or {}).get("duration_s"),
                      "tool_is_timeout": (a.get("tool") or {}).get("is_timeout"),
                      "relay_elapsed_s": a.get("relay_elapsed_s"), "events_agree": a.get("events_agree")})
    return lines


def _write_bundle(d: Path, ep, cfg, summary, attempts, calls) -> None:
    adir = d / "attempts"
    adir.mkdir(exist_ok=True)
    for a in attempts:
        _dump(adir / f"{a['attempt']}.json", a)
    _dump(d / "calls.json", calls)
    with (d / "trace.jsonl").open("w", encoding="utf-8") as f:
        for line in _trace_lines(ep, cfg, attempts):
            f.write(json.dumps(line, sort_keys=True) + "\n")
    _dump(d / "summary.json", summary)


# --- bundle check and the gate ---------------------------------------------------------------

_SUMMARY_KEYS = {"schema", "episode", "role", "scene_hash", "scene_version", "config_sha256",
                 "controller_sha256", "route_sha256", "player_tier", "t_attempt_s", "status", "detail",
                 "build", "n", "attempted", "successes", "counts", "attempts"}
_TRACE_KEYS = {"run_id", "episode", "scene_hash", "scene_version", "attempt", "player", "event",
               "t_wall", "t_server", "pos", "poll_interval_ms", "cause"}


def validate_bundle(episode_dir) -> list[str]:
    """Problems with an episode bundle ([] if it is complete and self-consistent)."""
    d = Path(episode_dir)
    errs = []
    for f in ("episode.json", "scene.json", "controller.luau", "summary.json", "trace.jsonl",
              "calls.json"):
        if not (d / f).exists():
            errs.append(f"missing {f}")
    if errs:
        return errs
    ep, s = _load(d / "episode.json"), _load(d / "summary.json")
    if ep.get("schema") != EPISODE_SCHEMA:
        errs.append("episode.json schema")
    if set(s) != _SUMMARY_KEYS or s.get("schema") != SUMMARY_SCHEMA:
        errs.append(f"summary.json keys {sorted(set(s) ^ _SUMMARY_KEYS)}")
        return errs
    if canonical_hash(load_scene(d / "scene.json")) != s["scene_hash"] or s["scene_hash"] != ep["scene_hash"]:
        errs.append("scene.json hash != summary scene_hash")
    ctl = (d / "controller.luau").read_text(encoding="utf-8")
    if ctl.count(p0.NONCE_SLOT) != 1 or p0.controller_sha256(ctl) != s["controller_sha256"]:
        errs.append("controller.luau hash != summary controller_sha256")
    for k in ("config_sha256", "controller_sha256", "route_sha256"):
        if s[k] != ep[k]:
            errs.append(f"{k} differs between episode.json and summary.json")
    if len(s["attempts"]) != s["attempted"] or s["successes"] != sum(a["outcome"] == "SUCCESS" for a in s["attempts"]):
        errs.append("summary counts are inconsistent")
    calls = _load(d / "calls.json")
    for i, a in enumerate(s["attempts"], start=1):
        detail = d / "attempts" / f"{i}.json"
        if not detail.exists():
            errs.append(f"missing attempts/{i}.json")
            continue
        ad = _load(detail)
        if ad["outcome"] != a["outcome"] or ad["nonce"] != ep["nonces"][i - 1]:
            errs.append(f"attempts/{i}.json disagrees with the summary or the nonce list")
        ci = ad.get("call_index")
        if ci is not None:
            raw = d / ad.get("raw_file", "")
            if not raw.is_file() or p0.sha256_text(raw.read_text(encoding="utf-8")) != ad.get("raw_sha256"):
                errs.append(f"attempt {i}: raw tool result missing or changed")
            if not 1 <= ci <= len(calls) or calls[ci - 1]["tool"] != "run_script_in_play_mode":
                errs.append(f"attempt {i}: call_index {ci} is not a play call")
    for n, line in enumerate((d / "trace.jsonl").read_text(encoding="utf-8").splitlines(), start=1):
        rec = json.loads(line)
        missing = _TRACE_KEYS - set(rec)
        if missing:
            errs.append(f"trace line {n} lacks {sorted(missing)}")
    return errs


def evaluate_gate(cfg: dict, before_dir, after_dir) -> dict:
    """The M0 gate as declared in the config. Raises GateError if the episodes are not
    comparable (different controller, config or route hash, wrong scenes or N)."""
    out = {"schema": GATE_SCHEMA, "config_sha256": cfg["_sha256"]}
    sums = {}
    for role, d in (("before", before_dir), ("after", after_dir)):
        errs = validate_bundle(d)
        if errs:
            raise GateError(f"{role} bundle: {errs}")
        s = _load(Path(d) / "summary.json")
        if s["role"] != role:
            raise GateError(f"{d} is a {s['role']} episode, not {role}")
        if s["scene_hash"] != cfg["scenes"][role]["canonical_hash"]:
            raise GateError(f"{role} scene hash is not the declared one")
        if s["config_sha256"] != cfg["_sha256"]:
            raise GateError(f"{role} ran under a different config")
        if s["n"] != cfg["n_attempts"]:
            raise GateError(f"{role} has n = {s['n']}, declared {cfg['n_attempts']}")
        sums[role] = s
    for k in ("controller_sha256", "route_sha256", "config_sha256"):
        if sums["before"][k] != sums["after"][k]:
            raise GateError(f"{k} differs between the scenes: refusing to compare")
    if sums["before"]["controller_sha256"] != cfg["controller"]["sha256"]:
        raise GateError("controller hash is not the declared one")
    k_req = cfg["pass_k"]
    b, a = sums["before"], sums["after"]
    b_hits = sum(x["outcome"] == "FELL" and x["fell_in_area"] is True for x in b["attempts"])
    a_hits = sum(x["outcome"] == "SUCCESS" for x in a["attempts"])
    b_pass = b["status"] == "complete" and b["build"]["ok"] and b["attempted"] == b["n"] and b_hits >= k_req
    a_pass = a["status"] == "complete" and a["build"]["ok"] and a["attempted"] == a["n"] and a_hits >= k_req
    out.update({
        "controller_sha256": b["controller_sha256"], "route_sha256": b["route_sha256"],
        "declared": {"before": dict(cfg["gate"]["before"], scene_hash=b["scene_hash"], k=k_req, n=b["n"]),
                     "after": dict(cfg["gate"]["after"], scene_hash=a["scene_hash"], k=k_req, n=a["n"])},
        "before": {"episode": b["episode"], "hits": b_hits, "n": b["n"], "readback_ok": b["build"]["ok"],
                   "outcomes": [x["outcome"] for x in b["attempts"]], "pass": b_pass},
        "after": {"episode": a["episode"], "hits": a_hits, "n": a["n"], "readback_ok": a["build"]["ok"],
                  "outcomes": [x["outcome"] for x in a["attempts"]], "pass": a_pass},
        "result": "PASS" if (b_pass and a_pass) else "FAIL",
    })
    return out
