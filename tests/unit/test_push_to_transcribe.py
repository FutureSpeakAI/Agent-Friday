"""Hold a key anywhere in Windows, speak, release, get the words.

A system-wide dictation key is the one surface where being wrong is expensive
in both directions: eat a keystroke and you break Alt+T in every application
that wanted it; miss one and the sentence is gone. Both halves are pinned
here, along with the privacy property — that a hook which is handed every key
in the system does nothing whatsoever with any key but its own.

Everything the service touches is injected, so none of this needs Windows, a
microphone, or a person holding a key down.
"""
import threading
import time

import pytest

ptt = pytest.importorskip("agent_friday.services.push_to_talk")

VK_T = ord("T")
VK_A = ord("A")
VK_ESC = 0x1B


class FakeRecorder:
    instances = []

    def __init__(self, rate=16000, on_level=None):
        self.on_level = on_level
        self.started = False
        self.stopped = False
        self.error = ""
        self.pcm = b"\x01\x02" * 16000       # a second of "speech"
        FakeRecorder.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        return self.pcm


class Spy:
    """Collects what the service did to the outside world."""

    def __init__(self):
        self.inserted = []
        self.replayed = []
        self.notes = []

    def insert(self, text):
        self.inserted.append(text)
        return True, ""

    def replay(self, mods, vk):
        self.replayed.append((frozenset(mods), vk))

    # indicator protocol
    def show(self, state, message):
        self.notes.append((state, message))

    def level(self, v):
        pass


@pytest.fixture(autouse=True)
def _clear():
    FakeRecorder.instances = []
    yield


def _svc(spy, hold_ms=40, transcribe=None, mods_held=True):
    return ptt.PushToTalk(
        hotkey="alt+t", hold_ms=hold_ms,
        transcribe=transcribe or (lambda pcm: "hello there"),
        insert=spy.insert, indicator=spy, replay=spy.replay,
        recorder_factory=FakeRecorder,
        modifiers_held=lambda mods: mods_held)


def _settle(svc, timeout=2.0):
    """Wait for the background finish worker."""
    end = time.time() + timeout
    while time.time() < end:
        if svc.last_result is not None:
            return
        time.sleep(0.01)


# ── The privacy property ────────────────────────────────────────────────────

def test_a_key_that_is_not_the_hotkey_is_passed_straight_through():
    """The hook is handed every key in the system. It must do nothing with
    any of them but its own."""
    spy = Spy()
    svc = _svc(spy)
    for vk in range(0x08, 0x90):
        if vk == VK_T:
            continue
        assert svc.on_key_event(vk, True) == ptt.PASS_THROUGH, (
            "vk 0x%02X was not passed through — a dictation key must not "
            "swallow other keystrokes" % vk)
        assert svc.on_key_event(vk, False) == ptt.PASS_THROUGH
    assert svc.state == ptt.IDLE
    assert not FakeRecorder.instances, "no other key may start the microphone"


def test_nothing_about_other_keys_is_retained():
    """There is no keystroke history here to leak."""
    spy = Spy()
    svc = _svc(spy)
    before = {k: v for k, v in vars(svc).items() if not k.startswith("_lock")}
    for vk in (VK_A, ord("B"), ord("1"), 0x20, 0x0D):
        svc.on_key_event(vk, True)
        svc.on_key_event(vk, False)
    after = {k: v for k, v in vars(svc).items() if not k.startswith("_lock")}
    changed = [k for k in before if repr(before[k]) != repr(after[k])]
    assert not changed, (
        "typing other keys changed %r — the module must hold no record of "
        "them" % changed)


def test_the_bare_key_without_its_modifier_belongs_to_the_application():
    """Pressing T while typing must not arm anything."""
    spy = Spy()
    svc = _svc(spy, mods_held=False)
    assert svc.on_key_event(VK_T, True) == ptt.PASS_THROUGH
    assert svc.state == ptt.IDLE


# ── Sharing Alt+T with every other application ──────────────────────────────

def test_a_quick_tap_is_given_back_to_the_focused_app():
    """Alt+T belongs to other programs too. A tap must reach them."""
    spy = Spy()
    svc = _svc(spy, hold_ms=150)
    assert svc.on_key_event(VK_T, True) == ptt.SUPPRESS, (
        "the key-down must be held back, or the app acts on it before we "
        "know whether this is a tap or a hold")
    assert svc.on_key_event(VK_T, False) == ptt.SUPPRESS
    assert spy.replayed == [(frozenset({"alt"}), VK_T)], (
        "a tap that is neither passed through nor replayed is a broken "
        "Alt+T in every application that wanted it")
    assert not FakeRecorder.instances, "a tap must not record"


def test_a_hold_starts_recording_and_a_release_transcribes():
    spy = Spy()
    svc = _svc(spy, hold_ms=30)
    svc.on_key_event(VK_T, True)
    time.sleep(0.12)
    assert svc.state == ptt.RECORDING
    assert FakeRecorder.instances and FakeRecorder.instances[0].started
    svc.on_key_event(VK_T, False)
    _settle(svc)
    assert spy.inserted == ["hello there"]
    assert not spy.replayed, "a real hold is ours; do not also send Alt+T"


def test_auto_repeat_while_held_does_not_start_a_second_recording():
    spy = Spy()
    svc = _svc(spy, hold_ms=20)
    svc.on_key_event(VK_T, True)
    time.sleep(0.1)
    for _ in range(10):
        assert svc.on_key_event(VK_T, True) == ptt.SUPPRESS
    assert len(FakeRecorder.instances) == 1
    svc.on_key_event(VK_T, False)
    _settle(svc)


# ── Getting out of it ───────────────────────────────────────────────────────

def test_escape_cancels_a_recording_and_nothing_is_inserted():
    spy = Spy()
    svc = _svc(spy, hold_ms=20)
    svc.on_key_event(VK_T, True)
    time.sleep(0.1)
    assert svc.on_key_event(VK_ESC, True) == ptt.SUPPRESS, (
        "Escape while recording is ours; the app should not also act on it")
    assert svc.state == ptt.IDLE
    assert FakeRecorder.instances[0].stopped
    svc.on_key_event(VK_T, False)
    time.sleep(0.15)
    assert spy.inserted == [], "a cancelled recording must insert nothing"


def test_escape_when_not_recording_is_left_alone():
    """Escape is a very load-bearing key. Only take it while listening."""
    spy = Spy()
    svc = _svc(spy)
    assert svc.on_key_event(VK_ESC, True) == ptt.PASS_THROUGH


def test_a_slip_of_the_hand_is_not_sent_to_the_transcriber():
    spy = Spy()
    called = []
    svc = _svc(spy, hold_ms=20,
               transcribe=lambda pcm: called.append(1) or "x")
    svc.on_key_event(VK_T, True)
    time.sleep(0.1)
    FakeRecorder.instances[0].pcm = b"\x00" * 200      # ~6 ms
    svc.on_key_event(VK_T, False)
    time.sleep(0.2)
    assert not called, "6ms of audio is a slip, not a sentence"
    assert any("Too short" in m for _s, m in spy.notes)


# ── Never losing the words ──────────────────────────────────────────────────

def test_when_the_window_refuses_the_paste_the_words_are_still_reachable():
    """An elevated window will not accept synthetic input. That is not a
    reason to throw away the sentence someone just spoke."""
    spy = Spy()

    def refuse(text):
        return False, "it is on your clipboard, press Ctrl+V"

    svc = _svc(spy, hold_ms=20)
    svc._insert = refuse
    svc.on_key_event(VK_T, True)
    time.sleep(0.1)
    svc.on_key_event(VK_T, False)
    _settle(svc)
    assert svc.last_result["text"] == "hello there"
    assert svc.last_result["ok"] is False
    assert any(s == "clipboard" for s, _m in spy.notes), (
        "the person must be told where their words went")


def test_a_transcriber_that_raises_says_so_instead_of_vanishing():
    spy = Spy()

    def boom(pcm):
        raise RuntimeError("the ear is not loaded")

    svc = _svc(spy, hold_ms=20, transcribe=boom)
    svc.on_key_event(VK_T, True)
    time.sleep(0.1)
    svc.on_key_event(VK_T, False)
    time.sleep(0.3)
    assert any(s == "error" and "not loaded" in m for s, m in spy.notes)


def test_the_microphone_failing_to_open_is_reported_in_words():
    spy = Spy()

    class Dead(FakeRecorder):
        def start(self):
            raise OSError("no microphone")

    svc = ptt.PushToTalk(hotkey="alt+t", hold_ms=20,
                         transcribe=lambda p: "x", insert=spy.insert,
                         indicator=spy, replay=spy.replay,
                         recorder_factory=Dead,
                         modifiers_held=lambda m: True)
    svc.on_key_event(VK_T, True)
    time.sleep(0.1)
    assert svc.state == ptt.IDLE
    assert any(s == "error" for s, _m in spy.notes)


# ── Rebinding ───────────────────────────────────────────────────────────────

def test_the_hotkey_is_rebindable():
    spy = Spy()
    svc = _svc(spy)
    svc.set_hotkey("ctrl+shift+space")
    assert svc.vk == 0x20
    assert svc.mods == {"ctrl", "shift"}
    assert svc.on_key_event(VK_T, True) == ptt.PASS_THROUGH, (
        "the old binding must stop firing")


@pytest.mark.parametrize("spec,why", [
    ("", "nothing bound"),
    ("alt", "a modifier alone would fire constantly"),
    ("alt+nonsense", "an unknown key"),
    ("meta+t", "an unknown modifier"),
])
def test_an_impossible_hotkey_says_why_rather_than_binding_something_else(
        spec, why):
    with pytest.raises(ptt.HotkeyError) as e:
        ptt.parse_hotkey(spec)
    assert str(e.value), why


def test_alt_t_is_the_default_because_alt_f_opens_the_browser_menu():
    assert ptt.DEFAULT_HOTKEY == "alt+t"
    assert ptt.describe_hotkey(ptt.DEFAULT_HOTKEY) == "Alt+T"


def test_hold_is_long_enough_to_tell_a_tap_from_a_hold_and_short_enough_to_feel_instant():
    assert 100 <= ptt.DEFAULT_HOLD_MS <= 300


# ── The clipboard is borrowed, not taken ────────────────────────────────────

class FakeClip:
    def __init__(self, value=None):
        self.value = value
        self.history = []

    def get(self):
        return self.value

    def set(self, v):
        self.value = v
        self.history.append(v)


def test_the_previous_clipboard_contents_come_back():
    clip = FakeClip("something the user had copied")
    ok, _note = ptt.insert_text("dictated words", send_input=lambda: None,
                                clipboard=clip)
    assert ok
    assert "dictated words" in clip.history
    end = time.time() + 2
    while time.time() < end and clip.value != "something the user had copied":
        time.sleep(0.02)
    assert clip.value == "something the user had copied", (
        "silently eating what someone had copied is its own small betrayal")


def test_a_paste_that_throws_leaves_the_text_on_the_clipboard():
    clip = FakeClip("old")

    def refuse():
        raise OSError("access denied")

    ok, note = ptt.insert_text("dictated words", send_input=refuse,
                               clipboard=clip)
    assert ok is False
    assert "Ctrl+V" in note
    assert clip.value == "dictated words", (
        "the words must survive a refused paste")


def test_empty_speech_is_not_pasted_over_the_clipboard():
    clip = FakeClip("precious")
    ok, _note = ptt.insert_text("   ", send_input=lambda: None, clipboard=clip)
    assert ok is False
    assert clip.value == "precious"
