"""services/first_synthesis.py -- "What I see so far": the first, modest synthesis (BD-09 I3).

MASTER section 7.7 / North Star 9.2-9.3: while the long first job runs, a short pass shows the
owner AT MOST FIVE items, each with seven fields

    observation, why it may matter, source, freshness, confidence, related goal or commitment,
    and a proposed next step,

and the owner marks each one: useful / incorrect / not important / sensitive / remember this /
forget this / do not surface items like this.

This pass is deterministic and local: no model, no network. Every item is a count or a record
Friday already holds (imports waiting for review, open follow-ups and commitments, threads the
owner wrote last, conversations brought in from another assistant), so it prefers one modest,
correct observation over a dramatic guess (NS-9.2-3). Nothing here writes memory except the
owner's own "remember this", which goes to the memory card as the owner's words.

Files, all under ~/.friday/first_synthesis/:
    briefing.json   the items last shown (marks name an item by its id)
    marks.jsonl     one line per mark: item id, kind, mark, when, by (no item text)
    receipt.json    the local onboarding receipt: when each source first came on, when the
                    first mark was made, and the minutes between the second source and that
                    mark (MASTER 7.7: written here and nowhere else)
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: At most this many items (NS-9.2-1).
MAX_ITEMS = 5
#: The seven marks (NS-9.3-1), in the order the page shows them.
MARKS = ("useful", "incorrect", "not_important", "sensitive", "remember", "forget", "dont_surface")
MARK_LABELS = {
    "useful": "Useful", "incorrect": "Wrong", "not_important": "Not important",
    "sensitive": "Sensitive", "remember": "Remember this", "forget": "Forget this",
    "dont_surface": "Don't show things like this",
}
#: Marks after which the item is not shown again.
_HIDES_ITEM = frozenset({"incorrect", "not_important", "sensitive", "forget", "dont_surface"})
#: The seven fields every item carries.
FIELDS = ("observation", "why", "source", "freshness", "confidence", "related", "next_step")

_lock = threading.RLock()


def _home() -> Path:
    from agent_friday import paths
    return paths.friday_home()


def _dir() -> Path:
    d = _home() / "first_synthesis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _marks() -> List[dict]:
    p = _dir() / "marks.jsonl"
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        pass
    return out


def _iid(kind: str, key: str) -> str:
    return "syn-" + hashlib.sha256(("%s\x00%s" % (kind, key)).encode("utf-8")).hexdigest()[:16]


def _day(ts) -> str:
    try:
        if isinstance(ts, (int, float)) and ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        if isinstance(ts, str) and ts:
            return ts[:10]
    except (OverflowError, OSError, ValueError):
        pass
    return ""


def _freshness(ts) -> str:
    d = _day(ts)
    return ("as of " + d) if d else "as of today"


def _item(kind: str, key: str, *, observation: str, why: str, source: str, freshness: str,
          confidence: str, related: Optional[dict], next_step: str, open_view: str = "") -> dict:
    return {"id": _iid(kind, key), "kind": kind, "observation": observation[:240], "why": why[:240],
            "source": source[:120], "freshness": freshness[:40], "confidence": confidence,
            "related": related, "next_step": next_step[:160], "open": open_view}


def _plural(n: int, one: str, many: str) -> str:
    return "%d %s" % (n, one if n == 1 else many)


# ── candidates: each a record Friday already holds ─────────────────────────────

_SOURCE_NAMES = {"linkedin_csv": "your LinkedIn connections export", "chatgpt": "your ChatGPT export",
                 "claude": "your Claude export"}
#: The workspace where each source's waiting items are answered.
_REVIEW_IN = {"linkedin_csv": "contacts", "chatgpt": "settings", "claude": "settings"}


def _imports_waiting() -> List[dict]:
    from agent_friday.services import import_staging as st
    out = []
    by_source: Dict[str, List[dict]] = {}
    for it in st.list_items(status="open"):
        by_source.setdefault(str(it.get("source") or ""), []).append(it)
    for source, items in sorted(by_source.items()):
        held = [i for i in items if str(i.get("sensitivity") or "") in ("sensitive", "special")]
        rest = len(items) - len(held)
        name = _SOURCE_NAMES.get(source, source)
        newest = max((i.get("staged_ts") or 0) for i in items)
        if rest:
            out.append(_item(
                "imports_waiting", source,
                observation="%s from %s %s for your answer." % (
                    _plural(rest, "item", "items"), name, "waits" if rest == 1 else "wait"),
                why="Nothing from an import is kept until you accept it, so these are not yet part of what I know.",
                source=name + " (waiting for review)", freshness=_freshness(newest), confidence="certain",
                related=None, next_step="Look through them and accept, reject or leave them for later.",
                open_view=_REVIEW_IN.get(source, "settings")))
        if held:
            out.append(_item(
                "held_back", source,
                observation="I held back %s from %s that look sensitive." % (
                    _plural(len(held), "item", "items"), name),
                why="Sensitive material waits for a decision of its own; none of it is used or shown elsewhere.",
                source=name + " (held back)", freshness=_freshness(newest), confidence="certain",
                related=None, next_step="Decide whether any of them should be kept.",
                open_view=_REVIEW_IN.get(source, "settings")))
    return out


def _follow_ups() -> List[dict]:
    from agent_friday.services import relationship_memory as rm
    out = []
    for f in (rm.list_follow_ups("open") or [])[:3]:
        if not isinstance(f, dict):
            continue
        who = str(f.get("person") or "someone")
        note = str(f.get("note") or "").strip()
        due = str(f.get("due") or "")
        out.append(_item(
            "follow_up", str(f.get("id") or who + note),
            observation=("You meant to follow up with %s" % who) + (": " + note[:120] if note else "") + ".",
            why=("It is due " + due[:10] + "." if due else "It is still open."),
            source="your follow-ups", freshness=_freshness(f.get("created")), confidence="certain",
            related={"kind": "follow_up", "id": str(f.get("id") or "")[:60]},
            next_step="Write to %s, or close the follow-up if it is done." % who, open_view="contacts"))
    return out


def _commitments() -> List[dict]:
    from agent_friday.services import countdowns as cd
    out = []
    for c in ((cd.countdowns(kind="commitment") or {}).get("countdowns") or [])[:2]:
        if not isinstance(c, dict) or not c.get("label"):
            continue
        days = c.get("days")
        when = "today" if days == 0 else ("tomorrow" if days == 1 else "in %s days" % days)
        out.append(_item(
            "commitment", str(c.get("id") or c["label"]),
            observation="%s is due %s." % (str(c["label"])[:100], when),
            why=str(c.get("why") or "It has a date and it is coming up."),
            source=str(c.get("source") or "your commitments"), freshness="as of today", confidence="certain",
            related={"kind": "commitment", "id": str(c.get("id") or "")[:60]},
            next_step="Decide what the next step on it is.", open_view="calendar"))
    return out


def _cold_threads() -> List[dict]:
    from agent_friday.services import relationship_memory as rm
    out = []
    for t in (rm.cold_threads() or [])[:1]:
        if not isinstance(t, dict):
            continue
        who = str(t.get("name") or t.get("email") or "someone")
        subject = str(t.get("subject") or "").strip()
        out.append(_item(
            "cold_thread", "%s/%s" % (t.get("account_id"), t.get("thread_id")),
            observation="You wrote last to %s %s days ago%s, and there is no reply yet." % (
                who, t.get("days"), (' ("%s")' % subject[:80]) if subject else ""),
            why="A thread you started may be waiting on them, or on a nudge from you.",
            source="your mail headers", freshness=_freshness(t.get("at")), confidence="likely",
            related=None, next_step="Nudge %s, or let it go." % who, open_view="messages"))
    return out


def _imported_history() -> List[dict]:
    from agent_friday.services import conversations as cv
    by_source: Dict[str, List[dict]] = {}
    for c in cv.list_all():
        imp = c.get("imported") if isinstance(c.get("imported"), dict) else None
        if imp and imp.get("source"):
            by_source.setdefault(str(imp["source"]), []).append(imp)
    out = []
    for source, rows in sorted(by_source.items()):
        oldest = min((r.get("created") for r in rows if isinstance(r.get("created"), (int, float))), default=None)
        out.append(_item(
            "imported_history", source,
            observation="You brought in %s from %s%s." % (
                _plural(len(rows), "conversation", "conversations"), _SOURCE_NAMES.get(source, source),
                (", the oldest from " + _day(oldest)) if oldest else ""),
            why="I can find things in them when you ask; they stay marked as another assistant's words.",
            source=_SOURCE_NAMES.get(source, source), freshness=_freshness(oldest), confidence="certain",
            related=None, next_step="Ask me about one of them, or delete any you do not want kept.",
            open_view="knowledge"))
    return out


#: In the order they are tried: what needs the owner first, then what is coming up.
_CANDIDATES = (_imports_waiting, _commitments, _follow_ups, _cold_threads, _imported_history)


def _hidden() -> tuple[set, set]:
    items, kinds = set(), set()
    for m in _marks():
        if m.get("mark") in _HIDES_ITEM:
            items.add(m.get("item"))
        if m.get("mark") == "dont_surface":
            kinds.add(m.get("kind"))
    return items, kinds


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return False


def build(save: bool = True) -> Dict[str, Any]:
    """The briefing: at most five items, each with the seven fields, and what could not be read.

    Off the record nothing is read or saved."""
    if _off_record():
        return {"ok": True, "items": [], "off_record": True, "marks": list(MARKS)}
    hidden_items, hidden_kinds = _hidden()
    items: List[dict] = []
    failed: List[str] = []
    for fn in _CANDIDATES:
        try:
            found = fn()
        except Exception:
            failed.append(fn.__name__.strip("_"))
            continue
        for it in found:
            if it["id"] in hidden_items or it["kind"] in hidden_kinds:
                continue
            if any(x["id"] == it["id"] for x in items):
                continue
            items.append(it)
    try:
        from agent_friday.services import forget_person as fp
        tomb = [t.lower() for t in list(fp.forgotten_names()) + list(fp.forgotten_emails()) if str(t).strip()]
    except Exception:
        tomb = []
    if tomb:
        items = [it for it in items
                 if not any(t in " ".join(str(it.get(k) or "") for k in FIELDS).lower() for t in tomb)]
    items = items[:MAX_ITEMS]
    with _lock:
        _note_sources()
        if save:
            _write(_dir() / "briefing.json", {"generated_ts": time.time(), "items": items})
    marked = {m.get("item"): m.get("mark") for m in _marks()}
    for it in items:
        it["marked"] = marked.get(it["id"])
    return {"ok": True, "items": items, "failed": failed, "marks": list(MARKS),
            "mark_labels": MARK_LABELS, "receipt": receipt()}


# ── marks ─────────────────────────────────────────────────────────────────────

def mark(item_id: str, answer: str, *, by: str = "owner") -> Dict[str, Any]:
    """The owner's mark on one item of the last briefing. "remember" saves the observation to
    the memory card as the owner's words; nothing else writes memory."""
    answer = str(answer or "")
    if answer not in MARKS:
        return {"ok": False, "error": "unknown mark"}
    if _off_record():
        return {"ok": False, "error": "Off the record is on, so nothing is marked or remembered."}
    with _lock:
        shown = (_read(_dir() / "briefing.json", {}) or {}).get("items") or []
        it = next((x for x in shown if isinstance(x, dict) and x.get("id") == str(item_id or "")), None)
        if it is None:
            return {"ok": False, "error": "That item is no longer shown; refresh the list."}
        said = ""
        if answer == "remember":
            from agent_friday.services import core_memory
            try:
                res = core_memory.add("user", it.get("observation") or "", actor="owner")
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            if not (isinstance(res, dict) and res.get("ok")):
                why = str((res or {}).get("error") or "the memory card refused it")[:160]
                return {"ok": False, "error": "It could not be remembered: " + why}
            said = "Remembered on your memory card, where you can change or delete it."
        elif answer == "forget":
            said = "I will not show this again. The record it came from is unchanged; delete it there if you want it gone."
        elif answer == "dont_surface":
            said = "I will not show things like this here again."
        elif answer == "sensitive":
            said = "Marked sensitive; it is not shown here again."
        row = {"ts": time.time(), "item": it["id"], "kind": it.get("kind"), "mark": answer, "by": str(by)[:40]}
        with open(_dir() / "marks.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        _note_first_mark(row["ts"])
    return {"ok": True, "mark": answer, "said": said or "Thank you; noted.", "receipt": receipt()}


def reviewed() -> bool:
    """True once the owner has marked at least one item (section 9.6: reviewed one synthesis)."""
    return bool(_marks())


def corrections() -> int:
    """Marks that confirm or correct what Friday holds: remember (confirmed) and incorrect."""
    return sum(1 for m in _marks() if m.get("mark") in ("remember", "incorrect"))


# ── forgetting a person (services/forget_registry) ────────────────────────────

def _naming(names, emails) -> List[dict]:
    terms = [str(t).lower() for t in list(names or []) + list(emails or []) if str(t).strip()]
    shown = (_read(_dir() / "briefing.json", {}) or {}).get("items") or []
    return [it for it in shown if isinstance(it, dict)
            and any(t in " ".join(str(it.get(k) or "") for k in FIELDS).lower() for t in terms)]


def forget_find(names, emails) -> int:
    return len(_naming(names, emails))


def forget_purge(names, emails) -> int:
    """Drops shown items that name the person. The marks log holds no text, so it stays."""
    with _lock:
        gone = {it["id"] for it in _naming(names, emails)}
        if not gone:
            return 0
        p = _dir() / "briefing.json"
        b = _read(p, {}) or {}
        b["items"] = [it for it in (b.get("items") or []) if isinstance(it, dict) and it.get("id") not in gone]
        _write(p, b)
    return len(gone)


# ── the local onboarding receipt (MASTER 7.7) ─────────────────────────────────

def _source_times() -> Dict[str, float]:
    """When each source first came on, from the records that hold it."""
    out: Dict[str, float] = {}
    try:
        from agent_friday.services import google_accounts as ga
        for a in ga.list_accounts() or []:
            ts = _iso_ts(a.get("created"))
            if ts:
                out["google:" + str(a.get("id") or a.get("email") or "")[:60]] = ts
    except Exception:
        pass
    try:
        from agent_friday.services.library import grants
        for s in grants.active_scopes("owner", expand=False) or []:
            if isinstance(s.get("created_ts"), (int, float)):
                out["library:" + str(s.get("id"))[:60]] = float(s["created_ts"])
    except Exception:
        pass
    try:
        from agent_friday.services import import_staging as st
        for it in st.list_items():
            ts = it.get("staged_ts") or it.get("reviewed_ts")
            if isinstance(ts, (int, float)):
                key = "import:" + str(it.get("source") or "")
                out[key] = min(out.get(key, ts), float(ts))
    except Exception:
        pass
    return out


def _iso_ts(value) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _note_sources() -> None:
    p = _dir() / "receipt.json"
    r = _read(p, {}) or {}
    seen = r.get("sources") if isinstance(r.get("sources"), dict) else {}
    changed = False
    for k, ts in _source_times().items():
        if k not in seen:
            seen[k] = ts
            changed = True
    if changed:
        r["sources"] = seen
        _finish(r)
        _write(p, r)


def _note_first_mark(ts: float) -> None:
    p = _dir() / "receipt.json"
    r = _read(p, {}) or {}
    if not r.get("first_mark_ts"):
        r["first_mark_ts"] = ts
        _finish(r)
        _write(p, r)


def _finish(r: dict) -> None:
    times = sorted(v for v in (r.get("sources") or {}).values() if isinstance(v, (int, float)))
    if len(times) >= 2:
        r["second_source_ts"] = times[1]
    first = r.get("first_mark_ts")
    if r.get("second_source_ts") and first and first >= r["second_source_ts"] and "minutes" not in r:
        r["minutes"] = round((first - r["second_source_ts"]) / 60.0, 1)


def receipt() -> Dict[str, Any]:
    r = _read(_dir() / "receipt.json", {}) or {}
    return {"sources": len(r.get("sources") or {}), "second_source_ts": r.get("second_source_ts"),
            "first_mark_ts": r.get("first_mark_ts"), "minutes": r.get("minutes")}
