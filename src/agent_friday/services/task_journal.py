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


def model_call(*, model, provider, seat, tokens_in=None, tokens_out=None, cost_usd=None,
               duration_ms=None, iteration=None, stop_reason=None, session_ctx=None, task_id=None):
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


def tool_call(*, name, args=None, result=None, ok=None, duration_ms=None, session_ctx=None, task_id=None):
    if ok is None:
        r = str(result or "")
        ok = not (r.startswith("[VAULT") or r.startswith("[Error") or r.lower().startswith("error"))
    return _emit("tool_call", session_ctx, task_id, name=name, args=_cap(args, _ARGS_CAP),
                 ok=bool(ok), duration_ms=duration_ms, result_summary=_cap(result, _SUMMARY_CAP * 2))


def decision(point: str, chosen, *, reason=None, alternatives=None, session_ctx=None, task_id=None):
    """A choice Friday's own code made at a known line, with the reason it
    already held. Points: seat_select, ladder_fallback, retry, gate, approval,
    spend_cap, chain_advance, evaluate."""
    return _emit("decision", session_ctx, task_id, point=point, chosen=_cap(chosen, _SUMMARY_CAP * 2),
                 alternatives=[_cap(a, _SUMMARY_CAP) for a in (alternatives or [])][:8] or None,
                 reason=_cap(reason, _TEXT_CAP // 4))


def steer(message: str, source: str = "user", *, session_ctx=None, task_id=None):
    return _emit("steer", session_ctx, task_id, message=_cap(message, _TEXT_CAP // 4), source=source)


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

    def stop(self):
        self._stop.set()

    def _run(self):
        last_journal = 0.0
        while not self._stop.wait(HEARTBEAT_STATE_S):
            now = time.time()
            try:
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
                  # task rows served by /api/tasks and /api/tasks/<id> (2026-09-06
                  # audit: these were on the observer allowlist but unsealed)
                  "log", "label", "output", "error")

WITHHELD = "[withheld by the privacy gate]"


def seal_for_principal(obj, principal: str, provider: str = "observer") -> tuple:
    """Run every free-text field of a journal payload through the egress gate
    before it leaves for a principal that is not the local user (TV11). The
    journal is the most sensitive text in the product; serving it to a
    cloud-backed agent is an egress, and is gated like any tool result.
    Returns (sealed_copy, redacted_count, withheld_count). Fails CLOSED: if
    the gate cannot be reached, every text field is withheld."""
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
            return {k: (_walk(v, k)) for k, v in node.items()}
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
    st = read_state(task_id)
    if st is not None:
        st["stop_requested"] = time.time()
        write_state(task_id, st)


def stop_requested(task_id: Optional[str]) -> bool:
    return bool(task_id) and str(task_id) in _STOP_REQUESTED


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
