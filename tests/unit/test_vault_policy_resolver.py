"""One vault resolver, and the divergence it exists to kill.

The headline test is `test_both_enforcement_points_agree`. It FAILS against the
pre-patch code, and it is the whole point of the patch: on 2026-09-01 the same
running server force-routed a vault question to a local model (routing said
"protected") while assembling every cloud prompt ungated (prompt assembly said
"not protected"). Both halves were behaving as written. One flag, two readers,
two answers, and no single place to ask what the machine was actually doing.
"""

import logging

import pytest

from agent_friday.privacy import vault_policy


# ── the resolver itself ─────────────────────────────────────────────────────

def test_absent_key_defaults_to_gated():
    """A thin model_routing block must resolve PROTECTIVE, not open.

    Stephen's live block carried 2 of 15 keys. Every gate resolving from a
    default has to default to the safe side.
    """
    p = vault_policy.resolve({}, announce=False)
    assert p.local_only is True
    assert p.gated is True
    assert p.explicit is False
    assert p.source == "default"


def test_explicit_false_is_the_only_way_to_open_the_gate():
    p = vault_policy.resolve({"vault_local_only": False}, announce=False)
    assert p.local_only is False
    assert p.gated is False
    assert p.explicit is True


def test_cloud_fallback_defaults_to_redact_and_rejects_junk():
    assert vault_policy.resolve({}, announce=False).cloud_fallback == "redact"
    assert vault_policy.resolve(
        {"vault_cloud_fallback": "deny"}, announce=False).cloud_fallback == "deny"
    # An unrecognised value must not become a third, undefined posture.
    assert vault_policy.resolve(
        {"vault_cloud_fallback": "yolo"}, announce=False).cloud_fallback == "redact"


def test_resolution_failure_fails_safe(monkeypatch):
    """A broken settings read must never open the gate."""
    import agent_friday.core as core

    def boom():
        raise RuntimeError("settings.json is a directory today")

    monkeypatch.setattr(core, "_load_settings", boom)
    p = vault_policy.resolve(None, announce=False)
    assert p.local_only is True, "a failed read must resolve to GATED"
    assert p.source == "error-default"


# ── fail loudly, not silently ───────────────────────────────────────────────

def test_ungated_posture_is_logged(caplog):
    """`_gated_vault_control()` returning None used to be completely silent."""
    vault_policy.reset_announcements()
    with caplog.at_level(logging.WARNING, logger="friday.vault"):
        vault_policy.resolve({"vault_local_only": False})
    assert any("Vault gating OFF" in r.message or "Vault gating OFF" in r.getMessage()
               for r in caplog.records), "turning the gate off must be announced"


def test_gated_posture_is_not_noisy(caplog):
    vault_policy.reset_announcements()
    with caplog.at_level(logging.WARNING, logger="friday.vault"):
        for _ in range(50):
            vault_policy.resolve({"vault_local_only": True})
    assert not caplog.records, "the healthy posture must not log at all"


def test_announcement_is_rate_capped(caplog):
    """Loud once, not 1,038 times a day -- the mistake the GPU error made."""
    vault_policy.reset_announcements()
    with caplog.at_level(logging.WARNING, logger="friday.vault"):
        for _ in range(200):
            vault_policy.resolve({"vault_local_only": False})
    warnings = [r for r in caplog.records if "Vault gating OFF" in r.getMessage()]
    assert len(warnings) == 1, f"expected 1 announcement, got {len(warnings)}"


def test_status_marks_ungated_as_degraded():
    """The UI needs a machine-readable 'this is off' flag."""
    st = vault_policy.status()
    assert set(st) >= {"gated", "degraded", "summary", "vault_local_only"}
    assert st["degraded"] is (not st["gated"])
    assert isinstance(st["summary"], str) and st["summary"]


def test_describe_names_the_setting_to_change():
    """A warning nobody can act on is decoration."""
    text = vault_policy.resolve({"vault_local_only": False},
                                announce=False).describe()
    assert "vault_local_only" in text
    assert "egress" in text.lower(), "say what IS still checking"


# ── the divergence: this is the test that fails pre-patch ───────────────────

@pytest.mark.parametrize("local_only", [True, False])
def test_both_enforcement_points_agree(monkeypatch, local_only):
    """Routing and prompt-assembly must resolve to the SAME posture.

    Pre-patch this fails at `local_only=False`: `_route_vault` force-routed
    every vault turn to a local model regardless of the setting (it never read
    it), while `_gated_vault_control()` honoured it and returned None. The
    machine held two postures at once and reported neither.
    """
    from agent_friday.routing.model_router import ModelRouter
    from agent_friday.services import model_router as svc

    cfg = {"mode": "smart", "vault_local_only": local_only}
    monkeypatch.setattr(svc, "_load_settings", lambda: {"model_routing": cfg})

    # Enforcement point 1 -- routing.
    router = ModelRouter(cfg)
    router._local_candidates = lambda: [{"name": "gemma4:12b", "size_gb": 7.0}]
    decision = router.route(
        [{"role": "user", "content": "remind me what my Chase account balance was"}],
        {}) or {}
    # `vault_access`, NOT `is_local`. Ordinary smart routing also lands on a
    # local model for a short prompt, so is_local answers "did this happen to
    # stay on device", which is luck. `vault_access` answers "did the vault
    # mechanism claim this turn", which is the posture under test.
    routing_protects = bool(decision.get("vault_access"))

    # Enforcement point 2 -- prompt assembly.
    prompt_protects = svc._gated_vault_control() is not None

    assert routing_protects == prompt_protects == local_only, (
        "vault posture diverged: routing_protects=%s prompt_protects=%s "
        "for vault_local_only=%s" % (routing_protects, prompt_protects, local_only)
    )


def test_router_and_service_read_one_resolver(monkeypatch):
    """Both paths must go through vault_policy.resolve -- not two readers."""
    from agent_friday.routing.model_router import ModelRouter
    from agent_friday.services import model_router as svc

    cfg = {"mode": "smart", "vault_local_only": True}
    monkeypatch.setattr(svc, "_load_settings", lambda: {"model_routing": cfg})

    seen = []
    real = vault_policy.resolve

    def spy(config=None, **kw):
        seen.append(config)
        return real(config, **kw)

    monkeypatch.setattr(vault_policy, "resolve", spy)

    ModelRouter(cfg)._route_vault({})
    svc._gated_vault_control()

    assert len(seen) >= 2, "both enforcement points must consult the resolver"


def test_vault_fallback_comes_from_the_resolver(monkeypatch):
    """`deny` set in settings must reach the router, which used to read its own."""
    from agent_friday.routing.model_router import ModelRouter

    cfg = {"mode": "smart", "vault_local_only": True, "vault_cloud_fallback": "deny"}
    router = ModelRouter(cfg)
    router._local_candidates = lambda: []          # no local model anywhere
    decision = router._route_vault({}) or {}
    assert decision.get("refuse") is True, "fallback=deny must refuse, not redact"
