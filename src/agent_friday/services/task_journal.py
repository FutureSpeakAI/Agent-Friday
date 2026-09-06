"""task_journal — the durable, append-only record of every background task.

Design: docs/design/active/task-visibility.md (rules TV1, TV2, TV8, TV12,
TV13). This module is phase 1 of that design: the record and its restart
reconciliation. Emission at loop checkpoints (phase 2), the query surface
(phase 3) and the tray (phase 4) build on it.

Layout under ``<friday home>/tasks/``::

    index.jsonl                 one line per task at creation and at each
                                status change: id, name, status, created, ended
    <task_id>/journal.jsonl     append-only events, monotonically sequenced;
                                the source of truth (TV1)
    <task_id>/state.json        the materialised snapshot, rewritten
                                atomically on every change (TV2)

Every line and file is written through ``credential_store.protect`` — the
vault key when a passphrase is set, otherwise Windows DPAPI, otherwise
plaintext with the store's one-time warning — because a journal holds
prompts, tool results and reasoning, which is at least as sensitive as
anything else the vault protects. The setting ``task_journal.encrypt_at_rest``
turns that off; it is on by default.

Journal writes never break work (TV12): a failed append marks the task
``unrecorded`` in memory and notifies once; it does not raise into the loop.

Retention is a user setting, never an invented threshold (TV13):
``task_journal.retention_days`` of 0 (the default) keeps everything;
``apply_retention()`` deletes only when the user set a positive number, and
only tasks that reached a terminal status before the cutoff.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_log = logging.getLogger("friday.task_journal")

_LOCK = threading.RLock()
_SEQ: Dict[str, int] = {}          # task_id -> last seq written this process
_UNRECORDED_NOTIFIED: set = set()  # task_ids already announced as unrecorded
_DELETED: set = set()              # user-deleted this process: late writes are dropped

# Tests may point the journal somewhere else without touching FRIDAY_HOME.
BASE_DIR_OVERRIDE: Optional[Path] = None

TERMINAL = ("complete", "completed", "completed_unverified", "done", "failed",
            "error", "timeout", "cancelled", "interrupted")


def is_terminal(status) -> bool:
    """The registry uses a few spellings of 'finished' (complete,
    completed_unverified, done). Anything starting with 'complete' counts."""
    s = str(status or "")
    return s in TERMINAL or s.startswith("complete")
_LINE_PREFIX = "enc:"   # a protected (base64) line; anything else is plain JSON


# ── locations ────────────────────────────────────────────────────────────────

def tasks_dir() -> Path:
    if BASE_DIR_OVERRIDE is not None:
        return Path(BASE_DIR_OVERRIDE)
    from agent_friday.paths import friday_home
    return friday_home() / "tasks"


def task_dir(task_id: str) -> Path:
    return tasks_dir() / str(task_id)


def _index_path() -> Path:
    return tasks_dir() / "index.jsonl"


# ── settings ─────────────────────────────────────────────────────────────────

def settings() -> Dict[str, Any]:
    """The task_journal settings block with defaults applied. Never raises."""
    try:
        from agent_friday.core import _load_settings, DEFAULT_SETTINGS
        base = dict(DEFAULT_SETTINGS.get("task_journal") or {})
        base.update((_load_settings() or {}).get("task_journal") or {})
        return base
    except Exception:
        return {"retention_days": 0, "capture_reasoning": True, "encrypt_at_rest": True}


def _encrypt_enabled() -> bool:
    return bool(settings().get("encrypt_at_rest", True))


# ── at-rest protection ───────────────────────────────────────────────────────

def _protect(data: bytes) -> bytes:
    if not _encrypt_enabled():
        return data
    try:
        from agent_friday.services import credential_store as cs
        blob, method = cs.protect(data)
        if method == "plaintext":
            return data
        return blob
    except Exception as e:
        # A protection failure must not lose the record; write plain and say so.
        _log.warning("task journal: at-rest protection unavailable (%s); writing plaintext", e)
        return data


def _unprotect(blob: bytes) -> bytes:
    try:
        from agent_friday.services import credential_store as cs
        if cs.looks_protected(blob):
            return cs.unprotect(blob)
    except Exception as e:
        _log.warning("task journal: could not unprotect a record (%s)", e)
        raise
    return blob


def _encode_line(obj: dict) -> str:
    raw = json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")
    prot = _protect(raw)
    if prot is raw or prot == raw:
        return raw.decode("utf-8")
    return _LINE_PREFIX + base64.b64encode(prot).decode("ascii")


def _decode_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    try:
        if line.startswith(_LINE_PREFIX):
            raw = _unprotect(base64.b64decode(line[len(_LINE_PREFIX):]))
            return json.loads(raw.decode("utf-8"))
        return json.loads(line)
    except Exception:
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ── the journal ──────────────────────────────────────────────────────────────

def _next_seq(task_id: str) -> int:
    if task_id not in _SEQ:
        last = 0
        for ev in read(task_id):
            last = max(last, int(ev.get("seq") or 0))
        _SEQ[task_id] = last
    _SEQ[task_id] += 1
    return _SEQ[task_id]


def append(task_id: str, kind: str, **fields) -> Optional[int]:
    """Append one event. Returns its seq, or None if the write failed (in
    which case the caller's task is marked unrecorded — see mark_unrecorded)."""
    with _LOCK:
        if task_id in _DELETED:
            return None
        try:
            seq = _next_seq(task_id)
            ev = {"seq": seq, "ts": time.time(), "task_id": task_id, "kind": kind}
            for k, v in fields.items():
                if v is not None:
                    ev[k] = v
            p = task_dir(task_id) / "journal.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(_encode_line(ev) + "\n")
            return seq
        except Exception as e:
            _SEQ.pop(task_id, None)
            _on_write_failure(task_id, f"append {kind}: {e}")
            return None


def read(task_id: str, since: int = 0, limit: Optional[int] = None) -> List[dict]:
    """Events with seq > since, oldest first."""
    p = task_dir(task_id) / "journal.jsonl"
    if not p.exists():
        return []
    out: List[dict] = []
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                ev = _decode_line(line)
                if ev is None or int(ev.get("seq") or 0) <= since:
                    continue
                out.append(ev)
                if limit and len(out) >= limit:
                    break
    except OSError:
        return out
    return out


# ── the state snapshot ───────────────────────────────────────────────────────

def write_state(task_id: str, state: Dict[str, Any]) -> bool:
    """Persist the materialised snapshot. Drops non-serialisable fields
    (callbacks) rather than failing. Returns False on failure."""
    clean = {}
    for k, v in (state or {}).items():
        if callable(v):
            continue
        try:
            json.dumps(v, default=str)
        except Exception:
            continue
        clean[k] = v
    clean["journal_seq"] = _SEQ.get(task_id, clean.get("journal_seq", 0))
    clean["state_written"] = time.time()
    with _LOCK:
        if task_id in _DELETED:
            return False
        try:
            raw = json.dumps(clean, default=str, ensure_ascii=False).encode("utf-8")
            _write_atomic(task_dir(task_id) / "state.json", _protect(raw))
            return True
        except Exception as e:
            _on_write_failure(task_id, f"state: {e}")
            return False


def read_state(task_id: str) -> Optional[Dict[str, Any]]:
    p = task_dir(task_id) / "state.json"
    if not p.exists():
        return None
    try:
        return json.loads(_unprotect(p.read_bytes()).decode("utf-8"))
    except Exception as e:
        _log.warning("task journal: unreadable state for %s (%s)", task_id, e)
        return None


# ── the index ────────────────────────────────────────────────────────────────

def index_put(task_id: str, name: str, status: str, created: Optional[float],
              ended: Optional[float] = None) -> None:
    with _LOCK:
        try:
            p = _index_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            row = {"task_id": task_id, "name": (name or "")[:200], "status": status,
                   "created": created, "ended": ended, "ts": time.time()}
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(_encode_line(row) + "\n")
        except Exception as e:
            _on_write_failure(task_id, f"index: {e}")


def index_read() -> Dict[str, dict]:
    """Latest index row per task_id (last write wins), keyed by task_id."""
    p = _index_path()
    out: Dict[str, dict] = {}
    if not p.exists():
        return out
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                row = _decode_line(line)
                if row and row.get("task_id"):
                    out[row["task_id"]] = row
    except OSError:
        pass
    # A task directory with no index row (index write failed) still counts.
    try:
        for d in tasks_dir().iterdir():
            if d.is_dir() and d.name not in out and (d / "state.json").exists():
                st = read_state(d.name) or {}
                out[d.name] = {"task_id": d.name, "name": st.get("name", ""),
                               "status": st.get("status", "unknown"),
                               "created": st.get("created"), "ended": st.get("ended")}
    except OSError:
        pass
    return out


# ── failure handling (TV12) ──────────────────────────────────────────────────

def _on_write_failure(task_id: str, detail: str) -> None:
    _log.error("task journal write failed for %s: %s", task_id, detail)
    try:
        from agent_friday.services import agent as _ag
        with _ag.TASKS_LOCK:
            t = _ag.TASKS.get(task_id)
            if t is not None:
                t["unrecorded"] = True
                t["unrecorded_detail"] = detail[:300]
    except Exception:
        pass
    if task_id in _UNRECORDED_NOTIFIED:
        return
    _UNRECORDED_NOTIFIED.add(task_id)
    try:
        import agent_friday.notifications_engine as ne
        ne.push(
            title="A task is running unrecorded",
            body=(f"Task {task_id[:8]}… could not write its journal ({detail[:120]}). "
                  "It keeps running, but its record from here on is incomplete. "
                  "Check free disk space and the vault passphrase."),
            priority="high", source="task-journal", kind="task_unrecorded",
            dedupe_key=f"task_unrecorded:{task_id}",
            target={"workspace": "system", "tab": "tasks"},
        )
    except Exception:
        pass


# ── deletion and retention (TV13) ────────────────────────────────────────────

def delete(task_id: str) -> bool:
    """Remove a task's journal directory and mark it deleted in the index.
    The user-visible delete; never automatic."""
    with _LOCK:
        d = task_dir(task_id)
        existed = d.exists()
        if existed:
            shutil.rmtree(d, ignore_errors=True)
        _SEQ.pop(task_id, None)
        _DELETED.add(task_id)      # a worker's late wrap-up write must not resurrect it
        if existed:
            index_put(task_id, "", "deleted", None, time.time())
        return existed


def apply_retention(now: Optional[float] = None) -> List[str]:
    """Delete journals of tasks that ended more than retention_days ago.
    A retention of 0 (the default) keeps everything. Returns deleted ids."""
    days = 0
    try:
        days = int(settings().get("retention_days") or 0)
    except Exception:
        days = 0
    if days <= 0:
        return []
    cutoff = (now or time.time()) - days * 86400
    gone: List[str] = []
    for tid, row in index_read().items():
        if row.get("status") == "deleted" or not is_terminal(row.get("status")):
            continue
        ended = row.get("ended")
        if ended and float(ended) < cutoff:
            if delete(tid):
                gone.append(tid)
    return gone


# ── boot reconciliation (TV8) ────────────────────────────────────────────────

def reconcile_on_boot(now: Optional[float] = None) -> Dict[str, Any]:
    """Mark every task the previous process left running/queued as
    interrupted, journal the halt, and return what was found so the caller
    can rebuild its cache and announce it. Never raises."""
    now = now or time.time()
    interrupted: List[dict] = []
    restored: List[dict] = []
    try:
        for tid, row in index_read().items():
            if row.get("status") == "deleted":
                continue
            st = read_state(tid)
            if not st:
                continue
            if st.get("status") in ("running", "queued"):
                events = read(tid)
                last_cp = next((e for e in reversed(events)
                                if e.get("kind") in ("checkpoint", "started", "created")), None)
                last_hb = next((e for e in reversed(events) if e.get("kind") == "heartbeat"), None)
                st["status"] = "interrupted"
                st["ended"] = st.get("ended") or (last_hb or last_cp or {}).get("ts") or st.get("state_written") or now
                st["result"] = st.get("result") or (
                    "[Interrupted] The process stopped before this task finished. "
                    "Its record is complete up to the last checkpoint; it was not resumed.")
                append(tid, "halt", cause="interrupted",
                       detail="process restarted while the task was running",
                       last_checkpoint=(last_cp or {}).get("summary") or (last_cp or {}).get("kind"),
                       last_seen=(last_hb or last_cp or {}).get("ts"),
                       resume_hint="Re-run the task from its prompt; nothing resumes automatically.")
                write_state(tid, st)
                index_put(tid, st.get("name", ""), "interrupted", st.get("created"), st["ended"])
                interrupted.append(st)
            else:
                restored.append(st)
    except Exception as e:
        _log.error("task journal reconciliation failed: %s", e)
    return {"interrupted": interrupted, "restored": restored}


def announce_interrupted(interrupted: Iterable[dict]) -> None:
    items = list(interrupted)
    if not items:
        return
    names = ", ".join((t.get("name") or t.get("task_id", "?"))[:40] for t in items[:5])
    more = f" and {len(items) - 5} more" if len(items) > 5 else ""
    try:
        import agent_friday.notifications_engine as ne
        ne.push(
            title=f"{len(items)} task{'s were' if len(items) != 1 else ' was'} interrupted by a restart",
            body=(f"{names}{more}. Each record is complete up to its last checkpoint. "
                  "Nothing was resumed: re-run any of them from the Task Tray."),
            priority="high", source="task-journal", kind="tasks_interrupted",
            dedupe_key=f"tasks_interrupted:{int(time.time())}",
            target={"workspace": "system", "tab": "tasks"},
        )
    except Exception:
        pass


def reset_for_tests() -> None:
    with _LOCK:
        _SEQ.clear()
        _UNRECORDED_NOTIFIED.clear()
        _DELETED.clear()
