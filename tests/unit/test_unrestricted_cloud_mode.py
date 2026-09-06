"""Unit tests for `model_routing.unrestricted_cloud` — Stephen's explicit
instruction, 2026-09-03: "cloud only mode means no privacy safeguards,
Friday operates completely with cloud models and no local inference. this
mode must be in the app. when active, no feature or data is held back from
the cloud."

This is a real, separate flag from the pre-existing `model_routing.mode:
"cloud_only"`, which has only ever meant provider ROUTING PREFERENCE and
has never touched the gate — verified earlier in this same investigation
(grep found zero references to model_routing anywhere in egress_gate.py
before this change). `unrestricted_cloud` is new, defaults False, and when
True bypasses every gate this codebase has for cloud sends: tier
classification, redaction, the PII scrub, and the never-send list — all of
it, "no data is held back" taken literally, since that is exactly what was
specified.

Each test below is written to describe today's CORRECT (post-change)
behavior; the false-by-default tests double as the reproduction of what
this looked like before the flag existed anywhere — proving OFF still
means what it always meant, and ON is a real, working bypass rather than a
no-op flag nobody wired up.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import egress_gate as eg

def _set_unrestricted(monkeypatch, value: bool):
    """Patch the actual settings loader is_unrestricted_cloud() reads --
    exercises the real read path, not a mocked-out version of the flag."""
    from agent_friday import core as _core
    monkeypatch.setattr(
        _core, "_load_settings",
        lambda: {"model_routing": {"unrestricted_cloud": value}})


class TestDefaultIsSafe:
    """OFF by default -- and OFF must mean the same thing it always did."""

    def test_default_setting_reads_false(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: {})
        assert eg.is_unrestricted_cloud() is False

    def test_settings_load_failure_fails_closed(self, monkeypatch):
        from agent_friday import core as _core
        def _boom():
            raise RuntimeError("disk error")
        monkeypatch.setattr(_core, "_load_settings", _boom)
        assert eg.is_unrestricted_cloud() is False

    def test_off_still_blocks_never_send_content(self, monkeypatch):
        _set_unrestricted(monkeypatch, False)
        monkeypatch.setattr(
            eg, "_never_send_covered_by_override", lambda text: False)
        from agent_friday.services import judgment_gate as jg
        monkeypatch.setattr(jg, "never_send_hits", lambda text: ["marker"])
        with pytest.raises(eg.NeverSendBlocked):
            eg._gate_text_span("contains the marker", "anthropic", "prompt")


class TestUnrestrictedModeBypassesEverything:
    """ON: exactly what was specified -- nothing held back, including the
    never-send list, which is the one floor every other mechanism in this
    codebase treats as absolute."""

    def test_never_send_content_passes_through_unchanged(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        from agent_friday.services import judgment_gate as jg
        monkeypatch.setattr(jg, "never_send_hits", lambda text: ["marker"])
        secret = "the never-send marker is right here"
        out = eg._gate_text_span(secret, "anthropic", "prompt")
        assert out == secret, "unrestricted mode must not withhold never-send content"

    def test_tier3_content_passes_through_unchanged(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        monkeypatch.setattr(eg, "_classify_cloud", lambda text: eg.Tier.SENSITIVE)
        text = "extremely sensitive TIER_3 content"
        out = eg._gate_text_span(text, "anthropic", "prompt")
        assert out == text

    def test_seal_outbound_returns_payload_completely_unscrubbed(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        payload = {
            "system": "custody hearing on the 14th, SSN 123-45-6789",  # pragma: allowlist secret
            "messages": [{"role": "user", "content": "my account number is 9876543210"}],
        }
        out = eg.seal_outbound(dict(payload), "anthropic")
        assert out["system"] == payload["system"]
        assert out["messages"][0]["content"] == payload["messages"][0]["content"]
        assert "[PII:" not in out["system"], "the PII scrub must also be skipped"

    def test_tool_prose_never_send_is_bypassed(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        from agent_friday.services import judgment_gate as jg
        monkeypatch.setattr(jg, "never_send_hits", lambda text: ["marker"])
        text = "a tool description containing the marker"
        out = eg._gate_tool_prose(text, "anthropic", "tool.description")
        assert out == text


def test_unrestricted_mode_is_a_distinct_flag_from_routing_mode(monkeypatch):
    """model_routing.mode == "cloud_only" (the pre-existing, default routing
    preference) must NOT, by itself, trip unrestricted mode -- conflating
    the two would silently disable every safeguard for the app's own
    factory default, which is not what was asked for."""
    from agent_friday import core as _core
    monkeypatch.setattr(
        _core, "_load_settings",
        lambda: {"model_routing": {"mode": "cloud_only"}})
    assert eg.is_unrestricted_cloud() is False


class TestKnowledgeGraphRoutingIsIndependentOfTheFlag:
    """Superseded 2026-09-03, same day: the KG indexer's old hard rule --
    TIER_2/3 chunks pinned to a local model 'in any mode' -- was a per-TIER
    override of what became a strict per-user choice: "he is not asking for
    a system that decides for people, he's asking for one that does what
    the person picked." `indexing_mode` ("local"/"cloud") now decides
    routing on its own, uniformly across sensitivity; `unrestricted_cloud`
    has no special case here at all -- its only effect is inside
    egress_gate itself (TestUnrestrictedModeBypassesEverything above),
    which every cloud call in the app, including a "cloud"-mode KG chunk,
    already passes through. These confirm the flag is simply irrelevant to
    this function, in both directions, rather than silently reintroducing a
    KG-level special case for it."""

    def test_cloud_mode_routes_cloud_regardless_of_the_flag(self, monkeypatch):
        from agent_friday.services.knowledge_graph import indexer as kgi
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        for flag in (True, False):
            monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: flag)
            model, pinned = kgi._resolve_model(3, "cloud")
            assert pinned is False, f"cloud mode must route cloud (flag={flag})"

    def test_local_mode_stays_local_regardless_of_the_flag(self, monkeypatch):
        from agent_friday.services.knowledge_graph import indexer as kgi
        monkeypatch.setattr(kgi, "_available_local_model", lambda: "qwen3:4b")
        for flag in (True, False):
            monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: flag)
            model, pinned = kgi._resolve_model(3, "local")
            assert pinned is True, f"local mode must stay local (flag={flag})"
