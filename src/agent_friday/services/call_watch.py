"""Call mode: Friday gets out of the way when a call starts.

A call on this PC needs the webcam, the microphone, the GPU and the RAM that
Friday's brain seat and 3D scene are using. The 1 PM call that dropped its
video and mic, and the Zoom crash that followed, were the machine being
starved. So when another app takes the camera or the mic, Friday stands back
on its own: the page releases the webcam and the mic and holds the scene on
a still, the brain seat is parked (stand_down: every seat released, VRAM and
RAM freed, background jobs paused), and a small chip says so. When the call
ends, everything comes back.

How a call is noticed. Windows' capability access manager records which app
is using the webcam and the microphone right now (services/camera_holders);
Zoom's meeting host process (CptHost.exe) means a meeting even when the store
has not recorded it. A browser holding a device is Friday's own page when the
page says it holds that device (it reports `devices` in its desktop state),
and a call in the browser (Meet) otherwise. Two polls to start, twenty seconds
clear to end, so a glance at the camera app does not stand Friday down and a
dropped frame does not bring it back mid-call.

The owner decides: the `call_mode` setting is automatic (recommended), ask
(a chip with the choice, decided once per call) or off. A call can also be
started or ended by hand, from the chip or by voice, whatever the mode.
"""
from __future__ import annotations

import logging
import threading
import time

_log = logging.getLogger("friday.call_mode")

POLL_S = 3.0
CONFIRM_POLLS = 2          # consecutive polls seeing a call before acting
CLEAR_S = 20.0             # seconds with no signal before the call is over
MODES = ("automatic", "ask", "off")

#: Processes that only run while a call is in progress.
CALL_PROCESSES = {"cpthost.exe": "Zoom"}
#: Holder names that may be Friday's own page rather than a call.
BROWSERS = ("Chrome", "Edge", "Firefox")


def mode_from_settings() -> str:
    try:
        from agent_friday.core import _load_settings
        m = str((_load_settings() or {}).get("call_mode") or "automatic").strip().lower()
    except Exception:
        m = "automatic"
    return m if m in MODES else "automatic"


def _default_holders(kind: str) -> list:
    from agent_friday.services import camera_holders
    return camera_holders.parse(camera_holders.read_registry(kind))


def _default_processes() -> list:
    from agent_friday.services import camera_holders
    return [str(n or "").lower() for n in camera_holders._process_names()]


def _default_page_devices() -> dict:
    from agent_friday.services import desktop_bus
    return desktop_bus.page_devices()


def _default_push(action: dict) -> dict:
    from agent_friday.services import desktop_bus
    try:
        return desktop_bus.send([action], timeout=2.0)
    except Exception as e:
        return {"delivered": False, "reason": str(e)}


class Watch:
    """The call detector and the switch it throws. Every dependency is an
    injectable callable so a test can play a call app taking the camera."""

    def __init__(self, *, holders=None, processes=None, page_devices=None,
                 mode=None, push=None, now=None):
        self._holders = holders or _default_holders
        self._processes = processes or _default_processes
        self._page = page_devices or _default_page_devices
        self._mode = mode or mode_from_settings
        self._push = push or _default_push
        self._now = now or time.time
        self._lock = threading.Lock()
        self.active = False
        self.app = ""
        self.since = 0.0
        self.manual = False
        self.by = ""
        self.pending_ask = ""
        self.declined = ""
        self.seen = 0
        self.last_seen_at = 0.0
        self.last_signals: list = []
        self.last_error = ""

    # ── what is going on ────────────────────────────────────────────────
    def signals(self) -> list:
        """The apps that look like a call right now, most certain first."""
        out: list = []
        page = {}
        try:
            page = dict(self._page() or {})
        except Exception:
            page = {}
        for kind, own in (("webcam", "camera"), ("microphone", "mic")):
            try:
                names = list(self._holders(kind) or [])
            except Exception as e:
                self.last_error = str(e)
                names = []
            for n in names:
                if n in BROWSERS and page.get(own):
                    continue                      # Friday's own page
                if n not in out:
                    out.append(n)
        try:
            for p in self._processes() or []:
                name = CALL_PROCESSES.get(str(p or "").lower())
                if name and name not in out:
                    out.append(name)
        except Exception as e:
            self.last_error = str(e)
        return out

    def adopt_stand_down(self) -> None:
        """A persisted call stand-down from before a restart is ours: it ends
        like any other once the call is gone."""
        try:
            from agent_friday.services import stand_down
            st = stand_down.state()
        except Exception:
            return
        if st.get("active") and st.get("kind") == "call":
            self.active, self.app = True, str(st.get("app") or "")
            self.since = float(st.get("since") or self._now())
            self.last_seen_at = self._now()

    # ── one poll ────────────────────────────────────────────────────────
    def tick(self) -> dict:
        with self._lock:
            now = self._now()
            apps = self.signals()
            self.last_signals = apps
            if apps:
                self.seen += 1
                self.last_seen_at = now
            else:
                self.seen = 0
                self.declined = ""
                self.pending_ask = ""
            if self.active:
                if not self.manual and not apps and now - self.last_seen_at >= CLEAR_S:
                    self._end("the call ended")
                return self.snapshot()
            mode = self._mode()
            if mode == "off" or not apps or self.seen < CONFIRM_POLLS:
                return self.snapshot()
            app = apps[0]
            if app == self.declined:
                return self.snapshot()
            if mode == "automatic":
                self._start(app, "automatic")
            elif mode == "ask" and self.pending_ask != app:
                self.pending_ask = app
                self._push({"type": "call", "op": "ask", "app": app})
            return self.snapshot()

    # ── the switch ──────────────────────────────────────────────────────
    def _start(self, app: str, by: str) -> None:
        from agent_friday.services import stand_down
        self.active, self.app, self.by = True, app or "a call", by
        self.since = self._now()
        self.last_seen_at = self.since
        self.pending_ask = ""
        try:
            stand_down.stand_down(requested_by="call mode: %s" % self.app, kind="call", app=self.app)
        except Exception as e:
            self.last_error = str(e)
            _log.error("call mode could not stand Friday down: %s", e)
        self._push({"type": "call", "op": "start", "app": self.app, "by": by})
        _log.info("call mode on (%s, %s)", self.app, by)

    def _end(self, why: str) -> None:
        from agent_friday.services import stand_down
        app = self.app
        self.active, self.app, self.manual, self.by = False, "", False, ""
        self.since, self.seen = 0.0, 0
        try:
            if stand_down.state().get("kind") == "call":
                stand_down.resume(requested_by=why)
        except Exception as e:
            self.last_error = str(e)
            _log.error("call mode could not resume Friday: %s", e)
        self._push({"type": "call", "op": "end", "app": app})
        _log.info("call mode off (%s)", why)

    def start(self, app: str = "") -> dict:
        """By hand: the chip, or 'start call mode' by voice. Not ended by the
        detector; the owner ends it, unless a detected call ends it first."""
        with self._lock:
            if not self.active:
                self._start(app or "a call", "by hand")
            self.manual = True
            return self.snapshot()

    def end(self) -> dict:
        with self._lock:
            if self.active:
                self._end("ended by hand")
            self.pending_ask = ""
            return self.snapshot()

    def decide(self, accept: bool, app: str = "") -> dict:
        """The answer to an 'ask' chip, once per call."""
        with self._lock:
            app = app or self.pending_ask
            if accept:
                if not self.active:
                    self._start(app, "asked")
            else:
                self.declined = app
                self.pending_ask = ""
                self._push({"type": "call", "op": "declined", "app": app})
            return self.snapshot()

    def snapshot(self) -> dict:
        return {"active": self.active, "app": self.app, "since": self.since,
                "by": self.by, "manual": self.manual, "mode": self._mode(),
                "pending_ask": self.pending_ask, "signals": list(self.last_signals),
                "error": self.last_error}


# ── the process-wide watcher ────────────────────────────────────────────
_WATCH: Watch | None = None
_THREAD: threading.Thread | None = None
_STOP = threading.Event()


def watch() -> Watch:
    global _WATCH
    if _WATCH is None:
        _WATCH = Watch()
    return _WATCH


def _loop() -> None:
    w = watch()
    w.adopt_stand_down()
    while not _STOP.is_set():
        try:
            w.tick()
        except Exception as e:  # never let the watcher die
            _log.error("call watch tick failed: %s", e)
        _STOP.wait(POLL_S)


def start() -> None:
    """Start the poll thread once. Inert under FRIDAY_TESTING."""
    global _THREAD
    try:
        from agent_friday import core
        if getattr(core, "_TESTING", False):
            return
    except Exception:
        pass
    if _THREAD is not None and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, daemon=True, name="friday-call-watch")
    _THREAD.start()


def reset() -> None:
    """Forget the watcher (tests)."""
    global _WATCH
    _STOP.set()
    _WATCH = None
