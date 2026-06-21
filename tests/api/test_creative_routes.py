"""API tests for the creative generation routes + FRIDAY tool handlers.

Exercises /api/creations/generate, /api/create/image, /api/create/video and the
generate_image / generate_video chat tools. The google-genai client is replaced
with fakes (via patching services.creative_engine._client), so nothing hits the
network and no real API key is consumed.
"""
import core
import pytest

from services import creative_engine as ce
from tests.unit.test_creative_engine import (
    _FakeImageClient, _FakeVideoClient, _DummyTimer,
)


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "test-key", raising=False)


@pytest.fixture
def no_gemini_key(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)


@pytest.fixture
def tame(monkeypatch):
    monkeypatch.setattr(ce, "_notify", lambda *_a, **_k: None)
    monkeypatch.setattr(ce.threading, "Timer", _DummyTimer)


# ── /api/creations/generate (image) ─────────────────────────────────────────
def test_generate_image_route_ok(client, creations_dir, gemini_key, tame, monkeypatch):
    monkeypatch.setattr(ce, "_client", lambda: _FakeImageClient())
    r = client.post("/api/creations/generate",
                    json={"kind": "image", "prompt": "a neon city",
                          "model": "gemini-nano-banana-pro", "aspect_ratio": "1:1"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok" and d["files"]
    # Back-compat top-level fields present.
    assert d["filename"] and d["url"].startswith("/api/creations/")
    assert (creations_dir / d["filename"]).exists()


def test_generate_route_blocks_prohibited(client, gemini_key):
    # Create routes return HTTP 200 by convention; the body carries the status.
    r = client.post("/api/creations/generate",
                    json={"kind": "image", "prompt": "a nude child"})
    assert r.status_code < 500
    d = r.get_json()
    assert d["status"] == "blocked" and "minors" in d["reason"]


def test_generate_image_route_unavailable_without_key(client, no_gemini_key):
    r = client.post("/api/creations/generate",
                    json={"kind": "image", "prompt": "a calm sea"})
    assert r.status_code < 500
    assert r.get_json()["status"] == "unavailable"


# ── /api/creations/generate dispatches video via kind ────────────────────────
def test_generate_route_dispatches_video(client, creations_dir, gemini_key, tame, monkeypatch):
    monkeypatch.setattr(ce, "_client", lambda: _FakeVideoClient())
    r = client.post("/api/creations/generate",
                    json={"kind": "video", "prompt": "waves crashing"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok" and d["kind"] == "video"
    assert (creations_dir / d["files"][0]["filename"]).suffix == ".mp4"


# ── legacy /api/create/video ─────────────────────────────────────────────────
def test_create_video_route_ok(client, creations_dir, gemini_key, tame, monkeypatch):
    monkeypatch.setattr(ce, "_client", lambda: _FakeVideoClient())
    r = client.post("/api/create/video", json={"prompt": "a sunrise timelapse"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_create_video_route_unavailable_without_key(client, no_gemini_key):
    r = client.post("/api/create/video", json={"prompt": "a sunrise"})
    assert r.status_code < 500
    assert r.get_json()["status"] == "unavailable"


# ── FRIDAY tool handlers ─────────────────────────────────────────────────────
def test_tool_generate_image_blocked(no_gemini_key):
    from services.agent import _tool_generate_image
    out = _tool_generate_image({"prompt": "a naked child"})
    assert "CONTENT SAFETY" in out


def test_tool_generate_image_requires_prompt():
    from services.agent import _tool_generate_image
    assert "required" in _tool_generate_image({"prompt": ""})


def test_tool_generate_image_ok(creations_dir, gemini_key, tame, monkeypatch):
    import json as _json
    monkeypatch.setattr(ce, "_client", lambda: _FakeImageClient())
    from services.agent import _tool_generate_image
    out = _tool_generate_image({"prompt": "a fox in snow", "model": "gemini-nano-banana-2"})
    parsed = _json.loads(out)
    assert parsed["status"] == "ok" and parsed["files"]


def test_tool_generate_video_registered():
    # The tool is wired into the unified registry at Ring 2 (network).
    from services.agent import CLAUDE_TOOL_HANDLERS, TOOL_RINGS
    assert "generate_image" in CLAUDE_TOOL_HANDLERS
    assert "generate_video" in CLAUDE_TOOL_HANDLERS
    assert TOOL_RINGS["generate_image"] == 2
    assert TOOL_RINGS["generate_video"] == 2
