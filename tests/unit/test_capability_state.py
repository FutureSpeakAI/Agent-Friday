"""Absent and unconfigured are different words, and the model is told which.

A surface that reports ABSENT for "no key" tells the model a keyable tool
(Firecrawl, Brave) is "not a tool" at all. services/capability_state.py is the one
answer; these tests pin the vocabulary, the probes and the prompt block.
"""
from __future__ import annotations

from agent_friday.services import capability_state as cs
from agent_friday.services import web_search as ws


def _unkeyed(monkeypatch):
    monkeypatch.setattr(ws, "brave_key", lambda: "")
    from agent_friday.services import firecrawl as fc
    monkeypatch.setattr(fc, "configured", lambda: False)
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: False)
    # wigolo runs on 127.0.0.1:3333 when it is installed, which makes these
    # tests pass or fail depending on whether a local service happens to be up
    # on the machine running them - it answers first and the assertions about
    # the keyed backends never get their turn (the detail comes back "local,
    # keyless" instead of DuckDuckGo's HTTP 202). "Unkeyed" has to mean every backend is out, including the one that needs
    # no key.
    monkeypatch.setattr(ws, "_wigolo_ready", lambda: False)


def test_a_missing_key_is_unconfigured_not_absent(monkeypatch):
    _unkeyed(monkeypatch)
    assert ws.health_state()["state"] == ws.UNCONFIGURED
    assert "BRAVE_SEARCH_API_KEY" in ws.health_state()["needs"]
    assert ws.firecrawl_health()["state"] == ws.UNCONFIGURED
    assert "FIRECRAWL_API_KEY" in ws.firecrawl_health()["needs"]
    b, f = cs.brave_state(), cs.firecrawl_state()
    assert b.state == cs.UNCONFIGURED and "BRAVE_SEARCH_API_KEY" in b.needs
    assert f.state == cs.UNCONFIGURED and "FIRECRAWL_API_KEY" in f.needs
    assert "start.bat" in b.fix and "settings.json" in b.fix


def test_a_keyed_backend_is_present_until_proven(monkeypatch):
    monkeypatch.setattr(ws, "brave_key", lambda: "k")
    monkeypatch.setattr(ws, "_HEALTH", {"state": ws.UNVERIFIED, "proven_on": None,
                                        "detail": "", "checked_at": 0.0})
    assert cs.brave_state().state == cs.PRESENT_UNVERIFIED
    monkeypatch.setattr(ws, "_HEALTH", {"state": ws.WORKING, "proven_on": "/res/v1/web/search",
                                        "detail": "a live search returned results",
                                        "checked_at": 1.0})
    assert cs.brave_state().state == cs.WORKING
    monkeypatch.setattr(ws, "_HEALTH", {"state": ws.PRESENT_FAILING, "proven_on": None,
                                        "detail": "HTTP 401", "checked_at": 1.0})
    st = cs.brave_state()
    assert st.state == cs.PRESENT_FAILING and "401" in st.detail


def test_the_local_seat_distinguishes_serving_installed_and_absent(monkeypatch):
    from agent_friday.services import local_seats, local_call
    monkeypatch.setattr(local_seats, "resolve", lambda role: "gemma4:e2b-fridayweaver-1.0")
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: "http://127.0.0.1:8090/v1")
    st = cs.local_brain_state()
    assert st.state == cs.WORKING and "8090" in st.detail
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    st = cs.local_brain_state()
    assert st.state == cs.PRESENT_FAILING and "nothing is serving it" in st.detail
    monkeypatch.setattr(local_seats, "resolve", lambda role: None)
    assert cs.local_brain_state().state == cs.ABSENT


def test_google_accounts_come_from_the_live_store(monkeypatch):
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "accounts_summary", lambda: {
        "total": 2, "healthy": 0, "connected": False, "degraded": False,
        "needs_attention": [{"email": "a@x", "summary": "token expired"},
                            {"email": "b@x", "summary": "token expired"}]})
    st = cs.google_accounts_state()
    assert st.state == cs.PRESENT_FAILING and "a@x" in st.detail and "Reconnect" in st.detail
    monkeypatch.setattr(ga, "accounts_summary", lambda: {
        "total": 0, "healthy": 0, "connected": False, "degraded": False, "needs_attention": []})
    assert cs.google_accounts_state().state == cs.UNCONFIGURED


def test_the_prompt_block_names_variables_and_forbids_the_lie(monkeypatch):
    _unkeyed(monkeypatch)
    text = cs.describe_for_model()
    assert text.startswith("== CAPABILITY STATE (live, read now) ==")
    assert "NEVER say it is not part of your toolkit" in text
    assert "Firecrawl: unconfigured" in text and "FIRECRAWL_API_KEY" in text
    assert "Brave: unconfigured" in text and "BRAVE_SEARCH_API_KEY" in text
    # The word "absent" is reserved for things not installed; unkeyed
    # backends never render with it.
    for line in text.splitlines():
        if "Firecrawl" in line or "Brave" in line:
            assert "absent" not in line


def test_the_block_rides_after_the_clock_in_the_volatile_tail(monkeypatch):
    _unkeyed(monkeypatch)
    from agent_friday.services.agent import _get_friday_system_prompt
    from agent_friday.services.prompt_cache import VOLATILE_MARKER
    p = _get_friday_system_prompt(provider="local", vault_control=None)
    i, j = p.find(VOLATILE_MARKER), p.find("== CAPABILITY STATE (live, read now) ==")
    assert i >= 0 and j > i, "the capability block must sit after the clock, in the volatile tail"


def test_failed_search_names_every_unkeyed_backend(monkeypatch):
    _unkeyed(monkeypatch)
    monkeypatch.setattr(ws, "_gate_search_query", lambda q, backend: (q, None))
    monkeypatch.setattr(ws, "_note_backend_health", lambda name, out: None)
    monkeypatch.setattr(ws, "_duckduckgo", lambda q, n: {
        "status": ws.SearchStatus.BACKEND_BROKEN, "detail": "DuckDuckGo returned HTTP 202"})
    out = ws.search("mahesh ganesana infosys", count=5)
    d = out["detail"]
    assert "HTTP 202" in d
    assert "Firecrawl" in d and "FIRECRAWL_API_KEY" in d
    assert "Brave" in d and "BRAVE_SEARCH_API_KEY" in d
    assert "not a tool" in d
    assert out["brave"]["configured"] is False and out["firecrawl"]["configured"] is False
