"""Target size (WCAG 2.2 AA 2.5.8; north star NS-33.1-13).

Every control a person presses is at least 24 by 24 CSS pixels.
tests/app/specs/target_size.spec.ts measures the drawn controls on the
desktop, in Settings and in several workspaces.

One blanket min-width/min-height rule was tried first and dropped: it replaces
the automatic minimum size of flex and grid items ("auto"), so cards laid out
in a grid (Media) shrank below their content and overlapped. Small controls are
fixed where they are drawn instead.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_app_suite_measures_it():
    assert "target_size.spec.ts" in (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")


def test_no_blanket_min_size_rule_on_every_button():
    """The blanket rule overlapped the Media cards (see the docstring)."""
    for page in ("index.html", "ui_parts/head.html"):
        css = (ROOT / page).read_text(encoding="utf-8")
        assert 'input[type="reset"] { min-width: 24px; min-height: 24px; }' not in css, page
