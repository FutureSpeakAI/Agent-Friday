"""evaluate_image and generate_image honour Local only.

Under Local only the image, the intent and the prompt never reach a cloud
provider: vision QA scores on the local seat or is skipped with a plain
reason, and cloud image generation says it is not done. With the cloud
allowed both paths are unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday import core
from agent_friday.services import creative_engine as ce
from agent_friday.services import local_vision, qa_gates, seed_images


def _mode(monkeypatch, mode):
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"model_routing": {"mode": mode}})


@pytest.fixture
def cloud_client(monkeypatch):
    """A Gemini client that records use; construction counts as a call."""
    calls = []

    class _Resp:
        text = '{"score": 0.9, "critique": "cloud", "suggestions": ""}'

    class _Models:
        def generate_content(self, **kw):
            calls.append(kw)
            return _Resp()

    class _Client:
        models = _Models()

    monkeypatch.setattr(ce, "is_available", lambda: True)
    monkeypatch.setattr(ce, "_client", lambda: (calls.append("client"), _Client())[1])
    return calls


@pytest.fixture
def image_ready(monkeypatch):
    monkeypatch.setattr(qa_gates, "qa_config",
                        lambda: {"vision_for_images": True, "threshold": 0.7})
    monkeypatch.setattr(seed_images, "check_running_call", lambda p: (True, ""))
    monkeypatch.setattr(ce, "load_local_image", lambda p: (b"\x89PNGdata", "image/png"))


# ── evaluate_image ──────────────────────────────────────────────────────────

def test_local_only_scores_on_the_local_seat_never_the_cloud(
        monkeypatch, cloud_client, image_ready):
    _mode(monkeypatch, "local_only")
    seen = []

    def _describe(b64, *, mime, prompt, **kw):
        seen.append(prompt)
        return {"ok": True, "text": '{"score": 0.8, "critique": "ok", "suggestions": ""}',
                "model": "seat"}

    monkeypatch.setattr(local_vision, "describe", _describe)
    v = qa_gates.evaluate_image("x.png", "a sunset")
    assert cloud_client == [], "the image or intent reached Gemini under Local only"
    assert v["status"] == "ok" and v["passed"] is True and v["score"] == 0.8
    assert seen and "a sunset" in seen[0]


def test_local_only_without_a_seat_skips_plainly(
        monkeypatch, cloud_client, image_ready):
    _mode(monkeypatch, "local_only")
    monkeypatch.setattr(local_vision, "describe",
                        lambda *a, **k: {"ok": False, "reason": "no seat"})
    v = qa_gates.evaluate_image("x.png", "a sunset")
    assert cloud_client == []
    assert v["status"] == "skipped" and v["passed"] is True
    assert "Local only is on" in v["critique"]


def test_local_only_skips_even_with_no_gemini_key(monkeypatch, image_ready):
    _mode(monkeypatch, "local_only")
    monkeypatch.setattr(ce, "is_available", lambda: False)
    monkeypatch.setattr(local_vision, "describe",
                        lambda *a, **k: {"ok": False, "reason": "no seat"})
    assert "Local only is on" in qa_gates.evaluate_image("x.png", "i")["critique"]


def test_cloud_allowed_vision_qa_still_uses_gemini(
        monkeypatch, cloud_client, image_ready):
    _mode(monkeypatch, "cloud_only")
    from agent_friday.services import egress_gate
    monkeypatch.setattr(egress_gate, "gate_text", lambda t, p, f, **k: t)
    from google.genai import types
    monkeypatch.setattr(types.Part, "from_bytes", staticmethod(lambda data, mime_type: "part"))
    v = qa_gates.evaluate_image("x.png", "a sunset")
    assert any(isinstance(c, dict) for c in cloud_client), "cloud path not taken"
    assert v["status"] == "ok" and v["score"] == 0.9


# ── generate_image ──────────────────────────────────────────────────────────

def test_local_only_image_generation_does_not_reach_a_cloud_provider(
        monkeypatch, cloud_client):
    _mode(monkeypatch, "local_only")
    out = ce.generate_image("a quiet harbour")
    assert cloud_client == [], "the prompt reached Gemini under Local only"
    assert out["status"] == "unavailable"
    assert "Local only" in out["message"]


def test_cloud_allowed_image_generation_proceeds_past_the_guard(monkeypatch):
    """With the cloud allowed and no key, the old 'needs a Gemini API key'
    answer comes back, i.e. the call got past the Local-only guard."""
    _mode(monkeypatch, "cloud_only")
    monkeypatch.setattr(ce, "is_available", lambda: False)
    out = ce.generate_image("a quiet harbour")
    assert out["status"] == "unavailable"
    assert "Gemini API key" in out["message"]
