"""Global activity ledger (B4 — Seats & Transparency).

Append-only record of *metadata-only* activity events: which model ran,
on which seat, for how long, with which orb/task correlation ids — plus tool
call outcomes and subagent spawns. This is the single global answer to
"what has Friday actually been doing?" across chat, voice, background tasks
and the orchestrator.

PRIVACY BY CONSTRUCTION: the schema whitelists metadata fields per kind and
silently drops everything else. Prompt text, tool arguments and tool results
can never enter the ledger — there are no such fields.

Every write is best-effort: a ledger failure must never break a turn.

Since the action ledger, rows are entries of kind "activity" in
services/action_ledger.py (one hash-chained store). `LEDGER_FILE` is where they
were kept before; only the one-time migration reads it.

History (A9, NS-11.8-2/-3): the activity record keeps no file of its own and
never rotates or drops anything. Its rows live in the action ledger, whose
segments are sealed (never overwritten), whose HMAC chain runs across them,
and whose only retention is the owner's archive period
(`ledger_archive_after_days`): whole old segments move into the encrypted
archive with a signed "archived" entry in the chain, never deleted.
`verify_chain()` reports that chain as it bears on activity rows. Nothing in
this module renames, rotates or deletes a file.
"""

import time

from agent_friday.core import FRIDAY_DIR

LEDGER_FILE = FRIDAY_DIR / "activity_ledger.jsonl"

#: verify_chain() reads at most this many ledger lines and this many seconds,
#: newest first; past either it says "partial" instead of going on.
VERIFY_MAX_LINES = 200_000
VERIFY_MAX_S = 5.0

# Whitelisted metadata fields per event kind. Anything not listed here is
# DROPPED on write — this is the egress-tier guarantee: names, ids, counts and
# durations only; never prompt text, never tool args/results.
_ALLOWED_FIELDS = {
    # services/first_action: the owner skipped the first governed action (A27): how, nothing else.
    "first_action_skipped": {"how"},
    # services/disk_guard: background work paused for a full disk, and resumed (no paths).
    "disk_full": {"free_mib", "state"},
    "model_invocation": {
        "model", "provider", "seat", "duration_ms",
        "tokens_in", "tokens_out", "orb_id", "task_id", "workspace",
        "turn_id",
    },
    # `status` and `reason` exist because ok=False on its own cannot be
    # diagnosed: 24 calls failed in one 2026-09-25 conversation and every
    # reason was empty, so the transcript had to be read to find out why.
    # `reason` is a CLASSIFICATION, never the tool's output -- this file is
    # plaintext and /api/processes serves from the same box.
    "tool_call": {"tool", "ok", "status", "reason", "duration_ms", "orb_id",
                  "task_id"},
    "subagent_spawn": {"task_id", "description", "model"},
    # A task journal served to a principal other than the local user
    # (task-visibility.md TV11): what left, to whom, and whether the gate
    # withheld anything. Never the content itself.
    "journal_read": {"task_id", "principal", "route", "events", "reasoning", "redacted", "withheld"},
    # A reasoning trace was archived (services/reasoning_trace.py). A pointer
    # into the encrypted trace ledger by seq/hash plus counts -- the reasoning
    # itself, the label and tool arguments stay in the encrypted record.
    "notification": {"notification_kind", "source", "tier", "muted"},
    # One line per notice (services/notice_registry): what it was called, which
    # of the four places it lives in, and whether it reached the owner or was
    # held. Counts only: no title, body, id or path.
    "notice": {"notice_kind", "place", "outcome", "safety", "emitter"},
    # A meeting's recording, transcript or notes were deleted for real
    # (services/meeting_policy). Counts only: no title, id, path or text.
    "meeting_deletion": {"scope", "reason", "audio_files", "meetings"},
    # Quiet turned on (services/interruption_policy.turn_on_quiet): where it was asked from, until when,
    # and whether the 12-hour cap shortened it. No words.
    "quiet_on": {"surface", "until_ts", "capped"},
    "reasoning_trace": {
        "trace_id", "parent_id", "task_id", "model", "seat", "trace_kind", "source",
        "tokens_in", "tokens_out", "reasoning_chars", "tool_calls", "duration_ms",
        "ledger_seq", "ledger_hash",
    },
}

#: Fields that can carry the user's words; dropped while off the record.
_FREE_TEXT_FIELDS = {"description", "workspace", "reason"}

# Free-text fields still get a hard length cap so a caller can't smuggle a
# prompt through e.g. `description`.
_TEXT_CAP = 200


def record(kind, /, **fields):
    """Append one activity event. Returns True on success, False otherwise.

    Unknown kinds and non-whitelisted fields are dropped. Never raises.

    `kind` is positional-only: it names the record, and a caller's own field
    called "kind" (a notification's kind) is a field, not a second record kind.
    It is stored as `notification_kind` so it can never overwrite the record's.
    """
    try:
        allowed = _ALLOWED_FIELDS.get(kind)
        if allowed is None:
            return False
        rec = {"kind": kind, "ts": time.time()}
        # Off the record the ledger keeps its receipt shape and loses any
        # free text (a subagent's description is the user's request).
        try:
            from agent_friday.services import off_record as _off
            _unsaved = _off.active()
        except Exception:
            _unsaved = False
        if _unsaved:
            rec["off_record"] = True
        # The row came from an off-record turn: nothing may LEARN from it
        # (NS-15.8-2; services/avatar_growth's nightly signals read this),
        # even when the owner keeps off-record content (no storage mark).
        try:
            if _unsaved or _off.excludes_learning():
                rec["no_learning"] = True
        except Exception:
            rec["no_learning"] = True
        if kind == "notification" and "kind" in fields:
            fields = dict(fields, notification_kind=fields.pop("kind"))
        for key, val in fields.items():
            if key not in allowed or val is None or key in ("kind", "ts"):
                continue          # a field never overwrites the record's own kind or time
            if _unsaved and key in _FREE_TEXT_FIELDS:
                continue
            if isinstance(val, str) and len(val) > _TEXT_CAP:
                val = val[:_TEXT_CAP]
            rec[key] = val
        # The one action ledger (services/action_ledger.py) holds every
        # activity row as an entry of kind "activity", in its hash chain.
        from agent_friday.services import action_ledger
        return action_ledger.try_append("activity", rec) is not None
    except Exception:
        return False


def read(limit=200, kind=None, model=None, since=None, until=None, task_id=None):
    """Return recent events, NEWEST FIRST, after filtering.

    since/until are unix timestamps (inclusive). Each event carries `seq`, its
    place in the action ledger. Never raises.
    """
    def _want(e):
        rec = e.get("rec")
        if not isinstance(rec, dict):
            return False
        if kind is not None and rec.get("kind") != kind:
            return False
        if model is not None and rec.get("model") != model:
            return False
        if task_id is not None and rec.get("task_id") != task_id:
            return False
        ts = rec.get("ts")
        if since is not None and (ts is None or ts < since):
            return False
        if until is not None and (ts is None or ts > until):
            return False
        return True
    try:
        from agent_friday.services import action_ledger
        rows = action_ledger.entries(["activity"], newest_first=True,
                                     limit=max(1, int(limit)), where=_want)
    except Exception:
        return []
    out = []
    for e in rows:
        rec = dict(e["rec"])
        rec.pop("_migrated", None)
        rec["seq"] = e.get("seq")
        out.append(rec)
    return out


def _is_signed(e: dict) -> bool:
    return all(isinstance(e.get(f), str) and e.get(f) for f in ("prev", "hash", "sig"))


def verify_chain(*, max_lines=None, max_seconds=None) -> dict:
    """The activity record's chain, in counts (A9; NS-11.8-2/-3).

    Activity rows are entries of the one action ledger, so the chain, its
    HMAC signatures and its checks are action_ledger's own (`verify()`); this
    adds what is specific to activity rows, read from the newest end:

      lines             activity rows in the live record (sealed segments and
                        the live file; `chained + legacy_unchained + unchained`)
      segments          ledger files read from (sealed + live); and
      activity_segments those holding an activity row
      chained           rows written into the chain
      legacy_unchained  rows written before the chain existed, brought in by
                        the one-time migration ("before chaining", not
                        tampering: the chain covers them from that day on)
      unchained         rows with no link or signature at all (a break)
      legacy_file_waiting  the old activity_ledger.jsonl is still on disk,
                        not yet brought in
      breaks            open (unacknowledged) problems in the chain, anywhere;
      activity_breaks   those on an activity row; first_break: the first one's
                        segment, line, seq and reason (never its content)
      acknowledged      problems the owner already reviewed
      archived_notes / archived_segments / archived_records
                        the retention (archive) steps: each is a signed
                        "archived" entry in the chain naming the segments
                        moved, their record count and last hash, so the gap in
                        the live files is accounted for, not a break
      partial           the line or time cap was reached (or a file could not
                        be read); counts cover the newest part only
      state             "intact", "before_chaining" (only legacy rows to
                        note), "broken", "partial" or "error"

    Read-only, no lock (readers never take the writer's), bounded by
    VERIFY_MAX_LINES / VERIFY_MAX_S. Never raises: a failure is `error`."""
    out = {"lines": 0, "segments": 0, "activity_segments": 0, "chained": 0,
           "legacy_unchained": 0, "unchained": 0, "legacy_file_waiting": False,
           "breaks": 0, "activity_breaks": 0, "first_break": None, "acknowledged": 0,
           "archived_notes": 0, "archived_segments": 0, "archived_records": 0,
           "unreadable_segments": 0, "scanned": 0, "partial": False, "state": "intact"}
    try:
        from agent_friday.services import action_ledger as L
        cap = VERIFY_MAX_LINES if max_lines is None else max(1, int(max_lines))
        budget = VERIFY_MAX_S if max_seconds is None else max(0.0, float(max_seconds))
        deadline = time.monotonic() + budget
        try:
            out["legacy_file_waiting"] = bool(LEDGER_FILE.exists())
        except OSError:
            pass
        segs = L._reader_segments()
        out["segments"] = len(segs)
        seqs = set()
        for p in reversed(segs):
            if out["partial"]:
                break
            here = 0
            try:
                for raw in L._reverse_lines(p):
                    if out["scanned"] >= cap or time.monotonic() > deadline:
                        out["partial"] = True
                        break
                    out["scanned"] += 1
                    e = L._safe_json(raw)
                    if not isinstance(e, dict):
                        continue
                    kind = e.get("kind")
                    if kind == "archived":
                        names = (e.get("rec") or {}).get("segments") if isinstance(e.get("rec"), dict) else None
                        if isinstance(names, list) and names:
                            out["archived_notes"] += 1
                            out["archived_segments"] += len(names)
                        continue
                    if kind != "activity":
                        continue
                    here += 1
                    if isinstance(e.get("seq"), int):
                        seqs.add(e["seq"])
                    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
                    if not _is_signed(e):
                        out["unchained"] += 1
                    elif rec.get("_migrated"):
                        out["legacy_unchained"] += 1
                    else:
                        out["chained"] += 1
            except OSError:
                out["unreadable_segments"] += 1
            out["lines"] += here
            if here:
                out["activity_segments"] += 1
        r = L.verify(force=False, migrate=False)
        out["archived_records"] = int(r.get("archived_records") or 0)
        probs = r.get("problems") or []
        open_ = [pr for pr in probs if not pr.get("acknowledged")]
        out["breaks"] = int(r.get("open_problem_count") or len(open_))
        out["acknowledged"] = max(0, int(r.get("problem_count") or 0) - out["breaks"])
        out["activity_breaks"] = sum(1 for pr in open_ if pr.get("seq") in seqs)
        if open_:
            f = open_[0]
            out["first_break"] = {"segment": f.get("segment"), "line": f.get("line"),
                                  "seq": f.get("seq"), "reason": f.get("reason")}
        if out["breaks"] or out["unchained"]:
            out["state"] = "broken"
        elif out["partial"] or out["unreadable_segments"]:
            out["state"] = "partial"
        elif out["legacy_unchained"] or out["legacy_file_waiting"]:
            out["state"] = "before_chaining"
    except Exception as e:  # noqa: BLE001 - fail-safe: a check never breaks its caller
        out["state"] = "error"
        out["error"] = type(e).__name__
    return out
