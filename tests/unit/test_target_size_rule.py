"""Target size (WCAG 2.2 AA 2.5.8, and 2.5.5 in Big mode; north star NS-33.1-13).

Every control a person presses is at least 24 by 24 CSS pixels, and in Big
mode (body.fr-big) at least 44 by 44, by one rule in the page's stylesheet
rather than control by control. tests/app/specs/target_size.spec.ts measures
the drawn controls on the desktop, in Settings and in several workspaces.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "head.html"]
ROLES = ("button", '[role="button"]', '[role="tab"]', '[role="switch"]', '[role="radio"]', '[role="checkbox"]',
         '[role="menuitem"]', '[role="option"]', "select", "summary")


def _rule(css: str, selector_start: str) -> str:
    i = css.index(selector_start)
    return css[i:css.index("}", i) + 1]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_control_is_at_least_24px(page):
    css = page.read_text(encoding="utf-8")
    rule = _rule(css, 'button, [role="button"], [role="tab"]')
    for r in ROLES:
        assert r in rule, r
    assert re.search(r"min-width:\s*24px;\s*min-height:\s*24px", rule)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_big_mode_makes_every_control_at_least_44px(page):
    css = page.read_text(encoding="utf-8")
    rule = _rule(css, "body.fr-big :is(button")
    for r in ROLES:
        assert r in rule, r
    assert re.search(r"min-width:\s*44px;\s*min-height:\s*44px", rule)


def test_the_app_suite_measures_it():
    assert "target_size.spec.ts" in (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
