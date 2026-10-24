"""How well memory is doing, measured locally, in counts only (north star
§15.12-1: precision, usefulness, stale errors, citation, corrections, leakage
attempts, duplicates, unconfirmed inference, live-state errors).

Nothing here ever records what a memory says. An event is a kind, a few small
numbers and, at most, a short label from a fixed vocabulary (which store,
which path). Where two events must be matched (a recalled item that is later
corrected), the item's id is kept as a short one-way hash, never the id and
never the text. The store is one append-only JSONL file under the Friday home
(``runtime/memory_metrics.jsonl``), compacted to ``RETENTION_DAYS`` when it
grows past ``MAX_BYTES``. Off the record nothing is written.

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
  leakage_blocked           where, n (the egress gate withheld recalled memory
                            from a cloud call)
  forget                    store, n

Counters other modules keep in their own process (recall near-duplicates in
services/recall_dedupe, special-category drops in services/sensitive_categories)
are read when those modules exist; absent, they read as zero.

``summary(days)`` turns the events into rates. "Precision" here is a PROXY:
the share of recalled items that were later corrected, subtracted from one.
True precision needs a person to label whether each recalled item was relevant
and right; nothing on this PC has those labels, so the proxy is a floor on
error, not a measure of relevance (offline relevance is measured by
services/memory_retrieval_eval on a fixed synthetic set).

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

#: Recall shown in a session, kept in memory only until the reply is seen.
PENDING_TTL_S = 3600.0
PENDING_MAX = 64

_LOCK = threading.Lock()
_PENDING: "OrderedDict[str, tuple]" = OrderedDict()

_CITE_RE = re.compile(r"\[(?:conversation|memory):[^\]/]*/([^\]\s\"/]{4,64})\]", re.I)


def _path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "runtime" / "memory_metrics.jsonl"


def ref_hash(item_id: Any) -> str:
    """A short one-way hash of an item id: enough to match a later correction to
    an earlier recall, nothing that reads back to the id or its text."""
    return hashlib.sha256(("memory-metrics:" + str(item_id)).encode("utf-8")).hexdigest()[:12]


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return False


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


def _compact(p: Path) -> None:
    cutoff = time.time() - RETENTION_DAYS * 86400
    rows = [r for r in _read(p) if float(r.get("ts") or 0) >= cutoff]
    keep = rows
    while keep and sum(len(json.dumps(r)) + 1 for r in keep) > MAX_BYTES // 2:
        keep = keep[len(keep) // 4 or 1:]
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    os.replace(tmp, p)


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
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            try:
                if p.stat().st_size > MAX_BYTES:
                    _compact(p)
            except Exception:
                pass
    except Exception:
        pass


# ── hooks ─────────────────────────────────────────────────────────────────────
def note_recall(session_id: Any, candidates: int, shown_ids: Iterable[Any]) -> None:
    """The memory block of a turn: how many came back from search, how many were
    shown. The shown ids' short refs wait (in memory) for the reply's citations."""
    try:
        ids = [str(i) for i in (shown_ids or []) if i is not None and str(i)]
        record("recall", candidates=int(candidates or 0), shown=len(ids),
               refs=[ref_hash(i) for i in ids])
        if ids and session_id:
            with _LOCK:
                _PENDING[str(session_id)] = (time.monotonic(), {i[:12] for i in ids})
                _PENDING.move_to_end(str(session_id))
                while len(_PENDING) > PENDING_MAX:
                    _PENDING.popitem(last=False)
    except Exception:
        pass


def note_reply(session_id: Any, reply: Any) -> None:
    """The reply to a turn that recalled memory: how many recalled items it cited
    as [conversation:DATE/REF] (or [memory:...]). Only the count is kept."""
    try:
        with _LOCK:
            hit = _PENDING.pop(str(session_id or ""), None)
        if not hit or time.monotonic() - hit[0] > PENDING_TTL_S:
            return
        shown = hit[1]
        cited = set()
        for m in _CITE_RE.finditer(str(reply or "")):
            tok = m.group(1).strip()
            for ref in shown:
                if tok[:12] == ref or (len(tok) >= 6 and ref.startswith(tok)):
                    cited.add(ref)
        record("citation", shown=len(shown), cited=len(cited))
    except Exception:
        pass


def note_supersession(ids: Iterable[Any], on: bool, superseded_by: str = "") -> None:
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
    rows = _read()
    win = [r for r in rows if float(r.get("ts") or 0) >= since]

    def total(kind: str, field: str = "n") -> int:
        return sum(int(r.get(field) or 0) for r in win if r.get("kind") == kind)

    recalls = [r for r in win if r.get("kind") == "recall"]
    shown = sum(int(r.get("shown") or 0) for r in recalls)
    candidates = sum(int(r.get("candidates") or 0) for r in recalls)
    cite_rows = [r for r in win if r.get("kind") == "citation"]
    cited = sum(int(r.get("cited") or 0) for r in cite_rows)
    cite_shown = sum(int(r.get("shown") or 0) for r in cite_rows)

    # Stale and live-state errors: a recalled item superseded within the window
    # after it was recalled. Recall rows are read from the whole retained file so
    # a correction early in the window still finds a recall just before it.
    last_recall: Dict[str, List[float]] = {}
    for r in rows:
        if r.get("kind") == "recall":
            for ref in r.get("refs") or []:
                last_recall.setdefault(ref, []).append(float(r.get("ts") or 0))
    stale = live_err = 0
    corrected_refs = set()
    for r in win:
        if r.get("kind") != "supersession":
            continue
        t = float(r.get("ts") or 0)
        for ref in r.get("refs") or []:
            times = last_recall.get(ref) or []
            if any(t - STALE_WINDOW_DAYS * 86400 <= rt <= t for rt in times):
                corrected_refs.add(ref)
                if int(r.get("live") or 0):
                    live_err += 1
                else:
                    stale += 1
    recalled_refs = {ref for r in recalls for ref in (r.get("refs") or [])}
    corrected_recalled = len(corrected_refs & recalled_refs) if recalled_refs else 0

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
        "stale_error_rate": _rate(stale + live_err, len(recalled_refs)),
        # PROXY: 1 - (recalled items later corrected / recalled items). True
        # precision needs relevance labels this PC does not have.
        "precision_proxy": (round(1.0 - corrected_recalled / len(recalled_refs), 4)
                            if recalled_refs else None),
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
                               "recall@k and MRR on a fixed synthetic set.",
            "stale_errors": f"A recalled item superseded within {STALE_WINDOW_DAYS} days of being "
                            "recalled; a live-state error when a live source replaced it.",
            "since_start": "Recall near-duplicates (recall:*) and special-category drops are "
                           "counted since Friday started, not over the window.",
            "retention": f"Events are kept {RETENTION_DAYS} days on this PC.",
        },
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()
