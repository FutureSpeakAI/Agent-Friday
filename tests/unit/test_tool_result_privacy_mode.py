"""Tool results honor recorded cloud consent independently of routing mode."""

import json
import types

import pytest

from agent_friday import core
from agent_friday.services import agent, egress_gate as eg, tool_hooks


@pytest.fixture
def private_result(monkeypatch):
    monkeypatch.setattr(core, "_load_privacy_watchlist", lambda: ["Private appointment"])
    monkeypatch.setattr(core, "_owner_emails", lambda: [])
    return json.dumps({
        "calendar": [{"title": "Private appointment", "time": "09:00"}],
        "unread": [{"from": "sender@example.test", "subject": "Private appointment"}],
    })


def _post_and_seal(result, lookup):
    ctx = tool_hooks.HookContext(tool_name="search_email", input={}, pii_lookup=lookup)
    processed = agent._hook_pii_scrub(ctx, result)
    payload = {"messages": [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "call-inbox", "content": processed,
    }]}]}
    return eg.seal_outbound(payload, "anthropic")["messages"][0]["content"][0]["content"]


@pytest.mark.parametrize("mode", ["local_preferred", "cloud_only"])
@pytest.mark.parametrize("with_lookup", [True, False])
def test_recorded_unrestricted_preserves_complete_tool_results(
        monkeypatch, private_result, mode, with_lookup):
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {
        "mode": mode, "unrestricted_cloud": False,
        "cloud_consent": {"answered": True, "choice": "cloud_unrestricted"},
    }})
    monkeypatch.setattr(eg, "_classify_cloud", lambda _: pytest.fail("privacy classifier ran"))
    lookup = {} if with_lookup else None

    assert _post_and_seal(private_result, lookup) == private_result
    assert not lookup


def test_recorded_guarded_overrides_stale_unrestricted_flag(monkeypatch, private_result):
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {
        "mode": "local_preferred", "unrestricted_cloud": True,
        "cloud_consent": {"answered": True, "choice": "cloud_guarded"},
    }})
    monkeypatch.setattr(eg, "_classify_cloud", lambda _: eg.Tier.SENSITIVE)
    assert eg.is_unrestricted_cloud() is False
    lookup = {}
    result = _post_and_seal(private_result, lookup)
    assert result != private_result
    assert "Private appointment" not in result
    assert "sender@example.test" not in result
    assert lookup


def test_failed_consent_read_keeps_tool_scrub(monkeypatch, private_result):
    def unreadable():
        raise OSError("settings unavailable")

    monkeypatch.setattr(core, "_load_settings", unreadable)
    ctx = tool_hooks.HookContext(tool_name="search_email", input={}, pii_lookup={})
    result = agent._hook_pii_scrub(ctx, private_result)
    assert "Private appointment" not in result
    assert "sender@example.test" not in result
    assert ctx.pii_lookup


@pytest.mark.parametrize("choice", ["cloud_unrestricted", "cloud_guarded"])
def test_native_sonnet_legacy_input_redaction_obeys_recorded_consent(monkeypatch, choice):
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {
        "mode": "local_preferred", "unrestricted_cloud": True,
        "cloud_consent": {"answered": True, "choice": choice},
    }})
    monkeypatch.setattr(core, "_load_privacy_watchlist", lambda: ["Private appointment"])
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Example response.")],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    monkeypatch.setattr(agent, "get_anthropic_client", lambda: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(agent, "_get_vault_control", lambda: None)
    monkeypatch.setattr(agent, "_register_agent_orb", lambda *a, **k: None)
    # Isolate the extra redactor before the separately tested outbound gate.
    monkeypatch.setattr(agent, "_seal_or_block", lambda payload, provider: payload)
    agent._call_claude_agent(
        [{"role": "user", "content": "Discuss Private appointment"}],
        system="Context: Private appointment", model="claude-sonnet-5-5",
        pii_lookup=None, session_ctx={},
    )
    assert len(seen) == 1
    sent = json.dumps({"messages": seen[0]["messages"], "system": seen[0]["system"]})
    if choice == "cloud_unrestricted":
        assert sent.count("Private appointment") == 2
        assert "[REDACTED]" not in sent
    else:
        assert "Private appointment" not in sent
        assert "[REDACTED]" in sent
