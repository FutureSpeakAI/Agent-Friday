"""The tray is where a system-wide hotkey lives, so the tray must wire it up.

The hotkey service is exercised in test_push_to_transcribe.py. What is pinned
here is the join between it and the rest of Friday: the tray reads the
settings, refuses a hotkey it cannot honour instead of binding something else,
and routes the audio to the local ear and nowhere near a cloud one. It
registers the key without waiting for the server, whose cold start can outlast
any wait, keeps trying until the hook holds, and says so out loud when it
cannot.
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
    """System-wide by default, not an opt-in."""
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


def test_a_hold_before_the_server_is_up_says_so(monkeypatch, bridge):
    """The key is registered before the server can transcribe, which is only
    right because a hold during a boot is told why nothing was typed."""
    import urllib.error

    def refused(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    monkeypatch.setattr(tray.urllib.request, "urlopen", refused)
    with pytest.raises(RuntimeError) as e:
        bridge._transcribe(b"\x01\x02" * 100)
    assert "starting up" in str(e.value)


# ── Registration: now, until it holds, and out loud when it cannot ──────────

ACTIVE = tray.PushToTranscribe.ACTIVE
OFF = tray.PushToTranscribe.OFF
RETRY = tray.PushToTranscribe.RETRY
BLOCKED = tray.PushToTranscribe.BLOCKED


class FakeIcon:
    def __init__(self):
        self.notifications = []

    def notify(self, message, title=None):
        self.notifications.append((message, title))


class _Stop(Exception):
    pass


def _tray(monkeypatch, outcomes, stop_after=None):
    """A tray whose bridge answers apply() from `outcomes` in turn. Sleeps are
    recorded, not slept; with `stop_after`, the loop is broken at that sleep."""
    t = tray.FridayTray.__new__(tray.FridayTray)
    t.ptt = tray.PushToTranscribe(server_url="http://127.0.0.1:1")
    t.icon = FakeIcon()
    t._refresh_menu = lambda: None
    queue = list(outcomes)
    calls = []

    def apply():
        outcome, detail = queue.pop(0)
        calls.append(outcome)
        t.ptt.detail = detail
        return outcome

    t.ptt.apply = apply
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if stop_after is not None and len(slept) >= stop_after:
            raise _Stop()

    monkeypatch.setattr(tray.time, "sleep", sleep)
    # A server that never answers, so a health gate (if one were back) fails
    # fast instead of polling for real.
    monkeypatch.setattr(tray, "_wait_for_health",
                        lambda **kw: (False, "NOT RESPONDING"))
    return t, calls, slept


def test_the_hotkey_is_registered_without_waiting_for_the_server(monkeypatch):
    """A cold start takes minutes on a slow machine and the hook needs none of
    it. Waiting for /api/health (120 s, then never again) is how the hotkey
    went missing after a restart."""
    t, calls, slept = _tray(monkeypatch, [(ACTIVE, "Alt+T")])

    def no_wait(**kw):
        raise AssertionError("registration must not wait for the server")

    monkeypatch.setattr(tray, "_wait_for_health", no_wait)
    t._start_push_to_transcribe()
    assert calls == [ACTIVE]
    assert slept == [], "registered at once, no waiting"
    assert t.icon.notifications == []


def test_a_failed_registration_is_retried_until_it_holds(monkeypatch):
    """There is no last attempt: the tray backs off to once a minute and keeps
    going, well past the two minutes that used to be the end of it."""
    fails = [(RETRY, "could not install the keyboard hook")] * 12
    t, calls, slept = _tray(monkeypatch, fails + [(ACTIVE, "Alt+T")])
    t._start_push_to_transcribe()
    assert len(calls) == 13 and calls[-1] == ACTIVE, (
        "it stops trying only when the hook holds: %r" % calls)
    assert slept[:3] == [2.0, 4.0, 8.0], slept
    assert max(slept) == tray.PTT_RETRY_MAX_S
    assert sum(slept) > 120


def test_a_registration_that_keeps_failing_is_said_out_loud(monkeypatch):
    """Under pythonw the log reaches no file: a hotkey that is not working
    says so in a notification, and says so again when it recovers."""
    t, calls, slept = _tray(
        monkeypatch,
        [(BLOCKED, "bad hotkey: 'alt+nonsense'")] * 3 + [(ACTIVE, "Alt+T")])
    t._start_push_to_transcribe()
    msgs = [m for m, _title in t.icon.notifications]
    assert len(msgs) == 2, msgs
    assert "not working" in msgs[0] and "bad hotkey" in msgs[0]
    assert "working now" in msgs[1] and "Alt+T" in msgs[1]
    assert all(title == "Friday Desktop" for _m, title in t.icon.notifications)


def test_the_menu_line_says_why_while_it_is_not_working(monkeypatch):
    t, calls, slept = _tray(
        monkeypatch, [(RETRY, "could not install the keyboard hook")] * 50,
        stop_after=5)
    with pytest.raises(_Stop):
        t._start_push_to_transcribe()
    assert "could not install the keyboard hook" in t.ptt.label()
    assert len(t.icon.notifications) == 1, "said once, not once a minute"


def test_one_failure_that_the_retry_fixes_is_not_worth_a_notification(
        monkeypatch):
    t, calls, slept = _tray(
        monkeypatch,
        [(RETRY, "could not install the keyboard hook"), (ACTIVE, "Alt+T")])
    t._start_push_to_transcribe()
    assert calls == [RETRY, ACTIVE]
    assert t.icon.notifications == []


def test_off_is_an_answer_not_a_failure(monkeypatch):
    t, calls, slept = _tray(monkeypatch, [(OFF, "off")])
    t._start_push_to_transcribe()
    assert calls == [OFF] and slept == [] and t.icon.notifications == []


def test_settings_json_is_read_while_the_server_is_starting(monkeypatch,
                                                            tmp_path):
    """Turned off in settings means no hook from the first second, not the
    default hook until the server answers. The file is read as the server
    reads it, BOM and all."""
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"push_to_transcribe": False}).encode())
    made = []
    _patch_service(monkeypatch, made)
    p = tray.PushToTranscribe(server_url="http://127.0.0.1:1")
    assert p.apply() == OFF
    assert not made, "off in settings.json is no hook, server or no server"


def test_a_rebound_hotkey_is_honoured_before_the_server_answers(monkeypatch,
                                                                tmp_path):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps({"push_to_transcribe_hotkey": "ctrl+shift+space"}),
        encoding="utf-8")
    made = []
    _patch_service(monkeypatch, made)
    p = tray.PushToTranscribe(server_url="http://127.0.0.1:1")
    assert p.apply() == ACTIVE
    assert made[0].hotkey == "ctrl+shift+space"
