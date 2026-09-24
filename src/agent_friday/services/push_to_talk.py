"""Hold a key anywhere in Windows, speak, release, and the words are typed.

This is the surface where "local by default" stops being a preference and
becomes a promise. A system-wide dictation key sees whatever the person is
doing — a password field, a message about someone's health, a half-written
resignation — so the audio never leaves this machine and the transcript is
never written anywhere but the window that had focus.

What the keyboard hook sees, stated plainly
-------------------------------------------
Detecting a *held* key needs key-down and key-up separately, and needs to
decide whether to swallow the keystroke before the focused app sees it.
Windows offers exactly one mechanism for that, a low-level keyboard hook, and
such a hook is necessarily handed every key event in the system. So the
honest claim is not "it cannot see other keys" — it is what this module
actually does with them, which is nothing:

  * the filter reads one field, ``vkCode``, and compares it against the one
    configured key;
  * anything else returns immediately, before any branch that could record,
    count, time or classify it;
  * nothing is written to disk, to a log, or to a buffer that outlives the
    call — there is no keystroke history here to leak, and the modifier state
    is *queried* at the moment the trigger fires rather than accumulated;
  * the audio buffer is discarded once transcribed.

Settings says the same thing in the same words. A promise the user cannot
check is worth less than a mechanism they can.

Passing a tap through
---------------------
Alt+T belongs to other applications too. A tap is therefore replayed to the
focused window rather than eaten: the key-down is suppressed, and if the key
comes back up inside ``hold_ms`` the keystroke is re-sent with SendInput and
nothing is recorded. Only a genuine hold starts the microphone.

Never losing the words
----------------------
Insertion can fail for a reason the user cannot do anything about — an
elevated window will not accept synthetic input from an unelevated process,
by design. When that happens the transcript stays on the clipboard and the
indicator says so. The sentence someone just spoke is not thrown away
because of a privilege boundary.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# 16 kHz mono 16-bit: what faster-whisper wants, so nothing is resampled.
CAPTURE_RATE = 16000
CAPTURE_WIDTH = 2
DEFAULT_HOTKEY = "alt+t"
#: A tap shorter than this belongs to the focused application, not to Friday.
DEFAULT_HOLD_MS = 150
#: Push-to-transcribe is for a sentence. A key held down by a book on the
#: keyboard should not become a twenty-minute recording.
MAX_RECORD_S = 120.0

VK_ESCAPE = 0x1B
_MODIFIER_VK = {"alt": 0x12, "ctrl": 0x11, "control": 0x11,
                "shift": 0x10, "win": 0x5B, "super": 0x5B, "cmd": 0x5B}
_NAMED_VK = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "escape": 0x1B, "esc": 0x1B, "backspace": 0x08, "insert": 0x2D,
    "delete": 0x2E, "home": 0x24, "end": 0x23,
    "`": 0xC0, "grave": 0xC0, ";": 0xBA, "'": 0xDE, ",": 0xBC,
    ".": 0xBE, "/": 0xBF, "\\": 0xDC, "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD,
}
for _i in range(1, 25):
    _NAMED_VK["f%d" % _i] = 0x6F + _i


class HotkeyError(ValueError):
    """A hotkey string that cannot be honoured, with a sentence saying why."""


def parse_hotkey(spec):
    """``"alt+t"`` → ``({"alt"}, 0x54)``.

    Raises :class:`HotkeyError` rather than silently binding something the
    user did not ask for: a hotkey that quietly became a different hotkey is
    indistinguishable from one that does not work.
    """
    parts = [p.strip().lower() for p in str(spec or "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("No hotkey set.")
    *mods, key = parts
    mod_set = set()
    for m in mods:
        if m not in _MODIFIER_VK:
            raise HotkeyError(
                "%r is not a modifier. Use alt, ctrl, shift or win." % m)
        mod_set.add("ctrl" if m == "control" else
                    "win" if m in ("super", "cmd") else m)
    if key in _MODIFIER_VK:
        raise HotkeyError(
            "A hotkey needs a key as well as modifiers — %r on its own would "
            "fire every time you pressed it." % key)
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key.upper())
    elif key in _NAMED_VK:
        vk = _NAMED_VK[key]
    else:
        raise HotkeyError("Friday does not know the key %r." % key)
    return mod_set, vk


def describe_hotkey(spec):
    """``"alt+t"`` → ``"Alt+T"``, for putting in front of a person."""
    try:
        parts = [p.strip() for p in str(spec or "").split("+") if p.strip()]
        pretty = {"alt": "Alt", "ctrl": "Ctrl", "control": "Ctrl",
                  "shift": "Shift", "win": "Win"}
        return "+".join(pretty.get(p.lower(), p.upper() if len(p) == 1
                                   else p.title()) for p in parts)
    except Exception:
        return str(spec)


# ── Microphone ──────────────────────────────────────────────────────────────
# winmm rather than PortAudio: this runs in the tray on Windows, and a global
# dictation key is a poor reason to add a binary dependency to the installer.

class WaveInRecorder:
    """Record 16 kHz mono PCM16 from the default input device.

    Buffers are handed back as they fill, so a level meter can move while
    someone is still speaking and the recording can be abandoned mid-sentence
    without waiting for anything.
    """

    def __init__(self, rate=CAPTURE_RATE, on_level=None):
        self.rate = int(rate)
        self.on_level = on_level
        self._chunks = []
        self._stop = threading.Event()
        self._thread = None
        self._error = ""

    @property
    def error(self):
        return self._error

    def start(self):
        self._stop.clear()
        self._chunks = []
        self._error = ""
        self._thread = threading.Thread(target=self._run, name="ptt-capture",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        """Stop and return the PCM captured so far."""
        self._stop.set()
        if self._thread:
            self._thread.join(3.0)
        return b"".join(self._chunks)

    # -- the winmm plumbing ------------------------------------------------
    def _run(self):
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as e:  # pragma: no cover - not Windows
            self._error = "no ctypes: %s" % e
            return

        winmm = ctypes.WinDLL("winmm.dll")

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [("wFormatTag", wintypes.WORD),
                        ("nChannels", wintypes.WORD),
                        ("nSamplesPerSec", wintypes.DWORD),
                        ("nAvgBytesPerSec", wintypes.DWORD),
                        ("nBlockAlign", wintypes.WORD),
                        ("wBitsPerSample", wintypes.WORD),
                        ("cbSize", wintypes.WORD)]

        class WAVEHDR(ctypes.Structure):
            pass

        WAVEHDR._fields_ = [("lpData", ctypes.c_char_p),
                            ("dwBufferLength", wintypes.DWORD),
                            ("dwBytesRecorded", wintypes.DWORD),
                            ("dwUser", ctypes.POINTER(wintypes.DWORD)),
                            ("dwFlags", wintypes.DWORD),
                            ("dwLoops", wintypes.DWORD),
                            ("lpNext", ctypes.POINTER(WAVEHDR)),
                            ("reserved", ctypes.POINTER(wintypes.DWORD))]

        fmt = WAVEFORMATEX(1, 1, self.rate, self.rate * CAPTURE_WIDTH,
                           CAPTURE_WIDTH, 8 * CAPTURE_WIDTH, 0)
        h = wintypes.HANDLE()
        WAVE_MAPPER = ctypes.c_uint(0xFFFFFFFF)
        rc = winmm.waveInOpen(ctypes.byref(h), WAVE_MAPPER, ctypes.byref(fmt),
                              0, 0, 0)
        if rc != 0:
            self._error = self._winmm_reason(rc)
            return

        # 20 ms per buffer, eight of them. The buffer size is the floor on
        # how much speech is lost at each end: a buffer only becomes readable
        # once it is full, and whatever is still in flight when the key comes
        # up has to be drained. At 100 ms this measured half a second missing
        # from every recording, which is a clipped first and last word; at
        # 20 ms it is about a tenth of that. Eight buffers keep 160 ms queued,
        # which is ample headroom for a 10 ms poll.
        n_buf, buf_bytes = 8, int(self.rate * CAPTURE_WIDTH * 0.02)
        bufs, hdrs = [], []
        try:
            for _ in range(n_buf):
                raw = ctypes.create_string_buffer(buf_bytes)
                hdr = WAVEHDR(ctypes.cast(raw, ctypes.c_char_p), buf_bytes,
                              0, None, 0, 0, None, None)
                winmm.waveInPrepareHeader(h, ctypes.byref(hdr),
                                          ctypes.sizeof(hdr))
                winmm.waveInAddBuffer(h, ctypes.byref(hdr),
                                      ctypes.sizeof(hdr))
                bufs.append(raw)
                hdrs.append(hdr)
            winmm.waveInStart(h)

            WHDR_DONE = 0x00000001
            started = time.monotonic()
            while not self._stop.is_set():
                if time.monotonic() - started > MAX_RECORD_S:
                    self._error = ("stopped after %d seconds — push-to-"
                                   "transcribe is for a sentence"
                                   % MAX_RECORD_S)
                    break
                idle = True
                for i, hdr in enumerate(hdrs):
                    if hdr.dwFlags & WHDR_DONE:
                        n = int(hdr.dwBytesRecorded)
                        if n:
                            data = bufs[i].raw[:n]
                            self._chunks.append(data)
                            if self.on_level:
                                try:
                                    self.on_level(_rms(data))
                                except Exception:
                                    pass
                        hdr.dwFlags &= ~WHDR_DONE
                        hdr.dwBytesRecorded = 0
                        winmm.waveInAddBuffer(h, ctypes.byref(hdr),
                                              ctypes.sizeof(hdr))
                        idle = False
                if idle:
                    time.sleep(0.01)
        except Exception as e:  # pragma: no cover - device-specific
            self._error = "%s: %s" % (type(e).__name__, e)
        finally:
            try:
                winmm.waveInStop(h)
                # waveInReset returns every queued buffer to us with whatever
                # it managed to record. Draining before the driver has written
                # those lengths back throws away the end of the sentence.
                winmm.waveInReset(h)
                time.sleep(0.03)
                # Indexed rather than looked up: hdrs.index(hdr) compares
                # ctypes structures, and pairing a header with the wrong
                # buffer would splice unrelated audio into the tail.
                for i, hdr in enumerate(hdrs):
                    n = int(hdr.dwBytesRecorded)
                    if n:
                        self._chunks.append(bufs[i].raw[:n])
                    winmm.waveInUnprepareHeader(h, ctypes.byref(hdr),
                                                ctypes.sizeof(hdr))
                winmm.waveInClose(h)
            except Exception:
                pass

    @staticmethod
    def _winmm_reason(rc):
        """waveIn error codes, in words rather than numbers."""
        return {
            2: "Windows has no microphone to record from. Plug one in, or "
               "check Sound settings.",
            4: "The microphone is already in use by another program.",
            32: "The microphone does not support 16 kHz mono recording.",
        }.get(rc, "Windows would not open the microphone (error %d). Check "
                  "Settings › Privacy › Microphone." % rc)


def _rms(pcm):
    """Loudness of a PCM16 buffer, 0.0-1.0, for the level meter."""
    if not pcm:
        return 0.0
    try:
        import array
        import math
        a = array.array("h")
        a.frombytes(pcm[:len(pcm) // 2 * 2])
        if not a:
            return 0.0
        return min(1.0, math.sqrt(sum(float(v) * v for v in a) / len(a))
                   / 8000.0)
    except Exception:
        return 0.0


# ── Putting the words where the person was looking ──────────────────────────

def foreground_window():
    """The window that has focus right now, or None."""
    try:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow() or None
    except Exception:
        return None


def refocus(hwnd):
    """Put focus back on ``hwnd``. True when it worked.

    Windows only grants this to a process that recently received input, which
    a dictation key has by definition — the user just held it.
    """
    if not hwnd:
        return False
    try:
        import ctypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        if u32.GetForegroundWindow() == hwnd:
            return True
        cur = k32.GetCurrentThreadId()
        tgt = u32.GetWindowThreadProcessId(hwnd, None)
        u32.AttachThreadInput(cur, tgt, True)
        try:
            u32.SetForegroundWindow(hwnd)
            u32.SetFocus(hwnd)
        finally:
            u32.AttachThreadInput(cur, tgt, False)
        time.sleep(0.05)
        return u32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def insert_text(text, send_input=None, clipboard=None, target=None):
    """Put ``text`` into the window the person was looking at.

    ``target`` is the window that had focus when the key went down. It is not
    the same as the window that has focus now: transcription takes a moment,
    and anything that steals focus in that moment — a notification, another
    application finishing its startup — would otherwise receive the sentence
    instead. Observed live, where a dictated sentence went to a different
    application entirely while its target sat in the background.

    Pasting private speech into whatever window happened to be in front is a
    disclosure, so when focus has moved and cannot be restored, this refuses
    and leaves the words on the clipboard.

    Clipboard-and-paste rather than synthetic typing: it is one event instead
    of one per character, it survives Chrome's and Word's input handling, and
    it does not scramble under a keyboard layout that disagrees about where a
    character lives. The previous clipboard contents are put back afterwards,
    because silently eating what someone had copied is its own small betrayal.

    Returns ``(ok, note)``. ``ok`` False means the words are on the clipboard
    and the caller should say so rather than pretend.
    """
    text = (text or "").strip()
    if not text:
        return False, "nothing was said"

    clip = clipboard or _Clipboard()
    previous = None
    try:
        previous = clip.get()
    except Exception:
        previous = None

    try:
        clip.set(text)
    except Exception as e:
        return False, "could not reach the clipboard: %s" % e

    if target is not None and not refocus(target):
        # The words are already on the clipboard above, so they are not lost.
        return False, (_why_refused() + " — the text is on your clipboard, "
                       "press Ctrl+V")

    try:
        (send_input or _send_ctrl_v)()
    except Exception as e:
        log.info("push-to-transcribe paste failed: %s", e)
        return False, (_why_refused() + " — the text is on your clipboard, "
                       "press Ctrl+V")

    # Long enough for the target app to service the paste, short enough that
    # nobody notices. Restoring too eagerly pastes the *old* clipboard.
    def _restore():
        time.sleep(0.4)
        try:
            if previous is not None:
                clip.set(previous)
        except Exception:
            pass

    threading.Thread(target=_restore, name="ptt-clip-restore",
                     daemon=True).start()
    return True, ""


class _Clipboard:
    def get(self):
        import win32clipboard as wc
        wc.OpenClipboard()
        try:
            import win32con
            if wc.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return wc.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            wc.CloseClipboard()

    def set(self, value):
        import win32clipboard as wc
        import win32con
        wc.OpenClipboard()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardData(win32con.CF_UNICODETEXT, value)
        finally:
            wc.CloseClipboard()


def _send_ctrl_v():
    from pynput.keyboard import Controller, Key
    kb = Controller()
    with kb.pressed(Key.ctrl):
        kb.press("v")
        kb.release("v")


def _why_refused():
    """Say which kind of refusal this was, since they need different answers.

    An administrator window is not a bug and not something the user can fix
    from Friday's side; a window that merely moved might just be moved back.
    Reporting both as "the paste failed" leaves someone retrying a thing that
    can never work.
    """
    if foreground_window_is_elevated():
        return ("Windows does not let Friday type into an administrator "
                "window")
    return "the window you dictated into would not take the text"


def foreground_window_is_elevated():
    """True when the focused window will refuse synthetic input.

    Windows blocks a lower-integrity process from sending input to a
    higher-integrity one. Nothing can be done about it from here, so the point
    of asking is to say something true instead of failing silently.
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(),
                                        ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                 pid.value)
        if not h:
            # Being refused the handle is itself the symptom of a process we
            # are not allowed to touch.
            return True
        kernel32.CloseHandle(h)
        return False
    except Exception:
        return False


# ── The hold-to-talk state machine ──────────────────────────────────────────
# Kept free of pynput, ctypes and audio so it can be driven directly: the
# interesting behaviour here is timing and suppression, and neither of those
# needs a microphone to be wrong.

IDLE = "idle"
PENDING = "pending"        # down, not yet held long enough to be ours
RECORDING = "recording"

#: What the caller should do with the key event it just handed us.
PASS_THROUGH = "pass"
SUPPRESS = "suppress"


class PushToTalk:
    """Hold the hotkey, speak, release; the transcript lands in the focus.

    Everything touching the outside world is injected — the recorder, the
    transcriber, the inserter, the clock — so the state machine can be driven
    at whatever speed a test likes.
    """

    def __init__(self, hotkey=DEFAULT_HOTKEY, hold_ms=DEFAULT_HOLD_MS,
                 transcribe=None, insert=None, indicator=None,
                 recorder_factory=None, modifiers_held=None, replay=None,
                 clock=None, mask=None):
        self.set_hotkey(hotkey)
        self.hold_ms = int(hold_ms)
        self._transcribe = transcribe
        self._insert = insert or insert_text
        self._indicator = indicator
        self._recorder_factory = recorder_factory or WaveInRecorder
        self._modifiers_held = modifiers_held or _modifiers_held
        self._replay = replay or _replay_chord
        self._mask = mask or (lambda: mask_alt_release(self.mods))
        self._clock = clock or time.monotonic
        self.state = IDLE
        self._rec = None
        self._mic_error = ""
        self._heard_audio = False
        self._target = None
        self._listener = None
        self._timer = None
        self._lock = threading.RLock()
        self.last_result = None      # {"text", "ok", "note"}

    # -- configuration -----------------------------------------------------
    def set_hotkey(self, spec):
        self.mods, self.vk = parse_hotkey(spec)
        self.hotkey = str(spec)

    # -- the one place that looks at a key ---------------------------------
    def on_key_event(self, vk, is_down):
        """Handle one key event. Returns PASS_THROUGH or SUPPRESS.

        Every key that is not the hotkey — or Escape while actually recording
        — leaves through the first branch, before any code that could record,
        time or classify it.
        """
        if vk != self.vk:
            if is_down and vk == VK_ESCAPE and self.state == RECORDING:
                self._cancel("Cancelled")
                return SUPPRESS
            return PASS_THROUGH

        if is_down:
            with self._lock:
                if self.state != IDLE:
                    return SUPPRESS          # auto-repeat while held
                if not self._modifiers_held(self.mods):
                    return PASS_THROUGH      # the bare key belongs to the app
                self.state = PENDING
                # Whose window this sentence belongs to is decided now, while
                # the person is looking at it — not later, when the transcript
                # is ready and focus may have moved.
                self._target = foreground_window()
                # Opening the microphone costs ~150ms (measured: waveInOpen is
                # 149ms on this machine). Paying that AFTER the hold threshold
                # would clip the first word off every sentence, because people
                # start speaking as they press. So the device opens now, during
                # the window in which we are still deciding whose keystroke
                # this is, and a tap throws the audio away unheard.
                self._start_recorder()
                self._timer = threading.Timer(self.hold_ms / 1000.0,
                                              self._promote)
                self._timer.daemon = True
                self._timer.start()
            return SUPPRESS

        # key up
        with self._lock:
            state = self.state
            if state == PENDING:
                self.state = IDLE
                if self._timer:
                    self._timer.cancel()
                    self._timer = None
        if state == PENDING:
            # A tap. It was never ours: discard the audio unheard and give the
            # keystroke back to whichever application was listening for it.
            self._discard_recorder()
            try:
                self._replay(self.mods, self.vk)
            except Exception as e:
                log.info("could not replay the tapped hotkey: %s", e)
            return SUPPRESS
        if state == RECORDING:
            # Before the user lifts Alt: give Windows a keystroke inside the
            # chord so the release is not read as a bare Alt.
            try:
                self._mask()
            except Exception as e:
                log.debug("could not mask the modifier release: %s", e)
            self._finish()
            return SUPPRESS
        return PASS_THROUGH

    # -- transitions -------------------------------------------------------
    def _start_recorder(self):
        """Open the microphone. Called on key-down, before we know whose
        keystroke this is; a tap discards whatever it captured."""
        self._heard_audio = False
        rec = self._recorder_factory(on_level=self._on_level)
        try:
            rec.start()
        except Exception as e:
            log.warning("push-to-transcribe could not start the mic: %s", e)
            self._notify("error", "Could not start the microphone: %s" % e)
            self._rec = None
            self._mic_error = str(e)
            return
        self._mic_error = ""
        self._rec = rec

    def _discard_recorder(self):
        rec, self._rec = self._rec, None
        if rec:
            try:
                rec.stop()
            except Exception:
                pass

    def _promote(self):
        """Still down past hold_ms, so this one is ours."""
        with self._lock:
            if self.state != PENDING:
                return
            if self._rec is None:
                self.state = IDLE
                return          # the mic never opened; _start_recorder said so
            self.state = RECORDING
        # Not "Listening…" yet. Windows takes about 600ms to deliver the first
        # sample after the device is opened (measured on this machine:
        # 0.57-0.81s), and no amount of buffer tuning moves it — it is the
        # capture stack starting up. Announcing "Listening" on a timer would
        # invite someone to start speaking into a microphone that is not
        # recording yet and lose their first word. So the card says what is
        # actually true, and the first real sample is what flips it.
        if not self._heard_audio:
            self._notify("arming", "Opening the microphone…")
        else:
            self._notify("recording",
                         "Listening… release to transcribe, Esc to cancel")

    def _cancel(self, why):
        with self._lock:
            self.state = IDLE
        rec, self._rec = self._rec, None
        if rec:
            try:
                rec.stop()
            except Exception:
                pass
        self._notify("idle", why)

    def _finish(self):
        with self._lock:
            self.state = IDLE
        rec, self._rec = self._rec, None
        if rec is None:
            return
        threading.Thread(target=self._finish_worker, args=(rec,),
                         name="ptt-finish", daemon=True).start()

    def _finish_worker(self, rec):
        try:
            pcm = rec.stop()
        except Exception as e:
            self._notify("error", "The microphone stopped badly: %s" % e)
            return
        if getattr(rec, "error", ""):
            self._notify("error", rec.error)
            return
        # Under a third of a second is a slip of the hand, not a sentence.
        if len(pcm) < CAPTURE_RATE * CAPTURE_WIDTH * 0.3:
            self._notify("idle", "Too short — hold the key while you speak")
            return
        self._notify("thinking", "Transcribing…")
        try:
            text = (self._transcribe(pcm) or "").strip()
        except Exception as e:
            log.warning("push-to-transcribe failed: %s: %s",
                        type(e).__name__, e)
            self._notify("error", str(e)[:200])
            return
        finally:
            pcm = None               # the audio does not outlive the call
        if not text:
            self._notify("idle", "Nothing was said")
            return
        ok, note = self._insert(text, target=self._target)
        self.last_result = {"text": text, "ok": bool(ok), "note": note}
        self._notify("done" if ok else "clipboard", note or text)

    def _on_level(self, level):
        # The first sample is the go signal: it is the moment the microphone
        # is genuinely recording rather than merely open.
        if not self._heard_audio:
            self._heard_audio = True
            if self.state == RECORDING:
                self._notify("recording",
                             "Listening… release to transcribe, Esc to cancel")
        if self._indicator:
            try:
                self._indicator.level(level)
            except Exception:
                pass

    def _notify(self, state, message):
        if self._indicator:
            try:
                self._indicator.show(state, message)
            except Exception:
                pass

    # -- the Windows hook --------------------------------------------------
    def start(self):
        """Install the low-level hook. True when it is listening."""
        try:
            from pynput import keyboard as kb
        except Exception as e:
            log.info("push-to-transcribe needs pynput: %s", e)
            return False

        WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
        WM_KEYUP, WM_SYSKEYUP = 0x0101, 0x0105

        def _filter(msg, data):
            # suppress_event() signals pynput by RAISING, so it has to be
            # called outside the guard. Catching it here instead swallows the
            # signal: the hook reports success, suppression silently never
            # happens, and every hold types a stray "t" into the document it
            # was dictating into.
            verdict = PASS_THROUGH
            try:
                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    verdict = self.on_key_event(data.vkCode, True)
                elif msg in (WM_KEYUP, WM_SYSKEYUP):
                    verdict = self.on_key_event(data.vkCode, False)
            except Exception as e:      # a hook that raises stops delivering
                log.warning("push-to-transcribe hook error: %s: %s",
                            type(e).__name__, e)
            if verdict == SUPPRESS:
                listener.suppress_event()
            return True

        listener = kb.Listener(win32_event_filter=_filter,
                               on_press=lambda *_a: None,
                               on_release=lambda *_a: None)
        listener.daemon = True
        listener.start()
        self._listener = listener
        log.info("push-to-transcribe listening for %s",
                 describe_hotkey(self.hotkey))
        return True

    def stop(self):
        lis, self._listener = self._listener, None
        if lis:
            try:
                lis.stop()
            except Exception:
                pass
        self._cancel("Stopped")


def _modifiers_held(mods):
    """Ask Windows which modifiers are down *now*.

    Asked on demand rather than accumulated from the key stream, so there is
    no modifier state for this module to have remembered.
    """
    if not mods:
        return True
    try:
        import ctypes
        g = ctypes.windll.user32.GetAsyncKeyState
        return all(g(_MODIFIER_VK[m]) & 0x8000 for m in mods)
    except Exception:
        return False


VK_SHIFT = 0x10


def keyboard_layout_count():
    """How many keyboard layouts this machine has installed."""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetKeyboardLayoutList(0, None)) or 1
    except Exception:
        return 1


def mask_alt_release(mods=("alt",), layouts=None):
    """Stop a suppressed Alt chord from reading as a bare Alt press.

    We swallow the T of Alt+T, which leaves Windows seeing Alt pressed and
    released with nothing in between — the chord that opens a menu bar. In
    Chrome that moves focus out of the text field, and the dictated sentence
    then pastes into nothing. Measured against a Chrome textarea: no Alt at
    all pastes; a bare Alt does not.

    Giving Windows a keystroke between the two is what prevents it. Of the
    candidates tried against Chrome only Shift worked — a Ctrl tap, a second
    Alt tap, and the documented dummy keys (VK_NONAME, vk07, 0xFF) all still
    lost the field.

    Alt+Shift is also the legacy layout-switch chord, so this only masks when
    the machine has a single keyboard layout and there is nothing to switch
    between. On a multi-layout machine the menu bar takes focus and the
    transcript stays on the clipboard, which is worse but not wrong.
    """
    if "alt" not in mods:
        return False
    if (layouts if layouts is not None else keyboard_layout_count()) > 1:
        return False
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.keybd_event(VK_SHIFT, 0, 0, 0)
        u32.keybd_event(VK_SHIFT, 0, 0x0002, 0)
        return True
    except Exception:
        return False


def _replay_chord(mods, vk):
    """Re-send a tapped hotkey to the focused window.

    The modifiers are still physically down — the user has not let go of Alt
    — so only the key itself needs replaying.
    """
    import ctypes
    KEYEVENTF_KEYUP = 0x0002
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
