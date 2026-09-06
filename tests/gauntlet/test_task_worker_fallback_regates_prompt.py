"""Gauntlet finding F30 (call site 1 of 4): services/agent.py's background-task
worker (`_task_worker`) predicts a provider ONCE (`_predict_route_provider`),
bakes the system prompt for that ONE provider via `_get_friday_system_prompt`
(full TIER_2/3 vault content when the prediction is 'local'), then hands the
baked string to `_generate_agent`'s fallback ladder. That ladder's own
`vault_access` guard only blocks fallback when the MESSAGE itself is
vault-forced -- not when the SYSTEM PROMPT was built assuming local for an
ordinary reason. Before the fix, if the primary local leg then failed
operationally (seat down, timeout), the ladder fell through to a cloud leg
REUSING the untouched, fully-tiered system string with zero re-gating for
that provider.

This probe predicts/routes 'local' for an ORDINARY (non-vault-forced) task,
bakes a real TIER_2-shaped system prompt (via the actual
`_get_friday_system_prompt` -> `_build_context_prompt` -> voice_engine live
context injection, not a stub of the tiering itself), makes the local leg
fail, and asserts the cloud leg that actually serves the task never sees the
TIER_2 secret.

Must be RED before the fix (`_task_worker` never threaded a `system_builder`
through to `_generate_agent`, so the static local-gated `system` string rode
onto the cloud leg unchanged) and GREEN after.
"""
from __future__ import annotations

import agent_friday.routing.model_router as rr
import agent_friday.services.agent as agent_mod
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
    # Demo mode returns a canned placeholder before the router is ever
    # consulted -- force it off so this test actually exercises the routed
    # dispatch path on any machine (F35's lesson, applied here too).
    monkeypatch.setattr(demo_mode, "is_demo", lambda *a, **k: False)
    # A REAL, unconditional TIER_2 injection point (not a stub of the tiering
    # logic under test) -- see tests/unit/test_ungated_prompt_sweep.py's
    # docstring for why this section is the right probe.
    monkeypatch.setattr(ve, "_load_live_context", lambda: TIER2_SECRET)
    # `_gated_vault_control` is imported into agent.py's OWN namespace
    # (`from ... import _gated_vault_control`), so it must be patched on
    # `agent_mod`, not `mr` -- patching the source module would not affect
    # the name already bound inside agent.py.
    monkeypatch.setattr(agent_mod, "_gated_vault_control",
                        lambda: VaultAccessControl())
    monkeypatch.setattr(agent_mod, "get_anthropic_client",
                        lambda *a, **k: object())
    # Predict AND route 'local' for an ORDINARY reason (vault_access=False) --
    # the exact scenario F30 describes, distinct from the already-fixed
    # vault-forced case (F35).
    monkeypatch.setattr(
        rr, "get_router",
        lambda *a, **k: _FakeRouter({
            "provider": "local", "model": "local-model-x", "is_local": True,
            "vault_access": False, "refuse": False,
        }))
    # Keep the fallback ladder's ordering deterministic -- these read ambient
    # health/mode state this test does not control.
    monkeypatch.setattr(mr, "_mode_filtered_attempts",
                        lambda attempts, *a, **k: attempts)
    monkeypatch.setattr(mr, "_health_order", lambda attempts, *a, **k: attempts)
    # The local leg fails operationally (seat down) -- the exact trigger F30
    # names ("its primary attempt fails operationally (seat down, timeout)").
    monkeypatch.setattr(agent_mod, "_call_ollama",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("local seat down")))


class TestTaskWorkerFallbackRegatesPrompt:
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

        agent_mod._task_worker(
            "t-f30-taskworker", "F30 probe", "summarize my pending errands")

        assert 'system' in captured, (
            "neither cloud fallback leg (_call_claude_agent / _call_openai) "
            "was ever reached -- the fallback ladder itself is broken, "
            "independent of this finding"
        )
        assert TIER2_SECRET not in (captured['system'] or ''), (
            "the cloud fallback leg received the system prompt baked for "
            "'local' (F30): TIER_2 content was not re-gated for the "
            "provider actually called"
        )

    def test_sanity_local_leg_keeps_tier2(self, monkeypatch):
        """Falsifiability check: when the local leg SUCCEEDS, the TIER_2
        secret must actually be present in what it receives -- otherwise the
        first test would pass vacuously because nothing was ever gated for
        'local' in the first place."""
        _patch_common(monkeypatch)
        captured = {}

        def _fake_ollama(messages, system=None, **kw):
            captured['system'] = system
            return "local reply", []

        monkeypatch.setattr(agent_mod, "_call_ollama", _fake_ollama)

        agent_mod._task_worker(
            "t-f30-taskworker-local", "F30 probe (local)",
            "summarize my pending errands")

        assert TIER2_SECRET in (captured.get('system') or ''), (
            "sanity check failed: the local leg itself never received the "
            "TIER_2 secret, so the fallback test above cannot be trusted"
        )
