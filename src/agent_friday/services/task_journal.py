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
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agent_friday.paths import contained, safe_name

_log = logging.getLogger("friday.task_journal")

_LOCK = threading.RLock()
_SEQ: Dict[str, int] = {}          # task_id -> last seq written this process
_UNRECORDED_NOTIFIED: set = set()  # task_ids already announced as unrecorded
_DELETED: set = set()              # user-deleted this process: late writes are dropped
#: Tasks started off the record: nothing about them is written, for their whole
#: life, even if off-record ends while they run (services/off_record).
_OFF_RECORD: set = set()

# Tests may point the journal somewhere else without touching FRIDAY_HOME.
BASE_DIR_OVERRIDE: Optional[Path] = None

TERMINAL = ("complete", "completed", "completed_unverified", "done", "failed",
            "error", "timeout", "cancelled", "interrupted", "superseded", "killed",
            # The worker is finished and what is left is the owner's decision on a card
            # (services/agent.settle_waiting_task): a restart must never run it again.
            "waiting_approval", "declined",
            # An approved action it waited on may or may not have happened (agent.settle_waiting_task):
            # finished, never done, and never run again by a restart.
            "outcome_unknown")


def is_terminal(status) -> bool:
    """The registry uses a few spellings of 'finished' (complete,
    completed_unverified, done). Anything starting with 'complete' counts, and
    so does a failure with a cause ("failed:watchdog-kill")."""
    s = str(status or "")
    return s in TERMINAL or s.startswith("complete") or s.startswith("failed:")
_LINE_PREFIX = "enc:"   # a protected (base64) line; anything else is plain JSON


# ── locations ────────────────────────────────────────────────────────────────

def tasks_dir() -> Path:
    if BASE_DIR_OVERRIDE is not None:
        return Path(BASE_DIR_OVERRIDE)
    from agent_friday.paths import friday_home
    return friday_home() / "tasks"


def task_dir(task_id: str) -> Path:
    """The task's journal directory. Raises ValueError for an id that is not
    a single plain name: a task id arrives in URLs, and `..` must never turn a
    delete of one task into a delete of the directory above it."""
    return contained(tasks_dir(), safe_name(task_id, what="task id"))


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

class _NotWritable(Exception):
    """Encryption at rest is on and nothing can encrypt this record right now, so it is not
    written to disk (never written in plain text instead)."""

    def __init__(self, msg="Friday's key store cannot encrypt this record, so it was not written"):
        super().__init__(msg)


_PROTECT_WARNED = False


def _protect(data: bytes) -> bytes:
    global _PROTECT_WARNED
    if not _encrypt_enabled():
        return data
    try:
        from agent_friday.services import credential_store as cs
        blob, method = cs.protect(data)
        _PROTECT_WARNED = False
        if method == "plaintext":
            return data
        return blob
    except Exception as e:
        # Encryption at rest is on and nothing can encrypt (Friday's key store cannot be used:
        # credential_store refuses rather than drop to another key). The record is NOT written,
        # and never in plain text: the caller treats it as a failed write (the task is marked
        # unrecorded, as for a full disk; the live task table still has it for this run). Said
        # once until a write succeeds again, not on every event.
        if not _PROTECT_WARNED:
            _PROTECT_WARNED = True
            _log.warning("task journal: at-rest protection unavailable (%s); records are not "
                         "written until Friday's key store can encrypt again", type(e).__name__)
        raise _NotWritable() from None


# Records that cannot be decrypted, by digest. A passphrase rotation can leave
# a journal permanently unreadable, and `_decode_line` correctly skips those
# rows -- but the tray polls /api/tasks every couple of seconds and each poll
# re-reads every record, so a fixed handful of broken rows was re-reported
# about ten times a second. That filled friday.log, and rotating it fails with
# PermissionError while a second process holds it, and logging raises the
# rotation failure -- printing a full traceback to stderr for every line it
# could not write. friday.log went dark and stderr grew 5.6 GB/hour.
#
# So: complain once per distinct broken record. The bound is the number of
# damaged rows, never the number of times anyone looks at them. A NEW failure
# is still reported, because that is a different digest.
_UNREADABLE: set[str] = set()
_UNREADABLE_LOCK = threading.Lock()
_UNREADABLE_CAP = 4096


def _forget_unreadable() -> None:
    """Drop the seen-set. For tests, and for a passphrase change that makes
    previously unreadable records readable again."""
    with _UNREADABLE_LOCK:
        _UNREADABLE.clear()


def _first_sighting(blob: bytes) -> bool:
    """True the first time this exact record fails to decrypt."""
    mark = hashlib.blake2b(blob, digest_size=16).hexdigest()
    with _UNREADABLE_LOCK:
        if mark in _UNREADABLE:
            return False
        if len(_UNREADABLE) >= _UNREADABLE_CAP:
            _UNREADABLE.clear()
        _UNREADABLE.add(mark)
        return True


def _unprotect(blob: bytes) -> bytes:
    try:
        from agent_friday.services import credential_store as cs
        if cs.looks_protected(blob):
            return cs.unprotect(blob)
    except Exception as e:
        if _first_sighting(blob):
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


# On Windows a file another thread is reading or replacing cannot be opened or
# replaced for a moment: a sharing violation, raised as PermissionError. It
# clears in milliseconds (a heartbeat rewrites state.json while a reader reads
# it), so it is retried, never taken for a missing or broken file. One that
# lasts is raised as before.
_SHARING_TRIES = 8
_SHARING_SLEEP_S = 0.02


def _shared(fn):
    for attempt in range(_SHARING_TRIES):
        try:
            return fn()
        except PermissionError:
            if attempt == _SHARING_TRIES - 1:
                raise
            time.sleep(_SHARING_SLEEP_S * (attempt + 1))


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    _shared(lambda: os.replace(tmp, path))


# ── the journal ──────────────────────────────────────────────────────────────

def _unwritten(task_id: str) -> bool:
    """True when this task must leave nothing on disk (it began off the record)."""
    if task_id in _OFF_RECORD:
        return True
    if task_id in _SEQ:
        return False                  # already recorded before off-record began
    try:
        from agent_friday.services import off_record
        if off_record.skip("task_journal"):
            _OFF_RECORD.add(task_id)
            return True
    except Exception:
        pass
    return False


def _next_seq(task_id: str) -> int:
    if task_id not in _SEQ:
        last = 0
        for ev in read(task_id):
            last = max(last, int(ev.get("seq") or 0))
        _SEQ[task_id] = last
    _SEQ[task_id] += 1
    return _SEQ[task_id]


def _append_line(path: Path, line: str) -> None:
    """Append one line. A process stopped mid-write can leave a last line without its newline;
    the reader drops that torn line, and a line appended straight onto it would be dropped with
    it, so a new line always starts on a fresh one."""
    with open(path, "a+b") as fh:
        lead = b""
        if fh.tell() > 0:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                lead = b"\n"
            fh.seek(0, os.SEEK_END)
        fh.write(lead + (line + "\n").encode("utf-8"))


def append(task_id: str, kind: str, **fields) -> Optional[int]:
    """Append one event. Returns its seq, or None if the write failed (in
    which case the caller's task is marked unrecorded — see mark_unrecorded)."""
    with _LOCK:
        if task_id in _DELETED:
            return None
        if _unwritten(task_id):
            _SEQ[f"off:{task_id}"] = _SEQ.get(f"off:{task_id}", 0) + 1
            return _SEQ[f"off:{task_id}"]
        try:
            seq = _next_seq(task_id)
            ev = {"seq": seq, "ts": time.time(), "task_id": task_id, "kind": kind}
            for k, v in fields.items():
                if v is not None:
                    ev[k] = v
            p = task_dir(task_id) / "journal.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            _append_line(p, _encode_line(ev))
            return seq
        except Exception as e:
            _SEQ.pop(task_id, None)
            _on_write_failure(task_id, f"append {kind}: {e}")
            return None


def read(task_id: str, since: int = 0, limit: Optional[int] = None, *,
         strict: bool = False, kind: Optional[str] = None) -> List[dict]:
    """Events with seq > since, oldest first. `kind` keeps only that kind of
    event. `strict` raises OSError/ValueError when the journal cannot be read
    or a line cannot be decoded (a check that relies on the record must not
    take an unreadable record for an empty one)."""
    try:
        p = task_dir(task_id) / "journal.jsonl"
    except ValueError:
        if strict:
            raise
        return []
    if not p.exists():
        return []
    out: List[dict] = []
    try:
        with open(p, encoding="utf-8", errors="strict" if strict else "replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                ev = _decode_line(line)
                if ev is None:
                    if strict:
                        raise ValueError("a journal line could not be decoded")
                    continue
                if int(ev.get("seq") or 0) <= since or (kind and ev.get("kind") != kind):
                    continue
                out.append(ev)
                if limit and len(out) >= limit:
                    break
    except (OSError, UnicodeDecodeError):
        if strict:
            raise
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
        if _unwritten(task_id):
            return True
        try:
            previous = read_state(task_id)
            if previous and previous.get("stop_requested"):
                clean["stop_requested"] = previous["stop_requested"]
            raw = json.dumps(clean, default=str, ensure_ascii=False).encode("utf-8")
            _write_atomic(task_dir(task_id) / "state.json", _protect(raw))
            return True
        except Exception as e:
            _on_write_failure(task_id, f"state: {e}")
            return False


def read_state(task_id: str) -> Optional[Dict[str, Any]]:
    try:
        p = task_dir(task_id) / "state.json"
    except ValueError:
        return None
    if not p.exists():
        return None
    try:
        return json.loads(_unprotect(_shared(p.read_bytes)).decode("utf-8"))
    except Exception as e:
        _log.warning("task journal: unreadable state for %s (%s)", task_id, e)
        return None


# ── side blobs ───────────────────────────────────────────────────────────────
# A named file in the task's own directory, written with the same atomicity
# and at-rest protection as state.json. Exists so a resume checkpoint
# (services/task_resume) does not have to reimplement either, and so deleting
# a task still takes everything with it: delete() rmtree's this directory.

def write_blob(task_id: str, name: str, obj: Any) -> bool:
    """Atomically write ``obj`` as JSON to ``<task dir>/<name>``. Never raises."""
    with _LOCK:
        if task_id in _DELETED:
            return False
        if _unwritten(task_id):
            return True
        try:
            raw = json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")
            _write_atomic(task_dir(task_id) / name, _protect(raw))
            return True
        except Exception as e:
            _on_write_failure(task_id, f"{name}: {e}")
            return False


def read_blob(task_id: str, name: str) -> Optional[Any]:
    try:
        p = task_dir(task_id) / name
    except ValueError:
        return None
    if not p.exists():
        return None
    try:
        return json.loads(_unprotect(_shared(p.read_bytes)).decode("utf-8"))
    except Exception as e:
        _log.warning("task journal: unreadable %s for %s (%s)", name, task_id, e)
        return None


def blob_exists(task_id: str, name: str) -> bool:
    """Is the file there, regardless of whether it can be READ?

    The distinction is not academic. ``read_blob`` returns None both when
    nothing was ever written and when at-rest protection cannot open what was
    — and on this machine the second case is real: rotating the vault
    passphrase left 20+ older ``state.json`` files failing with "GCM auth tag
    mismatch". A caller that cannot tell those apart reports "nothing was
    saved" about work that was saved and is now unreachable, which is the
    silent loss the journal exists to prevent.
    """
    try:
        return (task_dir(task_id) / name).exists()
    except Exception:
        return False


def delete_blob(task_id: str, name: str) -> bool:
    try:
        p = task_dir(task_id) / name
        if p.exists():
            p.unlink()
            return True
    except Exception as e:
        _log.warning("task journal: could not remove %s for %s (%s)", name, task_id, e)
    return False


# ── the index ────────────────────────────────────────────────────────────────

def index_put(task_id: str, name: str, status: str, created: Optional[float],
              ended: Optional[float] = None) -> None:
    with _LOCK:
        if _unwritten(task_id):
            return
        try:
            p = _index_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            row = {"task_id": task_id, "name": (name or "")[:200], "status": status,
                   "created": created, "ended": ended, "ts": time.time()}
            _append_line(p, _encode_line(row))
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
    try:
        d = task_dir(task_id)
    except ValueError:
        return False
    with _LOCK:
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


# ── how the previous process ended (BD-16, NS-11.3-8 / NS-32.11-1) ──────────────
# A clean quit or restart asks the server to get ready first (POST
# /api/server/prepare-for-exit, called by the tray before it stops the server:
# on Windows a terminated process runs no exit handler). That writes this marker.
# The next start reads it once: with it, the interrupted tasks were stopped by
# the owner closing (or restarting) Friday; without it, Friday stopped without
# being closed (a crash, a forced stop, a power loss).
EXIT_MARKER = "last_exit.json"
#: A marker older than the last sign of life of a task by more than this was
#: written by a process that kept running afterwards: it does not describe the end.
_MARKER_SLACK_S = 15.0


def mark_clean_exit(reason: str, planned: bool = False) -> bool:
    """Record that this process is about to be stopped on purpose. Never raises."""
    try:
        _write_atomic(tasks_dir() / EXIT_MARKER, json.dumps({
            "clean": True, "reason": str(reason or "quit")[:64], "planned": bool(planned),
            "ts": time.time(), "pid": os.getpid()}).encode("utf-8"))
        return True
    except Exception as e:
        _log.warning("could not record a clean exit: %s", type(e).__name__)
        return False


def _take_exit_marker() -> Optional[dict]:
    """The previous process's clean-exit marker, read once and removed."""
    p = tasks_dir() / EXIT_MARKER
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        m = None
    try:
        p.unlink()
    except Exception:
        pass
    return m if isinstance(m, dict) and m.get("clean") is True else None


def _exit_words(previous: dict) -> str:
    if previous.get("kind") == "closed":
        return ("Friday was restarted" if previous.get("planned") else "Friday was closed")
    return "Friday stopped without being closed (a crash, a forced stop or a power loss)"


# ── boot reconciliation (TV8) ────────────────────────────────────────────────

def reconcile_on_boot(now: Optional[float] = None) -> Dict[str, Any]:
    """Mark every task the previous process left unfinished (running, queued,
    waiting for a seat, paused: no worker survives a restart) as interrupted,
    journal the halt, and return what was found so the caller can rebuild its
    cache and announce it. Never raises."""
    now = now or time.time()
    interrupted: List[dict] = []
    restored: List[dict] = []
    marker = _take_exit_marker()
    previous: Dict[str, Any] = ({"kind": "closed", "reason": marker.get("reason"),
                                 "planned": bool(marker.get("planned")), "at": marker.get("ts")}
                                if marker else {"kind": "unexpected"})
    try:
        pending = []
        for tid, row in index_read().items():
            if row.get("status") == "deleted":
                continue
            st = read_state(tid)
            if not st:
                continue
            if not is_terminal(st.get("status")) and st.get("status") != "deleted":
                events = read(tid)
                last_cp = next((e for e in reversed(events)
                                if e.get("kind") in ("checkpoint", "started", "created")), None)
                last_hb = next((e for e in reversed(events) if e.get("kind") == "heartbeat"), None)
                seen = (last_hb or last_cp or {}).get("ts")
                if marker and seen and float(seen) > float(marker.get("ts") or 0) + _MARKER_SLACK_S:
                    # The task kept working after the marker was written: it did not describe
                    # how this process ended.
                    previous = {"kind": "unexpected"}
                pending.append((tid, st, last_cp, last_hb))
            else:
                if not is_terminal(row.get("status")) and is_terminal(st.get("status")):
                    # The record ended but its index row was never told.
                    index_put(tid, st.get("name", ""), st["status"], st.get("created"),
                              st.get("ended") or st.get("state_written") or now)
                restored.append(st)
        how = _exit_words(previous)
        for tid, st, last_cp, last_hb in pending:
            # NS-17.7-1: the record was recovered after Friday stopped; `exit` says how.
            append(tid, "state", state="recovered_after_crash", was=st.get("state"),
                   exit=previous.get("kind"))
            st["state"] = "recovered_after_crash"
            st["exit"] = previous.get("kind")
            st["status"] = "interrupted"
            st["ended"] = st.get("ended") or (last_hb or last_cp or {}).get("ts") or st.get("state_written") or now
            st["result"] = st.get("result") or (
                "[Interrupted] The process stopped before this task finished. "
                "Its record is complete up to the last checkpoint; it was not resumed.")
            append(tid, "halt", cause="interrupted",
                   detail="%s while the task was running" % how,
                   exit=previous.get("kind"),
                   last_checkpoint=(last_cp or {}).get("summary") or (last_cp or {}).get("kind"),
                   last_seen=(last_hb or last_cp or {}).get("ts"),
                   resume_hint="Re-run the task from its prompt; nothing resumes automatically.")
            write_state(tid, st)
            index_put(tid, st.get("name", ""), "interrupted", st.get("created"), st["ended"])
            interrupted.append(st)
    except Exception as e:
        _log.error("task journal reconciliation failed: %s", e)
    return {"interrupted": interrupted, "restored": restored, "previous_exit": previous}


def _incident(task_ids) -> str:
    """The same set of interrupted tasks is one incident; a new set is a new one."""
    import hashlib
    return hashlib.sha1(",".join(sorted(str(i) for i in task_ids)).encode("utf-8")).hexdigest()[:12]


def announce_interrupted(interrupted: Iterable[dict], previous_exit: Optional[dict] = None) -> None:
    items = list(interrupted)
    if not items:
        return
    names = ", ".join((t.get("name") or t.get("task_id", "?"))[:40] for t in items[:5])
    more = f" and {len(items) - 5} more" if len(items) > 5 else ""
    closed = (previous_exit or {}).get("kind") == "closed"
    n = f"{len(items)} task{'s were' if len(items) != 1 else ' was'}"
    if closed:
        title = (f"{n} stopped when Friday was restarted" if (previous_exit or {}).get("planned")
                 else f"{n} stopped when Friday was closed")
    else:
        title = f"{n} interrupted by a restart"
    why = "" if closed else (_exit_words(previous_exit or {}) + ". ")
    try:
        import agent_friday.notifications_engine as ne
        ne.push(
            title=title,
            body=(f"{why}{names}{more}. Each record is complete up to its last checkpoint. "
                  "Nothing was resumed: re-run any of them from their task cards in System."),
            priority="high", source="task-journal", kind="tasks_interrupted",
            emitter="E28", dedupe_key="tasks_interrupted:" + _incident([t.get("task_id") for t in items]),
            target={"workspace": "system", "tab": "tasks"},
        )
    except Exception:
        pass


def _redact_value(v: Any, fn) -> tuple:
    """`v` with every string passed through `fn(str) -> (str, changed)`: (new value, changed)."""
    if isinstance(v, str):
        return fn(v)
    if isinstance(v, list):
        res = [_redact_value(x, fn) for x in v]
        return [r[0] for r in res], any(r[1] for r in res)
    if isinstance(v, dict):
        res = {k: _redact_value(x, fn) for k, x in v.items()}
        return {k: r[0] for k, r in res.items()}, any(r[1] for r in res.values())
    return v, False


def redact(fn) -> Dict[str, Any]:
    """Take text out of every task record because the owner ordered something forgotten.

    `fn(text) -> (text, changed)` is applied to every string in every task's journal events and in
    its JSON files (state, ledger, resume checkpoint). A changed record is written back the way it
    was written (protected, atomically); sequence numbers and times are untouched. A record that
    cannot be read (a passphrase that no longer opens it) cannot be read by anything else either and
    is left as it is. Returns {"records": how many changed}."""
    out: Dict[str, Any] = {"records": 0}
    with _LOCK:
        root = tasks_dir()
        if not root.exists():
            return out
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            jp = d / "journal.jsonl"
            if jp.exists():
                try:
                    lines = jp.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                changed, new_lines = 0, []
                for raw in lines:
                    ev = _decode_line(raw)
                    if ev is None:
                        new_lines.append(raw)
                        if raw.strip().startswith(_LINE_PREFIX):
                            # a protected line no key opens: it may hold the text; say so
                            out["left_unchanged"] = out.get("left_unchanged", 0) + 1
                        continue
                    nev, c = _redact_value(ev, fn)
                    if c:
                        try:
                            new_lines.append(_encode_line(nev))
                        except _NotWritable:
                            # Nothing can encrypt right now. A line that was plain stays plain,
                            # with the text taken out; a protected one cannot be rewritten and
                            # is counted, never written back in plain text.
                            if raw.strip().startswith(_LINE_PREFIX):
                                new_lines.append(raw)
                                out["left_unchanged"] = out.get("left_unchanged", 0) + 1
                                continue
                            new_lines.append(json.dumps(nev, default=str, ensure_ascii=False))
                        changed += 1
                    else:
                        new_lines.append(raw)
                if changed:
                    _write_atomic(jp, ("\n".join(new_lines) + "\n").encode("utf-8"))
                    out["records"] += changed
            for f in sorted(d.glob("*.json")):
                stored = b""
                try:
                    stored = f.read_bytes()
                    obj = json.loads(_unprotect(stored).decode("utf-8"))
                except Exception:
                    if stored and stored.lstrip()[:1] not in (b"{", b"["):
                        # a protected record no key opens: it may hold the text; say so
                        out["left_unchanged"] = out.get("left_unchanged", 0) + 1
                    continue
                nobj, c = _redact_value(obj, fn)
                if c:
                    plain = json.dumps(nobj, default=str, ensure_ascii=False).encode("utf-8")
                    try:
                        _write_atomic(f, _protect(plain))
                    except _NotWritable:
                        if stored.lstrip()[:1] in (b"{", b"["):
                            _write_atomic(f, plain)          # it was plain: stays plain, text taken out
                        else:
                            out["left_unchanged"] = out.get("left_unchanged", 0) + 1
                            continue
                    out["records"] += 1
    return out


def reset_for_tests() -> None:
    with _LOCK:
        _SEQ.clear()
        _UNRECORDED_NOTIFIED.clear()
        _DELETED.clear()
        _STOP_REQUESTED.clear()


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 2 — required emission (TV3, TV4, TV7) and reasoning capture
# ═══════════════════════════════════════════════════════════════════════════
# The loops and decision points call these with no task id of their own; the
# worker pushes its task id onto a thread-local stack for the duration of the
# run, so a decision made three modules deep (the egress gate, the spend
# guard, the approval queue) lands in the right journal without plumbing.
# Outside a task (an interactive chat turn) every emitter is a no-op.

_CURRENT = threading.local()

_SUMMARY_CAP = 200
_TEXT_CAP = 4000
_ARGS_CAP = 1000


def push_task(task_id: str) -> None:
    stack = getattr(_CURRENT, "stack", None)
    if stack is None:
        stack = _CURRENT.stack = []
    stack.append(str(task_id))


def pop_task() -> None:
    stack = getattr(_CURRENT, "stack", None)
    if stack:
        stack.pop()


def current_task() -> Optional[str]:
    stack = getattr(_CURRENT, "stack", None)
    return stack[-1] if stack else None


def resolve_task_id(session_ctx=None, task_id=None) -> Optional[str]:
    """Explicit id, else the session context's, else the thread's current task."""
    if task_id:
        return str(task_id)
    tid = (session_ctx or {}).get("task_id") if isinstance(session_ctx, dict) else None
    return str(tid) if tid else current_task()


def capture_reasoning_enabled() -> bool:
    return bool(settings().get("capture_reasoning", True))


def _cap(text, n):
    if text is None:
        return None
    s = text if isinstance(text, str) else json.dumps(text, default=str, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def _emit(kind: str, session_ctx=None, task_id=None, **fields) -> Optional[int]:
    tid = resolve_task_id(session_ctx, task_id)
    if not tid:
        return None
    return append(tid, kind, **fields)


def checkpoint(iteration: int, phase: str, summary: str, *, session_ctx=None, task_id=None):
    """One per loop iteration, BEFORE the model call: the 'now:' a reader sees."""
    return _emit("checkpoint", session_ctx, task_id, iteration=iteration, phase=phase,
                 summary=_cap(summary, _SUMMARY_CAP))


def _to_ledger(fn_name: str, session_ctx, task_id, **kw) -> None:
    """Feed the task's compact ledger (§17.11: cost, routes, decisions,
    approvals, blockers). Never raises; a task with no ledger is skipped."""
    try:
        tid = resolve_task_id(session_ctx, task_id)
        if not tid:
            return
        from agent_friday.services import task_ledger as _tl
        getattr(_tl, fn_name)(tid, **kw)
    except Exception:
        pass


def model_call(*, model, provider, seat, tokens_in=None, tokens_out=None, cost_usd=None,
               duration_ms=None, iteration=None, stop_reason=None, session_ctx=None, task_id=None):
    _to_ledger("note_model_call", session_ctx, task_id, model=model, provider=provider, seat=seat,
               tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)
    return _emit("model_call", session_ctx, task_id, model=model, provider=provider, seat=seat,
                 tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd,
                 duration_ms=duration_ms, iteration=iteration, stop_reason=stop_reason)


def reasoning(*, text=None, thinking=None, iteration=None, model=None, session_ctx=None, task_id=None):
    """The model's own words between tool calls (and provider thinking when the
    provider returns it). Honours task_journal.capture_reasoning: when off,
    nothing of the prose is written anywhere in the journal."""
    if not capture_reasoning_enabled():
        return None
    if not (text or thinking):
        return None
    return _emit("reasoning", session_ctx, task_id, iteration=iteration, model=model,
                 text=_cap(text, _TEXT_CAP), thinking=_cap(thinking, _TEXT_CAP))


_NO_OUTCOME = object()


def tool_call(*, name, args=None, result=None, ok=None, duration_ms=None, session_ctx=None, task_id=None,
              outcome=_NO_OUTCOME, input_refs=None, output_refs=None, ended_at=None):
    """The tool_call event, and the call's step record (§30.9) beside it.

    `outcome` is the full result, used only for its keyed fingerprint (never
    stored; at most its first step_records.RESULT_FP_MAX_CHARS plus its
    length are read); without it the stored summary is fingerprinted.
    `input_refs` / `output_refs` are ids only (services/step_records)."""
    if ok is None:
        r = str(result or "")
        ok = not (r.startswith("[VAULT") or r.startswith("[Error") or r.lower().startswith("error"))
    # G10 (NS-29.4-2): the action id the gate gave this call, when its decision
    # is known on this thread (note_action); taken once, whatever happens next.
    action_id = _take_action(name)
    seq = _emit("tool_call", session_ctx, task_id, name=name, args=_cap(args, _ARGS_CAP),
                ok=bool(ok), duration_ms=duration_ms, result_summary=_cap(result, _SUMMARY_CAP * 2),
                action_id=action_id)
    if seq is not None:
        try:
            from agent_friday.services import step_records as _sr
            end = float(ended_at) if ended_at is not None else time.time()
            begin = end - float(duration_ms or 0) / 1000.0
            outs = list(output_refs or [])
            rref = _sr.result_ref(result if outcome is _NO_OUTCOME else outcome, _sr.fp_key())
            if rref:
                outs.insert(0, rref)
            step("tool_call", "done" if ok else "failed", name=name, input_refs=input_refs,
                 output_refs=outs, started_at=begin, ended_at=end,
                 session_ctx=session_ctx, task_id=task_id)
        except Exception:
            pass
    return seq


# ── step records (north star §30.9, NS-30.9-2) ───────────────────────────────
# Written to the same journal as everything else the task did, so off-record,
# encryption at rest, forgetting, retention and deletion all apply unchanged.
# A step record carries ids and fingerprints only (services/step_records).

def step(kind: str, state: str, *, name=None, input_refs=None, output_refs=None,
         started_at=None, ended_at=None, step_id=None, session_ctx=None, task_id=None) -> Optional[dict]:
    """Record one step. Returns the record (with its seq), or None when there
    is no task or the write failed. Off the record nothing is written (append)."""
    tid = resolve_task_id(session_ctx, task_id)
    if not tid:
        return None
    from agent_friday.services import step_records as _sr
    rec = _sr.record(kind, state, name=name, input_refs=input_refs, output_refs=output_refs,
                     started_at=started_at, ended_at=ended_at, step_id=step_id)
    seq = append(tid, "step", **rec)
    if seq is None:
        return None
    return dict(rec, seq=seq, task_id=tid)


STEPS_SERVED_MAX = 500


def steps(task_id: str, since: int = 0, limit: Optional[int] = None) -> List[dict]:
    """The task's step records, oldest first, in the §30.9 shape. A step
    recorded more than once (the same step_id) is shown as its latest record."""
    from agent_friday.services import step_records as _sr
    out: List[dict] = []
    where: Dict[str, int] = {}
    for ev in read(task_id, since=since, kind="step"):
        rec = _sr.public(ev)
        sid = rec.get("id")
        if sid and sid in where:
            out[where[sid]] = rec
            continue
        if sid:
            where[sid] = len(out)
        out.append(rec)
    if limit:
        out = out[-int(limit):]
    return out


def decision(point: str, chosen, *, reason=None, alternatives=None, session_ctx=None, task_id=None):
    """A choice Friday's own code made at a known line, with the reason it
    already held. Points: seat_select, ladder_fallback, retry, gate, approval,
    spend_cap, chain_advance, evaluate."""
    _to_ledger("note_decision", session_ctx, task_id, point=point, chosen=_cap(chosen, 200),
               reason=_cap(reason, 300))
    return _emit("decision", session_ctx, task_id, point=point, chosen=_cap(chosen, _SUMMARY_CAP * 2),
                 alternatives=[_cap(a, _SUMMARY_CAP) for a in (alternatives or [])][:8] or None,
                 reason=_cap(reason, _TEXT_CAP // 4))


def steer(message: str, source: str = "user", *, session_ctx=None, task_id=None):
    return _emit("steer", session_ctx, task_id, message=_cap(message, _TEXT_CAP // 4), source=source)


# ── linked events (NS-29.4-1 / NS-29.4-2, slice G10) ────────────────────────
# approval, verification, artifact, blocker, paused / resumed / stopped and
# receipt events. Each carries ids and fixed codes only, never text, a path
# or content. An event that relates to a gate decision names its action id
# (and the ledger receipt, where there is one), so the journal is linked to
# the action ledger (`ledger_links` checks that link). Every emitter here is
# fail-safe: it never raises into the work it records. Off the record nothing
# is written (`append`).

#: The receipt id of a ledger entry: its sequence number in the action ledger.
RECEIPT_PREFIX = "lgr_"
APPROVAL_OUTCOMES = {"approved": "approved", "auto_approved": "approved", "denied": "denied",
                     "blocked": "denied", "expired": "expired"}
VERIFICATION_RESULTS = ("passed", "failed", "unknown")
LIFECYCLE_KINDS = ("paused", "resumed", "stopped")
LINKED_KINDS = ("approval", "verification", "artifact", "blocker") + LIFECYCLE_KINDS + ("receipt",)

_ACTING = threading.local()
_CODE_CAP = 64


def _code(v) -> Optional[str]:
    """A fixed code (a category, a reason, a status word): short, and only
    letters, digits, '_', '-', ':', '+' and '.'. Anything else is not written."""
    s = str(v or "").strip()[:_CODE_CAP]
    if not s or not all((c.isascii() and c.isalnum()) or c in "_-:.+" for c in s):
        return None
    return s


def _linked_append(task_id, kind: str, **fields) -> Optional[int]:
    """append(), only for a task that already has a record: a linked event is
    never the thing that creates one (a goal check or a receipt naming a task
    id this machine never journaled leaves nothing)."""
    tid = str(task_id)
    try:
        d = task_dir(tid)
    except ValueError:
        return None
    if tid not in _SEQ and not ((d / "journal.jsonl").exists() or (d / "state.json").exists()):
        return None
    return append(tid, kind, **fields)


def _valid_action_id(aid) -> bool:
    try:
        from agent_friday.services.policy_version import valid_action_id
        return bool(valid_action_id(aid))
    except Exception:
        return False


def receipt_id(seq) -> Optional[str]:
    """The id of an action-ledger entry, from its sequence number."""
    try:
        n = int(seq)
    except (TypeError, ValueError):
        return None
    return f"{RECEIPT_PREFIX}{n}" if n > 0 else None


def note_action(name, action_id=None) -> None:
    """The action id the gate decided for the tool call running now on this
    thread (agent._execute_tool, after its governance hook), so the call's
    tool_call event names it. Each call replaces the last one's note."""
    _CURRENT.action = (str(name or ""), action_id if _valid_action_id(action_id) else None)


def _peek_action(name) -> Optional[str]:
    note = getattr(_CURRENT, "action", None)
    return note[1] if note and note[0] == str(name or "") else None


def _take_action(name) -> Optional[str]:
    aid = _peek_action(name)
    _CURRENT.action = None
    return aid


class acting_for:
    """While an executor carries out, outside the task's own worker, an action
    a task asked for (the approval executor running an approved card), the
    receipt it writes is linked to that task. Only `receipt` reads this."""

    def __init__(self, task_id):
        self.task_id = str(task_id) if task_id else None
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_ACTING, "task", None)
        _ACTING.task = self.task_id
        return self

    def __exit__(self, *exc):
        _ACTING.task = self._prev
        return False


def approval(record, phase: str) -> Optional[int]:
    """An approval card raised for a task (phase 'raised'), or decided
    (phase 'decided': approved, denied or expired). The task is the one the
    card's payload names, else the thread's current task. Ids only: the
    card's id, its kind code, who decided (owner or system) and, when the
    card was raised for a tool call whose gate decision is known on this
    thread, that decision's action id."""
    try:
        rec = record if isinstance(record, dict) else {}
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        tid = str(payload.get("task_id") or "") or current_task()
        if not tid or not rec.get("approval_id") or rec.get("off_record"):
            return None                 # a card raised off the record leaves no line either
        fields = {"approval_id": str(rec.get("approval_id"))[:80], "phase": _code(phase),
                  "card_kind": _code(rec.get("kind"))}
        if phase == "decided":
            fields["outcome"] = APPROVAL_OUTCOMES.get(str(rec.get("status") or ""))
            if not fields["outcome"]:
                return None
            by = str(rec.get("decided_by") or "")
            fields["by"] = ("system" if by.startswith(("system", "friday", "auto"))
                            or rec.get("decided_source") in ("system", "auto_policy", "law1_block")
                            else "grant" if by.startswith("grant:") else "owner")
        tool = payload.get("tool")
        if tool:
            fields["action_id"] = _peek_action(tool)
        return _linked_append(tid, "approval", **fields)
    except Exception:
        return None


def verification(task_id, check_id, result, *, basis=None) -> Optional[int]:
    """A check ran on a task's work: passed, failed or unknown (it could not
    run, or only the owner can judge it). `check_id` names the check."""
    try:
        if not task_id:
            return None
        res = result if result in VERIFICATION_RESULTS else "unknown"
        return _linked_append(task_id, "verification", check_id=str(check_id or "")[:120] or None,
                              result=res, basis=_code(basis))
    except Exception:
        return None


def artifact(task_id, artifact_id, *, tool=None, qa_ok=None, action_id=None) -> Optional[int]:
    """A file a task's creating tool made, by its artifact-manifest reference
    (never its path or content), with the tool's name, whether its check
    passed and the action id of the gate decision that allowed the call."""
    try:
        if not task_id or not artifact_id:
            return None
        return _linked_append(task_id, "artifact", artifact_id=str(artifact_id)[:80], tool=_code(tool),
                              qa_ok=(bool(qa_ok) if qa_ok is not None else None),
                              action_id=action_id if _valid_action_id(action_id) else None)
    except Exception:
        return None


def blocker(task_id, code, *, category=None) -> Optional[int]:
    """A typed blocker (services/goal_state) stopped the work this task did."""
    try:
        if not task_id:
            return None
        c = _code(code) or _code(category)
        if not c:
            return None
        return _linked_append(task_id, "blocker", code=c, category=_code(category))
    except Exception:
        return None


def lifecycle(task_id, kind: str, *, who: str, reason_code=None) -> Optional[int]:
    """paused / resumed / stopped, with who did it (owner or system) and a
    reason code."""
    try:
        if not task_id or kind not in LIFECYCLE_KINDS:
            return None
        return _linked_append(task_id, kind, who="owner" if who == "owner" else "system",
                              reason_code=_code(reason_code))
    except Exception:
        return None


def receipt(entry, *, approval_id=None, task_id=None) -> Optional[int]:
    """The outcome receipt the action ledger just wrote (`entry`, as
    action_ledger.append returned it), in the journal of the task it belongs
    to: the explicit task, else the thread's current task, else the task an
    executor acts for (acting_for). Action id, receipt id, the decision's
    receipt id and the outcome word."""
    try:
        e = entry if isinstance(entry, dict) else {}
        rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
        aid = rec.get("action_id")
        tid = task_id or current_task() or getattr(_ACTING, "task", None)
        if not tid or not _valid_action_id(aid):
            return None
        return _linked_append(tid, "receipt", action_id=aid, receipt_id=receipt_id(e.get("seq")),
                              decision_receipt_id=receipt_id(rec.get("decision_seq")),
                              outcome=_code(rec.get("outcome")), terminal=rec.get("terminal"),
                              approval_id=(str(approval_id or rec.get("approval") or "")[:80] or None))
    except Exception:
        return None


def ledger_links(task_id: str) -> Dict[str, Any]:
    """The journal -> ledger cross-check: every action id the task's journal
    names, and how many of them the action ledger holds a decision or an
    outcome for. Counts only: {checked, found, missing}. When the ledger
    cannot be read, found and missing are None and `unreadable` is set."""
    ids = set()
    for ev in read(task_id):
        aid = ev.get("action_id")
        if isinstance(aid, str) and aid:
            ids.add(aid)
    out: Dict[str, Any] = {"checked": len(ids), "found": 0, "missing": 0}
    if not ids:
        return out
    try:
        from agent_friday.services import action_ledger as _al
        seen = set()
        for e in _al.entries(["decision", "outcome"], newest_first=False, archive=True,
                             where=lambda e: (e.get("rec") or {}).get("action_id") in ids):
            seen.add((e.get("rec") or {}).get("action_id"))
        found = len(ids & seen)
    except Exception:
        return dict(out, found=None, missing=None, unreadable=True)
    out.update(found=found, missing=len(ids) - found)
    return out


# ── heartbeat (TV7) ──────────────────────────────────────────────────────────

HEARTBEAT_STATE_S = 10     # last_seen in state.json this often
HEARTBEAT_JOURNAL_S = 60   # and a journal row this often


class Heartbeat:
    """Daemon thread that proves a running task's thread is alive: writes
    `last_seen` into state.json every 10 s and a heartbeat event every 60 s
    until stop() is called. A stale last_seen renders as stalled, never as
    running."""

    def __init__(self, task_id: str):
        self.task_id = str(task_id)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{self.task_id[:8]}", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0):
        """Stop beating. Waits for a beat already in progress, so nothing is
        written for this task after stop() returns."""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _run(self):
        last_journal = 0.0
        while not self._stop.wait(HEARTBEAT_STATE_S):
            now = time.time()
            try:
                # Read, check and write under the journal lock: a status the
                # worker writes in between (complete, failed) must never be
                # replaced by this beat's older copy of the state.
                with _LOCK:
                    st = read_state(self.task_id)
                    if not st or is_terminal(st.get("status")):
                        return
                    st["last_seen"] = now
                    write_state(self.task_id, st)
                if now - last_journal >= HEARTBEAT_JOURNAL_S:
                    append(self.task_id, "heartbeat")
                    last_journal = now
            except Exception:
                continue


def last_seen(task_id: str) -> Optional[float]:
    st = read_state(task_id) or {}
    return st.get("last_seen") or st.get("state_written")


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 3 — reading the record: gaps, digest, sealing for other principals
# ═══════════════════════════════════════════════════════════════════════════

def read_with_gaps(task_id: str, since: int = 0, limit: Optional[int] = None) -> Dict[str, Any]:
    """Events with seq > since, plus every hole in the sequence.

    Sequence numbers exist so a reader can tell "I have everything" from
    "something is missing". A gap is reported, never papered over: between
    `since` and the first event returned, between consecutive events, and
    when `since` is beyond the newest event (a cursor ahead of the record —
    the journal was deleted or replaced under the reader)."""
    events = read(task_id, since=since, limit=limit)
    all_last = 0
    for ev in read(task_id):
        all_last = max(all_last, int(ev.get("seq") or 0))
    gaps: List[Dict[str, int]] = []
    expected = since + 1
    for ev in events:
        s = int(ev.get("seq") or 0)
        if s > expected:
            gaps.append({"missing_from": expected, "missing_to": s - 1})
        expected = s + 1
    cursor_ahead = since > all_last
    return {"events": events, "gaps": gaps, "cursor_ahead": cursor_ahead,
            "last_seq": (events[-1]["seq"] if events else since),
            "journal_last_seq": all_last, "complete": not gaps and not cursor_ahead}


_DIGEST_KINDS = ("checkpoint", "decision", "halt", "steer", "tool_call")


def digest(task_id: str, n: int = 20, include_reasoning: bool = False) -> Optional[Dict[str, Any]]:
    """A compact view sized for a prompt: status, cost, model/seat, the last
    n checkpoints and decisions, halts. Reasoning prose is NOT included
    unless explicitly asked for (the maintainer's ruling: an orchestrator
    gets decisions, status, model and cost by default; reasoning only on
    explicit request, and then only through the gate)."""
    st = read_state(task_id)
    events = read(task_id)
    if st is None and not events:
        return None
    st = st or {}
    model_calls = [e for e in events if e.get("kind") == "model_call"]
    cost = sum(float(e.get("cost_usd") or 0) for e in model_calls)
    last_model = model_calls[-1] if model_calls else {}
    decisions = [e for e in events if e.get("kind") == "decision"][-n:]
    checkpoints = [e for e in events if e.get("kind") == "checkpoint"][-n:]
    halts = [e for e in events if e.get("kind") == "halt"]
    tools = [e for e in events if e.get("kind") == "tool_call"]
    out: Dict[str, Any] = {
        "task_id": task_id,
        "name": st.get("name"),
        "status": st.get("status"),
        "created": st.get("created"), "started": st.get("started"), "ended": st.get("ended"),
        "last_seen": st.get("last_seen") or st.get("state_written"),
        "now": (checkpoints[-1].get("summary") if checkpoints else None),
        "iterations": len(model_calls),
        "model": last_model.get("model") or st.get("model"),
        "provider": last_model.get("provider"), "seat": last_model.get("seat"),
        "cost_usd": round(cost, 6),
        "tool_calls": len(tools),
        "decisions": [{"seq": d.get("seq"), "ts": d.get("ts"), "point": d.get("point"),
                       "chosen": d.get("chosen"), "reason": d.get("reason")} for d in decisions],
        "checkpoints": [{"seq": c.get("seq"), "ts": c.get("ts"), "iteration": c.get("iteration"),
                         "summary": c.get("summary")} for c in checkpoints],
        "halts": [{"seq": h.get("seq"), "cause": h.get("cause"), "detail": h.get("detail"),
                   "resume_hint": h.get("resume_hint")} for h in halts],
        "journal_last_seq": (events[-1]["seq"] if events else 0),
        "unrecorded": bool(st.get("unrecorded")),
    }
    if include_reasoning:
        out["reasoning"] = [{"seq": r.get("seq"), "iteration": r.get("iteration"),
                             "text": r.get("text"), "thinking": r.get("thinking")}
                            for r in events if r.get("kind") == "reasoning"][-n:]
    return out


# Fields that carry free text a task handled. Everything else in an event is
# metadata (ids, numbers, kinds) and travels as-is.
_SEALED_FIELDS = ("summary", "text", "thinking", "args", "result_summary", "reason", "chosen",
                  "alternatives", "prompt", "result", "message", "description", "name",
                  "detail", "last_checkpoint", "now",
                  # task rows served by /api/tasks and /api/tasks/<id> (these
                  # are on the observer allowlist, so they must be sealed)
                  "log", "label", "output", "error",
                  # Saved workflow contracts and output references also carry
                  # user content when included in task snapshots.
                  "inputs", "success_criteria", "title", "path", "content",
                  "result_tail", "log_tail", "note",
                  # a process's step list (routes/tasks.get_task fallback)
                  "steps")

WITHHELD = "[withheld by the privacy gate]"

# Fields an observer never receives at all: a step record's input and output
# refs (§30.9) are fingerprints of what the task handled, which only the
# owner's own /api/tasks/<id>/steps serves. An artifact event's reference
# (G10) is taken from the keyed signature of a manifest line that holds the
# file's hash, so it goes the same way. Action, receipt and approval ids stay:
# they are random or sequence ids, derived from nothing the task handled.
_OBSERVER_DROPPED_FIELDS = ("input_refs", "output_refs", "artifact_id")


def seal_for_principal(obj, principal: str, provider: str = "observer") -> tuple:
    """Run every free-text field of a journal payload through the egress gate
    before it leaves for a principal that is not the local user (TV11). The
    journal is the most sensitive text in the product; serving it to a
    cloud-backed agent is an egress, and is gated like any tool result.
    Returns (sealed_copy, redacted_count, withheld_count). Fails CLOSED: if
    the gate cannot be reached, every text field is withheld. Step refs
    (_OBSERVER_DROPPED_FIELDS) are left out of the copy altogether."""
    if principal == "user":
        return obj, 0, 0
    counts = {"redacted": 0, "withheld": 0}
    try:
        from agent_friday.services import egress_gate as _eg
        gate = _eg._gate_text
        blocked = _eg.NeverSendBlocked
    except Exception:
        gate = None
        blocked = Exception

    def _seal_text(val, field):
        if gate is None:
            counts["withheld"] += 1
            return WITHHELD
        try:
            out = gate(val, provider, f"task_journal.{field}")
        except blocked:
            counts["withheld"] += 1
            return WITHHELD
        except Exception:
            counts["withheld"] += 1
            return WITHHELD
        if out != val:
            counts["redacted"] += 1
        return out

    def _walk(node, field=None):
        if isinstance(node, dict):
            return {k: (_walk(v, k)) for k, v in node.items() if k not in _OBSERVER_DROPPED_FIELDS}
        if isinstance(node, list):
            return [_walk(v, field) for v in node]
        if isinstance(node, str) and field in _SEALED_FIELDS and node:
            return _seal_text(node, field)
        return node

    return _walk(obj), counts["redacted"], counts["withheld"]


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 4 — interruption that keeps the record (TV10)
# ═══════════════════════════════════════════════════════════════════════════
# "Stop after this step" is a flag the loops check at every checkpoint. Unlike
# a hard cancel it ends the task with a complete record: the current step
# finishes, the next one never starts, and the halt names the step.

_STOP_REQUESTED: set = set()
STALLED_AFTER_S = 30.0   # a running task whose heartbeat is older than this renders as stalled


def request_stop(task_id: str) -> None:
    with _LOCK:
        _STOP_REQUESTED.add(str(task_id))
    with _LOCK:
        st = read_state(task_id)
        if st is not None and not is_terminal(st.get("status")):
            # A finished task is not rewritten (it could be put back to running by a stale read).
            st["stop_requested"] = time.time()
            if st.get("state") != "cancel_requested":
                # NS-17.7-1: asked to stop, it stops at its next step.
                append(task_id, "state", state="cancel_requested", was=st.get("state"))
                st["state"] = "cancel_requested"
            write_state(task_id, st)


def stop_requested(task_id: Optional[str]) -> bool:
    if not task_id:
        return False
    try:  # Stop everything (services/global_stop): every task sees its stop.
        import agent_friday.core as _core
        if _core.GLOBAL_STOP.is_set():
            return True
    except Exception:
        pass
    if str(task_id) in _STOP_REQUESTED:
        return True
    state = read_state(str(task_id))
    return bool(state and state.get("stop_requested"))


def consume_stop(task_id: Optional[str]) -> bool:
    """True once if a stop was requested for this task; clears it."""
    if not task_id:
        return False
    with _LOCK:
        if str(task_id) in _STOP_REQUESTED:
            _STOP_REQUESTED.discard(str(task_id))
            return True
    return False


def liveness(task_id: str, status: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """last_seen / stalled / cost / now for a task row, from the record."""
    now = now or time.time()
    st = read_state(task_id) or {}
    seen = st.get("last_seen") or st.get("state_written")
    running = (status or st.get("status")) in ("running", "queued")
    stalled = bool(running and seen and (now - float(seen)) > STALLED_AFTER_S)
    cost = 0.0
    now_line = None
    for ev in read(task_id):
        k = ev.get("kind")
        if k == "model_call":
            cost += float(ev.get("cost_usd") or 0)
        elif k == "checkpoint":
            now_line = ev.get("summary") or now_line
    return {"last_seen": seen, "stalled": stalled, "cost_usd": round(cost, 6), "now": now_line,
            "stop_requested": bool(st.get("stop_requested")) or stop_requested(task_id)}
