"""Unit tests for privacy/cloud_consent.py -- the explicit cloud-consent
gate (2026-09-06). See that module's own docstring for the full design:
unrestricted cloud access is earned by a recorded, hardware-checked choice,
never inherited from `model_routing.mode`'s factory default.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import pytest

from agent_friday.privacy import cloud_consent as cc


def _settings(model_routing: dict):
    return {"model_routing": model_routing}


class TestResolve:
    def test_unanswered_by_default(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: _settings({}))
        status = cc.resolve()
        assert status.answered is False
        assert status.unrestricted is False
        assert status.source == "unanswered"

    def test_recorded_cloud_choice_is_unrestricted(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: _settings({
            "cloud_consent": {"answered": True, "choice": "cloud_unrestricted",
                              "at": "x", "capability_snapshot": None}}))
        status = cc.resolve()
        assert status.unrestricted is True

    def test_recorded_local_choice_stays_gated(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: _settings({
            "cloud_consent": {"answered": True, "choice": "local_private",
                              "at": "x", "capability_snapshot": None}}))
        status = cc.resolve()
        assert status.answered is True
        assert status.unrestricted is False

    def test_legacy_flag_grandfathers_in_as_answered(self, monkeypatch):
        """An install that already set the old standalone flag explicitly
        is treated as having already made this choice."""
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings",
                            lambda: _settings({"unrestricted_cloud": True}))
        status = cc.resolve()
        assert status.answered is True
        assert status.unrestricted is True
        assert status.source == "legacy_unrestricted_cloud_flag"

    def test_factory_default_mode_alone_never_grandfathers_in(self, monkeypatch):
        """The exact bug this module exists to close: cloud_only being the
        factory default must never read as an answered choice."""
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings",
                            lambda: _settings({"mode": "cloud_only"}))
        status = cc.resolve()
        assert status.answered is False
        assert status.unrestricted is False

    def test_read_failure_fails_safe_to_gated(self, monkeypatch):
        from agent_friday import core as _core
        def _boom():
            raise RuntimeError("settings unreadable")
        monkeypatch.setattr(_core, "_load_settings", _boom)
        status = cc.resolve()
        assert status.unrestricted is False


class TestAntiSpoofing:
    def test_generic_settings_save_cannot_set_cloud_consent(self, monkeypatch, tmp_path):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "FRIDAY_DIR", tmp_path)
        monkeypatch.setattr(_core, "SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(_core, "_SETTINGS_CACHE", {"data": None, "at": 0})
        # A generic caller -- exactly the shape of POST /api/settings, or a
        # model's own HTTP tool -- tries to claim an answered, unrestricted
        # choice through the normal write path.
        _core._save_settings({"model_routing": {"cloud_consent": {
            "answered": True, "choice": "cloud_unrestricted",
            "at": "spoofed", "capability_snapshot": None}}})
        saved = (_core._load_settings() or {}).get("model_routing", {}).get("cloud_consent")
        assert saved is None or saved.get("answered") is not True

    def test_internal_flag_write_succeeds(self, monkeypatch, tmp_path):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "FRIDAY_DIR", tmp_path)
        monkeypatch.setattr(_core, "SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(_core, "_SETTINGS_CACHE", {"data": None, "at": 0})
        _core._save_settings(
            {"model_routing": {"cloud_consent": {
                "answered": True, "choice": "cloud_unrestricted",
                "at": "legit", "capability_snapshot": None}}},
            _internal_cloud_consent_write=True)
        saved = (_core._load_settings() or {}).get("model_routing", {}).get("cloud_consent")
        assert saved["answered"] is True
        assert saved["choice"] == "cloud_unrestricted"


class TestRecordConsent:
    def test_rejects_local_private_on_insufficient_hardware(self, monkeypatch):
        monkeypatch.setattr(cc, "assess_local_capability",
                            lambda profile=None: {"capable": False, "roles": {},
                                                  "chain_ok": False, "chain_why": None})
        with pytest.raises(cc.ConsentRejected):
            cc.record_consent(cc.CHOICE_LOCAL)

    def test_accepts_cloud_unrestricted_regardless_of_capability(self, monkeypatch):
        monkeypatch.setattr(cc, "assess_local_capability",
                            lambda profile=None: {"capable": False, "roles": {},
                                                  "chain_ok": False, "chain_why": None})
        saved = {}
        def _fake_save(data, **kw):
            saved.update(data)
            return data
        monkeypatch.setattr("agent_friday.core._save_settings", _fake_save)
        record = cc.record_consent(cc.CHOICE_CLOUD)
        assert record["choice"] == cc.CHOICE_CLOUD
        assert saved["model_routing"]["cloud_consent"]["choice"] == cc.CHOICE_CLOUD

    def test_rejects_unknown_choice(self):
        with pytest.raises(ValueError):
            cc.record_consent("not_a_real_choice")


class TestTextCapabilityIsRunnerAgnostic:
    """The maintainer, 2026-09-06: 'We don't require the user have ollama installed
    at all, right?' -- verified rather than assumed. Ollama is one runner
    among several (llama.cpp/GGUF via Friday's own runtime store, ComfyUI
    for image/video); a check that only recognizes the curated
    model_plan.BRAIN_MODELS ladder would call a machine incapable while a
    real, resident, non-ladder GGUF model runs well on it -- exactly
    the maintainer's own machine after deleting both Ollama models."""

    def test_a_resident_non_ladder_model_counts(self, monkeypatch):
        from agent_friday.services import local_seats
        from agent_friday.services import residency_policy as rp
        monkeypatch.setattr(local_seats, "installed",
                            lambda: [("my-custom-gguf-q4km", 3.19)])
        monkeypatch.setattr(rp, "verdicts", lambda entry, profile: {
            "fits": {"status": "ready", "explanation": ""},
            "runs_well": {"status": "ready", "explanation": ""},
            "worth_it": {"status": "ready", "explanation": ""},
        })
        ok, why = cc._text_capable({}, [])
        assert ok is True

    def test_falls_back_to_ladder_when_nothing_resident(self, monkeypatch):
        from agent_friday.services import local_seats
        monkeypatch.setattr(local_seats, "installed", lambda: [])
        ladder_row = {"model_id": "gemma4:e2b", "verdicts": {
            "fits": {"status": "ready", "explanation": ""},
            "runs_well": {"status": "ready", "explanation": ""}}}
        ok, why = cc._text_capable({}, [ladder_row])
        assert ok is True

    def test_incapable_when_neither_resident_nor_ladder_runs_well(self, monkeypatch):
        from agent_friday.services import local_seats
        monkeypatch.setattr(local_seats, "installed", lambda: [])
        ok, why = cc._text_capable({}, [])
        assert ok is False
