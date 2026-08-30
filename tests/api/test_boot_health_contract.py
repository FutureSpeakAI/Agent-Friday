"""PR-6 — the boot-critical health contract on /api/health.

Top-level rule for this PR (stated directly by the project owner): "A health
check that cannot actually fail is a bug; every subsystem check must have a
test that makes it fail." So for every boot-critical subsystem below there
are TWO tests: one proving it reports healthy in the normal case, and one
that actually BREAKS that specific subsystem and proves `boot_critical_ok`
flips to False and that subsystem's own `ok` is False with a real detail
message — never a test that only calls the function and checks it returns
something (which would pass even if the check always returned True).

Additive-schema note: `status` (the pre-existing top-level field) is NOT
touched here — it is a different question (inference-provider reachability,
locked by
tests/api/test_devtools_system_routes.py::test_status_is_the_probe_verdict_not_a_constant)
answered by a different mechanism (services/provider_health.py). This PR adds
`boot_critical_ok`, `boot_status`, `subsystems`, `deployment` and
`health_schema_version` as NEW top-level keys — see services/health_check.py
for the full reasoning, including why `status` could not simply be
repurposed for the new enum.
"""
from __future__ import annotations

import sqlite3

import pytest

from agent_friday.services import health_check


@pytest.fixture(autouse=True)
def _clean_health_cache():
    """The module caches for _CACHE_TTL_S so a hot-polled /api/health does not
    re-run a disk round trip + Argon2id derivation on every request (see the
    module docstring). Tests must not see a stale verdict from a previous
    test's monkeypatch, or from this same test's own healthy-then-broken
    sequence — reset on both sides, matching
    tests/api/test_health_inference_probe.py's `_clean_probe_cache` pattern.
    """
    health_check.reset_health_cache()
    yield
    health_check.reset_health_cache()


def _health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    return resp.get_json()


# ── Shape / additive-schema ─────────────────────────────────────────────────

def test_contract_shape_is_present_and_additive(client):
    body = _health(client)
    # New keys.
    assert body["health_schema_version"] == 1
    assert isinstance(body["boot_critical_ok"], bool)
    assert body["boot_status"] in ("ok", "degraded", "failed")
    assert isinstance(body["subsystems"], dict)
    assert isinstance(body["deployment"], str) and body["deployment"]
    # Pre-existing keys other consumers depend on must still be there.
    assert "status" in body            # inference verdict — untouched
    assert "version" in body           # already services.app_version-sourced
    assert "uptime_seconds" in body
    assert "vault" in body
    assert "governance" in body


def test_all_four_boot_critical_subsystems_are_reported(client):
    body = _health(client)
    critical = {name for name, s in body["subsystems"].items() if s["critical"]}
    assert critical == {"config", "credential_store", "memory_db", "http_serving"}


def test_healthy_default_env_reports_boot_critical_ok(client):
    """In this suite's isolated temp home (FRIDAY_PASSWORD set by the root
    conftest, a real request in flight), all four critical subsystems should
    be healthy and boot_critical_ok True. This is the "proves it can also
    pass" half of every fail-forcing test below.
    """
    body = _health(client)
    for name in ("config", "credential_store", "memory_db", "http_serving"):
        sub = body["subsystems"][name]
        assert sub["ok"] is True, f"{name} unexpectedly unhealthy: {sub['detail']}"
    assert body["boot_critical_ok"] is True


# ── config: can it actually fail? ───────────────────────────────────────────

def test_config_check_healthy_when_settings_parse(client):
    ok, detail = health_check.check_config()
    assert ok is True
    assert detail


def test_config_check_fails_on_corrupt_settings_json(monkeypatch):
    import agent_friday.core as core
    path = core.SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8-sig") if path.exists() else None
    try:
        path.write_text("{not valid json at all", encoding="utf-8")
        ok, detail = health_check.check_config()
        assert ok is False, "corrupt settings.json must be reported unhealthy"
        assert "does not parse" in detail
    finally:
        if original is not None:
            path.write_text(original, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)


def test_broken_config_flips_boot_critical_ok_via_route(client, monkeypatch):
    monkeypatch.setattr(health_check, "check_config",
                        lambda: (False, "simulated: settings.json does not parse"))
    body = _health(client)
    assert body["subsystems"]["config"]["ok"] is False
    assert body["subsystems"]["config"]["detail"] == "simulated: settings.json does not parse"
    assert body["boot_critical_ok"] is False
    assert body["boot_status"] == "failed"


# ── credential_store: can it actually fail? ─────────────────────────────────

def test_credential_store_check_healthy_round_trip():
    ok, detail = health_check.check_credential_store()
    assert ok is True
    assert "round trip" in detail


def test_credential_store_check_fails_closed_under_os_mode_no_vault_no_dpapi(monkeypatch):
    """The real PR-5 fail-closed path: FRIDAY_OS_MODE on, no vault key
    derivable, no DPAPI available -> credential_store.protect() raises
    instead of writing plaintext. This exercises the ACTUAL subsystem code,
    not a stand-in.
    """
    from agent_friday.services import credential_store as cs

    # protect() tries _vault_key() then _dpapi(data, encrypt=True) directly —
    # NOT _dpapi_available() (that name is only consulted by
    # protection_method(), a reporting helper protect() does not call) — so
    # the real DPAPI encrypt/decrypt entry point must be patched to fail too,
    # or this Windows test host's real, working DPAPI silently succeeds and
    # the fail-closed branch this test targets is never reached.
    monkeypatch.setattr(cs, "is_os_mode", lambda: True)
    monkeypatch.setattr(cs, "_vault_key", lambda: None)
    monkeypatch.setattr(cs, "_dpapi", lambda *a, **k: None)

    ok, detail = health_check.check_credential_store()
    assert ok is False, "fail-closed protect() must surface as an unhealthy credential store"
    assert "OS_MODE" in detail or "PLAINTEXT" in detail


def test_broken_credential_store_flips_boot_critical_ok_via_route(client, monkeypatch):
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "is_os_mode", lambda: True)
    monkeypatch.setattr(cs, "_vault_key", lambda: None)
    monkeypatch.setattr(cs, "_dpapi", lambda *a, **k: None)

    body = _health(client)
    assert body["subsystems"]["credential_store"]["ok"] is False
    assert body["boot_critical_ok"] is False
    assert body["boot_status"] == "failed"


# ── memory_db: can it actually fail? ────────────────────────────────────────

def test_memory_db_check_healthy_opens_and_queries():
    ok, detail = health_check.check_memory_db()
    assert ok is True
    assert "opens" in detail


def test_memory_db_check_fails_when_db_path_is_unopenable(monkeypatch, tmp_path):
    from agent_friday.services import memory_dreaming as md

    # A directory where sqlite expects a file: sqlite3.connect() raises
    # OperationalError trying to open it — a real, forceable failure, not a
    # stand-in exception.
    blocked = tmp_path / "dreams_blocked.db"
    blocked.mkdir()
    monkeypatch.setattr(md, "DB_PATH", blocked)

    ok, detail = health_check.check_memory_db()
    assert ok is False, "an unopenable database file must be reported unhealthy"
    assert detail  # a real error message, not empty


def test_broken_memory_db_flips_boot_critical_ok_via_route(client, monkeypatch, tmp_path):
    from agent_friday.services import memory_dreaming as md
    blocked = tmp_path / "dreams_blocked2.db"
    blocked.mkdir()
    monkeypatch.setattr(md, "DB_PATH", blocked)

    body = _health(client)
    assert body["subsystems"]["memory_db"]["ok"] is False
    assert body["boot_critical_ok"] is False
    assert body["boot_status"] == "failed"


# ── http_serving: definitional inside the route, provable failure outside ──

def test_http_serving_is_definitionally_ok_when_served_over_http(client):
    body = _health(client)
    assert body["subsystems"]["http_serving"]["ok"] is True


def test_http_serving_check_fails_when_not_called_from_a_live_request():
    ok, detail = health_check.check_http_serving(False)
    assert ok is False
    assert detail


def test_boot_critical_report_fails_when_no_http_evidence_available():
    report = health_check.boot_critical_report(served_over_http=False, use_cache=False)
    assert report["subsystems"]["http_serving"]["ok"] is False
    assert report["boot_critical_ok"] is False
    assert report["boot_status"] == "failed"


def test_http_probe_succeeds_when_server_answers(monkeypatch):
    import urllib.request

    class _Resp:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    report = health_check.boot_critical_report(
        served_over_http=False, http_probe_url="http://localhost:3000/api/health",
        use_cache=False)
    assert report["subsystems"]["http_serving"]["ok"] is True


def test_http_probe_fails_when_server_unreachable(monkeypatch):
    import urllib.request

    def _raise(*a, **k):
        raise OSError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    report = health_check.boot_critical_report(
        served_over_http=False, http_probe_url="http://localhost:3000/api/health",
        use_cache=False)
    assert report["subsystems"]["http_serving"]["ok"] is False
    assert report["boot_critical_ok"] is False
    assert "Connection refused" in report["subsystems"]["http_serving"]["detail"]


# ── non-critical: absence must NOT flip boot_critical_ok ────────────────────

def test_noncritical_absence_does_not_flip_boot_critical_ok(monkeypatch):
    """The other half of the critical/non-critical split: it is not enough for
    the code to CLAIM cloud providers / model seats / voice are non-critical
    -- absence of all three must demonstrably leave boot_critical_ok True.
    """
    monkeypatch.setattr(health_check, "check_cloud_providers",
                        lambda: (False, "no cloud provider API key configured"))
    monkeypatch.setattr(health_check, "check_model_seats",
                        lambda: (False, "no orchestrator/subagent model seat configured"))
    monkeypatch.setattr(health_check, "check_voice",
                        lambda: (False, "no voice model configured"))

    report = health_check.boot_critical_report(served_over_http=True, use_cache=False)

    assert report["subsystems"]["cloud_providers"]["ok"] is False
    assert report["subsystems"]["model_seats"]["ok"] is False
    assert report["subsystems"]["voice"]["ok"] is False
    assert report["subsystems"]["cloud_providers"]["critical"] is False
    assert report["subsystems"]["model_seats"]["critical"] is False
    assert report["subsystems"]["voice"]["critical"] is False
    assert report["boot_critical_ok"] is True, (
        "non-critical subsystem absence must never flip boot_critical_ok")
    assert report["boot_status"] == "degraded"


def test_noncritical_absence_does_not_flip_boot_critical_ok_via_route(client, monkeypatch):
    monkeypatch.setattr(health_check, "check_cloud_providers",
                        lambda: (False, "no cloud provider API key configured"))
    monkeypatch.setattr(health_check, "check_model_seats",
                        lambda: (False, "no orchestrator/subagent model seat configured"))
    monkeypatch.setattr(health_check, "check_voice",
                        lambda: (False, "no voice model configured"))

    body = _health(client)
    assert body["boot_critical_ok"] is True
    assert body["boot_status"] == "degraded"


# ── deployment: honest placeholder, not a fabricated value ──────────────────

def test_deployment_id_reads_env_var_when_set(monkeypatch):
    monkeypatch.setenv("FRIDAY_DEPLOYMENT_ID", "deploy-42")
    assert health_check.deployment_id() == "deploy-42"


def test_deployment_id_is_honest_placeholder_when_unset(monkeypatch):
    monkeypatch.delenv("FRIDAY_DEPLOYMENT_ID", raising=False)
    assert health_check.deployment_id() == "unknown"
