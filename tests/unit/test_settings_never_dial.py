"""Resolving settings never opens a network connection on the caller's thread.

Every settings load resolves which provider owns each configured model
(core._load_settings -> _sync_capability_routing -> provider_descriptors.
resolve_provider_name). When the Ollama manager said the daemon was "up" and
its 5-second model list was stale, that resolution dialled localhost:11434 on
the CALLER's thread. With no daemon, a refused loopback connect costs seconds
on Windows, and the caller was a voice turn (off_record.active() reads
settings), which stalled the turn. The model list is now read from the cache
only; a stale one is refreshed behind the caller.
"""
import socket
import threading
import time
import urllib.request

import pytest

from agent_friday.routing import ollama_manager as om
from agent_friday.routing import provider_descriptors as pd


@pytest.fixture
def dials(monkeypatch):
    """Every connection attempt, by thread. Each one is refused."""
    seen = []

    def urlopen(*a, **k):
        seen.append(threading.get_ident())
        raise OSError("no network in this test")

    def create_connection(*a, **k):
        seen.append(threading.get_ident())
        raise OSError("no network in this test")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    return seen


@pytest.fixture
def stale_up_manager(monkeypatch):
    """The singleton says the daemon is up (fresh) but its model list is stale."""
    mgr = om.OllamaManager("http://localhost:11434")
    mgr._available, mgr._available_ts = True, time.time()
    mgr._models_cache = [{"name": "qwen3:8b"}]
    mgr._models_ts = 0                      # long stale
    monkeypatch.setattr(om, "_instance", mgr)
    return mgr


def _mine(seen):
    me = threading.get_ident()
    return [t for t in seen if t == me]


def test_the_model_check_answers_from_the_cache_without_dialling(dials, stale_up_manager):
    prov = {"name": "ollama-local", "base_url": "http://localhost:11434"}
    t0 = time.monotonic()
    assert pd._live_ollama_has(prov, "qwen3:8b") is True       # the last known list
    assert pd._live_ollama_has(prov, "llama9:70b") is False
    assert _mine(dials) == [], "the caller's thread opened a connection"
    assert time.monotonic() - t0 < 0.5


def test_peek_models_never_dials_and_refreshes_behind_the_caller(dials, stale_up_manager):
    mgr = stale_up_manager
    assert mgr.peek_models() == [{"name": "qwen3:8b"}]
    assert _mine(dials) == []
    # The refresh ran on its own thread (and was refused here).
    deadline = time.monotonic() + 3
    while not dials and time.monotonic() < deadline:
        time.sleep(0.01)
    assert dials and all(t != threading.get_ident() for t in dials)
    fresh = om.OllamaManager("http://localhost:11434")
    assert fresh.peek_models() is None                        # nothing known yet


def test_resolving_a_provider_name_opens_no_socket(dials, stale_up_manager):
    pd.resolve_provider_name("qwen3:8b")
    pd.resolve_provider_name("bonsai2:27b")
    assert _mine(dials) == []


def test_loading_settings_opens_no_socket(dials, stale_up_manager):
    from agent_friday.core import _load_settings
    _load_settings()
    assert _mine(dials) == []
