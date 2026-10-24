"""How well memory is doing, measured locally, in counts only (north star
§15.12-1: precision, usefulness, stale errors, citation, corrections, leakage
attempts, duplicates, unconfirmed inference, live-state errors).

Nothing here ever records what a memory says. An event is a kind, a few small
numbers and, at most, a short label from a fixed vocabulary (which store,
which path). Where two events must be matched (a recalled item that is later
corrected), the item's id is kept as a short one-way hash, never the id and
never the text. The store is one append-only JSONL file under the Friday home
(``runtime/memory_metrics.jsonl``). Events older than ``RETENTION_DAYS`` are
dropped once per process at the first record and on every summary, and the
oldest are dropped when the file grows past ``MAX_BYTES``. Off the record (or
when off the record cannot be read) nothing is written.

Events (``record(kind, **fields)``):

  recall                    candidates, shown, refs (hashes of the shown items)
  citation                  shown, cited (recalled items the reply cited)
  correction                store, op, n (the owner changed a remembered fact)
  supersession              n, live, refs (a conversation turn lost its
                            authority as evidence; live=1 when what replaced it
                            is a live source)
  unsupersession            n
  inference_queued          path, n (an unconfirmed inference waits for review)
  inference_kept            path, n
  inference_discarded       path, n
  duplicate_suppressed      path, n
  leakage_blocked           where, n (the egress gate withheld a cloud-bound
                            field, or a recalled block in one, that held
                            recalled memory)
  forget                    store, n

Counters other modules keep in their own process (recall near-duplicates in
services/recall_dedupe, special-category drops in services/sensitive_categories)
are read when those modules exist; absent, they read as zero.

``summary(days)`` turns the events into rates. "Precision" here is a PROXY:
the share of recalled items that were later corrected, subtracted from one.
True precision needs a person to label whether each recalled item was relevant
and right; nothing on this PC has those labels, so the proxy is a floor on
error, not a measure of relevance (offline relevance is measured by
services/memory_retrieval_eval on a fixed synthetic set). It, and the stale
and live-state error rates, read None until a supersession that names turns
(refs) falls in the window: card corrections carry no refs, so on their own
they cannot move these figures.

Every public function here is a hook on a hot path: it never raises and does
one small append.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

KINDS = frozenset({
    "recall", "citation", "correction", "supersession", "unsupersession",
    "inference_queued", "inference_kept", "inference_discarded",
    "duplicate_suppressed", "leakage_blocked", "forget",
})

#: Numeric fields an event may carry.
NUMBER_FIELDS = frozenset({"n", "candidates", "shown", "cited", "live"})
#: Label fields, and the shape a label must have (a short identifier, never prose).
LABEL_FIELDS = frozenset({"store", "op", "path", "where"})
_LABEL_RE = re.compile(r"^[a-z][a-z0-9_.:\-]{0,39}$")
#: Hashed references (see ref_hash), at most this many per event.
MAX_REFS = 12
_REF_RE = re.compile(r"^[0-9a-f]{12}$")

RETENTION_DAYS = 180
MAX_BYTES = 1_000_000
#: A recalled item corrected within this many days of being recalled counts as
#: a stale error (or a live-state error when a live source replaced it).
STALE_WINDOW_DAYS = 14

#: Recall shown in a turn, kept in memory only until that turn's reply is seen
#: (keyed by session and turn id; never written anywhere).
PENDING_TTL_S = 600.0
PENDING_MAX = 64
#: After a failed compaction (on Windows a reader holding the file open makes
#: os.replace fail), do not try again for this long.
COMPACT_BACKOFF_S = 300.0

_LOCK = threading.Lock()
_PENDING: "OrderedDict[str, tuple]" = OrderedDict()
_STATE = {"pruned": False, "compact_retry_at": 0.0}

_CITE_RE = re.compile(r"\[(?:conversation|memory):[^\]/]*/([^\]\s\"/]{4,64})\]", re.I)
#: [conversation:DATE/"short quote"] (or [memory:...]): the quote form the
#: cited-mode instructions teach. Matched against the snippets shown this turn.
_QUOTE_CITE_RE = re.compile(
    r"\[(?:conversation|memory):[^\]/]*/\s*[\"“”']([^\]]{3,400}?)[\"“”']\s*\]", re.I)
_QUOTE_MIN = 8
_NON_WORD = re.compile(r"[^0-9a-z]+")


def _norm(text: Any) -> str:
    return _NON_WORD.sub(" ", str(text or "").lower()).strip()


def _pending_key(session_id: Any, turn_key: Any) -> str:
    return str(session_id or "") + "\x1f" + str(turn_key or "")


def _path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "runtime" / "memory_metrics.jsonl"


def ref_hash(item_id: Any) -> str:
    """A short one-way hash of an item id: enough to match a later correction to
    an earlier recall, nothing that reads back to the id or its text."""
    return hashlib.sha256(("memory-metrics:" + str(item_id)).encode("utf-8")).hexdigest()[:12]


def _off_record() -> bool:
    """Fails closed: when off the record cannot be read, nothing is written."""
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True


def _clean(kind: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if kind not in KINDS:
        return None
    row: Dict[str, Any] = {"ts": round(time.time(), 3), "kind": kind}
    for k, v in fields.items():
        if k in NUMBER_FIELDS:
            if isinstance(v, bool):
                v = int(v)
            if isinstance(v, (int, float)) and v == v and abs(v) < 1e9:
                row[k] = int(v) if float(v).is_integer() else round(float(v), 4)
        elif k in LABEL_FIELDS:
            if isinstance(v, str) and _LABEL_RE.match(v):
                row[k] = v
        elif k == "refs":
            refs = [r for r in (v or []) if isinstance(r, str) and _REF_RE.match(r)]
            if refs:
                row["refs"] = refs[:MAX_REFS]
    return row


def _compact(p: Path, *, shrink: bool = True) -> None:
    """Drop events older than RETENTION_DAYS (and, with `shrink`, the oldest
    until the file is under half of MAX_BYTES). Caller holds _LOCK. Rewrites
    only when something is dropped."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    rows = _read(p)
    keep = [r for r in rows if float(r.get("ts") or 0) >= cutoff]
    if shrink:
        while keep and sum(len(json.dumps(r)) + 1 for r in keep) > MAX_BYTES // 2:
            keep = keep[len(keep) // 4 or 1:]
    if len(keep) == len(rows):
        return
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    os.replace(tmp, p)


def _maintain(p: Path, *, by_age: bool) -> None:
    """Retention, under _LOCK: by age when asked (once per process at the first
    record, and on every summary), by size whenever the file is past MAX_BYTES.
    A failure (e.g. Windows refusing os.replace while a reader has the file
    open) backs off COMPACT_BACKOFF_S before the next attempt. Never raises."""
    now = time.monotonic()
    if now < _STATE["compact_retry_at"]:
        return
    try:
        if not p.exists():
            return
        big = p.stat().st_size > MAX_BYTES
        if big or by_age:
            _compact(p, shrink=big)
    except Exception:
        _STATE["compact_retry_at"] = now + COMPACT_BACKOFF_S


def record(kind: str, **fields: Any) -> None:
    """Append one event. Never raises; does nothing off the record."""
    try:
        row = _clean(kind, fields)
        if row is None or _off_record():
            return
        p = _path()
        line = json.dumps(row, separators=(",", ":")) + "\n"
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            first = not _STATE["pruned"]
            _STATE["pruned"] = True
            if first:
                _maintain(p, by_age=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            _maintain(p, by_age=False)
    except Exception:
        pass


# ── hooks ─────────────────────────────────────────────────────────────────────
def note_recall(session_id: Any, candidates: int, shown_ids: Iterable[Any], *,
                turn_key: Any = None, shown_texts: Optional[Iterable[Any]] = None) -> None:
    """The memory block of a turn: how many came back from search, how many were
    shown. The shown ids' short refs wait (in memory, keyed by session and
    `turn_key`, for PENDING_TTL_S at most) for that turn's reply. `shown_texts`
    (the snippets shown, in the same order) are held the same way so a
    quote-style citation can be matched; they are never written."""
    try:
        raw = list(shown_ids or [])
        texts = list(shown_texts or [])
        ids, by_ref = [], {}
        for pos, i in enumerate(raw):
            if i is None or not str(i):
                continue
            ids.append(str(i))
            by_ref[str(i)[:12]] = _norm(texts[pos]) if pos < len(texts) else ""
        record("recall", candidates=int(candidates or 0), shown=len(ids),
               refs=[ref_hash(i) for i in ids])
        if ids and session_id:
            key = _pending_key(session_id, turn_key)
            now = time.monotonic()
            with _LOCK:
                for k in [k for k, v in _PENDING.items() if now - v[0] > PENDING_TTL_S]:
                    _PENDING.pop(k, None)
                _PENDING[key] = (now, by_ref)
                _PENDING.move_to_end(key)
                while len(_PENDING) > PENDING_MAX:
                    _PENDING.popitem(last=False)
    except Exception:
        pass


def _cited_refs(reply: str, shown: Dict[str, str]) -> set:
    cited = set()
    for m in _CITE_RE.finditer(reply):
        tok = m.group(1).strip()
        for ref in shown:
            if tok[:12] == ref or (len(tok) >= 6 and ref.startswith(tok)):
                cited.add(ref)
    for m in _QUOTE_CITE_RE.finditer(reply):
        q = _norm(m.group(1))
        if len(q) < _QUOTE_MIN:
            continue
        for ref, snippet in shown.items():
            if snippet and q in snippet:
                cited.add(ref)
                break
    return cited


def note_reply(session_id: Any, reply: Any, *, turn_key: Any = None) -> None:
    """The reply to a turn that recalled memory: how many recalled items it cited,
    as [conversation:DATE/REF] or [conversation:DATE/"quote"] (or [memory:...]),
    the quote matched against the snippets shown that turn. Only the count is
    kept. Matched to the recall of the same session and `turn_key` only."""
    try:
        with _LOCK:
            hit = _PENDING.pop(_pending_key(session_id, turn_key), None)
        if not hit or time.monotonic() - hit[0] > PENDING_TTL_S:
            return
        shown = hit[1]
        record("citation", shown=len(shown), cited=len(_cited_refs(str(reply or ""), shown)))
    except Exception:
        pass


def note_supersession(ids: Iterable[Any], on: bool, superseded_by: str = "") -> None:
    """`ids`: the turns whose supersession actually changed (the caller passes
    only the ids its store held)."""
    try:
        ids = [str(i) for i in (ids or []) if str(i).strip()]
        if not ids:
            return
        if on:
            by = str(superseded_by or "")
            live = int(by.startswith(("services.", "live:", "live_state")))
            record("supersession", n=len(ids), live=live, refs=[ref_hash(i) for i in ids])
        else:
            record("unsupersession", n=len(ids))
    except Exception:
        pass


def note_count(kind: str, n: int = 1, **labels: Any) -> None:
    """A counted event (correction, inference_*, duplicate_suppressed,
    leakage_blocked, forget). Zero and negative counts are not recorded."""
    try:
        n = int(n or 0)
        if n > 0:
            record(kind, n=n, **labels)
    except Exception:
        pass


# ── reading ───────────────────────────────────────────────────────────────────
def _read(p: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = p or _path()
    out: List[Dict[str, Any]] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("kind") not in KINDS:
                    continue
                # Only what record() writes is read back: numbers stay numbers.
                clean = _clean(r["kind"], {k: v for k, v in r.items() if k not in ("ts", "kind")})
                ts = r.get("ts")
                if clean is None or isinstance(ts, bool) or not isinstance(ts, (int, float)):
                    continue
                clean["ts"] = float(ts)
                out.append(clean)
    except (OSError, ValueError):
        pass
    return out


def _external_duplicates() -> Dict[str, int]:
    try:
        from agent_friday.services import recall_dedupe   # lands in a sibling change
        raw = recall_dedupe.suppressed_counts() or {}
    except Exception:
        return {}
    out: Dict[str, int] = {}
    for k, v in dict(raw).items():
        try:
            if isinstance(k, str) and _LABEL_RE.match(k.lower()):
                out[k.lower()] = out.get(k.lower(), 0) + max(0, int(v))
        except (TypeError, ValueError):
            continue
    return out


def _external_special_drops() -> Dict[str, int]:
    """{"where": count} from services/sensitive_categories (when present)."""
    try:
        from agent_friday.services import sensitive_categories   # lands in a sibling change
        raw = sensitive_categories.dropped_counts() or {}
    except Exception:
        return {}
    out: Dict[str, int] = {}
    for k, v in dict(raw).items():
        where = str(k).split(":", 1)[0].lower()
        try:
            n = max(0, int(v))
        except (TypeError, ValueError):
            continue
        if _LABEL_RE.match(where):
            out[where] = out.get(where, 0) + n
    return out


def _rate(num: float, den: float) -> Optional[float]:
    return round(num / den, 4) if den else None


def summary(days: int = 30) -> Dict[str, Any]:
    """Counts and rates over the last `days` days. Counts only, never content."""
    try:
        return _summary(days)
    except Exception:
        return {"ok": False, "error": "The memory measures could not be read.", "counts": {}, "rates": {}}


def _summary(days: int = 30) -> Dict[str, Any]:
    try:
        days = max(1, min(int(days or 30), RETENTION_DAYS))
    except (TypeError, ValueError):
        days = 30
    now = time.time()
    since = now - days * 86400
    # Under the writer's lock: retention by age runs here too (so "kept 180
    # days" holds however small the file stays), and no compaction can try to
    # replace the file while this process is reading it.
    p = _path()
    with _LOCK:
        _maintain(p, by_age=True)
        rows = _read(p)
    win = [r for r in rows if float(r.get("ts") or 0) >= since]

    def total(kind: str, field: str = "n") -> int:
        return sum(int(r.get(field) or 0) for r in win if r.get("kind") == kind)

    recalls = [r for r in win if r.get("kind") == "recall"]
    shown = sum(int(r.get("shown") or 0) for r in recalls)
    candidates = sum(int(r.get("candidates") or 0) for r in recalls)
    cite_rows = [r for r in win if r.get("kind") == "citation"]
    cited = sum(int(r.get("cited") or 0) for r in cite_rows)
    cite_shown = sum(int(r.get("shown") or 0) for r in cite_rows)

    # Stale and live-state errors: a distinct recalled item superseded within
    # STALE_WINDOW_DAYS after it was recalled. Recall rows are read from the
    # whole retained file so a correction early in the window still finds a
    # recall just before it; the count is then of items recalled in the window,
    # so neither rate can pass 1. An item superseded by a live source at any
    # point is a live-state error, otherwise a stale one.
    last_recall: Dict[str, List[float]] = {}
    for r in rows:
        if r.get("kind") == "recall":
            for ref in r.get("refs") or []:
                last_recall.setdefault(ref, []).append(float(r.get("ts") or 0))
    stale_refs, live_refs = set(), set()
    ref_supersessions = 0
    for r in win:
        if r.get("kind") != "supersession" or not r.get("refs"):
            continue
        ref_supersessions += 1
        t = float(r.get("ts") or 0)
        for ref in r.get("refs") or []:
            times = last_recall.get(ref) or []
            if any(t - STALE_WINDOW_DAYS * 86400 <= rt <= t for rt in times):
                (live_refs if int(r.get("live") or 0) else stale_refs).add(ref)
    recalled_refs = {ref for r in recalls for ref in (r.get("refs") or [])}
    live_refs &= recalled_refs
    stale_refs = (stale_refs & recalled_refs) - live_refs
    stale, live_err = len(stale_refs), len(live_refs)
    corrected_recalled = stale + live_err
    # The precision proxy and the stale/live rates read only supersessions that
    # carry refs (card corrections carry none). With none in the window the
    # figures could only ever read 100% / 0%, so they read "not enough yet".
    measurable = bool(recalled_refs) and ref_supersessions > 0

    dup_by_path: Dict[str, int] = {}
    for r in win:
        if r.get("kind") == "duplicate_suppressed":
            k = r.get("path") or "other"
            dup_by_path[k] = dup_by_path.get(k, 0) + int(r.get("n") or 0)
    ext_dup = _external_duplicates()
    for k, v in ext_dup.items():
        key = "recall:" + k
        dup_by_path[key] = dup_by_path.get(key, 0) + v
    special = _external_special_drops()

    corrections = total("correction") + total("supersession")
    kept, discarded = total("inference_kept"), total("inference_discarded")
    counts = {
        "recalls": len(recalls),
        "candidates": candidates,
        "shown": shown,
        "cited": cited,
        "corrections": corrections,
        "card_corrections": total("correction"),
        "supersessions": total("supersession"),
        "unsupersessions": total("unsupersession"),
        "inference_queued": total("inference_queued"),
        "inference_kept": kept,
        "inference_discarded": discarded,
        "duplicates_suppressed": sum(dup_by_path.values()),
        "special_category_dropped": sum(special.values()),
        "leakage_blocked": total("leakage_blocked"),
        "stale_errors": stale,
        "live_state_errors": live_err,
        "forgets": total("forget"),
        "recalled_items": len(recalled_refs),
        "recalled_items_later_corrected": corrected_recalled,
    }
    rates = {
        # Of the recalled items shown in turns whose reply was seen, how many
        # the reply cited.
        "citation_coverage": _rate(cited, cite_shown),
        # Corrections (owner card edits and deletes, and superseded turns) per
        # recalled item shown.
        "correction_rate": _rate(corrections, shown),
        "inference_acceptance_rate": _rate(kept, kept + discarded),
        # Distinct recalled items later superseded, per distinct item recalled
        # (None until a ref-bearing supersession exists in the window).
        "stale_error_rate": _rate(stale, len(recalled_refs)) if measurable else None,
        "live_state_error_rate": _rate(live_err, len(recalled_refs)) if measurable else None,
        # PROXY: 1 - (recalled items later corrected / recalled items). True
        # precision needs relevance labels this PC does not have.
        "precision_proxy": (round(1.0 - corrected_recalled / len(recalled_refs), 4)
                            if measurable else None),
        "shown_per_recall": _rate(shown, len(recalls)),
    }
    return {
        "ok": True,
        "days": days,
        "since": round(since, 3),
        "counts": counts,
        "rates": rates,
        "duplicates_by_path": dup_by_path,
        "special_category_by_path": special,
        "notes": {
            "content": "Counts only. No memory text, no ids (only short one-way hashes to match a "
                       "correction to an earlier recall) are stored.",
            "precision_proxy": "1 minus the share of recalled items later corrected. True precision "
                               "needs labelled relevance; the offline retrieval evaluation measures "
                               "recall@k and MRR on a fixed synthetic set. It reads 'not enough yet' "
                               "until a correction that names the recalled turns (a superseded "
                               "conversation turn) happens in the window: memory card edits do "
                               "not say which recalled turn they correct, so they cannot move it.",
            "stale_errors": f"Distinct recalled items superseded within {STALE_WINDOW_DAYS} days of "
                            "being recalled; a live-state error when a live source replaced it. "
                            "The rates read 'not enough yet' on the same condition as the "
                            "precision estimate.",
            "citations": "A citation counts when it names a recalled turn's ref, or quotes text "
                         "from a recalled snippet shown in the same turn (the snippet is held in "
                         "memory until the reply, never stored).",
            "leakage_blocked": "Cloud-bound fields (or recalled blocks within one) that held "
                               "recalled memory and were withheld; the reason for withholding "
                               "may be other text in the same field.",
            "since_start": "Recall near-duplicates (recall:*) and special-category drops are "
                           "counted since Friday started, not over the window.",
            "retention": f"Events are kept {RETENTION_DAYS} days on this PC.",
        },
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()
        _STATE.update(pruned=False, compact_retry_at=0.0)
