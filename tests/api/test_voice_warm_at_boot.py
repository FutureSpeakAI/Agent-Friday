"""The first spoken turn must not pay for loading the voice.

Measured on this machine: a cold Kokoro load is 91s, a warm one 0.19s. The
cold load was paid on the first spoken turn, and a minute and a half of
nothing after pressing the mic is indistinguishable, from the outside, from
voice being broken.

The machinery to pre-load existed — ``/api/voice/warm`` — and nothing outside
the test suite ever called it. These tests pin the two halves that make boot
warming real: the server asks for it, and the endpoint answers an
unauthenticated loopback request, because that is the only credential the
server has when talking to itself.
"""
import json

import pytest

from agent_friday import server as srv


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(payload=None):
    """An opener that records the request instead of making it."""
    seen = {}

    def opener(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["timeout"] = timeout
        return _FakeResponse(payload if payload is not None
                             else {"status": "ok", "state": "loading"})

    return seen, opener


def test_boot_asks_the_server_to_warm_the_local_voice():
    seen, opener = _capture()
    srv.warm_voice_after_boot("http://127.0.0.1:4321",
                              delay_s=0, opener=opener).join(5)
    assert seen.get("url") == "http://127.0.0.1:4321/api/voice/warm", (
        "boot must actually ask for the warm; the endpoint existing is not "
        "the same as anything calling it, which is how a 91s cold load "
        "survived to the first spoken turn")
    assert seen["method"] == "POST", "GET only reports state; POST starts it"


def test_warming_is_best_effort_and_never_takes_the_boot_down():
    """Voice is a feature. A failure to pre-load it costs the cold load, and
    must not cost the server."""
    def boom(req, timeout=None):
        raise OSError("connection refused")

    t = srv.warm_voice_after_boot("http://127.0.0.1:4321",
                                  delay_s=0, opener=boom)
    t.join(5)
    assert not t.is_alive()


def test_it_waits_rather_than_importing_torch_during_startup():
    """Importing Kokoro pulls in torch and transformers. Doing that while the
    rest of boot is importing them is the race that used to leave local voice
    dead until a restart, so the default must not be 'immediately'."""
    import inspect
    delay = inspect.signature(srv.warm_voice_after_boot).parameters["delay_s"]
    assert delay.default >= 10, (
        "warming during startup re-creates the import race it exists to "
        "help with: %r" % (delay.default,))


def test_the_warm_endpoint_answers_loopback_without_credentials(client):
    """The server has no credentials when it talks to itself.

    If ``@login_required`` turned this away, boot warming would silently 401
    forever and the only symptom would be the slow first sentence coming
    back.
    """
    r = client.post("/api/voice/warm", json={})
    assert r.status_code == 200, (
        "loopback is auth-trusted; a %s here means the boot warmer is a "
        "no-op" % r.status_code)
    assert r.get_json().get("status") == "ok"


def test_a_cloud_voice_session_has_nothing_to_warm(client, monkeypatch):
    """Warming must not load local models for someone who chose Gemini."""
    import agent_friday.routes.voice as v
    monkeypatch.setattr(v, "_resolve_voice_engine",
                        lambda *a, **k: {"engine": "gemini",
                                         "models_ready": True})
    called = []
    monkeypatch.setattr(v, "_warm_local_voice_async",
                        lambda: called.append(1))
    body = client.post("/api/voice/warm", json={}).get_json()
    assert body["state"] == "skipped"
    assert not called, "cloud voice selected: nothing local should load"
