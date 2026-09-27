"""Which provider a stored message was sent to, so recall knows what it may repeat.

Recalling an earlier conversation into a cloud model is only free of new
exposure when that same provider already received it: a line spoken in an
earlier Gemini Live call can be recalled into a Gemini Live call. Anything else
(a local-model chat, a turn sent to a different provider, a turn of unknown
origin) goes through the local model and the payload card
(services/local_context).

New turns record it: meta.provider (who answered) and meta.sent_to (the cloud
providers that received the exchange), plus meta.off_record. Older turns carry
neither, and are inferred only where the evidence is solid: a voice turn stored
inside a Gemini Live session window, as recorded in the egress audit log
("live voice session opened" / "closed"), was spoken to google-gemini.
Everything else counts as local-only.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

GEMINI = "google-gemini"
LOCAL = "local"
#: A turn is persisted when its reply completes, a moment after the audio.
_WINDOW_SLACK_S = 120.0
#: An opened session with no recorded close still counts for this long.
_OPEN_SESSION_MAX_S = 3 * 3600.0

_LOCK = threading.Lock()
_CACHE = {"key": None, "windows": []}


def normalize_provider(p) -> str:
    s = str(p or "").strip().lower()
    if not s:
        return "unknown"
    if "gemini" in s or s.startswith("google"):
        return GEMINI
    if s in ("local", "ollama", "llama-cpp", "llama-cpp-local", "llamacpp") or "local" in s:
        return LOCAL
    return s


def turn_meta(provider, off_record: bool) -> dict:
    """The provenance fields to store on a new turn."""
    p = normalize_provider(provider)
    return {"provider": p, "sent_to": [] if p in (LOCAL, "unknown") else [p],
            "off_record": bool(off_record)}


def chat_turn_meta(off_record: bool) -> dict:
    """Provenance of the chat turn just generated on this thread."""
    try:
        from agent_friday.services import attribution
        gen = attribution.last_generation() or {}
        provider = gen.get("provider") if isinstance(gen, dict) else getattr(gen, "provider", None)
    except Exception:
        provider = None
    return turn_meta(provider, off_record)


def _egress_log_path() -> Path:
    try:
        from agent_friday.services import egress_gate as eg
        return Path(eg._DEFAULT_LOG)
    except Exception:
        pass
    from agent_friday.paths import friday_home
    return friday_home() / "vault" / "egress-log.jsonl"


def gemini_windows(path: Path | None = None) -> list:
    """[(opened, closed)] of Gemini Live sessions, from the egress audit log."""
    path = Path(path) if path else _egress_log_path()
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (str(path), st.st_size, st.st_mtime)
    with _LOCK:
        if _CACHE["key"] == key:
            return list(_CACHE["windows"])
    windows, opened = [], None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "live voice session" not in line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if normalize_provider(e.get("provider")) != GEMINI:
                    continue
                reason = str(e.get("reason") or "")
                ts = float(e.get("ts") or 0)
                if "opened" in reason:
                    if opened is not None:
                        windows.append((opened, min(ts, opened + _OPEN_SESSION_MAX_S)))
                    opened = ts
                elif "closed" in reason and opened is not None:
                    windows.append((opened, ts))
                    opened = None
    except OSError:
        return []
    if opened is not None:
        windows.append((opened, opened + _OPEN_SESSION_MAX_S))
    with _LOCK:
        _CACHE.update(key=key, windows=windows)
    return list(windows)


def _in_gemini_window(ts: float, windows) -> bool:
    return any(a <= ts <= b + _WINDOW_SLACK_S for a, b in windows)


def sent_to(msg: dict, windows=None) -> set:
    """The cloud providers this stored message was sent to (explicit or inferred)."""
    meta = (msg or {}).get("meta") or {}
    if "sent_to" in meta:
        return {normalize_provider(p) for p in (meta.get("sent_to") or [])}
    if meta.get("via") == "voice":
        ts = float((msg or {}).get("ts") or 0)
        if ts and _in_gemini_window(ts, gemini_windows() if windows is None else windows):
            return {GEMINI}
    return set()


def is_off_record(msg: dict) -> bool:
    return bool(((msg or {}).get("meta") or {}).get("off_record"))


def stops_storage(settings: dict) -> bool:
    """Off-record writes nothing about the conversation (services/off_record)."""
    from agent_friday.services import off_record as _off
    return _off.stops_storage(settings)
