"""A local-only run uses the local seat, whatever the global routing mode.

News routines (Front Page, Briefing, podcast scripts) run inside
`local_only_guard.local_only(...)`: they may never reach a paid provider. The
guard refuses every cloud transport, which is right -- but the provider ladder
was still built from the global routing mode. Under `cloud_only` that ladder is
[cloud, openai] with the local leg filtered out, so a local-only run refused
both cloud legs and never tried the machine's own seat: it failed with "No
model provider could generate text" while the seat sat idle.

The rule: inside a local-only run the ladder is the local leg alone. The run's
own constraint outranks the global mode, because the global mode describes
where the owner's chat goes, and the run's mark describes where this job is
allowed to go at all. A cloud-pinned run is not a local-only run and keeps its
ladder.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import local_only_guard as guard  # noqa: E402
from agent_friday.services import model_router as mr  # noqa: E402

CLOUD_LADDER = [("cloud", "c", "claude-sonnet-5-5"), ("openai", "o", None), ("local", "l", None)]
LOCAL_LADDER = [("local", "l", "bonsai2:27b"), ("cloud", "c", None), ("openai", "o", None)]


def _names(attempts):
    return [a[0] for a in attempts]


@pytest.mark.parametrize("mode", ["cloud_only", "local_preferred", "smart", "", "local_only"])
def test_a_local_only_run_gets_only_the_local_leg(mode):
    with guard.local_only("Front Page"):
        out = mr._mode_filtered_attempts(CLOUD_LADDER, {"mode": mode})
    assert _names(out) == ["local"], (
        f"a local-only run under mode {mode!r} was handed {out}: it refuses every "
        "cloud leg, so anything but the local seat fails the run")
    assert out[0][2] is None, "a cloud model id must not be sent to the local seat"


def test_a_routed_local_model_is_kept():
    with guard.local_only("Briefing"):
        out = mr._mode_filtered_attempts(LOCAL_LADDER, {"mode": "cloud_only"})
    assert out == [LOCAL_LADDER[0]]


def test_outside_a_local_only_run_the_mode_still_decides():
    out = mr._mode_filtered_attempts(CLOUD_LADDER, {"mode": "cloud_only"})
    assert _names(out) == ["cloud", "openai"]


def test_a_cloud_pinned_run_keeps_its_cloud_ladder():
    with guard.cloud_pinned("claude-haiku-4-5-20251001", "Discuss"):
        out = mr._mode_filtered_attempts(CLOUD_LADDER, {"mode": "cloud_only"})
    assert _names(out) == ["cloud", "openai"]


def test_generate_text_under_cloud_only_calls_the_local_seat(monkeypatch):
    """End to end through `_generate_text_untraced`: global cloud_only, the
    router picks the cloud, the run is local-only. The local seat answers and no
    cloud transport is ever entered."""
    calls = []

    class _Router:
        def route(self, messages, task_context=None):
            return {"provider": "cloud", "model": "claude-sonnet-5-5"}

    import agent_friday.routing.model_router as routing
    monkeypatch.setattr(routing, "get_router", lambda cfg: _Router())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"model_routing": {"mode": "cloud_only"}})

    def _claude(*a, **k):
        calls.append("cloud")
        raise AssertionError("cloud transport entered")

    def _openai(*a, **k):
        calls.append("openai")
        raise AssertionError("openai transport entered")

    def _ollama(messages, system=None, model=None, **k):
        calls.append(("local", model))
        return ("local answer", [])

    monkeypatch.setattr(mr, "_call_claude", _claude)
    monkeypatch.setattr(mr, "_call_openai", _openai)
    monkeypatch.setattr(mr, "_call_ollama", _ollama)

    with guard.local_only("Front Page"):
        out = mr._generate_text_untraced([{"role": "user", "content": "hi"}])

    assert str(out) == "local answer"
    assert calls == [("local", None)], calls
