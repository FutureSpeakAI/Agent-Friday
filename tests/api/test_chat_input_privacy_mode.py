"""Chat input masking follows recorded cloud consent, not routing preference."""

import copy
import json

import pytest

from agent_friday import core
from agent_friday.routes import chat
from agent_friday.routing.model_router import ModelRouter


@pytest.mark.parametrize("choice", ["cloud_unrestricted", "cloud_guarded"])
def test_sonnet_chat_input_and_context_obey_recorded_consent(
        client, monkeypatch, patch_app, choice):
    settings = copy.deepcopy(core.DEFAULT_SETTINGS)
    settings["orchestrator_model"] = "claude-sonnet-5-5"
    settings["model_routing"].update({
        "mode": "local_preferred", "vault_local_only": False,
        "unrestricted_cloud": True,
        "cloud_consent": {"answered": True, "choice": choice},
    })
    patch_app("_load_settings", lambda: settings)
    monkeypatch.setattr(core, "_load_privacy_watchlist", lambda: ["Private appointment"])
    monkeypatch.setattr(core, "_owner_emails", lambda: [])
    patch_app("_build_memory_context_block", lambda *a, **k: "")
    patch_app("_build_context_prompt", lambda *a, **k: (
        "Context: Private appointment with sender@example.test", []))
    patch_app("_index_chat_turn", lambda *a, **k: None)
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": "cloud", "model": "claude-sonnet-5-5", "is_local": False,
        "vault_allowed": False, "vault_access": False, "scrub_pii": True,
        "refuse": False, "warning": None,
    })
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy({"messages": messages, **kwargs}))
        return "Example response.", []

    monkeypatch.setattr(chat, "_call_claude_agent", dispatch)
    response = client.post("/api/chat", json={
        "message": "Discuss Private appointment with sender@example.test",
    })
    assert response.status_code == 200
    assert len(seen) == 1
    sent = json.dumps({"messages": seen[0]["messages"], "system": seen[0]["system"]})
    if choice == "cloud_unrestricted":
        assert sent.count("Private appointment") >= 2
        assert sent.count("sender@example.test") >= 2
        assert not seen[0]["pii_lookup"]
        assert chat.PRIVACY_PLACEHOLDERS_NOTE not in seen[0]["system"]
    else:
        assert "Private appointment" not in sent
        assert "sender@example.test" not in sent
        assert seen[0]["pii_lookup"]
        # The note rides with the per-turn context in the newest user turn,
        # never in the system prompt (which stays the same every turn).
        assert chat.PRIVACY_PLACEHOLDERS_NOTE not in seen[0]["system"]
        turn = seen[0]["messages"][-1]["content"]
        turn_text = turn if isinstance(turn, str) else "".join(b.get("text", "") for b in turn)
        assert chat.PRIVACY_PLACEHOLDERS_NOTE in turn_text
