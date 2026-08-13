"""B2 — seat-change visibility (Incident 2; seats-and-transparency).

2026-08-13 10:16:59: model routing flipped to local_only (settings.json
mtime) with zero UI confirmation, silently seating gemma4:latest for an
entire chat while the UI still displayed the cloud orchestrator. Verified
defects F1-F3 all happened on that silently-seated model.

Contract: ANY change to the seat-relevant settings keys — whether written by
the UI, the CLI's raw _save_config, or a direct file edit — produces (a) a
persisted system line in the active chat and (b) a high-priority
notification naming old → new. Detection rides the settings 2s TTL
poll-on-read (core._SETTINGS_CACHE), the same mechanism the FR-1 runtime
seat gate documents as the un-bypassable layer, so there is no writer that
can flip a seat without this firing on the next observed turn.

observe_seats() is called from the chat turn path, the history rehydrate,
and the model catalog route — cheap (one small JSON state file) and
idempotent per actual change.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime

from agent_friday import core

_STATE_FILE = core.FRIDAY_DIR / "seat_state.json"
_LOCK = threading.Lock()

# Human-readable labels for what each watched key means (A3 taxonomy).
_WATCHED = {
    "orchestrator_model": "orchestrator seat (cloud)",
    "subagent_model": "subagent seat",
    "model_routing.mode": "routing mode",
    "model_routing.local_model": "local seat",
}

_MODE_MEANING = {
    "cloud_only": "all turns go to the cloud orchestrator",
    "local_only": "ALL turns go to the local seat — the cloud orchestrator "
                  "is not consulted",
    "auto": "the router picks local or cloud per turn",
    "local_first": "local seat preferred; cloud on fallback",
}


def effective_seat(settings) -> tuple:
    """The (model, seat_class) actually answering conversational turns under
    the current routing settings — the truth the 10:16:59 flip hid: mode
    local_only seats the local model regardless of orchestrator_model."""
    routing = settings.get("model_routing") or {}
    mode = routing.get("mode") or "cloud_only"
    if mode == "local_only":
        return (routing.get("local_model") or "(no local model)", "local")
    return (settings.get("orchestrator_model")
            or routing.get("default_cloud_model") or "", "cloud")


def _snapshot(settings) -> dict:
    routing = settings.get("model_routing") or {}
    return {
        "orchestrator_model": settings.get("orchestrator_model"),
        "subagent_model": settings.get("subagent_model"),
        "model_routing.mode": routing.get("mode"),
        "model_routing.local_model": routing.get("local_model"),
    }


def _read_state():
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_state(snap: dict):
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps({"seats": snap, "ts": time.time()}),
                               encoding="utf-8")
    except Exception:
        pass


def _describe(key: str, old, new) -> str:
    label = _WATCHED.get(key, key)
    line = f"Seat change: {label} {old or '(unset)'} → {new or '(unset)'}."
    if key == "model_routing.mode" and new in _MODE_MEANING:
        line += f" In {new} mode, {_MODE_MEANING[new]}."
    return line


def observe_seats(settings) -> list:
    """Compare the current seat-relevant settings against the last observed
    snapshot. On ANY difference: persist a system line into chat history,
    push a notification, and return the change events. First observation
    seeds silently. Returns [] when nothing changed."""
    snap = _snapshot(settings)
    with _LOCK:
        state = _read_state()
        if state is None or "seats" not in state:
            _write_state(snap)
            return []
        prev = state["seats"]
        changed = [k for k in snap if snap.get(k) != prev.get(k)]
        if not changed:
            return []
        _write_state(snap)

    events = []
    for key in changed:
        events.append({
            "key": key,
            "label": _WATCHED.get(key, key),
            "old": prev.get(key),
            "new": snap.get(key),
            "text": _describe(key, prev.get(key), snap.get(key)),
            "ts": time.time(),
        })

    summary = " ".join(e["text"] for e in events)

    # (a) Persisted system line in the active chat — survives reload.
    try:
        sys_msg = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "role": "system",
            "kind": "seat_change",
            "text": f"⚙ {summary}",
            "pinned": False,
        }
        core.CHAT_HISTORY.append(sys_msg)
        core._save_chat_history(core.CHAT_HISTORY)
    except Exception:
        pass

    # (b) High-priority notification naming old → new.
    try:
        from agent_friday.notifications_engine import push
        push(title="Model seat changed",
             body=summary,
             priority="high", source="seat_transparency",
             kind="seat_change",
             dedupe_key=f"seat_change:{json.dumps(snap, sort_keys=True)}")
    except Exception:
        pass

    return events
