"""Local transcripts for every audio and video card, so "find the video where
I said X" works.

faster-whisper base.en on the CPU (int8), loaded from the local cache only,
one file at a time, after the preview pass, waiting while the machine is short
of memory. The transcript (text and timed segments) is cached under the home
and folded into the Media index's full-text search; the hit carries the time
the words were said. Nothing is sent anywhere: no network, no telemetry.
Under tests (FRIDAY_TESTING) nothing starts by itself; ``ensure_all(sync=True)``
runs the queue inline with whatever ``transcriber`` the test gives.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

import agent_friday.core as core

VERSION = "v1"
RAM_FLOOR_MIB = 3072            #: the recogniser and its working set want room
PAUSE_S = 0.5
RAM_WAIT_S = 15.0
MAX_SECONDS = 3 * 3600          #: longer than this is skipped, with a note
AV_KINDS = ("audio", "music", "episode", "video")

_LOCK = threading.RLock()
_QUEUE: Deque[Dict[str, Any]] = deque()
_QUEUED: set = set()
_WORKER: Optional[threading.Thread] = None
_MODEL = None
_STATE: Dict[str, Any] = {"done": 0, "failed": 0, "skipped": 0, "building": None, "last": None}
#: Tests hand in a transcriber: ``f(path) -> {"text": str, "segments": [{"start", "end", "text"}]}``.
transcriber: Optional[Callable[[Path], Dict[str, Any]]] = None


def cache_dir() -> Path:
    return Path(core.FRIDAY_DIR) / "cache" / "media_transcripts"


def _key(card: Dict[str, Any], p: Optional[Path]) -> str:
    try:
        st = p.stat() if p else None
        stamp = f"{st.st_mtime_ns}|{st.st_size}" if st else "none"
    except OSError:
        stamp = "gone"
    return hashlib.sha1(f"{card.get('id')}|{p}|{stamp}|{VERSION}".encode("utf-8", "ignore")).hexdigest()


def _path_for(card: Dict[str, Any]) -> Optional[Path]:
    from agent_friday.services import media_previews as mp
    p = mp.media_path(card)
    if p is None or p.suffix.lower() not in (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".webm", ".mov", ".mkv"):
        return None
    return p


def _json_path(card: Dict[str, Any]) -> Optional[Path]:
    p = _path_for(card)
    if p is None:
        return None
    k = _key(card, p)
    return cache_dir() / k[:2] / (k + ".json")


def ready(card: Dict[str, Any]) -> bool:
    jp = _json_path(card)
    return jp is None or jp.exists()


def get(card: Dict[str, Any]) -> Dict[str, Any]:
    """{"text", "segments", "engine"} or {} when there is none yet."""
    jp = _json_path(card)
    if jp is None or not jp.exists():
        return {}
    try:
        return json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def status() -> Dict[str, Any]:
    with _LOCK:
        return {"pending": len(_QUEUE), "done": _STATE["done"], "failed": _STATE["failed"], "skipped": _STATE["skipped"], "building": _STATE["building"]}


def available() -> bool:
    if transcriber is not None:
        return True
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


# ── the queue ────────────────────────────────────────────────────────────────

def enqueue(cards: List[Dict[str, Any]]) -> int:
    n = 0
    with _LOCK:
        for c in cards:
            cid = c.get("id")
            if not cid or cid in _QUEUED or c.get("kind") not in AV_KINDS or ready(c):
                continue
            _QUEUED.add(cid)
            _QUEUE.append(c)
            n += 1
    if n and not os.environ.get("FRIDAY_TESTING"):
        _start_worker()
    return n


def _start_worker() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker, name="media-transcripts", daemon=True)
        _WORKER.start()


def _ram_mib() -> Optional[int]:
    try:
        from agent_friday.services.machine_monitor import _ram_available_mib
        return _ram_available_mib()
    except Exception:
        return None


def _room() -> bool:
    r = _ram_mib()
    return r is None or r >= RAM_FLOOR_MIB


def _worker() -> None:
    while True:
        with _LOCK:
            c = _QUEUE.popleft() if _QUEUE else None
        if c is None:
            return
        while not _room():
            time.sleep(RAM_WAIT_S)
        _run_one(c)
        time.sleep(PAUSE_S)


def _run_one(c: Dict[str, Any]) -> None:
    with _LOCK:
        _STATE["building"] = c.get("id")
    try:
        r = transcribe(c)
        with _LOCK:
            _STATE["done" if r.get("text") else "skipped"] += 1
    except Exception as e:
        with _LOCK:
            _STATE["failed"] += 1
            _STATE["last"] = f"{c.get('id')}: {str(e)[:160]}"
    finally:
        with _LOCK:
            _QUEUED.discard(c.get("id"))
            _STATE["building"] = None


def ensure_all(cards: Optional[List[Dict[str, Any]]] = None, *, sync: Optional[bool] = None, timeout: float = 600.0) -> Dict[str, Any]:
    if cards is None:
        from agent_friday.services import media_index as mi
        cards = mi.query(view="all", limit=100000)["cards"]
    if sync is None:
        sync = bool(os.environ.get("FRIDAY_TESTING"))
    enqueue(cards)
    if sync:
        while True:
            with _LOCK:
                c = _QUEUE.popleft() if _QUEUE else None
            if c is None:
                break
            _run_one(c)
    else:
        end = time.time() + timeout
        while time.time() < end and (_QUEUE or _STATE["building"]):
            time.sleep(0.1)
    return status()


# ── one transcript ───────────────────────────────────────────────────────────

def _model():
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            from faster_whisper import WhisperModel
            _MODEL = WhisperModel("base.en", device="cpu", compute_type="int8", local_files_only=True)
        return _MODEL


def _run_whisper(p: Path) -> Dict[str, Any]:
    model = _model()
    segs, info = model.transcribe(str(p), beam_size=1, vad_filter=True)
    out: List[Dict[str, Any]] = []
    for s in segs:
        t = (s.text or "").strip()
        if t:
            out.append({"start": round(float(s.start), 2), "end": round(float(s.end), 2), "text": t})
    return {"text": " ".join(x["text"] for x in out), "segments": out, "engine": "faster-whisper base.en, cpu int8",
            "language": getattr(info, "language", "en"), "duration_s": round(float(getattr(info, "duration", 0) or 0), 2)}


def transcribe(card: Dict[str, Any]) -> Dict[str, Any]:
    """Transcribe one card's file, cache it, fold it into the index. Returns the record."""
    p = _path_for(card)
    jp = _json_path(card)
    if p is None or jp is None:
        return {}
    if jp.exists():
        return get(card)
    dur = float(((card.get("details") or {}).get("duration_s") or (card.get("extra") or {}).get("duration_s") or 0))
    rec: Dict[str, Any]
    if dur and dur > MAX_SECONDS:
        rec = {"text": "", "segments": [], "engine": "skipped", "note": "longer than the local recogniser is asked to do"}
    else:
        fn = transcriber or _run_whisper
        rec = fn(p) or {"text": "", "segments": []}
        rec.setdefault("engine", "local")
    rec.update({"id": card.get("id"), "version": VERSION, "built": time.time()})
    jp.parent.mkdir(parents=True, exist_ok=True)
    tmp = jp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, jp)
    if rec.get("text"):
        try:
            from agent_friday.services import media_index as mi
            mi.enrich_text(card["id"], transcript=rec["text"])
        except Exception:
            pass
    return rec


def hit_time(card: Dict[str, Any], phrase: str) -> Optional[float]:
    """When the words were said: the start of the first segment holding the phrase."""
    rec = get(card)
    ql = (phrase or "").lower().strip()
    if not rec or not ql:
        return None
    words = [w for w in ql.split() if w]
    for s in rec.get("segments") or []:
        t = (s.get("text") or "").lower()
        if ql in t or (words and all(w in t for w in words)):
            return float(s.get("start") or 0)
    return None
