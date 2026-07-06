"""Adapter contract battery (spec §15) — every registered platform adapter
must pass this suite with its transport stubbed (all network blocked).

Adapters are discovered through the registry; a module that is absent or
broken skips cleanly (the registry reports it unavailable — parallel adapter
work must never break this file). Covers: capabilities sanity, prepare
validation, publish envelope shape, status() shape, budget bookkeeping, and
revoke semantics — plus mock-specific fault-injection round trips.
"""
from __future__ import annotations

import socket
import sys
import urllib.request
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg               # noqa: E402
from agent_friday.services.platforms import base as pbase         # noqa: E402
from agent_friday.services.platforms.base import (                # noqa: E402
    AUTH_MODES, DEGRADATION_LADDER)

ALL_ADAPTERS = sorted(preg.ADAPTER_MODULES)

_REQUIRED_CAP_KEYS = ("formats", "char_limit", "title_limit", "media", "thread",
                      "native_schedule", "native_delete", "analytics",
                      "hashtags_max", "notes")
_REQUIRED_STATUS_KEYS = ("connected", "account", "scopes", "expires_at",
                         "tier", "last_error")
_REQUIRED_BUDGET_KEYS = ("window", "used", "limit", "reset_at")


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    yield
    preg._reset_for_tests()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Stub the transport: any real HTTP/socket attempt fails loudly. Adapter
    methods must still return envelopes (never raise) under this condition."""
    def _blocked(*a, **k):
        raise RuntimeError("network blocked in contract tests")
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)


def _fresh(name):
    """A fresh, isolated instance of the registered adapter class, or skip."""
    a = preg.get_adapter(name)
    if a is None:
        pytest.skip(f"adapter '{name}' unavailable: {preg.import_error(name)}")
    inst = type(a)()
    inst.configure({})
    return inst


# ── capabilities sanity ───────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_identity(name):
    a = _fresh(name)
    assert isinstance(a.name, str) and a.name
    assert isinstance(a.label, str) and a.label
    assert a.auth_mode in AUTH_MODES, a.auth_mode
    assert a.automation_tier in DEGRADATION_LADDER, a.automation_tier


@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_capabilities(name):
    a = _fresh(name)
    caps = a.capabilities()
    assert isinstance(caps, dict)
    for k in _REQUIRED_CAP_KEYS:
        assert k in caps, f"{name}: capabilities missing '{k}'"
    assert isinstance(caps["formats"], list) and caps["formats"]
    assert caps["char_limit"] is None or (isinstance(caps["char_limit"], int)
                                          and caps["char_limit"] > 0)
    assert isinstance(caps["media"], dict)
    assert isinstance(caps["thread"], bool)
    assert isinstance(caps["native_schedule"], bool)
    assert isinstance(caps["native_delete"], bool)
    assert caps["analytics"] in ("full", "counts", "none")
    assert isinstance(caps["notes"], list)


# ── status shape ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_status_shape(name):
    a = _fresh(name)
    st = a.status()
    assert isinstance(st, dict)
    for k in _REQUIRED_STATUS_KEYS:
        assert k in st, f"{name}: status missing '{k}'"
    assert isinstance(st["connected"], bool)
    assert st["tier"] in DEGRADATION_LADDER
    assert isinstance(st["scopes"], list)


# ── prepare validation ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_prepare_envelope(name):
    a = _fresh(name)
    caps = a.capabilities()
    res = a.prepare({"adapted_body": "contract battery test post",
                     "format": caps["formats"][0], "id": "tgt_contract"},
                    {"id": "post_contract", "title": "t"})
    assert isinstance(res, dict) and "ok" in res
    if res["ok"]:
        assert "prepared" in res and isinstance(res["prepared"], dict)
        assert isinstance(res.get("warnings"), list)
    else:
        assert res.get("error")


@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_prepare_flags_over_limit_body(name):
    a = _fresh(name)
    limit = a.capabilities().get("char_limit")
    if not isinstance(limit, int) or limit <= 0:
        pytest.skip(f"{name}: no hard char limit")
    res = a.prepare({"adapted_body": "x" * (limit + 100), "format": "post"}, {})
    assert isinstance(res, dict)
    # an over-limit body must be rejected or at least loudly flagged
    assert (res.get("ok") is False) or res.get("warnings"), \
        f"{name}: over-limit body passed prepare silently"


@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_prepare_never_raises_on_garbage(name):
    a = _fresh(name)
    res = a.prepare(None, None)
    assert isinstance(res, dict) and "ok" in res


# ── publish envelope (transport stubbed) ──────────────────────────────────────
@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_publish_envelope(name):
    a = _fresh(name)
    res = a.publish({"platform": a.name, "body": "contract test", "segments": [],
                     "assets": [], "options": {}, "format": "post"})
    assert isinstance(res, dict) and "ok" in res, \
        f"{name}: publish must return an envelope, never raise"
    if res["ok"]:
        assert res.get("post_url") and res.get("platform_post_id")
    else:
        assert res.get("error")


@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_delete_envelope(name):
    a = _fresh(name)
    res = a.delete("nonexistent-id")
    assert isinstance(res, dict) and "ok" in res


@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_fetch_metrics_none_or_dict(name):
    a = _fresh(name)
    m = a.fetch_metrics("nonexistent-id")
    assert m is None or isinstance(m, dict)
    am = a.fetch_account_metrics()
    assert am is None or isinstance(am, dict)


# ── budget bookkeeping ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_rate_budget_shape_and_consume(name):
    a = _fresh(name)
    b = a.rate_budget()
    assert isinstance(b, dict)
    for k in _REQUIRED_BUDGET_KEYS:
        assert k in b, f"{name}: rate_budget missing '{k}'"
    assert isinstance(b["used"], int) and b["used"] >= 0
    assert isinstance(b["limit"], int)
    before = b["used"]
    res = a.consume_budget(1)
    assert isinstance(res, dict)
    assert a.rate_budget()["used"] >= before + 1
    assert isinstance(a.budget_would_exceed(), bool)


# ── revoke semantics ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_revoke_and_refresh(name):
    a = _fresh(name)
    assert isinstance(a.refresh(), bool)
    assert isinstance(a.revoke(), bool)
    # revoke is idempotent and status still answers afterwards
    assert isinstance(a.revoke(), bool)
    st = a.status()
    assert isinstance(st, dict) and isinstance(st["connected"], bool)


# ── degradation ladder helper (§4.14) ─────────────────────────────────────────
@pytest.mark.parametrize("name", ALL_ADAPTERS)
def test_contract_degradation_helper(name):
    a = _fresh(name)
    opts = a.degradation_options()
    assert isinstance(opts, list)
    assert all(o in DEGRADATION_LADDER for o in opts)
    env = a.degrade("contract test")
    assert env["ok"] is False and env["requires_user_choice"] is True
    assert env["declared_rung"] == a.automation_tier


# ── mock-specific: publish round trip + fault injection ──────────────────────
def test_mock_publish_round_trip():
    a = _fresh("mock")
    prep = a.prepare({"adapted_body": "hello mock", "format": "post",
                      "id": "tgt_1"}, {"id": "post_1"})
    assert prep["ok"], prep
    res = a.publish(prep["prepared"])
    assert res["ok"] is True
    pid = res["platform_post_id"]
    assert res["post_url"] == f"mock://post/{pid}"
    assert len(a.published) == 1
    assert a.published[0]["body"] == "hello mock"
    # budget consumed by the successful publish
    assert a.rate_budget()["used"] == 1
    # metrics exist for a published id, None for an unknown one
    assert isinstance(a.fetch_metrics(pid), dict)
    assert a.fetch_metrics("mock-999") is None
    a.set_metrics(pid, {"likes": 7})
    assert a.fetch_metrics(pid)["likes"] == 7
    # delete removes it
    assert a.delete(pid) == {"ok": True, "deleted": True}
    assert a.published == []
    assert a.delete(pid)["deleted"] is False


def test_mock_fault_injection_publish_then_recovers():
    a = _fresh("mock")
    a.set_failures(publish=2)
    p = {"platform": "mock", "body": "b"}
    r1, r2, r3 = a.publish(p), a.publish(p), a.publish(p)
    assert r1["ok"] is False and r2["ok"] is False
    assert r3["ok"] is True
    assert len(a.published) == 1
    # nothing consumed budget on the failed attempts
    assert a.rate_budget()["used"] == 1


def test_mock_fault_injection_prepare_and_fail_always():
    a = _fresh("mock")
    a.set_failures(prepare=1)
    bad = a.prepare({"adapted_body": "x"}, {})
    assert bad["ok"] is False and bad["error"] == "injected_prepare_failure"
    good = a.prepare({"adapted_body": "x"}, {})
    assert good["ok"] is True

    a.set_failures(always=True)
    assert a.publish({"body": "b"})["ok"] is False
    a.set_failures()  # clear
    assert a.publish({"body": "b"})["ok"] is True


def test_mock_configurable_error_capabilities_and_connection():
    a = _fresh("mock")
    a.configure({"publish_error": "quota exhausted", "fail_always": True,
                 "capabilities": {"char_limit": 42, "analytics": "full"}})
    assert a.publish({"body": "b"})["error"] == "quota exhausted"
    caps = a.capabilities()
    assert caps["char_limit"] == 42 and caps["analytics"] == "full"

    a.configure({"connected": False})
    assert a.status()["connected"] is False
    assert a.publish({"body": "b"})["error"] == "not_connected"
    assert a.refresh() is False
    # reconnect via callback; revoke disconnects again
    st = a.handle_callback({})
    assert st["connected"] is True
    assert a.revoke() is True
    assert a.status()["connected"] is False


def test_mock_reset_clears_state():
    a = _fresh("mock")
    a.publish({"body": "b"})
    a.set_failures(publish=5)
    a.reset()
    assert a.published == [] and a.publish_calls == 0
    assert a.publish({"body": "b"})["ok"] is True
