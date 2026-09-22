"""`local` terminates. It never falls through to a cloud provider.

Settled 2026-09-09, and the more important half of the `auto` fix in
test_resolve_engine_auto_never_cloud.py.

The defect this pins: a user selected the mode named "local", did not install
the Tier-1 deps (a separate opt-in step that is easy to skip), had a Gemini key
present, and had their microphone audio and Friday's spoken replies streamed to
Gemini Live - silently. The only guard was `model_routing.mode == 'local_only'`,
so the protection required saying "local" twice in two different places, and the
plain reading of the word the user actually selected bought them nothing.

A user who picks a mode called "local" and receives a cloud provider has been
lied to by the word itself. The honest failure is strictly better than the
dishonest success.
"""
from __future__ import annotations

import pytest

from agent_friday.routes import voice as voice_routes


@pytest.fixture
def _cloud_ready_local_dead(monkeypatch):
    """The exact configuration under which the old code returned gemini.

    Valid cloud key, online, local deps missing, and - critically -
    `model_routing.mode` NOT set to local_only. That last part is what makes
    this different from the existing D-AC3 test.
    """
    monkeypatch.setattr(voice_routes, "_network_status",
                        lambda: {"offline": False})
    monkeypatch.setattr(voice_routes, "resolve_gemini_key",
                        lambda *a, **k: {"valid": True})

    class _DeadEngine:
        def available(self):
            return False

        def models_ready(self):
            return False

        def resolve_tier(self, settings=None):
            return "cpu"

    monkeypatch.setattr(voice_routes, "get_local_voice_engine",
                        lambda: _DeadEngine())
    return monkeypatch


class TestLocalTerminates:

    def test_local_with_dead_engine_and_valid_key_does_not_use_cloud(
            self, _cloud_ready_local_dead):
        out = voice_routes._resolve_voice_engine({"voice_engine": "local"})
        assert out["engine"] != "gemini", (
            "the mode named 'local' served a cloud provider - the word itself "
            "lied to the person who chose it")
        assert out["engine"] == "demo"

    def test_default_preference_also_terminates(self, _cloud_ready_local_dead):
        """No `voice_engine` set at all defaults to local, and must behave so."""
        out = voice_routes._resolve_voice_engine({})
        assert out["engine"] != "gemini"

    def test_an_unrecognised_preference_does_not_open_the_cloud_path(
            self, _cloud_ready_local_dead):
        """A typo or a retired value must fail safe, not fail open."""
        out = voice_routes._resolve_voice_engine({"voice_engine": "loc al"})
        assert out["engine"] != "gemini"

    def test_the_refusal_names_the_problem_and_the_choice(
            self, _cloud_ready_local_dead):
        """Surface and offer. A bare refusal is indistinguishable from a bug."""
        out = voice_routes._resolve_voice_engine({"voice_engine": "local"})
        reason = (out.get("reason") or "").lower()
        assert "local voice is not ready" in reason, (
            "the user was not told WHY voice stopped working")
        assert "cloud" in reason and "unless you choose" in reason, (
            "the user was not told the cloud path exists and is theirs to "
            "choose - C1 says these are peers among which the user chooses")
        assert "voice-local-lite" in reason, "no remediation was offered"

    def test_local_still_works_when_local_works(self, monkeypatch):
        """The fix must not break the path it is protecting."""
        monkeypatch.setattr(voice_routes, "_network_status",
                            lambda: {"offline": False})
        monkeypatch.setattr(voice_routes, "resolve_gemini_key",
                            lambda *a, **k: {"valid": True})
        monkeypatch.setattr(voice_routes, "_local_brain_ready", lambda: True)

        class _LiveEngine:
            def available(self):
                return True

            def models_ready(self):
                return True

            def resolve_tier(self, settings=None):
                return "cpu"

        monkeypatch.setattr(voice_routes, "get_local_voice_engine",
                            lambda: _LiveEngine())
        out = voice_routes._resolve_voice_engine({"voice_engine": "local"})
        assert out["engine"] == "local"
        assert out["models_ready"] is True

    def test_explicit_gemini_is_still_honoured(self, _cloud_ready_local_dead):
        """C1 cuts both ways: a user who ASKS for cloud gets cloud.

        This fix removes a silent hop, not the user's ability to choose. If it
        also blocked the explicit choice it would be paternalism rather than
        transparency.
        """
        _cloud_ready_local_dead.setattr(
            voice_routes, "validate_live_model", lambda *a, **k: {"ok": True},
            raising=False)
        out = voice_routes._resolve_voice_engine({"voice_engine": "gemini"})
        assert out["engine"] == "gemini"
