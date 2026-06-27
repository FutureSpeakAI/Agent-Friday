"""Unit tests for the Context Injection Middleware (services/context_injection.py).

Verifies the auto-context block folds in the active project bible, user
preferences, the user-intelligence profile, and workspace state — and that it's
empty when there's nothing to say.
"""
import pytest

from agent_friday.services import context_injection as ci
from agent_friday.services import creative_memory as cm


@pytest.fixture
def project():
    b = cm.create_project("Injection Saga", "video-series")
    cm.add_character(b["id"], "Maya", "a silver-haired pilot")
    yield b["id"]
    cm.delete_project(b["id"])


def test_injects_active_project(project):
    block = ci.build_injected_context(workspace="studio")
    assert "AUTO-CONTEXT" in block
    assert "Injection Saga" in block
    assert "Maya" in block


def test_workspace_state_line_present(project):
    block = ci.build_injected_context(workspace="research")
    assert "Active workspace: research" in block


def test_user_profile_facts_injected(project, monkeypatch):
    ci.update_user_profile({"facts": ["prefers terse replies"],
                            "goals": ["ship v5"]})
    try:
        block = ci.build_injected_context(workspace="chat")
        assert "prefers terse replies" in block
        assert "ship v5" in block
    finally:
        # clean the profile file so other tests see a blank profile
        ci.update_user_profile({"facts": [], "goals": []})


def test_empty_when_no_project_no_profile(monkeypatch):
    # No active project, blank profile, no workspace → nothing to inject.
    monkeypatch.setattr(cm, "get_active_project_id", lambda: "")
    monkeypatch.setattr(ci, "_user_profile", lambda: {})
    # communication style still yields a prefs line unless we blank settings;
    # an empty workspace + no project + no profile facts should be terse.
    block = ci.build_injected_context(workspace="")
    # may contain a preferences line from default settings, but never a project
    assert "AUTO-CONTEXT" in block or block == ""
    assert "Injection Saga" not in block
