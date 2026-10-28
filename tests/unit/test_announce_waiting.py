"""What is waiting for you is heard as well as seen (north star NS-33.3-6; WCAG 4.1.3).

An approval card appeared on screen in a popup that a screen reader did not
announce. The page now keeps two standing live regions (polite and assertive,
read and not drawn), fridayAnnounce() sets text into them, and the approvals
popup says what has just started waiting. tests/app/specs/announce.spec.ts
checks it in a real page.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _t(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/styles_and_scene.html"])
def test_the_page_keeps_two_live_regions(rel):
    s = _t(rel)
    assert '<div id="fr-announce-polite" role="status" aria-live="polite" aria-atomic="true"' in s
    assert '<div id="fr-announce-assertive" role="alert" aria-live="assertive" aria-atomic="true"' in s


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_announce_and_the_approvals_popup_use_them(rel):
    s = _t(rel)
    fn = s[s.index("function fridayAnnounce(msg, opts) {"):]
    fn = fn[:fn.index("\n}\n")]
    assert "opts && opts.assertive ? 'fr-announce-assertive' : 'fr-announce-polite'" in fn
    assert "el.textContent = '';" in fn and "el._frAnnounce = setTimeout(function () { el.textContent = text; }, 60);" in fn
    assert "clearTimeout(el._frAnnounce);" in fn
    assert "if (fresh) fridayAnnounce(freshItems.length === 1" in s
    assert "' is waiting for your approval: ' + (freshItems[0].title || 'a request') + '.'" in s


def test_the_app_suite_checks_it():
    assert "announce.spec.ts" in _t(".github/workflows/tests.yml")