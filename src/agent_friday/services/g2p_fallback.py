"""Kokoro's pronunciation fallback, with espeak-ng kept OUT of Friday's process.

misaki, Kokoro's English G2P (Apache-2.0), knows words from its lexicon; an
out-of-vocabulary word (most people's names) needs a fallback, and without
one misaki either crashes on the word or Kokoro silently skips it. The usual
fallback is espeak-ng through ``phonemizer``, both GPL-3.0. Friday never
imports or loads them: the espeak-ng lookup runs in a separate helper
program (``agent_friday/voice/espeak_helper.py``, started by file path so it
does not even import Friday) that Friday talks to over a pipe, one JSON line
per word. Friday's repository vendors no GPL code or binary; the helper's
GPL dependencies (``phonemizer-fork`` and ``espeakng-loader``) are an
optional install the owner chooses, shown with their licence.

When the helper is not installed or does not answer, a name is SPELLED OUT
letter by letter (``spell_out``), with a log line saying why: never skipped,
never a crash.

``install_stub()`` puts a stand-in ``misaki.espeak`` module in place before
Kokoro is imported (kokoro.pipeline does ``from misaki import en, espeak``,
and the real module imports phonemizer at import time), so Kokoro builds its
fallback from ``PipeFallback`` instead. Both Kokoro paths, in this process
and in the GPU voice worker (also Friday's code), go through it.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import types
from pathlib import Path

log = logging.getLogger("friday.g2p_fallback")

#: The helper program, run by path with this interpreter.
HELPER_PATH = Path(__file__).resolve().parent.parent / "voice" / "espeak_helper.py"

#: One word's lookup may take this long before the word is spelled out.
LOOKUP_TIMEOUT_S = 5.0

#: misaki's US phonemes for the names of the letters and digits, used to spell
#: a word out when no pronunciation is available.
LETTER_PHONEMES = {
    "a": "ˈA", "b": "bˈi", "c": "sˈi", "d": "dˈi", "e": "ˈi", "f": "ˈɛf",
    "g": "ʤˈi", "h": "ˈAʧ", "i": "ˈI", "j": "ʤˈA", "k": "kˈA", "l": "ˈɛl",
    "m": "ˈɛm", "n": "ˈɛn", "o": "ˈO", "p": "pˈi", "q": "kjˈu", "r": "ˈɑɹ",
    "s": "ˈɛs", "t": "tˈi", "u": "jˈu", "v": "vˈi", "w": "dˈʌbəljˌu",
    "x": "ˈɛks", "y": "wˈI", "z": "zˈi",
    "0": "zˈɪɹO", "1": "wˈʌn", "2": "tˈu", "3": "θɹˈi", "4": "fˈɔɹ",
    "5": "fˈIv", "6": "sˈɪks", "7": "sˈɛvən", "8": "ˈAt", "9": "nˈIn",
}


def helper_installed() -> bool:
    """Are the helper's (GPL) dependencies installed? Checked WITHOUT
    importing them into this process."""
    try:
        return (importlib.util.find_spec("phonemizer") is not None
                and importlib.util.find_spec("espeakng_loader") is not None
                and HELPER_PATH.is_file())
    except Exception:
        return False


def spell_out(text: str) -> str:
    """Phonemes that say `text` letter by letter ("" when nothing is
    spellable)."""
    return " ".join(LETTER_PHONEMES[c] for c in str(text or "").lower()
                    if c in LETTER_PHONEMES)


class _Helper:
    """The one helper process this process talks to. Started on first use,
    restarted once if it dies; a word whose lookup fails is spelled out."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self.unavailable = ""         # why the helper cannot be used, if it cannot

    def _start(self):
        env = dict(os.environ)
        env.update({"PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1"})
        p = subprocess.Popen(
            [sys.executable, str(HELPER_PATH)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        hello = self._read(p)
        if not (hello or {}).get("ready"):
            try:
                p.kill()
            except Exception:
                pass
            raise RuntimeError((hello or {}).get("error") or "the helper did not start")
        return p

    @staticmethod
    def _read(p):
        box = {}

        def _r():
            box["line"] = p.stdout.readline()
        th = threading.Thread(target=_r, daemon=True)
        th.start()
        th.join(LOOKUP_TIMEOUT_S)
        if th.is_alive() or not box.get("line"):
            return None
        try:
            return json.loads(box["line"])
        except ValueError:
            return None

    def lookup(self, word: str, british: bool):
        """``(phonemes, rating)`` from espeak-ng, or None."""
        if self.unavailable:
            return None
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._proc is None or self._proc.poll() is not None:
                        self._proc = self._start()
                    self._proc.stdin.write(json.dumps({"text": word,
                                                       "british": bool(british)}) + "\n")
                    self._proc.stdin.flush()
                    out = self._read(self._proc)
                    if out is None:
                        raise RuntimeError("no answer within %.0f s" % LOOKUP_TIMEOUT_S)
                    if out.get("ps"):
                        return out["ps"], out.get("rating") or 2
                    return None
                except Exception as e:  # noqa: BLE001
                    try:
                        if self._proc is not None:
                            self._proc.kill()
                    except Exception:
                        pass
                    self._proc = None
                    if attempt == 2:
                        self.unavailable = "%s: %s" % (type(e).__name__, str(e)[:160])
                        log.warning("espeak helper unavailable (%s); unknown words "
                                    "will be spelled out", self.unavailable)
            return None

    def close(self):
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
                self._proc = None


_HELPER = _Helper()
_SPELLED_LOGGED: set = set()


class PipeFallback:
    """misaki's fallback interface (``fallback(token) -> (phonemes, rating)``)
    served by the helper process, else by spelling the word out."""

    def __init__(self, british: bool = False):
        self.british = bool(british)

    def __call__(self, token):
        text = getattr(token, "text", "") or ""
        if helper_installed():
            got = _HELPER.lookup(text, self.british)
            if got:
                return got
            why = _HELPER.unavailable or "the helper had no pronunciation"
        else:
            why = "the espeak-ng helper is not installed"
        ps = spell_out(text)
        key = text.lower()
        if key not in _SPELLED_LOGGED:          # one line per word, not per use
            if len(_SPELLED_LOGGED) > 1000:
                _SPELLED_LOGGED.clear()
            _SPELLED_LOGGED.add(key)
            log.info("pronunciation: spelling out %r (%s)", text, why)
        return (ps or None), 1


class _NoEspeakG2P:
    """misaki's non-English espeak G2P is not available in Friday's process."""

    def __init__(self, *a, **k):
        raise RuntimeError("non-English pronunciation needs espeak-ng, which "
                           "Friday does not load into its own process")


def install_stub() -> dict:
    """Make ``misaki.espeak`` the pipe-backed stand-in before Kokoro imports.

    Returns ``{"stubbed", "helper", "detail"}``. If the real module (and with
    it phonemizer) was already imported into this process by something else,
    that is reported, not hidden.
    """
    cur = sys.modules.get("misaki.espeak")
    if cur is not None and not getattr(cur, "_FRIDAY_STUB", False):
        return {"stubbed": False, "helper": helper_installed(),
                "detail": "misaki.espeak (and phonemizer) were already imported "
                          "into this process"}
    if cur is None:
        stub = types.ModuleType("misaki.espeak")
        stub._FRIDAY_STUB = True
        stub.EspeakFallback = PipeFallback
        stub.EspeakG2P = _NoEspeakG2P
        sys.modules["misaki.espeak"] = stub
        pkg = sys.modules.get("misaki")
        if pkg is not None:
            setattr(pkg, "espeak", stub)
    helper = helper_installed()
    return {"stubbed": True, "helper": helper,
            "detail": "" if helper else "espeak-ng helper not installed; unknown "
                                        "words are spelled out"}
