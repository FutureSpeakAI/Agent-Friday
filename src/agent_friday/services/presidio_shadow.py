"""
Presidio shadow mode - observe first, enforce only on explicit opt-in.

Contract
--------
1. It CHANGES NOTHING. ``observe()`` returns None and has no effect on any tier
   decision. Enforcement is a separate, explicit opt-in
   (FRIDAY_PRESIDIO_ENFORCE=1) for which this module deliberately provides no
   call path.
2. It COSTS NOTHING in the hot path. Presidio analysis runs on a background
   daemon thread behind a bounded queue. Every egress decision pays one
   ``queue.put_nowait`` and nothing else; if the worker falls behind, samples
   are DROPPED (and counted), never queued unboundedly and never blocking.
3. Its OWN LOG IS NOT A LEAK. This is the subtle one. A naive shadow log writes
   "would have redacted: <the sensitive text>", creating a plaintext file of
   exactly the material the gate exists to protect. So the log records entity
   TYPE, offsets, score, and a salted hash prefix. Never the matched substring,
   never the source text.

Why shadow mode at all
----------------------
Presidio's PERSON / LOCATION / DATE_TIME recognisers fire on ordinary prose.
This codebase already carries three scars from over-broad classification:
'courtesy' matching 'court', 'Sovereign Vault' nuking Friday's own system
prompt, and 'family picture-book aesthetic' killing a storybook turn. Turning a
fresh NER layer straight on would be a fourth. A week of logs first.

Read the observations with:
    python -m agent_friday.services.presidio_shadow
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
from typing import Optional

log = logging.getLogger("friday.privacy.shadow")

_MAX_QUEUE = 256          # bounded: back-pressure drops, never blocks
_MAX_TEXT = 20_000        # skip absurdly large payloads
_SALT_ENV = "FRIDAY_SHADOW_SALT"

_worker: Optional[threading.Thread] = None
_q: queue.Queue = queue.Queue(maxsize=_MAX_QUEUE)
_lock = threading.Lock()
_stats = {"seen": 0, "dropped": 0, "analyzed": 0, "errors": 0, "would_escalate": 0}


def _privacy_dir() -> str:
    base = os.path.join(os.path.expanduser("~"), ".friday", "privacy")
    os.makedirs(base, exist_ok=True)
    return base


def _log_path() -> str:
    return os.path.join(_privacy_dir(), "presidio_shadow.jsonl")


def _salt() -> str:
    """Per-install salt so hashes are not comparable across machines."""
    env = os.environ.get(_SALT_ENV)
    if env:
        return env
    path = os.path.join(_privacy_dir(), ".shadow_salt")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                val = fh.read().strip()
            if val:
                return val
        val = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(val)
        return val
    except Exception:
        return "ephemeral"


def _fingerprint(text: str) -> str:
    """Salted, truncated hash. Enough to spot repeats, useless for recovery."""
    return hashlib.sha256((_salt() + text).encode("utf-8", "replace")).hexdigest()[:12]


def _worker_loop() -> None:
    from agent_friday.services.sensitivity_classifier import (
        _load_presidio, _presidio_tier,
    )
    while True:
        try:
            text, current_tier, context = _q.get()
        except Exception:
            continue
        try:
            analyzer = _load_presidio()
            if analyzer is None:
                continue
            t0 = time.perf_counter()
            results = analyzer.analyze(text=text, language="en")
            dur_ms = (time.perf_counter() - t0) * 1000.0
            with _lock:
                _stats["analyzed"] += 1
            # Entity summary ONLY - type/score/offsets, never the substring.
            ents = [
                {
                    "type": r.entity_type,
                    "score": round(float(r.score), 3),
                    "start": int(r.start),
                    "end": int(r.end),
                }
                for r in results
            ]
            would = int(_presidio_tier(text) or 0)
            escalates = would > int(current_tier or 0)
            if escalates:
                with _lock:
                    _stats["would_escalate"] += 1
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "context": context,
                "fp": _fingerprint(text),
                "len": len(text),
                "current_tier": int(current_tier or 0),
                "presidio_tier": would,
                "would_escalate": bool(escalates),
                "ms": round(dur_ms, 2),
                "entities": ents,
            }
            with open(_log_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=True) + "\n")
        except Exception as exc:            # never let observation break anything
            with _lock:
                _stats["errors"] += 1
            log.debug("presidio shadow error: %s", exc)


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(
            target=_worker_loop, name="presidio-shadow", daemon=True
        )
        _worker.start()


def observe(text: str, current_tier: int = 0, context: str = "") -> None:
    """Queue text for shadow analysis. Returns None, always. Never raises.

    This is the ONLY entry point, and it deliberately has no return value so
    that no caller can accidentally start depending on Presidio's opinion.
    """
    try:
        from agent_friday.services.privacy_layers import (
            shadow_enabled, probe_layers,
        )
        if not shadow_enabled():
            return
        if not text or not isinstance(text, str) or len(text) > _MAX_TEXT:
            return
        if not probe_layers()["presidio"]["active"]:
            return
        with _lock:
            _stats["seen"] += 1
        _ensure_worker()
        try:
            _q.put_nowait((text, current_tier, context))
        except queue.Full:
            with _lock:
                _stats["dropped"] += 1
    except Exception:
        return  # observation must never affect the caller


def stats() -> dict:
    with _lock:
        return dict(_stats)


def summarize(path: Optional[str] = None) -> dict:
    """Aggregate the shadow log - the thing to read after a week."""
    path = path or _log_path()
    records = 0
    would = 0
    by_entity: dict = {}
    by_tier: dict = {}
    durations: list = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                records += 1
                if rec.get("would_escalate"):
                    would += 1
                key = "%s->%s" % (rec.get("current_tier"), rec.get("presidio_tier"))
                by_tier[key] = by_tier.get(key, 0) + 1
                for ent in rec.get("entities", []):
                    name = ent.get("type", "?")
                    by_entity[name] = by_entity.get(name, 0) + 1
                if isinstance(rec.get("ms"), (int, float)):
                    durations.append(rec["ms"])
    except FileNotFoundError:
        pass
    return {
        "log": path,
        "records": records,
        "would_escalate": would,
        "escalation_rate": round(would / records, 4) if records else None,
        "by_entity": by_entity,
        "by_tier": by_tier,
        "avg_ms": round(sum(durations) / len(durations), 2) if durations else None,
    }


if __name__ == "__main__":
    print(json.dumps(summarize(), indent=2, sort_keys=True))
