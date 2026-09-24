"""A small always-on-top card that says Friday is listening.

Dictation without feedback is a guess: you cannot tell a hotkey that did not
fire from a microphone that is not recording from a transcriber that is still
thinking, and all three feel like nothing happening. So there is a card near
the cursor, it carries a level meter that moves with the room, and it says
which of those three you are in.

Tk lives on its own thread with its own root and its own mainloop, and every
call from outside is queued onto it — Tk is not thread-safe, and the tray owns
the main thread. Nothing here is load-bearing: if Tk is unavailable the
service runs on without a card rather than not at all.
"""
from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger(__name__)

_COLOR = {
    "arming": "#64748b",
    "recording": "#ef4444",
    "thinking": "#e0a030",
    "done": "#22c55e",
    "clipboard": "#e0a030",
    "error": "#ef4444",
    "idle": "#94a3b8",
}
#: How long a finished card stays up before fading out, per state.
_LINGER_MS = {"done": 1200, "idle": 1400, "clipboard": 4000, "error": 6000}


class Indicator:
    """The protocol the hold-to-talk service expects: ``show`` and ``level``."""

    def __init__(self):
        self._q = queue.Queue()
        self._thread = None
        self._started = threading.Event()
        self._failed = False

    # -- called from any thread -------------------------------------------
    def show(self, state, message):
        self._ensure()
        self._q.put(("show", state, message))

    def level(self, value):
        if self._started.is_set():
            self._q.put(("level", float(value), ""))

    def close(self):
        self._q.put(("quit", "", ""))

    # -- the Tk thread -----------------------------------------------------
    def _ensure(self):
        if self._failed or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="ptt-indicator",
                                        daemon=True)
        self._thread.start()
        self._started.wait(2.0)

    def _run(self):
        try:
            import tkinter as tk
        except Exception as e:  # pragma: no cover - headless
            log.info("push-to-transcribe indicator unavailable: %s", e)
            self._failed = True
            self._started.set()
            return

        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)          # no title bar, no taskbar entry
        root.attributes("-topmost", True)
        root.configure(bg="#0b1020")
        try:
            root.attributes("-alpha", 0.95)
        except Exception:
            pass

        frame = tk.Frame(root, bg="#0b1020", padx=12, pady=9,
                         highlightthickness=1,
                         highlightbackground="#2a3550")
        frame.pack(fill="both", expand=True)
        dot = tk.Canvas(frame, width=12, height=12, bg="#0b1020",
                        highlightthickness=0)
        dot.grid(row=0, column=0, padx=(0, 8))
        blob = dot.create_oval(1, 1, 11, 11, fill=_COLOR["idle"], outline="")
        label = tk.Label(frame, text="", bg="#0b1020", fg="#e8eefc",
                         font=("Segoe UI", 9), anchor="w", justify="left",
                         wraplength=320)
        label.grid(row=0, column=1, sticky="w")
        meter = tk.Canvas(frame, width=332, height=4, bg="#18203a",
                          highlightthickness=0)
        meter.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        bar = meter.create_rectangle(0, 0, 0, 4, fill=_COLOR["recording"],
                                     outline="")

        state = {"hide_after": None}

        def _place():
            """Near the cursor, but always fully on the screen."""
            root.update_idletasks()
            w, h = root.winfo_reqwidth(), root.winfo_reqheight()
            try:
                x = root.winfo_pointerx() + 18
                y = root.winfo_pointery() + 22
            except Exception:
                x = y = 40
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            x = max(8, min(x, sw - w - 8))
            y = max(8, min(y, sh - h - 8))
            root.geometry("+%d+%d" % (x, y))

        def _pump():
            try:
                while True:
                    kind, a, b = self._q.get_nowait()
                    if kind == "quit":
                        root.destroy()
                        return
                    if kind == "level":
                        meter.coords(bar, 0, 0, max(2, int(332 * min(1.0, a))), 4)
                    elif kind == "show":
                        dot.itemconfig(blob, fill=_COLOR.get(a, _COLOR["idle"]))
                        label.config(text=b or "")
                        meter.itemconfig(
                            bar, state="normal" if a == "recording" else "hidden")
                        if a == "recording":
                            meter.coords(bar, 0, 0, 0, 4)
                        if state["hide_after"]:
                            root.after_cancel(state["hide_after"])
                            state["hide_after"] = None
                        if not root.winfo_viewable():
                            root.deiconify()
                        _place()
                        linger = _LINGER_MS.get(a)
                        if linger:
                            state["hide_after"] = root.after(linger, root.withdraw)
            except queue.Empty:
                pass
            except Exception as e:
                log.debug("indicator pump: %s", e)
            root.after(50, _pump)

        self._started.set()
        root.after(50, _pump)
        try:
            root.mainloop()
        except Exception as e:  # pragma: no cover
            log.info("push-to-transcribe indicator stopped: %s", e)
        finally:
            self._started.clear()
