"""Helpers for the WS-11 tests: a direct (non-relay) simulated Studio client and fake architects.

Simulated answers only (worldsmith.runner.dryrun.FakeOperator): shape, not physics, not Roblox.
"""

import json
from pathlib import Path

from worldsmith.architect.client import ArchitectCallError, RawResponse
from worldsmith.runner.attempts import CallResult, CallTimeout
from worldsmith.runner.dryrun import FakeOperator


class ScriptedOperator(FakeOperator):
    """FakeOperator whose play outcomes can be scripted: `outcomes[b]` = per-attempt outcomes for
    the b-th build of the run ("SUCCESS", "FELL", "CALL_TIMEOUT"; missing = the stand-in rule);
    `build_ok=False` makes every build print no OK line."""

    def __init__(self, cfg, outcomes=None, build_ok=True):
        super().__init__(cfg)
        self.outcomes = outcomes or {}
        self.build_ok = build_ok
        self.builds = 0
        self.play_calls = 0

    def _want(self, ep_dir, i):
        plan = self.outcomes.get(self.builds)
        return plan[i - 1] if plan else None

    def outcome_for(self, ep_dir, i):
        return self._want(ep_dir, i)

    def answer_code(self, ep_dir, tool, code):
        if tool == "run_code" and code and "WS_BUILD_OK" in code:
            self.builds += 1
            if not self.build_ok:
                return "[ERROR] simulated build failure"
        if tool == "run_script_in_play_mode":
            self.play_calls += 1
            i = self.plays.get(str(ep_dir), 0) + 1
            if self._want(ep_dir, i) == "CALL_TIMEOUT":
                self.plays[str(ep_dir)] = i
                raise CallTimeout("simulated hang")
        return super().answer_code(ep_dir, tool, code)


class Direct:
    """A StudioClient that answers at once through an operator (no relay files)."""

    def __init__(self, op, ep_dir):
        self.op, self.ep_dir, self.log = op, Path(ep_dir), []

    def call(self, tool, args, timeout_s):
        self.log.append(tool)
        code = args.get("command") or args.get("code")
        return CallResult(text=self.op.answer_code(self.ep_dir, tool, code))


class SeqArchitect:
    """Replies with `texts` in order; '{base}' is replaced by the request's scene hash. An
    exception instance in the list is raised instead."""
    provider = "fake"

    def __init__(self, texts, role="haiku"):
        self.texts, self.model_role, self.model_id = list(texts), role, f"fake-{role}"
        self.calls = 0
        self.requests = []

    def propose(self, req):
        t = self.texts[self.calls]
        self.calls += 1
        self.requests.append(req.request_sha)
        if isinstance(t, BaseException):
            raise t
        return RawResponse(text=t.replace("{base}", req.context["scene_hash"]), model_role=self.model_role,
                           model_id=self.model_id, provider=self.provider, stop_reason="end_turn",
                           usage={"input_tokens": 10, "output_tokens": 5}, cost_usd=0.001)


def set_len(v):
    return json.dumps({"schema": "worldsmith.patch/0.1", "base_hash": "{base}",
                       "ops": [{"op": "set", "id": "bridge_1", "field": "length", "value": v}]})


def provider_error():
    return ArchitectCallError("APIStatusError: simulated 529 overloaded")
