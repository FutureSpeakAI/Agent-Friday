"""Gauntlet finding F30 (call site 2 of 4): services/channels/manager.py's
`_run_agent` predicts a provider ONCE (via `_system_prompt` ->
`_predict_route_provider`), bakes the system prompt for that ONE provider
(full TIER_2/3 vault content when the prediction is 'local'), then hands the
baked string to `_generate_agent`'s fallback ladder. That ladder's own
`vault_access` guard only blocks fallback when the MESSAGE itself is
vault-forced -- not when the SYSTEM PROMPT was built assuming local for an
ordinary reason. Before the fix, if the primary local leg then failed
operationally, the ladder fell through to a cloud leg REUSING the untouched,
fully-tiered system string with zero re-gating for that provider.

This probe predicts/routes 'local' for an ORDINARY (non-vault-forced)
channel message, bakes a real TIER_2-shaped system prompt, makes the local
leg fail, and asserts the cloud leg that actually replies to the channel
never sees the TIER_2 secret.

Must be RED before the fix (`_run_agent` never threaded a `system_builder`
through to `_generate_agent`, so the static local-gated `system` string rode
onto the cloud leg unchanged) and GREEN after.
"""
from __future__ import annotations

import agent_friday.routing.model_router as rr
import agent_friday.services.agent as agent_mod
import agent_friday.services.channels.manager as chan_mod
import agent_friday.services.demo_mode as demo_mode
import agent_friday.services.model_router as mr
import agent_friday.services.voice_engine as ve
from agent_friday.privacy.vault_access import VaultAccessControl

TIER2_SECRET = "renew the passport before the custody hearing"


class _FakeRouter:
    def __init__(self, result):
        self._result = result

    def route(self, messages, task_context=None):
        return self._result


def _patch_common(monkeypatch):
    monkeypatch.setattr(demo_mode, "is_demo", lambda *a, **k: False)
    monkeypatch.setattr(ve, "_load_live_context", lambda: TIER2_SECRET)
    # `_gated_system_prompt` (channels/manager.py) imports `_gated_vault_control`
    # LAZILY, fresh, on every call -- so patching the source module (`mr`)
    # takes effect, unlike a module-top `from ... import` binding.
    monkeypatch.setattr(mr, "_gated_vault_control", lambda: VaultAccessControl())
    # `_generate_agent`'s own provider primitives are bound into agent.py's
    # namespace at import time -- must patch them there, not on `mr`.
    monkeypatch.setattr(agent_mod, "get_anthropic_client",
                        lambda *a, **k: object())
    monkeypatch.setattr(
        rr, "get_router",
        lambda *a, **k: _FakeRouter({
            "provider": "local", "model": "local-model-x", "is_local": True,
            "vault_access": False, "refuse": False,
        }))
    monkeypatch.setattr(mr, "_mode_filtered_attempts",
                        lambda attempts, *a, **k: attempts)
    monkeypatch.setattr(mr, "_health_order", lambda attempts, *a, **k: attempts)
    monkeypatch.setattr(agent_mod, "_call_ollama",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("local seat down")))


class TestChannelsManagerFallbackRegatesPrompt:
    def test_cloud_fallback_never_sees_local_gated_tier2_content(self, monkeypatch):
        _patch_common(monkeypatch)
        captured = {}

        def _fake_claude_agent(messages, system=None, **kw):
            captured['system'] = system
            return "cloud reply", []

        def _fake_openai(messages, system=None, **kw):
            captured.setdefault('system', system)
            return "openai reply", []

        monkeypatch.setattr(agent_mod, "_call_claude_agent", _fake_claude_agent)
        monkeypatch.setattr(agent_mod, "_call_openai", _fake_openai)

        reply = chan_mod._run_agent("what's on my errand list today?")

        assert reply in ("cloud reply", "openai reply")
        assert 'system' in captured, (
            "neither cloud fallback leg was ever reached -- the fallback "
            "ladder itself is broken, independent of this finding"
        )
        assert TIER2_SECRET not in (captured['system'] or ''), (
            "the cloud fallback leg received the system prompt baked for "
            "'local' (F30): TIER_2 content was not re-gated for the "
            "provider actually called"
        )

    def test_sanity_local_leg_keeps_tier2(self, monkeypatch):
        """Falsifiability check: the local leg must actually receive the
        TIER_2 secret, or the fallback test above passes vacuously."""
        _patch_common(monkeypatch)
        captured = {}

        def _fake_ollama(messages, system=None, **kw):
            captured['system'] = system
            return "local reply", []

        monkeypatch.setattr(agent_mod, "_call_ollama", _fake_ollama)

        reply = chan_mod._run_agent("what's on my errand list today?")

        assert reply == "local reply"
        assert TIER2_SECRET in (captured.get('system') or ''), (
            "sanity check failed: the local leg itself never received the "
            "TIER_2 secret, so the fallback test above cannot be trusted"
        )
