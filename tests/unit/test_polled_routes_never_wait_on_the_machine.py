"""A request must never wait on a daemon that is not there, or on a machine read.

Measured on Windows: a connection to a CLOSED loopback port is not refused in
a millisecond. The stack retries the SYN, so it costs about 2 s, and 4 s for
``localhost`` (::1 then 127.0.0.1). The settings loader resolves provider names
on every cache miss and the auth gate loads the settings on every request, so
one unreachable Ollama daemon held a server worker, per request, for seconds.
On a 4-core CI runner those waits stacked until POST /api/settings took a
minute and a GET never came back.

These tests stand a slow, failing daemon (and slow machine reads) behind the
code under test and hold the request path to a bound.
"""
from __future__ import annotations

import threading
import time
import urllib.error

import pytest
from flask import Flask

from agent_friday.routing import ollama_manager as om

SLOW_S = 2.5          # what a refused loopback connect costs on Windows
BOUND_S = 1.0         # what a request may spend on the same read


def _request():
    return Flask(__name__).test_request_context("/")


@pytest.fixture
def slow_dead_daemon(monkeypatch):
    calls = {"n": 0}
    lock = threading.Lock()

    def urlopen(*a, **kw):
        with lock:
            calls["n"] += 1
        time.sleep(SLOW_S)
        raise urllib.error.URLError("[WinError 10061] connection refused")

    monkeypatch.setattr(om.urllib.request, "urlopen", urlopen)
    return calls


@pytest.fixture
def mgr():
    return om.OllamaManager("http://127.0.0.1:1")


def test_a_request_does_not_wait_out_a_dead_daemon(mgr, slow_dead_daemon):
    with _request():
        t0 = time.time()
        assert mgr.is_available() is False
        assert time.time() - t0 < BOUND_S


def test_an_unanswered_probe_is_not_cached_as_a_verdict(mgr, slow_dead_daemon):
    with _request():
        mgr.is_available()
    assert mgr._available is None, "a probe that has not finished says nothing"


def test_a_request_gets_the_stale_answer_at_once_and_refreshes_behind_it(
        mgr, slow_dead_daemon):
    mgr._available, mgr._available_ts = True, time.time() - 3600
    with _request():
        t0 = time.time()
        assert mgr.is_available() is True
        assert time.time() - t0 < 0.3
    time.sleep(SLOW_S + 0.5)
    assert mgr._available is False, "the refresh behind the request lands"


def test_peek_never_waits_and_starts_the_probe(mgr, slow_dead_daemon):
    t0 = time.time()
    assert mgr.peek_available() is None
    assert time.time() - t0 < 0.3
    time.sleep(SLOW_S + 0.5)
    assert mgr.peek_available() is False
    assert slow_dead_daemon["n"] == 1


def test_a_burst_of_callers_dials_the_daemon_once(mgr, slow_dead_daemon):
    out = []
    ts = [threading.Thread(target=lambda: out.append(mgr.is_available()))
          for _ in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    assert out == [False] * 12
    assert slow_dead_daemon["n"] == 1, (
        "%d connections for one dead daemon" % slow_dead_daemon["n"])


def test_inventory_reads_in_a_request_do_not_dial_a_daemon_known_to_be_down(
        mgr, slow_dead_daemon):
    mgr._available, mgr._available_ts = False, time.time()
    with _request():
        t0 = time.time()
        assert mgr.list_models() == []
        assert mgr.list_running() == []
        assert time.time() - t0 < 0.3
    assert slow_dead_daemon["n"] == 0


def test_resolving_a_provider_name_does_not_dial_the_daemon(
        slow_dead_daemon, monkeypatch):
    """The settings loader's path: ``_sync_capability_routing`` ->
    ``_provider_for_model`` -> ``_live_ollama_has``."""
    from agent_friday.routing import provider_descriptors as pd
    fresh = om.OllamaManager("http://127.0.0.1:1")
    monkeypatch.setattr(om, "get_manager", lambda *a, **k: fresh)
    t0 = time.time()
    assert pd._live_ollama_has({"base_url": "http://127.0.0.1:1"}, "gemma4:12b") is False
    assert time.time() - t0 < 0.3


def test_outside_a_request_the_caller_still_gets_the_real_answer(
        mgr, slow_dead_daemon):
    assert mgr.is_available() is False
    assert mgr._available is False
    assert slow_dead_daemon["n"] == 1


def test_one_dead_daemon_costs_the_inventory_probe_one_dial(monkeypatch):
    from agent_friday.routes import intelligence as I
    I.reset_ollama_probe_state_for_tests()
    calls = {"n": 0}

    def get(*a, **kw):
        calls["n"] += 1
        time.sleep(1.0)
        raise OSError("refused")

    import requests
    monkeypatch.setattr(requests, "get", get)
    ts = [threading.Thread(target=I._ollama_sizes) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    I.reset_ollama_probe_state_for_tests()
    assert calls["n"] == 1


# ── the machine reads behind the same routes ────────────────────────────────

def test_static_machine_facts_are_read_once(monkeypatch):
    from agent_friday.services import hardware_profile as hp
    hp.reset_static_facts()
    monkeypatch.setattr(hp, "_os_family", lambda: "windows")
    runs = {"n": 0}

    def run(cmd, timeout=15):
        runs["n"] += 1
        return '{"Name": "Test CPU", "NumberOfCores": 4}'

    monkeypatch.setattr(hp, "_run", run)
    for _ in range(5):
        assert hp.detect_cpu()["model"] == "Test CPU"
    assert runs["n"] == 1, "PowerShell was started %d times for one CPU" % runs["n"]
    hp.reset_static_facts()


def test_displays_are_read_once_per_window(monkeypatch):
    from agent_friday.services import hardware_profile as hp
    hp.reset_static_facts()
    reads = {"n": 0}

    def read():
        reads["n"] += 1
        return {"count": 2, "hidpi": 0, "indirect": 0, "adapters": [], "ok": True}

    monkeypatch.setattr(hp, "_read_displays", read)
    first = hp.detect_displays()
    first["count"] = 99                      # a caller's edit must not leak
    assert hp.detect_displays()["count"] == 2
    assert reads["n"] == 1
    hp.reset_static_facts()


def test_concurrent_profile_misses_run_one_detection(monkeypatch):
    from agent_friday.services import hardware_profile as hp
    monkeypatch.setitem(hp._MEMO, "profile", None)
    monkeypatch.setitem(hp._MEMO, "at", 0.0)
    runs = {"n": 0}

    def once(force, measure, now):
        runs["n"] += 1
        time.sleep(0.6)
        hp._MEMO["at"], hp._MEMO["profile"] = time.time(), {"profile_id": "x"}
        return hp._MEMO["profile"]

    monkeypatch.setattr(hp, "_detect_once", once)
    out = []
    ts = [threading.Thread(target=lambda: out.append(hp.get())) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    assert len(out) == 8 and runs["n"] == 1


def test_a_request_is_handed_the_last_profile_while_one_thread_refreshes(monkeypatch):
    from agent_friday.services import hardware_profile as hp
    old = {"profile_id": "old"}
    monkeypatch.setitem(hp._MEMO, "profile", old)
    monkeypatch.setitem(hp._MEMO, "at", time.time() - 3600)
    started = threading.Event()

    def once(force, measure, now):
        started.set()
        time.sleep(1.5)
        hp._MEMO["at"], hp._MEMO["profile"] = time.time(), {"profile_id": "new"}
        return hp._MEMO["profile"]

    monkeypatch.setattr(hp, "_detect_once", once)
    with _request():
        t0 = time.time()
        assert hp.get() is old
        assert time.time() - t0 < 0.3
    assert started.wait(5)
    time.sleep(2.0)
    assert hp.get()["profile_id"] == "new"


def test_cloud_consent_status_in_a_request_answers_within_its_budget(monkeypatch):
    """The unanswered-consent screen asks on every page load; the assessment
    prices the whole model catalogue."""
    from agent_friday.privacy import cloud_consent as cc
    from agent_friday.services import swr_cache
    swr_cache.invalidate("consent.capability")
    runs = {"n": 0}

    def assess(profile=None):
        runs["n"] += 1
        time.sleep(1.2)
        return {"capable": True, "roles": {}, "chain_ok": True, "chain_why": None}

    monkeypatch.setattr(cc, "assess_local_capability", assess)
    monkeypatch.setattr(cc, "CAPABILITY_BUDGET_S", 0.2)
    with _request():
        t0 = time.time()
        first = cc._capability_for_status(None)
        assert time.time() - t0 < 0.8
        assert first.get("pending") is True and first["capable"] is False
    time.sleep(1.6)
    with _request():
        t0 = time.time()
        second = cc._capability_for_status(None)
        assert time.time() - t0 < 0.3
        assert second["capable"] is True and not second.get("pending")
    assert runs["n"] == 1, "the second page load recomputed the assessment"
    swr_cache.invalidate("consent.capability")


def test_the_system_panel_reads_processes_without_starting_powershell(monkeypatch):
    from agent_friday.routes import core_routes
    started = []
    monkeypatch.setattr(core_routes.subprocess, "run",
                        lambda *a, **k: started.append(a) or (_ for _ in ()).throw(AssertionError("spawned")))
    rows = core_routes._system_top_processes()
    assert started == []
    assert 0 < len(rows) <= 8
    assert set(rows[0]) == {"Name", "CPU_s", "MemMB"}
    assert [r["CPU_s"] for r in rows] == sorted((r["CPU_s"] for r in rows), reverse=True)


def test_the_health_canary_does_not_start_icacls(monkeypatch, tmp_path):
    """Every health computation round-trips a canary secret. Hardening a file
    that lives for one round trip started two icacls processes each time."""
    from agent_friday.services import credential_store as cs
    from agent_friday.services import health_check
    hardened = []
    monkeypatch.setattr(cs, "harden_permissions", lambda p: hardened.append(str(p)))
    ok, _detail = health_check.check_credential_store()
    assert ok is True
    assert hardened == []
    cs.write_secret(tmp_path / "real.enc", b"secret")
    assert len(hardened) == 2, "a real secret is still hardened"


def test_a_build_waiter_gives_up_before_the_page_does():
    """The Intelligence panel aborts its own fetch at 30 s; a waiter that sits
    longer than that holds a worker for a page that has gone."""
    from agent_friday.routes import intelligence
    assert intelligence.CARD_WAIT_S < 30
