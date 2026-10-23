"""The first backup, offered once setup is done (north star §34.4-9, BD-17).

A fresh install keeps everything in one data folder and nothing anywhere
else. Once setup is complete and no scheduled bundle is on, Friday offers a
backup: a quiet card in Needs you (emitter L08) that opens the scheduled
bundles section of the Doctor page. The owner answers "Not now" (asked again
after ASK_AGAIN_DAYS, at most MAX_ASKS times) or "Don't ask again". Turning
scheduled bundles on is the yes; the offer then stops by itself.

The offer never turns anything on, never picks a folder or a passphrase and
sends nothing. Its state file holds only when it was offered and what the
owner answered.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

ASK_AGAIN_DAYS = 7
MAX_ASKS = 3
ANSWERS = ("not_now", "never")

_LOCK = threading.Lock()


class FirstBackupError(ValueError):
    """Said plainly to the owner."""


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _path() -> Path:
    return _home() / "backups" / "first_backup.json"


def _state() -> dict:
    st = {"asked": 0, "last_asked": None, "answer": None, "answered_at": None}
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if isinstance(d, dict):
        if isinstance(d.get("asked"), int) and 0 <= d["asked"] <= MAX_ASKS:
            st["asked"] = d["asked"]
        for k in ("last_asked", "answered_at"):
            v = d.get(k)
            if isinstance(v, (int, float)) and 0 < v <= time.time() + 300:
                st[k] = float(v)
        if d.get("answer") in ANSWERS:
            st["answer"] = d["answer"]
    return st


def _save(st: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps({k: st.get(k) for k in ("asked", "last_asked", "answer", "answered_at")}),
                   encoding="utf-8")
    os.replace(tmp, p)


def setup_done() -> bool:
    return (_home() / ".setup_complete").exists()


def _scheduled() -> bool:
    try:
        from agent_friday.services import backup_schedule
        return bool(backup_schedule.state().get("enabled"))
    except Exception:
        return False


def status(now: Optional[float] = None) -> dict:
    """{due, why, setup_done, scheduled, asked, answer}. `due`: the offer
    would be shown now. `why` names what keeps it back otherwise."""
    now = time.time() if now is None else float(now)
    st = _state()
    done, sched = setup_done(), _scheduled()
    if not done:
        why = "setup_not_done"
    elif sched:
        why = "scheduled"
    elif st["answer"] == "never":
        why = "declined"
    elif st["asked"] >= MAX_ASKS:
        why = "asked_enough"
    elif st["last_asked"] and now - st["last_asked"] < ASK_AGAIN_DAYS * 86400:
        why = "asked_recently"
    else:
        why = None
    return {"due": why is None, "why": why, "setup_done": done, "scheduled": sched,
            "asked": st["asked"], "answer": st["answer"]}


def answer(choice: str) -> dict:
    """The owner's answer to the offer: "not_now" or "never"."""
    if choice not in ANSWERS:
        raise FirstBackupError("Choose: not now, or don't ask again.")
    with _LOCK:
        st = _state()
        st["answer"] = choice
        st["answered_at"] = time.time()
        if choice == "not_now":
            st["last_asked"] = time.time()
        _save(st)
    try:
        from agent_friday.services import action_ledger
        action_ledger.try_append("backup", {"event": "first_backup_" + choice})
    except Exception:
        pass
    return status()


def tick(now: Optional[float] = None) -> Optional[dict]:
    """Every quarter hour (the server's backup loop): raise the offer when it
    is due. Turns nothing on and sends nothing."""
    s = status(now)
    if not s["due"]:
        return None
    try:
        from agent_friday import notifications_engine as ne
        ne.push(title="Keep a backup of Friday?",
                body="Everything Friday knows is in one folder on this PC. A scheduled, encrypted "
                     "bundle in a folder you choose keeps it safe if this PC is lost or breaks.",
                priority="low", source="backup", kind="first_backup", tier="fyi", emitter="L08",
                dedupe_key="first-backup:%d" % (s["asked"] + 1),
                target={"type": "url", "url": "/doctor#schedule"})
    except Exception:
        return None
    with _LOCK:
        st = _state()
        st["asked"] = min(MAX_ASKS, st["asked"] + 1)
        st["last_asked"] = time.time() if now is None else float(now)
        if st["answer"] == "not_now":
            st["answer"] = None
        _save(st)
    return {"offered": st["asked"]}
