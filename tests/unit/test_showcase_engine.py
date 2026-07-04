"""Unit tests for the showcase engine (slide decks + websites).

The LLM produces a JSON spec; a fixed template renders it. These tests pin the
contract both ways: spec parsing tolerates real-world LLM output (fences,
prose), and the renderers always emit self-contained, escaped HTML.
"""
import json

import pytest

from agent_friday.services import showcase_engine as se


# ── JSON spec parsing ─────────────────────────────────────────────────────

def test_parse_json_spec_plain():
    assert se._parse_json_spec('{"a": 1}') == {"a": 1}


def test_parse_json_spec_fenced_and_prosy():
    assert se._parse_json_spec('Sure!\n```json\n{"a": [1, 2]}\n```\nDone.') == {"a": [1, 2]}
    assert se._parse_json_spec('preamble {"x": {"y": 2}} trailing words') == {"x": {"y": 2}}


def test_parse_json_spec_garbage_is_none():
    assert se._parse_json_spec("no json here") is None
    assert se._parse_json_spec("") is None
    assert se._parse_json_spec(None) is None


# ── Deck rendering ────────────────────────────────────────────────────────

_DECK_SPEC = {
    "title": "Agent Friday",
    "subtitle": "A sovereign AI",
    "slides": [
        {"kicker": "Intro", "title": "What is Friday?",
         "bullets": ["Private by design", "Multi-model router"],
         "notes": "Open warm."},
        {"kicker": "Demo", "title": "What can it do?",
         "bullets": ["Voice", "Studio", "Agents"]},
    ],
    "closing": "Own your AI.",
}


def test_deck_render_structure():
    out = se._render_deck_html(_DECK_SPEC)
    # Title + 2 content slides + closing = 4 sections.
    assert out.count('<section class="slide') == 4
    assert "Agent Friday" in out and "Own your AI." in out
    # Self-contained: keyboard nav + notes toggle inline, no external fetches.
    assert "addEventListener('keydown'" in out and "show-notes" in out
    assert "http://" not in out and "https://" not in out
    # Speaker notes render only for slides that have them.
    assert out.count('class="notes"') == 1


def test_deck_render_escapes_llm_text():
    out = se._render_deck_html({
        "title": "<script>alert(1)</script>",
        "slides": [{"title": "t", "bullets": ["<img onerror=x>"]}],
    })
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out and "&lt;img" in out


# ── Site rendering ────────────────────────────────────────────────────────

_SITE_SPEC = {
    "site_title": "FridayHQ", "tagline": "own your AI",
    "pages": [
        {"slug": "home", "nav": "Home", "title": "Welcome", "hero": "Hero.",
         "sections": [{"heading": "Why", "body": "Because.",
                       "bullets": ["a"],
                       "cards": [{"title": "C1", "text": "t1"}]}]},
        {"slug": "docs", "nav": "Docs", "title": "Docs", "hero": "h",
         "sections": [{"heading": "S", "body": "B"}]},
    ],
    "footer": "f",
}


def test_site_render_structure():
    out = se._render_site_html(_SITE_SPEC)
    assert out.count('class="page"') == 2
    assert 'id="page-home"' in out and 'id="page-docs"' in out
    assert 'href="#/docs"' in out and "hashchange" in out
    assert "http://" not in out and "https://" not in out


def test_site_slug_sanitization():
    out = se._render_site_html({
        "site_title": "S", "pages": [
            {"slug": "Weird Slug!!", "nav": "N", "title": "T", "hero": "h",
             "sections": []}],
    })
    assert 'id="page-weird-slug"' in out


# ── End-to-end generators (LLM stubbed) ───────────────────────────────────

@pytest.fixture
def _stub_text_llm(monkeypatch):
    """Route _generate_text to a canned JSON spec; record the prompt."""
    calls = {}

    def fake_generate_text(prompt, **kw):
        calls["prompt"] = prompt
        if "presentation deck" in prompt:
            return json.dumps(_DECK_SPEC)
        return json.dumps(_SITE_SPEC)

    from agent_friday.services import model_router
    monkeypatch.setattr(model_router, "_generate_text", fake_generate_text)
    return calls


def test_generate_presentation_writes_gallery_file(_stub_text_llm):
    res = se.generate_presentation("Agent Friday, for a demo video", slides=3)
    assert res["status"] == "ok"
    f = res["files"][0]
    assert f["filename"].startswith("friday-deck-") and f["filename"].endswith(".html")
    assert f["url"] == f"/api/creations/{f['filename']}"
    from agent_friday.core import CREATIONS_DIR
    body = (CREATIONS_DIR / f["filename"]).read_text(encoding="utf-8")
    assert "Agent Friday" in body
    # Requested slide count must reach the prompt (floor-clamped to 3).
    assert "exactly 3 content slides" in _stub_text_llm["prompt"].lower()


def test_generate_website_writes_gallery_file(_stub_text_llm):
    res = se.generate_website("A site about Agent Friday", pages=2)
    assert res["status"] == "ok"
    f = res["files"][0]
    assert f["filename"].startswith("friday-site-") and f["filename"].endswith(".html")
    from agent_friday.core import CREATIONS_DIR
    assert (CREATIONS_DIR / f["filename"]).exists()


def test_generate_presentation_requires_topic():
    assert se.generate_presentation("")["status"] == "error"
    assert se.generate_website("")["status"] == "error"


def test_generate_presentation_bad_llm_output(monkeypatch):
    from agent_friday.services import model_router
    monkeypatch.setattr(model_router, "_generate_text",
                        lambda prompt, **kw: "I cannot help with that.")
    res = se.generate_presentation("anything")
    assert res["status"] == "error"


def test_agent_tools_registered():
    """The chat agent must expose both tools with Ring 2 (network) privilege."""
    from agent_friday.services.agent import (
        CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS)
    names = {t["name"] for t in CLAUDE_TOOLS}
    assert {"create_presentation", "create_website"} <= names
    assert "create_presentation" in CLAUDE_TOOL_HANDLERS
    assert "create_website" in CLAUDE_TOOL_HANDLERS
    assert TOOL_RINGS["create_presentation"] == 2
    assert TOOL_RINGS["create_website"] == 2
