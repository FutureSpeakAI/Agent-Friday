"""Gauntlet finding F35: services/model_router.py's _generate_text() never
checked the router's `refuse`/`vault_access` verdicts at all -- unlike its
sibling services/agent.py::_generate_agent(), which has (and already tests
elsewhere, see tests/unit/test_fallback_honours_mode.py) exactly this
guard: "Honor the router's verdicts BEFORE any provider sees the request...
refuse=True means vault access was required and the configured fallback is
deny/warn -- no model call is permitted at all" and "A vault-forced local
route must NEVER retry on a cloud provider."

_generate_text is used by many non-chat callers (briefings, the weekly
digest/editorial, calendar/message drafting, wiki identity bootstrap, KG
description-summarization) that can carry real personal/vault-adjacent
text. Without this guard, a vault-forced route whose local leg failed (or
an explicit refuse=True "deny" verdict) fell straight through to the
unconditional cloud/openai fallback legs, silently defeating
model_routing.vault_cloud_fallback's "deny"/"warn" contract.

This probe must be RED before the fix (a refuse=True verdict is ignored;
a vault_access=True local route still tries cloud on local failure) and
GREEN after.
"""
from __future__ import annotations

import agent_friday.routing.model_router as rr
import agent_friday.services.model_router as mr

MSG = [{"role": "user", "content": "what's my vault passphrase policy?"}]


class _FakeRouter:
    def __init__(self, result):
        self._result = result

    def route(self, messages, task_context=None):
        return self._result


def _patch_router(monkeypatch, result):
    monkeypatch.setattr(rr, "get_router", lambda *a, **k: _FakeRouter(result))


class TestGenerateTextHonorsRefuse:
    def test_refuse_true_never_calls_any_provider(self, monkeypatch):
        _patch_router(monkeypatch, {
            "provider": "local", "model": None, "refuse": True,
            "warning": "vault access denied — local model required",
        })
        calls = {"claude": 0, "openai": 0, "ollama": 0}
        monkeypatch.setattr(mr, "_call_claude",
                            lambda *a, **k: calls.__setitem__("claude", calls["claude"] + 1) or "x")
        monkeypatch.setattr(mr, "_call_openai",
                            lambda *a, **k: calls.__setitem__("openai", calls["openai"] + 1) or ("x", []))
        monkeypatch.setattr(mr, "_call_ollama",
                            lambda *a, **k: calls.__setitem__("ollama", calls["ollama"] + 1) or ("x", []))
        monkeypatch.setattr(mr, "get_anthropic_client", lambda *a, **k: object())

        out = mr._generate_text(MSG, system="be brief")

        assert calls == {"claude": 0, "openai": 0, "ollama": 0}, (
            "_generate_text called a provider despite the router returning "
            "refuse=True -- 'no model call is permitted at all' was not "
            "honored"
        )
        assert "vault access denied" in out


class TestGenerateTextHonorsVaultAccess:
    def test_vault_forced_local_never_falls_back_to_cloud(self, monkeypatch):
        _patch_router(monkeypatch, {
            "provider": "local", "model": "gemma4:e4b",
            "vault_access": True, "refuse": False,
        })
        cloud_calls = []
        openai_calls = []
        monkeypatch.setattr(mr, "_call_ollama",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("local seat down")))
        monkeypatch.setattr(mr, "_call_claude",
                            lambda *a, **k: cloud_calls.append(1) or "cloud reply")
        monkeypatch.setattr(mr, "_call_openai",
                            lambda *a, **k: (openai_calls.append(1), ("openai reply", []))[1])
        monkeypatch.setattr(mr, "get_anthropic_client", lambda *a, **k: object())

        try:
            mr._generate_text(MSG, system="be brief")
        except Exception:
            pass  # failing outright is fine; reaching the cloud is not

        assert not cloud_calls, (
            "a vault-forced local route (vault_access=True) fell through to "
            "_call_claude when the local leg failed -- vault_cloud_fallback "
            "is supposed to prevent exactly this"
        )
        assert not openai_calls

    def test_non_vault_local_route_still_falls_back_to_cloud(self, monkeypatch):
        """No-op-shaped sanity check: this fix must not break ordinary
        resilience for a ROUTINE local route (vault_access=False) whose
        local leg fails -- it should still fall back to cloud, exactly like
        before."""
        _patch_router(monkeypatch, {
            "provider": "local", "model": "gemma4:e4b",
            "vault_access": False, "refuse": False,
        })
        cloud_calls = []
        monkeypatch.setattr(mr, "_call_ollama",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("local seat down")))
        monkeypatch.setattr(mr, "_call_claude",
                            lambda *a, **k: cloud_calls.append(1) or "cloud reply")
        monkeypatch.setattr(mr, "get_anthropic_client", lambda *a, **k: object())

        out = mr._generate_text(MSG, system="be brief")

        assert cloud_calls, "an ordinary (non-vault) local failure must still fall back to cloud"
        assert out == "cloud reply"
