"""Gauntlet finding Q20: task_overrides.voice (a documented, user-facing
config key -- docs/user-guide/configuration.md:50) was permanently inert dead code.

routing/model_router.py's _route_basic() has three branches gated on
TaskType.VOICE (the cloud_only seat-skip, the task_overrides lookup skip,
and the "Voice stays on cloud/Gemini pipeline" default) -- but
classify_task() could only ever return SIMPLE/TOOL_USE/CODE/RESEARCH. It
never returned TaskType.VOICE, and nothing anywhere in src/ ever injected a
pre-classified VOICE task_type. A user who set task_overrides.voice per the
documented config got silently NO EFFECT at all.

This is NOT a safety-relevant fix. routes/voice.py's actual local-only
enforcement for voice (whether Gemini Live can be used at all) lives in a
separate, already-correct mechanism (_ws_local_only/_renewal_local_only)
directly in routes/voice.py. This fix is only about making the documented
task_overrides.voice knob reachable so it actually redirects which
model/provider handles a voice-originated turn, as documented.

Fix: classify_task() gained an explicit `is_voice` origin signal (never
inferred from message content -- content-based classification would risk
misclassifying an ordinary text turn that happens to mention "voice"). The
ONE existing call site inside _route_basic() now passes
`is_voice=bool(ctx.get("is_voice"))`. The voice pipeline's own call site
(routes/voice.py's /ws/voice-local handler, its one call to
services.agent._generate_agent) now sets session_ctx["is_voice"] = True;
services/agent.py's _generate_agent threads that into the task_context dict
it hands to router.route().

This probe must be RED before the fix (classify_task cannot return
TaskType.VOICE under any input) and GREEN after.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.routing.model_router import ModelRouter, TaskType


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _msgs(*texts: str) -> list:
    return [_user(t) for t in texts]


def _router(mode="smart", **extra) -> ModelRouter:
    cfg = {"mode": mode, **extra}
    return ModelRouter(config=cfg)


class TestVoiceTaskTypeReachable:
    """classify_task() must be able to return TaskType.VOICE when a caller
    explicitly signals the request originated from the voice pipeline."""

    def test_is_voice_signal_classifies_as_voice(self):
        r = _router()
        # Ordinary short message -- content alone would classify as SIMPLE --
        # but the explicit origin signal must win.
        assert r.classify_task(_msgs("what's the weather"), is_voice=True) == TaskType.VOICE

    def test_is_voice_signal_wins_over_content_that_looks_like_other_types(self):
        r = _router()
        # A message that would otherwise classify as CODE (has_tools False)
        # must still come back VOICE when the origin signal is set -- origin
        # is checked first, before any content heuristic.
        assert r.classify_task(_msgs("write code for me"), is_voice=True) == TaskType.VOICE

    def test_route_reaches_voice_branch_via_ctx_signal(self):
        # End-to-end through _route_basic()/route(): a voice-originated turn
        # with no task_overrides configured must land on the existing
        # "Voice stays on cloud/Gemini pipeline" branch (previously
        # unreachable dead code).
        r = _router(cloud_model="claude-sonnet-5")
        result = r.route(_msgs("hi"), task_context={"is_voice": True})
        assert result["task_type"] == TaskType.VOICE
        assert result["reason"] == "Voice stays on cloud/Gemini pipeline"

    def test_task_overrides_voice_now_takes_effect(self):
        # The actual documented config key (docs/user-guide/configuration.md:50): a user
        # who sets task_overrides.voice must now see it actually chosen for a
        # voice-originated turn -- this was the exact dead-code path.
        r = _router(task_overrides={
            "voice": {"provider": "openai", "model": "gpt-4o-mini"},
        })
        result = r.route(_msgs("hi"), task_context={"is_voice": True})
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o-mini"
        assert result["task_type"] == TaskType.VOICE


class TestOrdinaryClassificationUnaffected:
    """No-op-shaped sanity check: ordinary (non-voice) messages must classify
    EXACTLY as before this fix -- is_voice defaults to False and changes
    nothing about existing content-based classification."""

    def test_empty_messages_still_simple(self):
        r = _router()
        assert r.classify_task([]) == TaskType.SIMPLE

    def test_short_plain_message_still_simple(self):
        r = _router()
        assert r.classify_task(_msgs("hi there")) == TaskType.SIMPLE

    def test_has_tools_still_overrides_to_tool_use(self):
        r = _router()
        assert r.classify_task(_msgs("write code for me"), has_tools=True) == TaskType.TOOL_USE

    def test_code_keywords_still_classify_as_code(self):
        r = _router()
        assert r.classify_task(_msgs("please refactor this function")) == TaskType.CODE

    def test_research_keywords_still_classify_as_research(self):
        r = _router()
        assert r.classify_task(_msgs("do a comprehensive analysis of this")) == TaskType.RESEARCH

    def test_ordinary_route_never_returns_voice_without_the_signal(self):
        r = _router()
        result = r.route(_msgs("hi there"), task_context={})
        assert result["task_type"] != TaskType.VOICE
