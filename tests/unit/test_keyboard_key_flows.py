"""The key flows by keyboard and screen reader (north star NS-34.14-2; WCAG 2.1.1, 2.4.6, 4.1.2).

tests/app/specs/keyboard_flows.spec.ts does four flows with no mouse, finding
each control by the name a screen reader announces: the privacy settings,
approving one waiting action and denying another, stopping a running task,
and finding a receipt. Writing it found three faults, fixed here:
  - in the Activity dialog the arrow keys changed the selected tab but left
    focus on the old one, now out of the Tab order (WAI-ARIA tabs: focus goes
    with the selection; Home and End too);
  - every receipt's button was named just "Open";
  - with two approvals waiting, a screen reader heard two identical "Approve"
    buttons: each card is now a group named by its title.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["index.html", "ui_parts/app.html"]


def _t(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", PAGES)
def test_activity_tabs_move_focus_with_the_selection(rel):
    s = _t(rel)
    body = s[s.index("function LaneBody("):]
    body = body[:body.index("return h(React.Fragment")]
    assert "e.key === 'Home' ? 0 : e.key === 'End' ? n - 1" in body
    assert "setTimeout(() => { const b = document.getElementById('lane-tab-' + next); if (b) b.focus(); }, 0);" in body


@pytest.mark.parametrize("rel", PAGES)
def test_a_receipts_button_names_its_receipt(rel):
    assert "'aria-label': 'Open the trace: ' + (t.label || t.kind || 'a trace')" in _t(rel)


def test_each_waiting_card_is_a_named_group():
    assert '"data-approval-id": a.approval_id,\n    role: "group",\n    "aria-label": a.title || \'A request waiting for you\',' in _t("index.html")
    assert "data-approval-id={a.approval_id} role=\"group\" aria-label={a.title||'A request waiting for you'}" in _t("ui_parts/app.html")


def test_the_app_suite_runs_the_flows():
    assert "keyboard_flows.spec.ts" in _t(".github/workflows/tests.yml")
    spec = _t("tests/app/specs/keyboard_flows.spec.ts")
    assert ".click(" not in spec, "no mouse in a keyboard flow"
