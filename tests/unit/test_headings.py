"""Headings a screen reader can move by (WCAG 2.2 1.3.1 and 2.4.6; north star NS-33.1-6).

The page had no top-level heading, and a window's title was a plain span, so a
person who lists headings (or presses H in a screen reader) could not go from
window to window. The page now has one h1 (read, not drawn) and each window's
title is a level-2 heading. tests/app/specs/headings.spec.ts checks it in a
real page and prints each window's outline.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _t(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/styles_and_scene.html"])
def test_the_page_has_one_top_level_heading(rel):
    s = _t(rel)
    assert s.count('<h1 class="fr-sr-only">Agent Friday™</h1>') == 1
    assert s.index('<div id="ui-root"') < s.index('<h1 class="fr-sr-only">')


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/head.html"])
def test_read_but_not_drawn(rel):
    css = _t(rel)
    assert ".fr-sr-only { position: absolute !important; width: 1px; height: 1px;" in css
    assert "clip: rect(0 0 0 0); clip-path: inset(50%);" in css


def test_a_windows_title_is_a_level_2_heading():
    assert 'className: "fwin-title-text",\n    role: "heading",\n    "aria-level": 2\n  }, title)' in _t("index.html")
    assert '<span className="fwin-title-text" role="heading" aria-level={2}>{title}</span>' in _t("ui_parts/app.html")


def test_the_app_suite_checks_it():
    assert "headings.spec.ts" in _t(".github/workflows/tests.yml")


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_a_documents_headings_sit_under_the_window_title(rel):
    """Markdown rendered in a window (a page, a reply) is read with its headings two levels down: its
    "# Title" is level 3, under the window's level 2, and never a second h1."""
    s = _t(rel)
    assert "body = body.replace(/<h([1-6])(?=[\\s>])/gi, (m, n) => m + ' aria-level=\"' + Math.min(6, Number(n) + 2) + '\"');" in s
    i = s.index("function renderFridayMarkdown(")
    assert s.index("aria-level=\"' + Math.min(6", i) < s.index("\n}", i), "inside renderFridayMarkdown"


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_a_windows_own_headings_are_read_under_its_title(rel):
    s = _t(rel)
    fwin = s[s.index("function FWin("):]
    assert "body.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {" in fwin
    assert "if (!h.hasAttribute('aria-level')) h.setAttribute('aria-level', String(Math.max(3, Math.min(6, Number(h.tagName[1]) + 1))));" in fwin
    assert "mo.observe(body, { childList: true, subtree: true });" in fwin
