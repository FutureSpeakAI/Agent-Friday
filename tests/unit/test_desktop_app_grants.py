"""Desktop control is granted per app, and some actions always ask.

One standing Computer Control grant used to cover every app on the machine.
Each app now has its own tier (none, observe, act); Friday's ring-3 tools and
the Windows desktop connector's pointer and keyboard tools name the app they
land on and are refused unless it is granted enough. Typing into a password
field, clicking something named Delete/Send/Pay/Submit, and Delete outside a
text field wait for the owner whatever the tier.

The desktop itself is faked: a probe says which app is under a point or in
front and what control has the focus.
"""
from __future__ import annotations

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate
from agent_friday.services import approvals, desktop_grants as dg, taint
from agent_friday.services.desktop_grants import Control


class FakeProbe(dg.NullProbe):
    def __init__(self, fg="notepad.exe", at=None, focus=None, under=None, cursor=(5, 5)):
        self.fg = fg
        self.at = at or {}
        self.focus = focus or Control(True, False, "Text editor", "edit")
        self.under = under or {}
        self.cursor = cursor

    def foreground_app(self):
        return self.fg

    def app_at(self, x, y):
        return self.at.get((x, y), self.fg)

    def cursor_pos(self):
        return self.cursor

    def focused_control(self):
        return self.focus

    def control_at(self, x, y):
        return self.under.get((x, y), Control(True, False, "", "pane"))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "_grants_file", lambda: tmp_path / "desktop_app_grants.json")
    monkeypatch.setattr(dg, "_legacy_grant_file", lambda: tmp_path / "cc_permission")
    monkeypatch.setattr(dg, "_POINT_MAPPER", lambda x, y: (x, y))
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    prev = dg.set_probe(FakeProbe())
    yield
    dg.set_probe(prev)
    taint.reset()


def _klass(tool, args=None):
    return action_gate.classify(tool, args or {})[0]


# ── Defaults and the carried-over grant ─────────────────────────────────────

def test_a_new_install_grants_no_app():
    assert dg.grants() == {}
    assert _klass("click", {"x": 1, "y": 1}) == "forbidden"
    assert _klass("screenshot") == "forbidden"


def test_the_old_single_grant_carries_over_as_every_app_act(tmp_path):
    (tmp_path / "cc_permission").write_text("granted", encoding="utf-8")
    assert dg.grants() == {"*": "act"}
    assert dg.load()["origin"].startswith("carried over")
    assert _klass("click", {"x": 1, "y": 1}) == action_gate.INTERNAL


def test_the_carry_over_happens_once(tmp_path):
    dg.grants()                                           # new install: nothing
    (tmp_path / "cc_permission").write_text("granted", encoding="utf-8")
    assert dg.grants() == {}, "a grant given after the list exists must not become every-app"


# ── Per-app decisions ───────────────────────────────────────────────────────

def test_a_granted_app_may_be_clicked_and_an_ungranted_one_may_not():
    dg.set_grant("notepad.exe", "act")
    dg.set_probe(FakeProbe(fg="notepad.exe", at={(10, 10): "notepad.exe",
                                                 (20, 20): "bank.exe"}))
    assert _klass("click", {"x": 10, "y": 10}) == action_gate.INTERNAL
    klass, why = action_gate.classify("click", {"x": 20, "y": 20})
    assert klass == "forbidden" and "bank.exe" in why


def test_observe_only_allows_a_screenshot_and_blocks_a_click_and_typing():
    dg.set_grant("excel.exe", "observe")
    dg.set_probe(FakeProbe(fg="excel.exe"))
    assert _klass("screenshot") == action_gate.INTERNAL
    assert _klass("click", {"x": 3, "y": 3}) == "forbidden"
    assert _klass("type_text", {"text": "hi"}) == "forbidden"
    assert _klass("scroll", {"direction": "down"}) == "forbidden"


def test_an_app_set_to_none_is_refused_even_under_every_app():
    dg.set_grant("*", "act")
    dg.set_grant("bank.exe", "none")
    dg.set_probe(FakeProbe(fg="bank.exe"))
    assert _klass("click", {"x": 1, "y": 1}) == "forbidden"
    assert _klass("screenshot") == "forbidden"


def test_app_names_are_normalised_and_junk_is_refused():
    assert dg.normalize_app(r"C:\Program Files\App\Notepad.EXE") == "notepad.exe"
    assert dg.normalize_app("notepad") == "notepad.exe"
    assert dg.normalize_app("*") == "*"
    assert dg.normalize_app("../../Tools/app.exe") == "app.exe"   # the file name only
    for junk in ("", "bad;name", "a|b.exe", "report.pdf"):
        with pytest.raises(ValueError):
            dg.set_grant(junk, "act")
    with pytest.raises(ValueError):
        dg.set_grant("notepad.exe", "everything")


# ── Always ask ──────────────────────────────────────────────────────────────

def test_typing_into_a_password_field_asks_even_in_a_granted_app():
    dg.set_grant("chrome.exe", "act")
    dg.set_probe(FakeProbe(fg="chrome.exe", focus=Control(True, True, "Password", "edit")))
    klass, why = action_gate.classify("type_text", {"text": "hunter2"})
    assert klass == action_gate.OUTWARD and "password" in why


def test_typing_is_refused_when_the_focused_control_cannot_be_identified():
    dg.set_grant("chrome.exe", "act")
    dg.set_probe(FakeProbe(fg="chrome.exe", focus=Control(False)))
    klass, why = action_gate.classify("type_text", {"text": "hello"})
    assert klass == "forbidden" and "password" in why


def test_clicking_something_named_delete_or_send_asks():
    dg.set_grant("outlook.exe", "act")
    dg.set_probe(FakeProbe(fg="outlook.exe", under={
        (1, 1): Control(True, False, "Delete", "button"),
        (2, 2): Control(True, False, "Send", "button"),
        (3, 3): Control(True, False, "Pay now", "button"),
        (4, 4): Control(True, False, "Bold", "button")}))
    assert _klass("click", {"x": 1, "y": 1}) == action_gate.OUTWARD
    assert _klass("click", {"x": 2, "y": 2}) == action_gate.OUTWARD
    assert _klass("click", {"x": 3, "y": 3}) == action_gate.OUTWARD
    assert _klass("click", {"x": 4, "y": 4}) == action_gate.INTERNAL


def test_enter_on_a_submit_control_and_delete_outside_a_text_field_ask():
    dg.set_grant("app.exe", "act")
    dg.set_probe(FakeProbe(fg="app.exe", focus=Control(True, False, "Submit order", "button")))
    assert _klass("press_key", {"key": "enter"}) == action_gate.OUTWARD
    assert _klass("press_key", {"key": "tab"}) == action_gate.INTERNAL
    dg.set_probe(FakeProbe(fg="app.exe", focus=Control(True, False, "Files", "listitem")))
    assert _klass("press_key", {"key": "delete"}) == action_gate.OUTWARD
    dg.set_probe(FakeProbe(fg="app.exe", focus=Control(True, False, "Body", "edit")))
    assert _klass("press_key", {"key": "delete"}) == action_gate.INTERNAL


# ── End to end through the checkpoint and the handler ───────────────────────

@pytest.fixture
def cc_on(monkeypatch):
    monkeypatch.setattr(agent, "_cc_check", lambda: (True, None))
    clicks = []

    class Pag:
        def click(self, x, y, button="left"):
            clicks.append((x, y))

    monkeypatch.setattr(agent, "_pag", Pag())
    monkeypatch.setitem(agent._CC_LAST_SHOT, "scale_x", 1.0)
    monkeypatch.setitem(agent._CC_LAST_SHOT, "scale_y", 1.0)
    return clicks


BG = {"authenticated": True, "is_background_task": True, "task_id": "t-desktop"}


def test_the_real_click_tool_runs_in_a_granted_app_only(cc_on):
    dg.set_grant("notepad.exe", "act")
    dg.set_probe(FakeProbe(at={(10, 10): "notepad.exe", (20, 20): "bank.exe"}))
    out = agent._execute_tool("click", {"x": 10, "y": 10}, session_ctx=BG)
    assert cc_on == [(10, 10)], out
    out = agent._execute_tool("click", {"x": 20, "y": 20}, session_ctx=BG)
    assert cc_on == [(10, 10)] and "bank.exe" in out


def test_a_click_that_asks_is_not_done_without_a_decision(cc_on):
    dg.set_grant("outlook.exe", "act")
    dg.set_probe(FakeProbe(fg="outlook.exe",
                           under={(5, 5): Control(True, False, "Send", "button")}))
    out = agent._execute_tool("click", {"x": 5, "y": 5}, session_ctx=BG)
    assert cc_on == [] and "APPROVAL CARD" in out


def test_switching_windows_after_the_check_does_not_carry_the_grant(cc_on, monkeypatch):
    """The checkpoint saw notepad; by the time the handler runs the point is
    over another app. The handler looks again and does not click."""
    dg.set_grant("notepad.exe", "act")
    probe = FakeProbe(at={(10, 10): "notepad.exe"})
    dg.set_probe(probe)
    real = dg.classify

    def classify_then_switch(tool, args):
        r = real(tool, args)
        probe.at[(10, 10)] = "bank.exe"
        return r
    monkeypatch.setattr(dg, "classify", classify_then_switch)
    out = agent._execute_tool("click", {"x": 10, "y": 10}, session_ctx=BG)
    assert cc_on == [] and "bank.exe" in out


# ── The Windows desktop connector ───────────────────────────────────────────

def _register_desktop(monkeypatch, enabled_tools):
    cfg = {"servers": {"windows_desktop": {"command": "uvx", "args": ["windows-mcp"],
                                           "enabled": True,
                                           "enabled_tools": enabled_tools}}}
    monkeypatch.setattr(agent, "_load_mcp_servers", lambda: cfg)
    tools = [{"name": n, "inputSchema": {"type": "object", "properties": {}}}
             for n in ("State-Tool", "Click-Tool", "Type-Tool", "Powershell-Tool",
                       "Launch-Tool", "Wait-Tool")]
    return agent._mcp_register_server_tools("windows_desktop", tools)


def test_only_the_allowlisted_desktop_tools_register_and_they_are_ring_3(monkeypatch):
    names = _register_desktop(monkeypatch, ["State-Tool", "Wait-Tool"])
    try:
        assert sorted(names) == ["mcp_windows_desktop_State-Tool",
                                 "mcp_windows_desktop_Wait-Tool"]
        assert "mcp_windows_desktop_Click-Tool" not in agent.CLAUDE_TOOL_HANDLERS
        assert all(agent.TOOL_RINGS[n] == 3 for n in names)
    finally:
        agent._mcp_unregister_server_tools("windows_desktop")


def test_desktop_connector_tools_are_judged_by_app_once_enabled(monkeypatch):
    names = _register_desktop(monkeypatch, ["State-Tool", "Click-Tool", "Type-Tool",
                                            "Powershell-Tool"])
    try:
        dg.set_grant("notepad.exe", "observe")
        dg.set_probe(FakeProbe(fg="notepad.exe", at={(7, 7): "notepad.exe"}))
        assert _klass("mcp_windows_desktop_State-Tool") == action_gate.INTERNAL
        assert _klass("mcp_windows_desktop_Click-Tool", {"loc": [7, 7]}) == "forbidden"
        dg.set_grant("notepad.exe", "act")
        assert _klass("mcp_windows_desktop_Click-Tool", {"loc": [7, 7]}) == action_gate.INTERNAL
        # Not tied to one app: always a decision, whatever is granted.
        assert _klass("mcp_windows_desktop_Powershell-Tool",
                      {"command": "dir"}) == action_gate.OUTWARD
        assert "mcp_windows_desktop_Powershell-Tool" in names
    finally:
        agent._mcp_unregister_server_tools("windows_desktop")


def test_a_desktop_connector_tool_needs_the_computer_control_grant(monkeypatch):
    _register_desktop(monkeypatch, ["State-Tool"])
    try:
        dg.set_grant("*", "act")
        monkeypatch.setattr(agent, "_cc_check", lambda: (False, "Computer control is off"))
        allowed, reason = agent._governance_check(
            "mcp_windows_desktop_State-Tool", {}, session_ctx=BG)
        assert not allowed and "Computer control is off" in reason
    finally:
        agent._mcp_unregister_server_tools("windows_desktop")
