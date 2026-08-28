"""A dead Ollama daemon must cost one slow probe, not one per request.

Measured on Windows 2026-08-28: a connection to a CLOSED localhost port does
not fail instantly the way the loopback interface suggests it should. It costs
about 2 seconds, because the stack retries the SYN before giving up. A
black-holed address costs the full 3-second timeout.

/api/intelligence probes the daemon on every call, and the Intelligence panel
polls that route on an interval. So on a machine where Ollama is not running --
a supported state, and the state of at least one real install -- every single
render paid two seconds for the same refusal, against a client abort that was
set to twelve.

Backoff, not removal: the daemon can be started at any moment, so the probe has
to keep trying. It just must not re-learn the same "no" at full price several
times a minute.
"""

import time

import pytest

from agent_friday.routes import intelligence as I


@pytest.fixture(autouse=True)
def _clear():
    I.reset_ollama_probe_state_for_tests()
    yield
    I.reset_ollama_probe_state_for_tests()


class _Boom:
    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        raise OSError("connection refused")


def test_a_refusal_is_remembered_for_the_backoff_window(monkeypatch):
    boom = _Boom()
    import requests
    monkeypatch.setattr(requests, "get", boom)

    for _ in range(5):
        assert I._ollama_sizes() == {}
    assert boom.calls == 1, (
        f"{boom.calls} probes for one dead daemon — each costs ~2s on Windows")


def test_it_tries_again_once_the_window_expires(monkeypatch):
    boom = _Boom()
    import requests
    monkeypatch.setattr(requests, "get", boom)

    I._ollama_sizes()
    assert boom.calls == 1
    # Ollama can be started at any moment; backoff must expire, not latch.
    monkeypatch.setattr(I, "_OLLAMA_DOWN_BACKOFF_S", 0.0)
    I._ollama_sizes()
    assert boom.calls == 2


def test_a_success_is_cached_briefly_and_returned(monkeypatch):
    calls = {"n": 0}

    class _Resp:
        @staticmethod
        def json():
            return {"models": [{"name": "qwen3:4b", "size": 2500000000}]}

    def _ok(*a, **kw):
        calls["n"] += 1
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "get", _ok)

    first = I._ollama_sizes()
    assert first == {"qwen3:4b": 2500000000}
    for _ in range(4):
        assert I._ollama_sizes() == first
    assert calls["n"] == 1, "the model list was re-fetched on every render"


def test_a_recovered_daemon_is_picked_up(monkeypatch):
    """Backoff must not outlive the failure it describes."""
    import requests
    boom = _Boom()
    monkeypatch.setattr(requests, "get", boom)
    assert I._ollama_sizes() == {}

    class _Resp:
        @staticmethod
        def json():
            return {"models": [{"name": "qwen3:4b", "size": 1}]}

    monkeypatch.setattr(I, "_OLLAMA_DOWN_BACKOFF_S", 0.0)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Resp())
    assert I._ollama_sizes() == {"qwen3:4b": 1}


def test_the_probe_never_raises(monkeypatch):
    """This feeds a display. A panel that 500s because a daemon is off is worse
    than a panel with no wake estimate."""
    import requests

    def _weird(*a, **kw):
        raise ValueError("nonsense from the daemon")
    monkeypatch.setattr(requests, "get", _weird)
    assert I._ollama_sizes() == {}
