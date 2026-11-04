"""Logical focus order (north star NS-33.1-3; WCAG 2.2 2.4.3, failure F44).

Tab follows the order of the page only while nothing sets a positive tabindex: a
tabindex of 1 or more jumps that control ahead of everything else. None of the
pages does today; this keeps it so. The app spec focus_order.spec.ts walks every
workspace with Tab and fails on a stop no one can see (a closed panel or a hidden
control still taking focus), and runs in the app-specs job.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["index.html", "ui_parts/app.html", "ui_parts/head.html", "ui_parts/styles_and_scene.html"]
POSITIVE = re.compile(r"""tabindex\s*=\s*["']?\s*[1-9]|tabIndex\s*[:=]\s*\{?\s*["']?[1-9]|\.tabIndex\s*=\s*[1-9]""", re.I)


@pytest.mark.parametrize("rel", PAGES)
def test_no_control_jumps_the_tab_order(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    hits = [text[max(0, m.start() - 60):m.end() + 20].replace("\n", " ") for m in POSITIVE.finditer(text)]
    assert hits == [], "a positive tabindex moves a control out of page order: %s" % hits[:5]


def test_the_rule_catches_what_it_should():
    for s in ('tabindex="2"', "tabIndex: 1,", "tabIndex={3}", "el.tabIndex = 5"):
        assert POSITIVE.search(s), s
    for s in ('tabindex="0"', 'tabindex="-1"', "tabIndex: 0,", "tabIndex={-1}", "tabIndex={0}"):
        assert not POSITIVE.search(s), s


def test_the_focus_walk_runs_in_ci():
    spec = ROOT / "tests" / "app" / "specs" / "focus_order.spec.ts"
    assert spec.exists(), "no app spec walks the workspaces with Tab"
    wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "focus_order.spec.ts" in wf, "the focus walk is not in the app-specs job"
