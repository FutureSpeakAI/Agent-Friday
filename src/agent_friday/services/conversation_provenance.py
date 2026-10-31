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
    """[(opened, closed)] of Gemini Live sessions, from the egress audit log.

    The log only grows, and this runs for every stored message (north star
    §34.15-2: a large store must not make each turn slower), so only the bytes
    appended since the last read are scanned. A file that shrank or was
    replaced is read again from the start. A line still being written (no
    newline yet) waits for the next read."""
    path = Path(path) if path else _egress_log_path()
    try:
        st = os.stat(path)
    except OSError:
        return []
    with _LOCK:
        c = dict(_CACHE)
    same_file = c.get("path") == str(path) and c.get("ino") == getattr(st, "st_ino", None)
    if same_file and c.get("size") == st.st_size and c.get("mtime") == st.st_mtime:
        return _windows_of(c)
    if same_file and st.st_size >= int(c.get("offset") or 0) and _fingerprint(path, int(c.get("offset") or 0)) \
            == c.get("print"):
        offset, closed, opened = int(c.get("offset") or 0), list(c.get("closed") or []), c.get("opened")
    else:
        # Shrunk, replaced, or rewritten in place (the bytes already read are
        # not the ones there now): read it all again, so no session is missed.
        offset, closed, opened = 0, [], None
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return []
    end = data.rfind(b"\n") + 1                     # whole lines only
    for raw_line in data[:end].decode("utf-8", errors="replace").split("\n"):
        if "live voice session" not in raw_line:
            continue
        try:
            e = json.loads(raw_line)
        except Exception:
            continue
        if normalize_provider(e.get("provider")) != GEMINI:
            continue
        reason = str(e.get("reason") or "")
        try:
            ts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if "opened" in reason:
            if opened is not None:
                closed.append((opened, min(ts, opened + _OPEN_SESSION_MAX_S)))
            opened = ts
        elif "closed" in reason and opened is not None:
            closed.append((opened, ts))
            opened = None
    state = {"key": None, "path": str(path), "ino": getattr(st, "st_ino", None), "size": st.st_size,
             "mtime": st.st_mtime, "offset": offset + end, "closed": closed, "opened": opened,
             "print": _fingerprint(path, offset + end)}
    with _LOCK:
        _CACHE.clear()
        _CACHE.update(state)
    return _windows_of(state)


def _fingerprint(path: Path, offset: int):
    """The first bytes of the file and the bytes just before `offset`: if
    either changed, what was read before is not what is there now."""
    try:
        with open(path, "rb") as f:
            head = f.read(min(256, offset))
            f.seek(max(0, offset - 64))
            tail = f.read(min(64, offset))
        return (head, tail)
    except OSError:
        return None


def _windows_of(state: dict) -> list:
    out = list(state.get("closed") or [])
    if state.get("opened") is not None:
        out.append((state["opened"], state["opened"] + _OPEN_SESSION_MAX_S))
    return out


def _in_gemini_window(ts: float, windows) -> bool:
    return any(a <= ts <= b + _WINDOW_SLACK_S for a, b in windows)


def sent_to(msg: dict, windows=None) -> set:
    """The cloud providers this stored message was sent to (explicit or inferred)."""
    meta = (msg or {}).get("meta") or {}
    if meta.get("local_only"):
        # A result kept on this PC (services/seat_handoff) is never counted
        # as received by a cloud provider, so no recall hands it to one.
        return set()
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
