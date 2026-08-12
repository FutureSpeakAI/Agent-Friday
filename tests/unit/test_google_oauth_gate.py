"""FR-6 — connecting Google routes through the Phase A3 human-gate primitive
(services/approvals.py::gate_action) instead of opening the OAuth consent
screen unattended (docs: toolcall-integrity-v5).

The OAuth flow itself already exists and works (routes/google.py,
routes/google_accounts.py) — this only covers the AGENT-mediated path: what
happens when the open_url tool is asked to open the Google authorize URL.
Directly visiting /api/google/auth in a browser, or running
scripts/friday_google_connect.py, is already an explicit human action and
is out of scope for this gate.
"""
from __future__ import annotations

from unittest.mock import patch

import agent_friday.services.agent as agent_mod
from agent_friday.services import approvals


class TestIsGoogleOauthUrl:
    def test_recognizes_legacy_auth_url(self):
        assert agent_mod._is_google_oauth_url("http://localhost:3000/api/google/auth")

    def test_recognizes_multi_account_connect_url(self):
        assert agent_mod._is_google_oauth_url(
            "http://localhost:3000/api/google/accounts/connect")

    def test_does_not_flag_unrelated_urls(self):
        assert not agent_mod._is_google_oauth_url("https://reddit.com")
        assert not agent_mod._is_google_oauth_url("http://localhost:3000/api/google/status")


class TestOpenUrlToolGatesGoogleOauth:
    def test_pending_approval_does_not_open_browser(self, monkeypatch):
        opened = []
        monkeypatch.setattr(agent_mod, "_open_url_in_browser",
                             lambda url: opened.append(url) or f"Opened: {url}")
        monkeypatch.setattr(
            approvals, "gate_action",
            lambda **kw: {"status": "pending", "approval": {"approval_id": "x"}})
        result = agent_mod._tool_open_url({"url": "http://localhost:3000/api/google/auth"})
        assert opened == []
        assert "approval request" in result
        assert "Settings" in result

    def test_auto_approved_opens_immediately(self, monkeypatch):
        opened = []
        monkeypatch.setattr(agent_mod, "_open_url_in_browser",
                             lambda url: opened.append(url) or f"Opened: {url}")
        monkeypatch.setattr(
            approvals, "gate_action",
            lambda **kw: {"status": "auto_approved", "approval": {}})
        result = agent_mod._tool_open_url({"url": "http://localhost:3000/api/google/auth"})
        assert opened == ["http://localhost:3000/api/google/auth"]
        assert "Opened" in result

    def test_denied_does_not_open_browser(self, monkeypatch):
        opened = []
        monkeypatch.setattr(agent_mod, "_open_url_in_browser",
                             lambda url: opened.append(url) or "Opened")
        monkeypatch.setattr(
            approvals, "gate_action",
            lambda **kw: {"status": "denied", "approval": {}})
        result = agent_mod._tool_open_url({"url": "http://localhost:3000/api/google/auth"})
        assert opened == []
        assert "declined" in result.lower()

    def test_gate_action_called_with_force_gate_and_never_send_scope_language(self, monkeypatch):
        captured = {}

        def fake_gate_action(**kw):
            captured.update(kw)
            return {"status": "pending", "approval": {}}

        monkeypatch.setattr(agent_mod, "_open_url_in_browser", lambda url: "n/a")
        monkeypatch.setattr(approvals, "gate_action", fake_gate_action)
        agent_mod._tool_open_url({"url": "http://localhost:3000/api/google/auth"})
        assert captured["force_gate"] is True
        assert captured["kind"] == "connector_auth"
        assert "gmail.send" in captured["action_description"]

    def test_ordinary_url_bypasses_the_gate_entirely(self, monkeypatch):
        gate_calls = []
        monkeypatch.setattr(approvals, "gate_action",
                             lambda **kw: gate_calls.append(kw) or {"status": "denied"})
        with patch.object(agent_mod, "_open_url_in_browser", return_value="Opened") as m:
            agent_mod._tool_open_url({"url": "https://reddit.com"})
        assert gate_calls == []
        m.assert_called_once_with("https://reddit.com")


class TestResumeHookOnApproval:
    def test_approved_record_opens_the_stored_url(self, monkeypatch):
        opened = []
        monkeypatch.setattr(agent_mod, "_open_url_in_browser", lambda url: opened.append(url))
        agent_mod._resume_google_oauth_open({
            "status": "approved",
            "payload": {"url": "http://localhost:3000/api/google/auth"},
        })
        assert opened == ["http://localhost:3000/api/google/auth"]

    def test_denied_record_does_not_open_anything(self, monkeypatch):
        opened = []
        monkeypatch.setattr(agent_mod, "_open_url_in_browser", lambda url: opened.append(url))
        agent_mod._resume_google_oauth_open({
            "status": "denied",
            "payload": {"url": "http://localhost:3000/api/google/auth"},
        })
        assert opened == []

    def test_registration_call_wires_the_right_function_to_the_right_kind(self):
        # NOTE: don't assert against the ambient approvals._HOOKS state — at
        # least one other test file (test_goal_restart_persistence.py)
        # legitimately does importlib.reload(approvals), which re-executes
        # `_HOOKS: Dict[...] = {}` and wipes every hook registered by modules
        # (like agent.py) that were only ever imported once, not reloaded.
        # That's correct behavior for a real process (agent.py registers once
        # at startup and is never reloaded) — it's only an artifact of
        # sharing one Python process across the test suite. Exercise the
        # exact registration call agent.py's module-level code makes instead
        # of depending on ambient global state.
        before = list(approvals._HOOKS.get("connector_auth", []))
        approvals.register_decision_hook("connector_auth", agent_mod._resume_google_oauth_open)
        after = approvals._HOOKS.get("connector_auth", [])
        assert after == before + [agent_mod._resume_google_oauth_open]
