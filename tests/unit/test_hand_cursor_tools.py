"""big_mode and hand_cursor: declared per the voice tool contract, and honest about the page.

The contract: declared once in the text registry and shared into voice; internal (ring 1,
INTERNAL_TOOLS); a change is persisted before it is pushed; the reply reports what the page
answered and never claims a move the page did not make. `select` never fires a guarded action.
"""
from __future__ import annotations

import pytest

from agent_friday.services import hand_cursor_tools as hct


def test_tools_are_declared_shared_and_internal():
    from agent_friday.services.agent import CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS, WORKSPACE_TOOLS
    from agent_friday.services.voice_engine import _VOICE_SHARED_TOOLS
    from agent_friday.governance.action_gate import INTERNAL_TOOLS

    # Declared always-on or on demand (agent.ON_DEMAND_TOOLS): the loader and voice reach both.
    names = {t["name"] for t in CLAUDE_TOOLS + WORKSPACE_TOOLS.get("on_demand", [])}
    for name in ("big_mode", "hand_cursor"):
        assert name in names, name
        assert name in CLAUDE_TOOL_HANDLERS, name
        assert TOOL_RINGS.get(name) == 1, name
        assert name in _VOICE_SHARED_TOOLS, name
        assert name in INTERNAL_TOOLS, name


def test_big_mode_persists_then_pushes_and_reports_the_page(monkeypatch):
    saved = {}
    pushed = []
    monkeypatch.setattr(hct, "persist_big_mode", lambda m: saved.setdefault("mode", m))
    monkeypatch.setattr(hct, "push", lambda a: (pushed.append(a), {"delivered": True, "ack": {"0": {"ok": True, "code": "BIG_MODE_ON", "mode": "on"}}})[1])
    said = hct.handle_big_mode({"mode": "on"})
    assert saved["mode"] == "on"
    assert pushed == [{"type": "hand_cursor", "op": "big_mode", "mode": "on"}]
    assert said == "Big mode is on."


def test_big_mode_without_a_page_says_so_and_still_persists(monkeypatch):
    saved = {}
    monkeypatch.setattr(hct, "persist_big_mode", lambda m: saved.setdefault("mode", m))
    monkeypatch.setattr(hct, "push", lambda a: {"delivered": False, "reason": "no page"})
    said = hct.handle_big_mode({"mode": "off"})
    assert saved["mode"] == "off"
    assert "takes effect when the Friday window is open" in said


def test_big_mode_rejects_an_unknown_mode_without_saving(monkeypatch):
    monkeypatch.setattr(hct, "persist_big_mode", lambda m: pytest.fail("must not save"))
    assert hct.handle_big_mode({"mode": "huge"}).startswith("big_mode error")


def test_hand_cursor_select_on_a_guarded_target_never_fires(monkeypatch):
    monkeypatch.setattr(hct, "push", lambda a: {"delivered": True, "ack": {"0": {"ok": False, "code": "CURSOR_GUARDED", "label": "send"}}})
    said = hct.handle_hand_cursor({"action": "select"})
    assert "Pinch and hold" in said and "send" in said


@pytest.mark.parametrize("op,code,label,expect", [
    ("next", "CURSOR_MOVED", "compose", "On compose."),
    ("select", "CURSOR_SELECTED", "refresh", "Selected refresh."),
    ("back", "CURSOR_BACK", "", "Back."),
    ("next", "CURSOR_NO_TARGET", "", "There is nothing to select here."),
])
def test_hand_cursor_reports_what_the_page_did(monkeypatch, op, code, label, expect):
    monkeypatch.setattr(hct, "push", lambda a: {"delivered": True, "ack": {"0": {"ok": True, "code": code, "label": label}}})
    assert hct.handle_hand_cursor({"action": op}) == expect


def test_hand_cursor_without_a_page_does_not_claim_a_move(monkeypatch):
    monkeypatch.setattr(hct, "push", lambda a: {"delivered": False, "reason": "no page"})
    assert "without the Friday window open" in hct.handle_hand_cursor({"action": "next"})


def test_unknown_cursor_action_is_refused_without_a_push(monkeypatch):
    monkeypatch.setattr(hct, "push", lambda a: pytest.fail("must not push"))
    assert hct.handle_hand_cursor({"action": "fling"}).startswith("hand_cursor error")
