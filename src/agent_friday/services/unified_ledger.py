"""Everything, together: every receipt family Friday keeps, in one list
(north star NS-37.7-1, slice A12).

Friday keeps its records in a few stores. Most receipt families are already
kinds of entry in the one action ledger (services/action_ledger.py); the rest
keep stores of their own. This module reads them all through one registry of
sources and gives back one list, newest first, in one row shape:

  family    what kind of record it is (FAMILIES)
  kind      the record's own kind code
  at, ts    when (unix seconds, and the same as UTC text)
  ids       the ids the record names: action_id, receipt_id, goal_id,
            task_id, approval_id, call_id, artifact_id ... Ids only: a value
            that is not an id-shaped token (letters, digits, _ . : + -) is
            never put here, so a path, an address or a sentence cannot be.
  signed    the record carries a signature (an HMAC or Ed25519 field). Whether
            it holds is the integrity view's to say (A8/A10, /api/ledger/
            integrity); this list reads, it does not check.
  chained   the record carries a link to the record before it
  where     the store, in a few fixed words
  summary   fixed short words chosen from tables here (FAMILY_LABELS and the
            word lists): never a record's content, path or recipient
  source    the registered source that read it, and `ref`, its stable
            reference within that source (used by the paging cursor)

The sources (SOURCES):

  action_ledger      decisions (the decision BOM), outcomes, organize receipts
                     (action_journal), goal receipts, tool calls and reasoning
                     trace pointers (activity rows), website trust, inbox
                     cleanup, pre-approved sends, outcome-memory deletion
                     receipts, approved change runs, dissent and the ledger's
                     own entries. Signed and chained.
  task_journal       linked task events (G10): approvals, checks, artifacts,
                     blockers, pauses, receipts and tool calls, by ids only.
                     Encrypted at rest; neither signed nor chained.
  egress             the egress ledger's call rows (sends leaving this PC):
                     channel, result and ids, never the destination.
  artifact_manifest  files Friday made (signed lines), by reference only.
  provenance         the content-credential ledger (chained; the manifests it
                     points at are signed in their sidecars).
  publish_log        post publish attempts (a rotating log).
  setup_record       first-run choices (chained), never the values.
  memory_ledger      memory writes (chained), never the key or the hash.
  skill_ledgers      skill changes (signed and chained), never the name.

Bounded and fail-safe. Each source reads at most PER_SOURCE_MAX rows (newest
first) within SOURCE_BUDGET_S (the action ledger is asked to stop line by
line; a task journal is read from its last PER_TASK_BYTES, at most
PER_TASK_LINES lines, the time checked per line), and the whole read stops
starting sources, and trace passes, after TOTAL_BUDGET_S. A source that fails
or runs out of time is reported in `sources` with an error code and the
others go on; `truncated` names the sources that left older records unread
(the task cap, a file's tail limit), apart from `capped` (a source gave as
many rows as it may). One read runs at a time (another waits BUSY_WAIT_S,
then is told Busy), and the plain first page is kept CACHE_S. Nothing here
writes.

Signatures are never shown: an id made from one (an artifact's reference, a
decision's) is a keyed hash of it (VIEW_DOMAIN, from artifact_manifest's
derived keys), the same on both sides so a trace still joins them.

Paging: rows are ordered by (at, source, ref), newest first. `next_cursor`
names the last row of a page; the next page holds the rows after it, so a
page does not move when newer records arrive.

Tracing one id (`id=`): the rows that name it (link "direct"); then the rows
of the tasks those rows name ("via_task") and the artifacts made under the
decisions they name ("via_decision"); then the goal receipts of the goals any
of those name ("via_goal"). One step each, never further, so a goal never
pulls in every action of every task it ever ran: a task named only by a goal
check that names the traced id (goal -> its check rows) is not followed.

No path is ever built from what a caller sends: the id and the cursor are only
compared with values read from the stores.
"""
from __future__ import annotations

import base64
import binascii
import contextvars
import copy
import hashlib
import hmac as _hmac
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

#: The families, as the Ledger page names them.
FAMILY_LABELS: Dict[str, str] = {
    "decision": "Checkpoint decision",
    "outcome": "What an action did",
    "action_journal": "Change to your things",
    "goal": "Goal receipt",
    "tool": "Tool call",
    "trace": "Reasoning trace",
    "activity": "Activity",
    "journal": "Task journal event",
    "egress": "Send leaving this PC",
    "artifact": "File Friday made",
    "provenance": "Content credential",
    "publish": "Post publish attempt",
    "website_trust": "Website trust",
    "inbox_cleanup": "Inbox cleanup",
    "send_grant": "Pre-approved send",
    "forget": "Deletion receipt",
    "batch_run": "Approved change run",
    "dissent": "Friday pushed back",
    "setup": "Setup choice",
    "memory": "Memory write",
    "skill": "Skill change",
    "ledger": "Ledger entry",
}
FAMILIES = tuple(FAMILY_LABELS)

#: How many rows one source reads at most, newest first.
PER_SOURCE_MAX = 500
#: How long one source may read.
SOURCE_BUDGET_S = 2.0
#: After this long no further source is started (each one left says so).
TOTAL_BUDGET_S = 6.0
#: The most rows one page holds.
PAGE_MAX = 200
#: How many tasks' journals one read opens (newest first).
MAX_TASKS = 50
#: How many skill folders one read opens.
MAX_SKILLS = 200
#: How much of the end of a log file one read takes.
TAIL_BYTES = 4 * 1024 * 1024
#: How much of the end of one task's journal one read takes, and how many of
#: its lines it decodes at most (an encrypted line costs a decryption).
PER_TASK_BYTES = 512 * 1024
PER_TASK_LINES = 4000
#: How long a read waits for another one to finish before it says Busy.
BUSY_WAIT_S = 3.0
#: How long the plain first page (no id, no cursor) is kept.
CACHE_S = 15.0
#: The purpose a signature is hashed for before it is shown as an id.
VIEW_DOMAIN = b"friday-unified-ledger-view-ref-v1"

#: An id: letters, digits and _ . : + - only, at most 128 characters. Paths
#: (slashes, backslashes, spaces), addresses (@) and sentences never pass.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+\-]{0,127}")
#: A kind or word code.
_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,47}")

#: The id fields a row may carry. Anything else is dropped.
ID_KEYS = ("action_id", "receipt_id", "ledger_receipt_id", "decision_receipt_id", "decision_ref",
           "goal_id", "milestone_id", "task_id", "approval_id", "turn_id", "trace_id",
           "parent_trace_id", "call_id", "manifest_id", "artifact_id", "content_hash",
           "correlation_id", "run_id", "check_id", "post_id", "target_id", "skill_ref")

#: The words a summary may carry after its family's label.
DECISION_WORDS = ("allow", "confirm", "card", "deny")
OUTCOME_WORDS = ("executed", "approval_required", "denied", "forbidden", "waiting", "failed",
                 "completed_verified", "unconfirmed")
ACTION_WORDS = ("complete", "partial", "failed", "pending", "started", "running", "refused",
                "error", "undone")
GOAL_WORDS = ("goal_defined", "denied", "budget_blocked", "milestone_complete",
              "verification_failed_escalated", "milestone_human_approved_override",
              "milestone_manual_confirmed", "milestone_interrupted",
              "milestone_interrupted_before_work", "milestone_restarted_by_owner")
JOURNAL_WORDS = ("raised", "decided", "approved", "auto_approved", "denied", "expired", "passed",
                 "failed", "unknown", "paused", "resumed", "stopped", "ok", "blocked") + OUTCOME_WORDS
EGRESS_WORDS = ("sent", "sent_with_parts_withheld", "sent_unconfirmed", "failed", "nothing_sent",
                "blocked", "refused")
PUBLISH_WORDS = ("confirmed", "failed", "held", "retrying", "deferred", "sent", "takedown",
                 "taken_down", "takedown_failed")
SETUP_WORDS = ("you", "recommended", "default")
MEMORY_WORDS = ("write", "quarantine", "rehabilitate", "rollback", "delete", "purge", "forget")
SKILL_WORDS = ("create", "update", "archive", "restore", "import", "delete", "write", "edit",
               "rename", "save")
GENERIC_WORDS = ("added", "removed", "revoked", "granted", "trusted", "blocked", "allowed",
                 "restored", "started", "finished", "interrupted", "ok", "failed", "sent",
                 "archived", "undone", "used", "expired")

#: The task journal events this list shows (they carry ids and codes only).
JOURNAL_KINDS = ("approval", "verification", "artifact", "blocker", "paused", "resumed",
                 "stopped", "receipt", "tool_call")

#: action ledger kind -> family (activity rows are split by their own kind).
LEDGER_KIND_FAMILY = {
    "decision": "decision", "outcome": "outcome", "action": "action_journal", "goal": "goal",
    "web_site": "website_trust", "inbox_cleanup": "inbox_cleanup", "send_grant": "send_grant",
    "outcome_memory_forget": "forget", "item_batch_run": "batch_run", "dissent": "dissent",
}
ACTIVITY_FAMILY = {"tool_call": "tool", "reasoning_trace": "trace"}

#: The time of a row whose store gave none: it sorts last.
_UNDATED = 0.0


def _clock() -> float:
    """The clock every limit here is measured on (a test may move it)."""
    return time.monotonic()


class _Ctx:
    """What one source is asked for."""

    def __init__(self, *, limit: int, families: Optional[Set[str]], before: Optional[float],
                 match: Optional[Callable[[dict], bool]], wanted: Set[str], budget: float):
        self.limit = limit
        self.families = families
        self.before = before
        self.match = match
        self.wanted = wanted
        self.deadline = _clock() + budget
        self.partial = False
        #: older records were left unread (a cap or a tail limit), not for time
        self.truncated = False

    def expired(self) -> bool:
        if _clock() > self.deadline:
            self.partial = True
            return True
        return False

    def keep(self, row: Optional[dict]) -> bool:
        """The row is wanted: its family, before the cursor's time, and the
        id being traced."""
        if not row:
            return False
        if self.families is not None and row["family"] not in self.families:
            return False
        if self.before is not None and row["at"] > self.before:
            return False
        if self.match is not None and not self.match(row["ids"]):
            return False
        return True


# ── small helpers ────────────────────────────────────────────────────────────

def _id(v: Any) -> Optional[str]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        v = str(v)
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if _ID_RE.fullmatch(v) else None


def _code(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower()
    return s if _CODE_RE.fullmatch(s) else None


def _word(v: Any, allowed: Iterable[str]) -> Optional[str]:
    s = _code(v)
    return s if s and s in allowed else None


def _ids(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in pairs:
        if k not in ID_KEYS or k in out:
            continue
        i = _id(v)
        if i:
            out[k] = i
    return out


def _at(v: Any) -> float:
    """Unix seconds from a number or an ISO time; _UNDATED when neither."""
    if isinstance(v, bool):
        return _UNDATED
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip():
        s = v.strip()
        try:
            return float(s)
        except ValueError:
            pass
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.timestamp()
        except ValueError:
            return _UNDATED
    return _UNDATED


def _iso(at: float) -> Optional[str]:
    if not at:
        return None
    try:
        return datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _summary(family: str, word: Optional[str] = None) -> str:
    label = FAMILY_LABELS.get(family, "Record")
    return label + (": " + word.replace("_", " ") if word else "")


def _row(family: str, kind: Any, at: Any, ids: Dict[str, str], *, signed: bool, chained: bool,
         where: str, word: Optional[str], source: str, ref: Any) -> Optional[dict]:
    if family not in FAMILY_LABELS:
        return None
    t = _at(at)
    r = _id(ref)
    if r is None:
        r = hashlib.sha256(str(ref).encode("utf-8", "replace")).hexdigest()[:24]
    return {"family": family, "kind": _code(kind) or "other", "at": t, "ts": _iso(t),
            "ids": ids, "signed": bool(signed), "chained": bool(chained), "where": where,
            "summary": _summary(family, word), "source": source, "ref": r}


def _tail_lines(path: Path, max_bytes: int = TAIL_BYTES, ctx: Optional[_Ctx] = None) -> List[bytes]:
    """The file's last lines, NEWEST FIRST, from at most `max_bytes` of its
    end (a first line cut in half is dropped). [] when it cannot be read.
    A file longer than that marks `ctx` truncated."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > max_bytes:
        lines = lines[1:]
        if ctx is not None:
            ctx.truncated = True
    return [ln for ln in reversed(lines) if ln.strip()]


# The view key of one read (set by unified(); a direct call loads its own).
_VIEW_KEY: "contextvars.ContextVar[Optional[bytes]]" = contextvars.ContextVar(
    "unified_ledger_view_key", default=None)


def _load_view_key() -> Optional[bytes]:
    """A key for showing signatures as ids, derived from the governance key
    for this purpose only (artifact_manifest.derived_key). None when it
    cannot be had: then no id is made from a signature at all."""
    try:
        from agent_friday.services import artifact_manifest as am
        return am.derived_key(VIEW_DOMAIN)
    except Exception:  # noqa: BLE001
        return None


def _keyed(value: Any, prefix: str) -> Optional[str]:
    """`prefix` and a keyed hash of `value` (a signature, or a reference made
    from one): never any of its own characters. None without a key."""
    if not isinstance(value, str) or not value:
        return None
    k = _VIEW_KEY.get() or _load_view_key()
    if not k:
        return None
    return prefix + _hmac.new(k, value.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:24]


def _artifact_view_id(journal_ref: Any) -> Optional[str]:
    """An artifact's id in this list, from the reference the task journal
    keeps for it (artifact_manifest.artifact_ref, G10): the manifest's rows
    and the journal's rows both come through here, so they match."""
    return _keyed(journal_ref, "art_")


def _json(raw: bytes) -> Optional[dict]:
    try:
        v = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return v if isinstance(v, dict) else None


def _scope_ids(scope: Any) -> Dict[str, str]:
    """A deletion receipt's scope ("action:<id>", "goal:<id>", ...) as ids."""
    s = str(scope or "")
    kind, _, ident = s.partition(":")
    key = {"action": "action_id", "goal": "goal_id", "milestone": "goal_id",
           "task": "task_id"}.get(kind)
    return _ids([(key, ident)]) if key else {}


def _goal_of_check(check_id: Any) -> Dict[str, str]:
    """A goal check's id ("goal:<goal id>:<milestone id>", as goals writes it
    into the task journal) as the goal and milestone it names."""
    s = str(check_id or "")
    if not s.startswith("goal:"):
        return {}
    parts = s.split(":")
    return _ids([("goal_id", parts[1] if len(parts) > 1 else None),
                 ("milestone_id", parts[2] if len(parts) > 2 else None)])


# ── source: the action ledger ────────────────────────────────────────────────

def _ledger_family(e: dict) -> Optional[str]:
    k = e.get("kind")
    if k == "activity":
        rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
        return ACTIVITY_FAMILY.get(rec.get("kind"), "activity")
    return LEDGER_KIND_FAMILY.get(k, "ledger")


def _ledger_row(e: dict) -> Optional[dict]:
    if not isinstance(e, dict):
        return None
    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
    fam = _ledger_family(e)
    kind = e.get("kind")
    seq = e.get("seq")
    lgr = ("lgr_%d" % seq) if isinstance(seq, int) and seq > 0 else None
    pairs: List[Tuple[str, Any]] = [("ledger_receipt_id", lgr)]
    env = rec.get("envelope") if isinstance(rec.get("envelope"), dict) else {}
    dref = rec.get("decision_ref") if isinstance(rec.get("decision_ref"), dict) else {}
    pairs += [("action_id", rec.get("action_id") or env.get("action_id") or dref.get("action_id")),
              ("receipt_id", rec.get("receipt_id")),
              ("goal_id", rec.get("goal_id")), ("milestone_id", rec.get("milestone_id")),
              ("task_id", rec.get("task_id")),
              ("approval_id", rec.get("approval_id") or rec.get("approval")),
              ("turn_id", rec.get("turn_id")), ("correlation_id", rec.get("correlation_id")),
              ("run_id", rec.get("run_id"))]
    if isinstance(rec.get("decision_seq"), int) and rec["decision_seq"] > 0:
        pairs.append(("decision_receipt_id", "lgr_%d" % rec["decision_seq"]))
    if fam == "decision":
        # a keyed hash of the decision's signature, as the artifact rows name it
        pairs.append(("decision_ref", _keyed(rec.get("hmac"), "dref_")))
    if fam == "trace":
        pairs += [("trace_id", rec.get("trace_id")), ("parent_trace_id", rec.get("parent_id"))]
    ids = _ids(pairs)
    if fam == "forget":
        for k, v in _scope_ids(rec.get("scope")).items():
            ids.setdefault(k, v)
    word: Optional[str] = None
    if fam == "decision":
        word = _word(rec.get("decision"), DECISION_WORDS)
    elif fam == "outcome":
        word = _word(rec.get("outcome"), OUTCOME_WORDS)
    elif fam == "action_journal":
        word = "undone" if rec.get("undone") else _word(rec.get("status"), ACTION_WORDS)
    elif fam == "goal":
        word = _word(rec.get("kind"), GOAL_WORDS)
    elif fam == "tool":
        word = "ok" if rec.get("ok") is True else ("failed" if rec.get("ok") is False else None)
    elif fam == "batch_run":
        word = _word(rec.get("state"), GENERIC_WORDS)
    elif fam in ("activity", "trace", "forget", "dissent", "ledger"):
        word = None
    else:
        word = _word(rec.get("event") or rec.get("status") or rec.get("state"), GENERIC_WORDS)
    shown_kind = rec.get("kind") if kind == "activity" else kind
    return _row(fam, shown_kind, e.get("t") if e.get("t") is not None else e.get("ts"), ids,
                signed=isinstance(e.get("sig"), str) and bool(e.get("sig")),
                chained=isinstance(e.get("prev"), str) and bool(e.get("prev")),
                where="action ledger", word=word, source="action_ledger", ref=lgr or e.get("hash"))


_LEDGER_FAMILIES = set(LEDGER_KIND_FAMILY.values()) | set(ACTIVITY_FAMILY.values()) | {"activity", "ledger"}


def _ledger_kinds(families: Optional[Set[str]]) -> Optional[List[str]]:
    """The action ledger kinds to read for these families (None: all kinds)."""
    if families is None or "ledger" in families:
        return None
    kinds = [k for k, f in LEDGER_KIND_FAMILY.items() if f in families]
    if families & ({"activity"} | set(ACTIVITY_FAMILY.values())):
        kinds.append("activity")
    return kinds


def _src_action_ledger(ctx: _Ctx) -> List[dict]:
    from agent_friday.services import action_ledger
    kinds = _ledger_kinds(ctx.families)
    if kinds == []:
        return []
    out: Dict[str, dict] = {}

    def where(e: dict) -> bool:
        row = _ledger_row(e)
        if not ctx.keep(row):
            return False
        out.setdefault(row["ref"], row)       # a re-read after a seal gives a line twice
        return True

    # asked before every line: the read ends on its deadline, not only at
    # `limit` matches (a trace whose id names nothing would read it all)
    action_ledger.entries(kinds, newest_first=True, limit=ctx.limit, where=where,
                          stop=ctx.expired)
    return list(out.values())[:ctx.limit]


# ── source: the task journal ─────────────────────────────────────────────────

def _journal_row(ev: dict) -> Optional[dict]:
    kind = ev.get("kind")
    if kind not in JOURNAL_KINDS:
        return None
    tid = _id(ev.get("task_id"))
    seq = ev.get("seq")
    ids = _ids([("task_id", tid), ("action_id", ev.get("action_id")),
                ("approval_id", ev.get("approval_id")), ("receipt_id", ev.get("receipt_id")),
                ("decision_receipt_id", ev.get("decision_receipt_id")),
                ("artifact_id", _artifact_view_id(ev.get("artifact_id"))),
                ("check_id", ev.get("check_id"))])
    for k, v in _goal_of_check(ev.get("check_id")).items():
        ids.setdefault(k, v)
    if kind == "approval":
        word = _word(ev.get("outcome") or ev.get("phase"), JOURNAL_WORDS)
    elif kind == "verification":
        word = _word(ev.get("result"), JOURNAL_WORDS)
    elif kind == "receipt":
        word = _word(ev.get("outcome"), JOURNAL_WORDS)
    elif kind == "tool_call":
        word = "ok" if ev.get("ok") is True else ("failed" if ev.get("ok") is False else None)
    elif kind == "blocker":
        word = "blocked"
    else:
        word = _word(kind, JOURNAL_WORDS)
    return _row("journal", kind, ev.get("ts"), ids, signed=False, chained=False,
                where="task journal", word=word, source="task_journal",
                ref="%s#%s" % (tid or "task", seq if isinstance(seq, int) else "0"))


def _src_task_journal(ctx: _Ctx) -> List[dict]:
    from agent_friday.services import task_journal as tj
    if ctx.families is not None and "journal" not in ctx.families:
        return []
    when: Dict[str, float] = {}
    for t, row in (tj.index_read() or {}).items():
        when[t] = _at((row or {}).get("ts") or (row or {}).get("created"))
    try:
        # a task whose index row was never written still has its journal
        for n, d in enumerate(tj.tasks_dir().iterdir()):
            if n >= 20 * MAX_TASKS:
                ctx.truncated = True
                break
            if ctx.expired():
                break
            if d.name not in when and d.is_dir() and (d / "journal.jsonl").is_file():
                when[d.name] = d.stat().st_mtime
    except OSError:
        pass
    # The tasks to open come from the disk (the index and the folder), never
    # from the request: a traced id that is a task id picks the disk's own name.
    named = [t for t in when if t in ctx.wanted]
    newest = sorted(when, key=lambda t: -when[t])
    order = named + [t for t in newest[:MAX_TASKS] if t not in named]
    if len(order) < len(when):
        ctx.truncated = True                  # more tasks than one read opens
    out: List[dict] = []
    for tid in order:
        if ctx.expired() or len(out) >= ctx.limit * 4:
            break
        lines = [0]

        def stop() -> bool:
            # asked before each line is decoded: the time, and this task's cap
            lines[0] += 1
            return lines[0] > PER_TASK_LINES or ctx.expired()

        evs = tj.read(tid, stop=stop, tail_bytes=PER_TASK_BYTES)
        if lines[0] > PER_TASK_LINES or _journal_bytes(tj, tid) > PER_TASK_BYTES:
            ctx.truncated = True
        for ev in evs:
            row = _journal_row(ev) if isinstance(ev, dict) else None
            if ctx.keep(row):
                out.append(row)
    out.sort(key=_sort_key, reverse=True)
    return out[:ctx.limit]


def _journal_bytes(tj: Any, tid: str) -> int:
    try:
        return (tj.task_dir(tid) / "journal.jsonl").stat().st_size
    except (OSError, ValueError):
        return 0


# ── source: the egress ledger ────────────────────────────────────────────────

def _egress_row(e: dict) -> Optional[dict]:
    if e.get("kind") != "call":
        return None
    via = str(e.get("via") or "")
    ids = _ids([("call_id", e.get("call_id")), ("action_id", e.get("action_id")),
                ("turn_id", e.get("turn_id")), ("manifest_id", e.get("manifest_id"))])
    try:
        from agent_friday.services.egress_gate import SERVICE_CHANNELS
    except Exception:  # noqa: BLE001
        SERVICE_CHANNELS = ()
    ch = e.get("channel")
    kind = ch if ch in SERVICE_CHANNELS else ("service_send" if via.startswith("service:") else "model_call")
    return _row("egress", kind, e.get("ts"), ids, signed=False, chained=False,
                where="egress ledger", word=_word(e.get("result"), EGRESS_WORDS), source="egress",
                ref=e.get("call_id") or "eg@%s" % e.get("ts"))


def _egress_path() -> Path:
    from agent_friday.services import egress_gate
    return Path(egress_gate._DEFAULT_LOG)


def _src_egress(ctx: _Ctx) -> List[dict]:
    if ctx.families is not None and "egress" not in ctx.families:
        return []
    out: List[dict] = []
    for raw in _tail_lines(_egress_path(), TAIL_BYTES, ctx):
        if ctx.expired() or len(out) >= ctx.limit:
            break
        if b'"call"' not in raw:
            continue
        e = _json(raw)
        row = _egress_row(e) if e else None
        if ctx.keep(row):
            out.append(row)
    return out


# ── source: the artifact manifest ────────────────────────────────────────────

def _artifact_row(e: dict) -> Optional[dict]:
    if e.get("kind") != "artifact":
        return None
    from agent_friday.services import artifact_manifest as am
    sig = e.get("hmac") if isinstance(e.get("hmac"), str) else ""
    receipt = e.get("receipt") if isinstance(e.get("receipt"), dict) else {}
    sha = e.get("sha256")
    # Ids made from signatures are keyed hashes, never their characters: the
    # artifact's from the reference the task journal keeps for it (G10's
    # artifact_ref, so the journal's rows match), the decision's as the
    # decision row makes it.
    ids = _ids([("artifact_id", _artifact_view_id(am.artifact_ref(e))),
                ("task_id", e.get("task_id")), ("turn_id", e.get("turn_id")),
                ("decision_ref", _keyed(receipt.get("hmac"), "dref_")),
                ("content_hash", ("sha256:" + sha) if isinstance(sha, str) and sha else None)])
    qa = e.get("qa") if isinstance(e.get("qa"), dict) else {}
    word = "ok" if qa.get("ok") is True else ("failed" if qa.get("ok") is False else None)
    ref = ("a" + hashlib.sha256(sig.encode("utf-8", "replace")).hexdigest()[:24]) if sig else e.get("ts")
    return _row("artifact", "artifact", e.get("ts"), ids, signed=bool(sig), chained=False,
                where="artifact manifest", word=word, source="artifact_manifest", ref=ref)


def _src_artifact_manifest(ctx: _Ctx) -> List[dict]:
    if ctx.families is not None and "artifact" not in ctx.families:
        return []
    from agent_friday.services import artifact_manifest as am
    out: List[dict] = []
    for raw in _tail_lines(Path(am.manifest_path()), TAIL_BYTES, ctx):
        if ctx.expired() or len(out) >= ctx.limit:
            break
        e = _json(raw)
        row = _artifact_row(e) if e else None
        if ctx.keep(row):
            out.append(row)
    return out


# ── source: the provenance ledger ────────────────────────────────────────────

def _provenance_row(e: dict) -> Optional[dict]:
    ids = _ids([("content_hash", e.get("content_hash"))])
    pub = e.get("event") == "publication"
    prev = e.get("prev") if isinstance(e.get("prev"), str) else ""
    return _row("provenance", "publication" if pub else "credential", e.get("ts"), ids,
                signed=False, chained=bool(prev), where="provenance ledger",
                word="publication" if pub else None, source="provenance",
                ref="%s.%s" % (prev[:16] or "first", ids.get("content_hash", "")[-12:]))


def _src_provenance(ctx: _Ctx) -> List[dict]:
    if ctx.families is not None and "provenance" not in ctx.families:
        return []
    from agent_friday.services import provenance
    out: List[dict] = []
    for raw in _tail_lines(Path(provenance.LEDGER_FILE), TAIL_BYTES, ctx):
        if ctx.expired() or len(out) >= ctx.limit:
            break
        e = _json(raw)
        row = _provenance_row(e) if e else None
        if ctx.keep(row):
            out.append(row)
    return out


# ── source: the publish log ──────────────────────────────────────────────────

def _publish_row(e: dict, raw: bytes = b"") -> Optional[dict]:
    ids = _ids([("target_id", e.get("target_id")), ("post_id", e.get("post_id")),
                ("action_id", e.get("action_id"))])
    word = _word(e.get("outcome") or e.get("event"), PUBLISH_WORDS)
    # the line's own short hash too: two events in the same second for the
    # same target (an attempt and its outcome) never share a reference
    ref = "%s.%s.%s.%s" % (int(_at(e.get("ts"))), ids.get("target_id", "t"),
                           _id(e.get("attempt")) or "0",
                           hashlib.sha256(raw.strip()).hexdigest()[:12])
    return _row("publish", e.get("event") or "publish", e.get("ts"), ids, signed=False,
                chained=False, where="publish log", word=word, source="publish_log", ref=ref)


def _src_publish_log(ctx: _Ctx) -> List[dict]:
    if ctx.families is not None and "publish" not in ctx.families:
        return []
    from agent_friday.services import content_pipeline
    out: List[dict] = []
    for raw in _tail_lines(Path(content_pipeline.PUBLISH_LOG), TAIL_BYTES, ctx):
        if ctx.expired() or len(out) >= ctx.limit:
            break
        e = _json(raw)
        row = _publish_row(e, raw) if e else None
        if ctx.keep(row):
            out.append(row)
    return out


# ── source: the setup record ─────────────────────────────────────────────────

def _setup_row(e: dict) -> Optional[dict]:
    seq = e.get("seq")
    return _row("setup", "setup_choice", e.get("ts"), {}, signed=False,
                chained=isinstance(e.get("prev"), str), where="setup record",
                word=_word(e.get("how"), SETUP_WORDS), source="setup_record",
                ref="setup#%s" % (seq if isinstance(seq, int) else "0"))


def _src_setup_record(ctx: _Ctx) -> List[dict]:
    if ctx.families is not None and "setup" not in ctx.families:
        return []
    from agent_friday.services import setup_record
    out: List[dict] = []
    for raw in _tail_lines(Path(setup_record.ledger_path()), TAIL_BYTES, ctx):
        if ctx.expired() or len(out) >= ctx.limit:
            break
        e = _json(raw)
        row = _setup_row(e) if e else None
        if ctx.keep(row):
            out.append(row)
    return out


# ── source: the memory ledger ────────────────────────────────────────────────

def _memory_ledger_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "memory" / "memory_ledger.jsonl"


def _memory_row(e: dict) -> Optional[dict]:
    h = e.get("entry_hash") if isinstance(e.get("entry_hash"), str) else ""
    op = _word(e.get("op"), MEMORY_WORDS)
    return _row("memory", op or "memory", e.get("ts"), {}, signed=False,
                chained=isinstance(e.get("prev_hash"), str), where="memory ledger",
                word=op, source="memory_ledger", ref=h[:24] or e.get("ts"))


def _src_memory_ledger(ctx: _Ctx) -> List[dict]:
    if ctx.families is not None and "memory" not in ctx.families:
        return []
    out: List[dict] = []
    for raw in _tail_lines(_memory_ledger_path(), TAIL_BYTES, ctx):
        if ctx.expired() or len(out) >= ctx.limit:
            break
        e = _json(raw)
        row = _memory_row(e) if e else None
        if ctx.keep(row):
            out.append(row)
    return out


# ── source: the skill ledgers ────────────────────────────────────────────────

def _skills_root() -> Path:
    from agent_friday import skill_registry
    return Path(skill_registry.SKILLS_DIR)


def _skill_row(e: dict, skill_ref: str) -> Optional[dict]:
    mac = e.get("mac") if isinstance(e.get("mac"), str) else ""
    prev = e.get("prev") if isinstance(e.get("prev"), str) else ""
    act = _word(e.get("action"), SKILL_WORDS)
    # the reference is a hash of the line's signature (or link), never its characters
    tag = hashlib.sha256((mac or prev).encode("utf-8", "replace")).hexdigest()[:16] if (mac or prev) else "0"
    return _row("skill", act or "skill", e.get("ts"), {"skill_ref": skill_ref}, signed=bool(mac),
                chained=bool(prev), where="skill ledger", word=act, source="skill_ledgers",
                ref="%s.%s" % (skill_ref, tag))


def _src_skill_ledgers(ctx: _Ctx) -> List[dict]:
    if ctx.families is not None and "skill" not in ctx.families:
        return []
    root = _skills_root()
    try:
        folders = sorted((d for d in root.iterdir()
                          if d.is_dir() and not d.is_symlink() and not d.name.startswith(".")),
                         key=lambda d: d.name)
    except OSError:
        return []
    if len(folders) > MAX_SKILLS:
        ctx.truncated = True
        folders = folders[:MAX_SKILLS]
    out: List[dict] = []
    for d in folders:
        if ctx.expired():
            break
        # an opaque reference: the folder's name is the owner's words
        ref = "skl_" + hashlib.sha256(d.name.encode("utf-8", "replace")).hexdigest()[:12]
        for raw in _tail_lines(d / ".ledger.jsonl", 256 * 1024, ctx)[:ctx.limit]:
            e = _json(raw)
            row = _skill_row(e, ref) if e else None
            if ctx.keep(row):
                out.append(row)
    out.sort(key=_sort_key, reverse=True)
    return out[:ctx.limit]


# ── the registry ─────────────────────────────────────────────────────────────

#: (name, the families it can give, reader). The order breaks ties in time.
SOURCES: List[Tuple[str, Tuple[str, ...], Callable[[_Ctx], List[dict]]]] = [
    ("action_ledger", tuple(sorted(_LEDGER_FAMILIES)), _src_action_ledger),
    ("task_journal", ("journal",), _src_task_journal),
    ("egress", ("egress",), _src_egress),
    ("artifact_manifest", ("artifact",), _src_artifact_manifest),
    ("provenance", ("provenance",), _src_provenance),
    ("publish_log", ("publish",), _src_publish_log),
    ("setup_record", ("setup",), _src_setup_record),
    ("memory_ledger", ("memory",), _src_memory_ledger),
    ("skill_ledgers", ("skill",), _src_skill_ledgers),
]


def _source_index(name: str) -> int:
    for i, (n, _f, _r) in enumerate(SOURCES):
        if n == name:
            return i
    return len(SOURCES)


def _sort_key(row: dict) -> tuple:
    # newest first when sorted in reverse: time, then the source's place
    # (lower places first, so it is negated), then the reference
    return (row["at"], -_source_index(row["source"]), row["ref"])


def _clean(row: dict) -> Optional[dict]:
    """The row in the one shape, with nothing else in it."""
    if not isinstance(row, dict) or row.get("family") not in FAMILY_LABELS:
        return None
    ids = {k: v for k, v in (row.get("ids") or {}).items() if k in ID_KEYS and _id(v) == v}
    return {"family": row["family"], "kind": _code(row.get("kind")) or "other",
            "at": float(row.get("at") or 0.0), "ts": row.get("ts"), "ids": ids,
            "signed": bool(row.get("signed")), "chained": bool(row.get("chained")),
            "where": str(row.get("where") or ""), "summary": str(row.get("summary") or ""),
            "source": str(row.get("source") or ""), "ref": str(row.get("ref") or "")}


def _read_sources(*, limit: int, families: Optional[Set[str]], before: Optional[float],
                  match: Optional[Callable[[dict], bool]], wanted: Set[str],
                  report: Dict[str, dict], started: float) -> List[dict]:
    rows: List[dict] = []
    for name, fams, reader in SOURCES:
        if families is not None and not (set(fams) & families):
            report.setdefault(name, {"ok": True, "rows": 0, "skipped": "family"})
            continue
        left = TOTAL_BUDGET_S - (_clock() - started)
        if left <= 0:
            prev = report.get(name) or {}
            report[name] = dict(prev, ok=False, rows=prev.get("rows", 0), error="out_of_time")
            continue
        ctx = _Ctx(limit=limit, families=families, before=before, match=match, wanted=wanted,
                   budget=min(SOURCE_BUDGET_S, left))
        prev = report.get(name) or {}
        try:
            got = [r for r in (_clean(x) for x in (reader(ctx) or [])) if r][:limit]
        except Exception as e:  # noqa: BLE001 - one store failing never hides the others
            # the error is the exception's type name only: never its message,
            # which can carry a path
            report[name] = dict(prev, ok=False, rows=prev.get("rows", 0), error=type(e).__name__)
            continue
        # capped: the source gave as many rows as it may; truncated: it left
        # older records unread (the task cap, a file's tail limit)
        report[name] = dict(prev, ok=prev.get("ok", True), rows=prev.get("rows", 0) + len(got),
                            capped=bool(prev.get("capped")) or len(got) >= limit,
                            truncated=bool(prev.get("truncated")) or ctx.truncated,
                            out_of_time=bool(prev.get("out_of_time")) or ctx.partial)
        report[name].pop("skipped", None)
        rows.extend(got)
    return rows


# ── the cursor ───────────────────────────────────────────────────────────────

class BadCursor(ValueError):
    """The cursor is not one this list gave out."""


def _cursor_of(row: dict) -> str:
    raw = json.dumps([row["at"], row["source"], row["ref"]], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _parse_cursor(cursor: Optional[str]) -> Optional[tuple]:
    if not cursor:
        return None
    try:
        s = str(cursor)[:400]
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        at, source, ref = json.loads(raw.decode("utf-8"))
        if not isinstance(source, str) or not isinstance(ref, str):
            raise ValueError("cursor")
        return (float(at), -_source_index(source), ref)
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
        raise BadCursor("not a cursor this list gave out") from None


# ── the list ─────────────────────────────────────────────────────────────────

def _families_of(family: Any) -> Optional[Set[str]]:
    if family in (None, "", []):
        return None
    names = family if isinstance(family, (list, tuple, set)) else str(family).split(",")
    want = {str(n).strip() for n in names if str(n).strip() in FAMILY_LABELS}
    return want or set()


#: The ids a goal check row names the goal by (task_journal verification
#: rows: check_id "goal:<goal>:<milestone>", and the goal and milestone read
#: from it). A task named only through these is not followed.
_GOAL_CHECK_KEYS = frozenset({"goal_id", "milestone_id", "check_id"})


def _only_as_goal_check(row: dict, values: Set[str]) -> bool:
    """The row names the traced id only as a goal check (goal -> its check
    rows -> stop): its task is not the traced id's task."""
    ids = row.get("ids") or {}
    if not ids.get("check_id"):
        return False
    hits = {k for k, v in ids.items() if v in values}
    return bool(hits) and hits <= _GOAL_CHECK_KEYS


def _trace(want: str, families: Optional[Set[str]], report: Dict[str, dict],
           started: float, skipped: List[str]) -> List[dict]:
    """The rows that name `want` ("direct"); then the rows of the tasks those
    rows name ("via_task"), except a task named only by a goal check that
    names `want`, and the artifacts made under the decisions they name
    ("via_decision"); then the goal receipts of the goals any of those name
    ("via_goal"). A pass that would start after TOTAL_BUDGET_S is not started
    and is named in `skipped`."""
    found: Dict[tuple, dict] = {}

    def add(rows: List[dict], link: str) -> None:
        for r in rows:
            k = (r["source"], r["ref"])
            if k not in found:
                found[k] = dict(r, link=link)

    def by_value(values: Set[str]) -> Callable[[dict], bool]:
        return lambda ids: any(v in values for v in ids.values())

    def by_key(key: str, values: Set[str]) -> Callable[[dict], bool]:
        return lambda ids: ids.get(key) in values

    def time_left(name: str) -> bool:
        if TOTAL_BUDGET_S - (_clock() - started) > 0:
            return True
        skipped.append(name)
        return False

    # the traced id itself, and, for an artifact reference as the task
    # journal keeps it (G10), the id this list shows for that artifact
    values = {want}
    if want.startswith("art_"):
        shown = _artifact_view_id(want)
        if shown:
            values.add(shown)
    lim = PER_SOURCE_MAX
    direct = _read_sources(limit=lim, families=None, before=None, match=by_value(values),
                           wanted={want}, report=report, started=started)
    add(direct, "direct")
    tasks = {r["ids"]["task_id"] for r in direct
             if r["ids"].get("task_id") and not _only_as_goal_check(r, values)} - values
    drefs = {r["ids"]["decision_ref"] for r in direct
             if r["family"] == "decision" and r["ids"].get("decision_ref")}
    if tasks and time_left("via_task"):
        add(_read_sources(limit=lim, families=None, before=None, match=by_key("task_id", tasks),
                          wanted=tasks, report=report, started=started), "via_task")
    if drefs and time_left("via_decision"):
        add(_read_sources(limit=lim, families={"artifact"}, before=None,
                          match=by_key("decision_ref", drefs), wanted=set(), report=report,
                          started=started), "via_decision")
    goals = {r["ids"]["goal_id"] for r in found.values() if r["ids"].get("goal_id")} - values
    if goals and time_left("via_goal"):
        add(_read_sources(limit=lim, families={"goal"}, before=None,
                          match=by_key("goal_id", goals), wanted=set(), report=report,
                          started=started), "via_goal")
    rows = list(found.values())
    if families is not None:
        rows = [r for r in rows if r["family"] in families]
    return rows


class Busy(RuntimeError):
    """Another read of the list is running and did not finish in BUSY_WAIT_S."""


#: One read at a time: each opens every store, so two at once only make both slow.
_RUN_LOCK = threading.Lock()
#: (families, limit) -> (when, page) for the plain first page.
_CACHE: Dict[tuple, Tuple[float, dict]] = {}
_CACHE_MAX = 32


def reset_cache() -> None:
    """Forget the kept first pages (tests; nothing else needs to)."""
    _CACHE.clear()


def _cached(key: tuple) -> Optional[dict]:
    hit = _CACHE.get(key)
    if hit is None:
        return None
    at, page = hit
    if _clock() - at > CACHE_S:
        _CACHE.pop(key, None)
        return None
    return dict(copy.deepcopy(page), cached=True)


def _cacheable(out: dict) -> bool:
    """Only a whole read is kept: none failed, ran out of time or was skipped."""
    if out.get("skipped_passes"):
        return False
    return all(s.get("ok", True) and not s.get("out_of_time")
               for s in (out.get("sources") or {}).values())


def unified(*, family: Any = None, id: Optional[str] = None, limit: int = 50,
            cursor: Optional[str] = None) -> dict:
    """One page of every receipt family, newest first.

    `family`: one family or a comma-separated list (FAMILIES); an unknown name
    selects nothing. `id`: trace this id (an action, goal, task, approval,
    receipt or call id) across every family. `limit`: 1 to PAGE_MAX.
    `cursor`: the `next_cursor` of the page before. Raises BadCursor for a
    cursor this list did not give out, and Busy when another read held the
    list for BUSY_WAIT_S; otherwise never raises: a store that cannot be read
    is named in `sources` with an error code. The plain first page (no id, no
    cursor) is kept CACHE_S (`cached` says so)."""
    try:
        limit = max(1, min(int(limit), PAGE_MAX))
    except (TypeError, ValueError):
        limit = 50
    after = _parse_cursor(cursor)
    families = _families_of(family)
    plain = cursor in (None, "") and id in (None, "")
    key = ((tuple(sorted(families)) if families is not None else None), limit) if plain else None
    if key is not None:
        hit = _cached(key)
        if hit is not None:
            return hit
    if not _RUN_LOCK.acquire(timeout=BUSY_WAIT_S):
        raise Busy("the list is being read for another request")
    try:
        if key is not None:
            hit = _cached(key)                # the read this one waited for made it
            if hit is not None:
                return hit
        token = _VIEW_KEY.set(_load_view_key())
        try:
            out = _unified_once(families=families, id=id, limit=limit, after=after)
        finally:
            _VIEW_KEY.reset(token)
        if key is not None and _cacheable(out):
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = (_clock(), copy.deepcopy(out))
        return out
    finally:
        _RUN_LOCK.release()


def _unified_once(*, families: Optional[Set[str]], id: Optional[str], limit: int,
                  after: Optional[tuple]) -> dict:
    want = _id(id) if id not in (None, "") else None
    report: Dict[str, dict] = {}
    skipped: List[str] = []
    started = _clock()
    if id not in (None, "") and want is None:
        rows: List[dict] = []                 # not an id: nothing can name it
    elif families is not None and not families:
        rows = []
    elif want is not None:
        rows = _trace(want, families, report, started, skipped)
    else:
        fetch = min(PER_SOURCE_MAX, limit * 2 + 20)
        rows = _read_sources(limit=fetch, families=families,
                             before=after[0] if after else None, match=None, wanted=set(),
                             report=report, started=started)
    rows.sort(key=_sort_key, reverse=True)
    if after is not None:
        rows = [r for r in rows if _sort_key(r) < after]
    page = rows[:limit]
    more = len(rows) > limit
    out_of_time = bool(skipped) or any(s.get("out_of_time") or s.get("error") == "out_of_time"
                                       for s in report.values())
    return {"rows": page, "count": len(page),
            "next_cursor": _cursor_of(page[-1]) if (page and more) else None,
            "families": dict(FAMILY_LABELS), "sources": report,
            "truncated": sorted(n for n, s in report.items() if s.get("truncated")),
            "out_of_time": out_of_time, "skipped_passes": skipped, "cached": False,
            "traced": bool(want), "bounded": {"per_source": PER_SOURCE_MAX,
                                              "source_seconds": SOURCE_BUDGET_S,
                                              "total_seconds": TOTAL_BUDGET_S,
                                              "tasks": MAX_TASKS,
                                              "task_bytes": PER_TASK_BYTES,
                                              "task_lines": PER_TASK_LINES,
                                              "tail_bytes": TAIL_BYTES}}
