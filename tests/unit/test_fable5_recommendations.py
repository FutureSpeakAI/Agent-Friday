"""
Fable 5 adversarial-review RECOMMENDATIONS — regression tests for the build pass.

Covers the non-blocking recommendations implemented after the review:

  R2 — egress-gate startup self-test; cloud routing refused when non-functional
  R3 — centralized _seal_or_block wrapper (single fail-closed enforcement point)
  +   classifier rate limiting (queue, never crash)
  +   request-size cap (MAX_CONTENT_LENGTH + JSON 413 handler)
  +   API session-token rotation with a post-rotation grace window
  +   input validation hardening across the v5 services

(R1 — refusing a keyless non-loopback bind — lives in server.py's __main__
block and is exercised manually; the request-time deny path it backs up is
covered in test_fable5_security.py.)
"""
import os
import sys
import time
import pathlib

import pytest

# Make src importable without an editable install.
_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("FRIDAY_TESTING", "1")


# ── R2: startup self-test + operational gating ────────────────────────────────
def test_startup_self_test_passes_with_healthy_gate():
    from agent_friday.services import egress_gate as eg
    prev = eg._SELF_TEST_RESULT
    try:
        res = eg.startup_self_test()
        assert res["ok"] is True, f"healthy gate failed its own self-test: {res}"
        assert eg.gate_operational() is True
    finally:
        eg._SELF_TEST_RESULT = prev


def test_gate_operational_semantics():
    from agent_friday.services import egress_gate as eg
    prev = eg._SELF_TEST_RESULT
    try:
        eg._SELF_TEST_RESULT = None          # not yet run (tests / library use)
        assert eg.gate_operational() is True
        eg._SELF_TEST_RESULT = {"ok": True}
        assert eg.gate_operational() is True
        eg._SELF_TEST_RESULT = {"ok": False, "error": "boom"}
        assert eg.gate_operational() is False
    finally:
        eg._SELF_TEST_RESULT = prev


# ── R3: the centralized fail-closed wrapper ───────────────────────────────────
def test_seal_or_block_refuses_cloud_when_selftest_failed(monkeypatch):
    from agent_friday.services import model_router as mr
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "_SELF_TEST_RESULT", {"ok": False, "error": "x"})
    with pytest.raises(RuntimeError):
        mr._seal_or_block({"messages": []}, "anthropic")


def test_seal_or_block_blocks_when_gate_raises(monkeypatch):
    from agent_friday.services import model_router as mr
    from agent_friday.services import egress_gate as eg

    def _boom(payload, provider, *a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(eg, "seal_outbound", _boom)
    with pytest.raises(RuntimeError):
        mr._seal_or_block({"messages": []}, "anthropic")


def test_seal_or_block_passes_through_healthy_gate(monkeypatch):
    from agent_friday.services import model_router as mr
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(
        eg, "seal_outbound", lambda p, prov, *a, **k: {**p, "sealed": True})
    out = mr._seal_or_block({"messages": []}, "anthropic")
    assert out.get("sealed") is True


# ── Classifier rate limiting: queue, never crash ──────────────────────────────
def test_classifier_rate_limit_queues_not_crashes():
    from agent_friday.services import egress_gate as eg
    # Burst well past the one-second budget; every call must return normally
    # (excess callers wait briefly — they are never raised at or dropped).
    for _ in range(eg._RATE_MAX_PER_SEC + 5):
        eg._rate_limit()


# ── Request-size cap ──────────────────────────────────────────────────────────
def test_max_content_length_configured():
    import agent_friday.core as core
    limit = core.app.config.get("MAX_CONTENT_LENGTH")
    assert limit, "MAX_CONTENT_LENGTH must be set (request-size cap)"
    assert limit >= 1024 * 1024  # at least 1 MB so normal API use is unaffected


def test_413_handler_returns_json():
    import agent_friday.core as core
    with core.app.test_request_context("/api/chat", method="POST"):
        rv = core._request_too_large(None)
    body, status = rv
    assert status == 413
    assert b"too large" in body.get_data()


# ── API session-token rotation ────────────────────────────────────────────────
def test_api_token_rotates_after_interval(monkeypatch):
    import agent_friday.core as core
    if core._API_TOKEN_ROTATE_S <= 0:
        pytest.skip("rotation disabled via FRIDAY_API_TOKEN_ROTATE_HOURS=0")
    old_tok = core._current_api_token()
    # Age the token past the rotation interval, then ask again.
    monkeypatch.setattr(
        core, "_API_TOKEN_ISSUED_AT",
        time.time() - core._API_TOKEN_ROTATE_S - 1)
    new_tok = core._current_api_token()
    assert new_tok != old_tok, "token must rotate once the interval has elapsed"
    assert core._api_token_valid(new_tok) is True
    # Grace window: the previous token is still accepted right after rotation
    # (a page served moments before rotation must not break mid-request).
    assert core._api_token_valid(old_tok) is True
    assert core._api_token_valid("definitely-not-a-token") is False
    assert core._api_token_valid(None) is False


def test_served_html_uses_current_token():
    """serve_ui must embed the CURRENT (rotating) token, not a startup constant."""
    import inspect
    from agent_friday.routes import core_routes
    src = inspect.getsource(core_routes.serve_ui)
    assert "_current_api_token()" in src


# ── v5 service hardening ──────────────────────────────────────────────────────
def test_dream_rejects_path_traversal_day():
    from agent_friday.services import memory_dreaming as md
    res = md.dream(day="../../evil")
    assert res["ok"] is False
    res2 = md.dream(day="2026-06-30'; DROP TABLE dreams;--")
    assert res2["ok"] is False
    res3 = md.dream(day="..\\..\\windows\\evil")
    assert res3["ok"] is False


def test_learning_loop_coercion_helpers():
    from agent_friday.services import learning_loop as ll
    assert ll._coerce_int("7") == 7
    assert ll._coerce_int("junk", 3) == 3
    assert ll._coerce_int(None, 5) == 5
    assert ll._coerce_float("2.5") == 2.5
    assert ll._coerce_float(object(), 1.5) == 1.5


def test_record_trial_rejects_bad_skill_id():
    from agent_friday.services import learning_loop as ll
    assert ll.record_trial("", True)["ok"] is False
    assert ll.record_trial(None, True)["ok"] is False


def test_set_trait_rejects_bad_key():
    from agent_friday.services import user_model as um
    assert um.set_trait("", "x")["ok"] is False
    assert um.set_trait(None, "x")["ok"] is False
    assert um.set_trait("   ", "x")["ok"] is False


def test_note_fact_rejects_non_string():
    from agent_friday.services import user_model as um
    assert um.note_fact("bio", None)["ok"] is False
    assert um.note_fact("bio", 12345)["ok"] is False
    assert um.note_fact("bio", "   ")["ok"] is False


def test_configure_channel_rejects_bad_types():
    from agent_friday.services.channels import manager
    r = manager.configure_channel("telegram", {"allowlist": "not-a-list"})
    assert r["ok"] is False
    r2 = manager.configure_channel("telegram", {"poll_interval": "fast"})
    assert r2["ok"] is False


def test_telegram_parse_updates_ignores_malformed():
    from agent_friday.services.channels.telegram_bridge import TelegramBridge
    payload = {"result": [
        "garbage",
        {"update_id": "NaN", "message": "not-a-dict"},
        {"update_id": 5, "message": {"text": "hi", "chat": "not-a-dict"}},
        {"update_id": 6, "message": {"text": 42, "chat": {"id": 7}}},
        {"update_id": 8, "message": {"text": "ok", "chat": {"id": 42}}},
    ]}
    out = TelegramBridge.parse_updates(payload)
    assert out == [{"chat_id": "42", "text": "ok"}]


def test_handle_incoming_rejects_non_string_text():
    from agent_friday.services.channels import manager
    assert manager.handle_incoming("telegram", "1", None) is None
    assert manager.handle_incoming("telegram", "1", 123) is None
    assert manager.handle_incoming("telegram", "1", "   ") is None


# ── Launch-script bootstrap ordering (found during the build pass) ────────────
def test_auth_constants_rederived_after_env_bootstrap():
    """FRIDAY_PASSWORD living only in start.bat must still arm HTTP auth and
    the vault: the constants are first computed BEFORE the env bootstrap runs,
    so core must re-derive them immediately after the bootstrap call."""
    import inspect
    import agent_friday.core as core
    src = inspect.getsource(core)
    boot_call = src.index("_bootstrap_env_from_launch_scripts()\n")
    rederive = src.index("Re-derive the auth/vault constants")
    assert rederive > boot_call
    assert "if not _HTTP_AUTH_KEY:" in src[rederive:]
    assert "if not FRIDAY_VAULT_PASSPHRASE:" in src[rederive:]
