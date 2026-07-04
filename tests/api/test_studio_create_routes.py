"""API tests for the Studio prompt-bar creation flow.

The Studio prompt bar posts DIRECTLY to /api/create/<type> — engines only,
never the agent loop or the vibe-code (Claude Code) launcher. These tests pin
that contract:

* /api/create/text         — routed-provider text generation, standard envelope
* /api/create/availability — per-type availability the chips render from
* /api/create/video        — actionable 'unavailable' envelope without a key
* ui_parts/app.html        — source-level regression: the prompt bar wires to
                             /api/create/<type>, not the Claude Code launcher
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import agent_friday.core as core


# ── /api/create/text ─────────────────────────────────────────────────────────

def _stub_text(monkeypatch, reply="# Neon Rain\n\nGlass streets hum."):
    """Force non-demo mode and stub the routed text seam, recording the call."""
    from agent_friday.services import demo_mode, model_router
    monkeypatch.setattr(demo_mode, "is_demo", lambda *a, **k: False)
    calls = {}

    def fake(messages, system=None, **kw):
        calls["messages"] = messages
        calls["system"] = system
        calls["workspace"] = kw.get("workspace")
        return reply

    monkeypatch.setattr(model_router, "_generate_text", fake)
    return calls


def test_create_text_saves_markdown_and_returns_envelope(client, creations_dir,
                                                         monkeypatch):
    calls = _stub_text(monkeypatch)
    r = client.post("/api/create/text", json={"prompt": "a poem about neon rain"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok" and d["kind"] == "text"
    assert d["text"].startswith("# Neon Rain")
    assert d["files"] and d["files"][0]["url"] == d["url"]
    assert d["url"].startswith("/api/creations/")
    saved = creations_dir / d["filename"]
    assert saved.exists()
    assert saved.read_text(encoding="utf-8").startswith("# Neon Rain")
    # The user's prompt reached the routed provider on the studio workspace.
    assert "neon rain" in str(calls["messages"]).lower()
    assert calls["workspace"] == "studio"


def test_create_text_requires_prompt(client, monkeypatch):
    _stub_text(monkeypatch)
    d = client.post("/api/create/text", json={}).get_json()
    assert d["status"] == "error"


def test_create_text_demo_mode_is_actionable(client, monkeypatch):
    from agent_friday.services import demo_mode
    monkeypatch.setattr(demo_mode, "is_demo", lambda *a, **k: True)
    d = client.post("/api/create/text", json={"prompt": "hello"}).get_json()
    assert d["status"] == "unavailable"
    assert "Providers" in d["message"]


# ── /api/create/availability ─────────────────────────────────────────────────

def test_availability_reports_every_studio_type(client, monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    d = client.get("/api/create/availability").get_json()
    assert d["status"] == "ok"
    for t in ("image", "video", "music", "text", "code-art"):
        assert t in d["types"], f"missing type {t}"
        assert "available" in d["types"][t]
    assert d["types"]["video"]["available"] is False
    assert "Gemini" in d["types"]["video"]["reason"]


def test_availability_with_key(client, monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "test-key", raising=False)
    d = client.get("/api/create/availability").get_json()
    assert d["types"]["image"]["available"] is True
    assert d["types"]["image"]["reason"] is None
    assert d["types"]["video"]["available"] is True


# ── /api/create/video without a key ──────────────────────────────────────────

def test_create_video_without_key_is_actionable_not_agentic(client, monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    d = client.post("/api/create/video", json={"prompt": "a drone shot"}).get_json()
    assert d["status"] == "unavailable"
    assert "Gemini" in d["message"]


# ── UI wiring regression ──────────────────────────────────────────────────────

def _app_html_source() -> str:
    p = Path(__file__).resolve().parent.parent.parent / "ui_parts" / "app.html"
    return p.read_text(encoding="utf-8")


def test_studio_prompt_bar_posts_to_engines_not_claude_code():
    """The regression this suite exists for: typing a prompt and picking Video
    used to launch a Claude Code terminal running the DAILY creation task
    (via the vibe-code launcher) instead of calling Veo. The prompt bar must
    post to /api/create/<type> and must never reference the terminal launcher."""
    src = _app_html_source()
    assert "function StudioPromptBar" in src
    bar = src.split("function StudioPromptBar", 1)[1].split("function StudioWS", 1)[0]
    assert "fetch('/api/create/'+type" in bar
    assert "vibe-code" not in bar
    assert 'placeholder="Describe what you want to create..."' in bar
    assert "/api/create/availability" in bar


def test_studio_ws_daily_agent_is_optin_and_labelled():
    """The daily-creation agent launcher survives, but only as the clearly
    labelled opt-in button (behind a confirm), never as the prompt-bar action,
    and the old instant-fire quick bar is gone."""
    src = _app_html_source()
    ws = src.split("function StudioWS", 1)[1].split("function DefederationPanel", 1)[0]
    assert "StudioPromptBar onCreated" in ws
    assert "runDailyAgent" in ws and "confirm(" in ws
    assert "Describe a quick text/code creation" not in ws
    assert ">Create Now</button>" not in ws
