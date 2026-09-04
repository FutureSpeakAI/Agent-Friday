"""Gauntlet finding F30 (call site 3 of 4): services/creative_pipeline.py's
`_exec_text_stage` predicts a provider ONCE (`_predict_route_provider`),
bakes the system prompt for that ONE provider via `_get_friday_system_prompt`
(full TIER_2/3 vault content when the prediction is 'local'), then hands the
baked string to `_generate_text`'s fallback ladder (services/model_router.py).
That ladder's own `vault_access` guard only blocks fallback when the MESSAGE
itself is vault-forced -- not when the SYSTEM PROMPT was built assuming
local for an ordinary reason. Before the fix, if the primary local leg then
failed operationally, the ladder fell through to a cloud leg REUSING the
untouched, fully-tiered system string with zero re-gating for that provider.

This probe predicts/routes 'local' for an ORDINARY (non-vault-forced)
pipeline text stage, bakes a real TIER_2-shaped system prompt, makes the
local leg fail, and asserts the cloud leg that actually produces the stage's
output never sees the TIER_2 secret.

Must be RED before the fix (`_exec_text_stage` never threaded a
`system_builder` through to `_generate_text`, so the static local-gated
`system` string rode onto the cloud leg unchanged) and GREEN after.
"""
from __future__ import annotations

import agent_friday.routing.model_router as rr
import agent_friday.services.creative_pipeline as cp_mod
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
    # `_exec_text_stage` and `_generate_text` both reference `_gated_vault_control`
    # via `mr` (creative_pipeline.py imports it lazily inside the function;
    # `_generate_text` calls it via its own `system_builder` closure, which is
    # itself defined in creative_pipeline.py and also resolves the name
    # lazily) -- patching the source module covers both.
    monkeypatch.setattr(mr, "_gated_vault_control", lambda: VaultAccessControl())
    monkeypatch.setattr(mr, "get_anthropic_client", lambda *a, **k: object())
    monkeypatch.setattr(
        rr, "get_router",
        lambda *a, **k: _FakeRouter({
            "provider": "local", "model": "local-model-x", "is_local": True,
            "vault_access": False, "refuse": False,
        }))
    monkeypatch.setattr(mr, "_mode_filtered_attempts",
                        lambda attempts, *a, **k: attempts)
    monkeypatch.setattr(mr, "_health_order", lambda attempts, *a, **k: attempts)
    monkeypatch.setattr(mr, "_call_ollama",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("local seat down")))


class TestCreativePipelineFallbackRegatesPrompt:
    def test_cloud_fallback_never_sees_local_gated_tier2_content(self, monkeypatch):
        _patch_common(monkeypatch)
        captured = {}

        def _fake_claude(messages, system=None, **kw):
            captured['system'] = system
            return "cloud stage output"

        def _fake_openai(messages, system=None, **kw):
            captured.setdefault('system', system)
            return ("openai stage output", [])

        monkeypatch.setattr(mr, "_call_claude", _fake_claude)
        monkeypatch.setattr(mr, "_call_openai", _fake_openai)

        out = cp_mod._exec_text_stage(
            {"workspace": "content"}, "draft the newsletter intro", {})

        assert out in ("cloud stage output", "openai stage output")
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
            return ("local stage output", [])

        monkeypatch.setattr(mr, "_call_ollama", _fake_ollama)

        out = cp_mod._exec_text_stage(
            {"workspace": "content"}, "draft the newsletter intro", {})

        assert out == "local stage output"
        assert TIER2_SECRET in (captured.get('system') or ''), (
            "sanity check failed: the local leg itself never received the "
            "TIER_2 secret, so the fallback test above cannot be trusted"
        )
