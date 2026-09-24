"""The tray is where a system-wide hotkey lives, so the tray must wire it up.

The hotkey service is exercised in test_push_to_transcribe.py. What is pinned
here is the join between it and the rest of Friday: the tray reads the
settings, refuses a hotkey it cannot honour instead of binding something else,
routes the audio to the local ear and nowhere near a cloud one, and waits for
the server before installing a key whose first press would otherwise reach
nothing.
"""
import json

import pytest

tray = pytest.importorskip("agent_friday.friday_tray")


class FakeService:
    def __init__(self, **kw):
        self.kw = kw
        self.hotkey = kw.get("hotkey")
        self.hold_ms = kw.get("hold_ms")
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return True

    def stop(self):
        self.stopped = True

    def set_hotkey(self, spec):
        self.hotkey = spec


@pytest.fixture
def bridge(monkeypatch):
    p = tray.PushToTranscribe(server_url="http://127.0.0.1:1")
    monkeypatch.setattr(p, "_settings", lambda: dict(_SETTINGS))
    return p


_SETTINGS = {"push_to_transcribe": True, "push_to_transcribe_hotkey": "alt+t",
             "push_to_transcribe_hold_ms": 150}


def _patch_service(monkeypatch, made):
    import agent_friday.services.push_to_talk as ptt

    def factory(**kw):
        s = FakeService(**kw)
        made.append(s)
        return s

    monkeypatch.setattr(ptt, "PushToTalk", factory)
    return ptt


def test_it_is_on_and_system_wide_by_default():
    """Stephen asked for system-wide, not for an opt-in."""
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["push_to_transcribe"] is True
    assert DEFAULT_SETTINGS["push_to_transcribe_hotkey"] == "alt+t", (
        "Alt+F opens Chrome's menu on Windows; Alt+T is the agreed binding")


def test_turning_it_off_removes_the_hook_entirely(monkeypatch, bridge):
    made = []
    _patch_service(monkeypatch, made)
    monkeypatch.setattr(bridge, "_settings",
                        lambda: {"push_to_transcribe": False})
    bridge.apply()
    assert bridge.service is None
    assert not made, "off must mean no keyboard hook at all, not a quiet one"
    assert bridge.detail == "off"


def test_a_hotkey_it_cannot_honour_is_refused_not_quietly_replaced(
        monkeypatch, bridge):
    made = []
    _patch_service(monkeypatch, made)
    monkeypatch.setattr(bridge, "_settings", lambda: {
        "push_to_transcribe": True, "push_to_transcribe_hotkey": "alt+nonsense"})
    bridge.apply()
    assert bridge.service is None
    assert "bad hotkey" in bridge.detail, (
        "binding something the user did not ask for is worse than not "
        "binding: %r" % bridge.detail)


def test_rebinding_does_not_stack_a_second_hook(monkeypatch, bridge):
    made = []
    _patch_service(monkeypatch, made)
    bridge.apply()
    assert len(made) == 1 and made[0].started
    monkeypatch.setattr(bridge, "_settings", lambda: {
        "push_to_transcribe": True,
        "push_to_transcribe_hotkey": "ctrl+shift+space"})
    bridge.apply()
    assert len(made) == 1, "rebinding must reuse the hook, not add one"
    assert made[0].hotkey == "ctrl+shift+space"


def test_the_audio_goes_to_the_local_ear(monkeypatch, bridge):
    """No cloud STT. Not as a preference — as a route that does not exist."""
    seen = {}

    class R:
        def read(self):
            return json.dumps({"text": "hello"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = req.data
        return R()

    monkeypatch.setattr(tray.urllib.request, "urlopen", fake_urlopen)
    out = bridge._transcribe(b"\x01\x02" * 100)
    assert out == "hello"
    assert seen["url"].endswith("/api/voice/transcribe")
    assert seen["url"].startswith("http://127.0.0.1"), (
        "push-to-transcribe audio must not leave this machine: %r"
        % seen["url"])
    assert seen["body"] == b"\x01\x02" * 100


def test_a_server_error_becomes_a_sentence_not_a_stack_trace(monkeypatch,
                                                             bridge):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 503, "Service Unavailable", {},
            __import__("io").BytesIO(
                json.dumps({"error": "The voice models have not been "
                                     "downloaded yet."}).encode()))

    monkeypatch.setattr(tray.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as e:
        bridge._transcribe(b"\x01\x02" * 100)
    assert "downloaded" in str(e.value)


def test_it_waits_for_the_server_before_installing_the_key(monkeypatch):
    """A hotkey whose first press reaches nothing gets written off."""
    t = tray.FridayTray.__new__(tray.FridayTray)
    t.ptt = tray.PushToTranscribe()
    applied = []
    t.ptt.apply = lambda: applied.append(1)
    t._refresh_menu = lambda: None
    monkeypatch.setattr(tray, "_wait_for_health",
                        lambda **kw: (False, "never came up"))
    t._start_push_to_transcribe()
    assert not applied, "no server, no hotkey"

    monkeypatch.setattr(tray, "_wait_for_health", lambda **kw: (True, "ok"))
    t._start_push_to_transcribe()
    assert applied == [1]


def test_health_is_read_as_a_pair_not_a_truthy_tuple():
    """_wait_for_health returns (ok, detail). Testing the tuple itself is
    always true, which would install the hotkey against a dead server."""
    import inspect
    src = inspect.getsource(tray.FridayTray._start_push_to_transcribe)
    assert "if not _wait_for_health(" not in src, (
        "a non-empty tuple is truthy; unpack it")
