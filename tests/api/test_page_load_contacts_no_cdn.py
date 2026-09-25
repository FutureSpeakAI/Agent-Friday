"""A page load contacts no CDN unless the owner allows Google Fonts.

The page's typefaces come from /static/fonts/fonts.css (local files, then
system faces). Google Fonts is added only while the font files are missing
and `web_fonts_from_google` is on. MediaPipe is fetched only when tracking is
turned on, never with the page.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import web_fonts

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fonts_setting(monkeypatch):
    real = core._load_settings

    def set_to(value):
        monkeypatch.setattr(core, "_load_settings",
                            lambda *a, **k: {**(real(*a, **k) or {}),
                                             "web_fonts_from_google": value})
    return set_to


def _external_loads(html: str) -> list:
    """Every <link href> and <script src> that points off this PC."""
    tags = re.findall(r"<(?:link|script)\b[^>]*\b(?:href|src)\s*=\s*[\"'](https?://[^\"']+)", html)
    return [t for t in tags if not re.match(r"https?://(127\.0\.0\.1|localhost)", t)]


@pytest.mark.parametrize("path", ["/", "/w/news", "/widget"])
def test_with_google_fonts_off_the_page_loads_nothing_external(client, fonts_setting, monkeypatch, path):
    monkeypatch.setattr(web_fonts, "vendored", lambda: False)
    fonts_setting(False)
    html = client.get(path).get_data(as_text=True)
    assert "/static/fonts/fonts.css" in html
    assert _external_loads(html) == []


def test_google_fonts_is_used_only_while_the_files_are_missing(client, fonts_setting, monkeypatch):
    fonts_setting(True)
    monkeypatch.setattr(web_fonts, "vendored", lambda: False)
    assert "fonts.googleapis.com" in client.get("/").get_data(as_text=True)
    monkeypatch.setattr(web_fonts, "vendored", lambda: True)
    assert "fonts.googleapis.com" not in client.get("/").get_data(as_text=True)


def test_the_local_font_stylesheet_is_served(client):
    r = client.get("/static/fonts/fonts.css")
    assert r.status_code == 200
    css = r.get_data(as_text=True)
    for family in ("Orbitron", "Inter", "JetBrains Mono"):
        assert f"font-family: '{family}'" in css
    assert "http" not in css


def test_the_login_page_uses_local_fonts():
    assert "fonts.googleapis.com" not in core.LOGIN_HTML
    assert "/static/fonts/fonts.css" in core.LOGIN_HTML


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/styles_and_scene.html"])
def test_mediapipe_loads_only_when_tracking_is_turned_on(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert not re.search(r"<script[^>]*src=[\"']https://cdn\.jsdelivr\.net/npm/@mediapipe", text)
    for ctor in ("new Hands(", "new FaceDetection("):
        i = text.index(ctor)
        fn = text.rfind("async function toggle", 0, i)
        assert "await loadMediaPipe()" in text[fn:i], ctor
