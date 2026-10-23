"""A built-in tool that reaches the owner's Google account is held in safe
mode (connectors off) and while all action authority is revoked, though the
checkpoint judges it internal. Found by discovery: every registered tool
whose handler reaches Google is in action_gate.CONNECTED_SERVICE_TOOLS."""
from __future__ import annotations

import inspect
import sys

import pytest

from agent_friday.governance import action_gate
from agent_friday.governance import proof_of_integrity as poi

GOOGLE_MARKERS = ("google_accounts", "gmail_send", "_google_multi_account_tool",
                  "_google_connectivity", "google_api", "gmail_api")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(h))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", h / "vault" / ".governance-key")
    monkeypatch.setitem(sys.modules, "keyring", None)
    from agent_friday.services import action_ledger
    action_ledger.reset_caches()
    return h


def _decide(tool):
    return action_gate.authorize(tool, {"query": "x", "file_id": "f", "title": "t"},
                                 {"is_background_task": True})


def test_every_tool_that_reaches_google_is_listed():
    from agent_friday.services import agent
    missing = []
    for name, handler in agent.CLAUDE_TOOL_HANDLERS.items():
        try:
            src = inspect.getsource(handler)
        except (OSError, TypeError):
            continue
        if any(m in src for m in GOOGLE_MARKERS) and action_gate.classify(name, {})[0] == action_gate.INTERNAL:
            if name not in action_gate.CONNECTED_SERVICE_TOOLS:
                missing.append(name)
    assert not missing, missing


@pytest.mark.parametrize("tool", sorted(action_gate.CONNECTED_SERVICE_TOOLS))
def test_held_in_safe_mode(home, monkeypatch, tool):
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "active", lambda *a, **k: True)
    v = _decide(tool)
    assert v.action == "deny" and "safe mode" in v.reason, v


@pytest.mark.parametrize("tool", sorted(action_gate.CONNECTED_SERVICE_TOOLS))
def test_held_while_authority_is_revoked(home, monkeypatch, tool):
    from agent_friday.services import authority
    monkeypatch.setattr(authority, "hold_reason",
                        lambda: "you revoked all action authority (test); restore it on the Doctor page")
    v = _decide(tool)
    assert v.action == "deny" and "revoked" in v.reason, v


def test_allowed_otherwise(home):
    v = _decide("search_email")
    assert v.action != "deny", v
