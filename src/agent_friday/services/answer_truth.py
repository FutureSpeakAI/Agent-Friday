"""Which model actually answered the owner's last chat message: one record.

The field report: the System panel said Cloud only while the local status
panel showed bonsai2:27b serving, and a Calendar chat was in fact answered by
the local model. The panel's "Now answering" row was a PREDICTION from the
routing settings, never a fact, so a chat kept local by something the
prediction does not model (a per-chat or workspace model choice, a seat
substitution, a fallback) read as cloud.

This module keeps the fact: the chat routes write the model that generated
the persisted reply (attribution.last_generation(), the same value the reply's
badge shows) and the Models card reads it back. No message text is stored:
model, provider, seat, workspace, conversation id, time, and the route's own
reason.

Pure apart from one small JSON file under the Friday runtime dir; every
failure is logged and swallowed, because a status record must never break a
chat turn.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("friday.answer_truth")

_LOCK = threading.Lock()
FILE_NAME = "last_answer.json"


def _path() -> Path:
    from agent_friday.core import runtime_dir
    return Path(runtime_dir()) / "state" / FILE_NAME


def record(model, *, seat=None, provider=None, workspace=None, conversation_id=None,
           why=None, surface="chat") -> None:
    """The reply the owner just got came from `model` on `seat`."""
    if not model:
        return
    row = {"model": str(model), "seat": str(seat or ""), "provider": str(provider or ""),
           "workspace": str(workspace or ""), "conversation_id": str(conversation_id or ""),
           "why": str(why or "")[:300], "surface": surface, "at": time.time()}
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        with _LOCK:
            tmp.write_text(json.dumps(row), encoding="utf-8")
            os.replace(tmp, p)
    except Exception as e:  # noqa: BLE001 - never break a turn over a status file
        log.warning("could not record the answering model: %s", e)


def read() -> dict | None:
    try:
        row = json.loads(_path().read_text(encoding="utf-8"))
        return row if isinstance(row, dict) and row.get("model") else None
    except (OSError, ValueError):
        return None


def is_local_seat(seat: str, provider: str = "") -> bool:
    s = (seat or "").lower()
    if s in ("local", "llama-cpp-local", "ollama-local"):
        return True
    if s in ("cloud", "openai", "anthropic", "openrouter"):
        return False
    return "local" in (provider or "").lower()


def reconcile(now: dict, last: dict | None, mode: str) -> dict:
    """One truth for the Models card.

    Returns {"answering": <row>, "next": <row or None>, "disagrees": bool,
    "note": str}. `answering` is the model that ACTUALLY answered the last
    message when one is on record, else the prediction marked as such; `next`
    is the prediction, shown only when it differs from what answered.
    """
    now = dict(now or {})
    if not last:
        now["basis"] = "predicted"
        return {"answering": now, "next": None, "disagrees": False,
                "note": "No reply on record yet; this is what your settings choose."}
    last_local = is_local_seat(last.get("seat"), last.get("provider"))
    answering = {"model": last.get("model"), "label": last.get("model"),
                 "where": "local" if last_local else "cloud",
                 "provider": last.get("provider") or ("this machine" if last_local else ""),
                 "at": last.get("at"), "workspace": last.get("workspace") or "",
                 "basis": "answered", "why": last.get("why") or ""}
    same_model = str(now.get("model") or "") == str(last.get("model") or "")
    same_place = (now.get("where") == answering["where"])
    disagrees = not (same_model and same_place)
    note = ""
    if disagrees:
        if mode == "cloud_only" and last_local:
            note = ("Your routing mode is Cloud only, but the last reply%s came from %s on this "
                    "machine%s. A model chosen for that chat or workspace keeps it local; change "
                    "it in that chat's model picker." % (
                        (" (in " + answering["workspace"] + ")") if answering["workspace"] else "",
                        answering["model"],
                        (": " + answering["why"]) if answering["why"] else ""))
        else:
            note = ("The last reply came from %s, not %s, the model your settings choose next%s."
                    % (answering["model"], now.get("model") or "the predicted model",
                       (": " + answering["why"]) if answering["why"] else ""))
    nxt = dict(now, basis="predicted") if disagrees else None
    return {"answering": answering, "next": nxt, "disagrees": disagrees, "note": note}
