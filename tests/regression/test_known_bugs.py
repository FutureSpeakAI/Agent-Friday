"""Regression tests locking in fixes for previously-shipped bugs:

  1. Model-selector snap-back — settings save must send only the caller's DELTA,
     never a full spread that re-persists the non-persistent offline overlay.
  2. Restructure path shims — the root core.py / server.py shims must import the
     real modules under src/agent_friday.
  3. Static-file serving — no project-root path traversal exposure.
  4. FRIDAY_PASSWORD triple-coupling — auth key, vault passphrase, and remote key
     are now separately overridable env vars.
  5. Channel egress fail-closed — a double classifier failure must NOT leak text.
"""
from __future__ import annotations

import os

import pytest


# ── 1. Model-selector snap-back ───────────────────────────────────────────────

class TestModelSelectorSnapBack:
    def test_save_settings_merges_only_delta(self, monkeypatch, tmp_path):
        import agent_friday.core as core
        f = tmp_path / "settings.json"
        monkeypatch.setattr(core, "SETTINGS_FILE", f)
        core._invalidate_settings_cache()
        # Seed an initial model choice.
        core._save_settings({"orchestrator_model": "claude-opus-4-8"})
        # Save an UNRELATED delta — the model choice must survive (no snap-back).
        core._save_settings({"agent_name": "X"})
        core._invalidate_settings_cache()
        loaded = core._load_settings_raw()
        assert loaded["orchestrator_model"] == "claude-opus-4-8"
        assert loaded["agent_name"] == "X"

    def test_offline_overlay_not_persisted(self, monkeypatch, tmp_path):
        import agent_friday.core as core
        f = tmp_path / "settings.json"
        monkeypatch.setattr(core, "SETTINGS_FILE", f)
        core._invalidate_settings_cache()
        core._save_settings({"orchestrator_model": "claude-opus-4-8"})
        # Simulate offline: _load_settings applies a transient local overlay.
        monkeypatch.setattr(core, "_network_is_offline", lambda: True)
        core._load_settings()
        # But the RAW persisted delta must not bake the overlay in.
        raw = core._load_settings_raw()
        assert raw["orchestrator_model"] == "claude-opus-4-8"


# ── 2. Restructure path shims ─────────────────────────────────────────────────

class TestPathShims:
    def test_root_core_shim_resolves(self):
        import core as root_core
        # The shim does `from agent_friday.core import *`, so it re-exports the
        # PUBLIC names (underscore-prefixed internals aren't in `*`).
        assert hasattr(root_core, "app")
        assert hasattr(root_core, "login_required")
        assert hasattr(root_core, "FRIDAY_DIR")

    def test_agent_friday_core_importable(self):
        import agent_friday.core as c
        assert hasattr(c, "FRIDAY_DIR")
        assert hasattr(c, "DEFAULT_SETTINGS")
        # Internals ARE reachable via the real module (just not via the `*` shim).
        assert hasattr(c, "_load_settings")


# ── 3. Static-file serving security ───────────────────────────────────────────

class TestStaticFileSecurity:
    def test_static_traversal_blocked(self, client):
        # send_from_directory must refuse to escape the static/ dir.
        for attack in ("../server.py", "..%2f..%2fcore.py", "....//server.py"):
            r = client.get(f"/static/{attack}")
            assert r.status_code in (400, 403, 404)

    def test_normal_static_asset_reachable_or_404(self, client):
        # A legit request path resolves inside static/ (200) or is missing (404),
        # but never leaks a project-root file.
        r = client.get("/static/nonexistent-asset.js")
        assert r.status_code in (200, 404)


# ── 4. FRIDAY_PASSWORD triple-coupling decoupled ──────────────────────────────

class TestPasswordDecoupling:
    def test_vault_and_http_key_are_separate_names(self):
        import agent_friday.core as core
        # The three roles now exist as distinct module attributes.
        assert hasattr(core, "FRIDAY_VAULT_PASSPHRASE")
        assert hasattr(core, "_HTTP_AUTH_KEY")
        assert hasattr(core, "FRIDAY_PASSWORD")

    def test_password_still_backfills_when_dedicated_unset(self):
        # Backward-compat: FRIDAY_PASSWORD backfills both roles if the dedicated
        # vars are unset. When a password exists, the vault passphrase is non-empty.
        import agent_friday.core as core
        if core.FRIDAY_PASSWORD:
            assert core.FRIDAY_VAULT_PASSPHRASE


# ── 5. Channel egress fail-closed on double classifier failure ────────────────

class TestChannelFailClosed:
    def test_double_failure_withholds(self, monkeypatch):
        from agent_friday.services.channels import manager
        from agent_friday.services import egress_gate, sensitivity_classifier as sc

        # Primary gate raises AND the classifier backstop also raises.
        monkeypatch.setattr(egress_gate, "seal_outbound",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate down")))
        monkeypatch.setattr(sc, "classify",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("classifier down")))
        out = manager.gate_reply("some reply text", channel="telegram")
        # Must NOT return the raw text — fail closed.
        assert out != "some reply text"
        assert "withheld" in out.lower()

    def test_backstop_passes_public_on_primary_failure(self, monkeypatch):
        from agent_friday.services.channels import manager
        from agent_friday.services import egress_gate, sensitivity_classifier as sc
        monkeypatch.setattr(egress_gate, "seal_outbound",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate down")))
        monkeypatch.setattr(sc, "classify", lambda *a, **k: sc.Tier.PUBLIC)
        out = manager.gate_reply("the weather is nice", channel="discord")
        # Backstop positively rated it PUBLIC → released.
        assert out == "the weather is nice"
