"""The owner's memory export (north star NS-15.11-1, the knowledge workspace's
"export").

``build()`` gathers what the owner's memory search shows them, ready to hand
over as a file they keep:

* with a query, the same results the search panel shows for it (the same
  kinds, dates and "show corrected" switch, through services/memory_search);
* with no query, the whole of what Friday keeps about the owner, kind by
  kind: every turn of every stored conversation, every memory card line, the
  card's review queue, user-model facts, the Memory keeper's suggestions
  (pending or kept) and Library titles. At most ``EXPORT_MAX`` items; past
  that the export says it was cut short.

Each item is ``kind``, ``text``, ``when``, ``source`` (where it came from, in
the same words the search panel uses) and ``status`` (``current``,
``superseded``, ``pending`` or ``inferred``), with ``status_reason`` when there
is one. ``to_json()`` and ``to_markdown()`` write the file's text (Markdown
grouped by kind); the route streams it as an attachment and nothing is written
to disk on this PC.

Two things never reach an export:

* anything that names a person the owner asked Friday to forget, or carries
  one of their addresses: the same whole-item check the search applies
  (memory_search._forgotten_filter);
* a key or password: every string goes through services/pasted_secrets
  (``scrub``) on its way out, whatever store it came from.

Off-record turns are never stored, and the ones held for this session only
(services/off_record) are not exported. Nothing here logs a query or an item.

Leaf module: no Flask; never raises to a caller.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from agent_friday.services import memory_search as ms

#: The most items one export carries (the whole memory can be large; the file stays a size a person can open).
EXPORT_MAX = 5000
FORMATS = ("json", "md")

#: Heading for each kind in the Markdown file, in the order the search panel lists them.
SECTIONS = (
    ("conversation", "Conversations"),
    ("card", "Memory card"),
    ("card_change", "Memory card: corrected lines"),
    ("suggestion", "Waiting for your review"),
    ("fact", "Facts"),
    ("proposal", "Suggested facts"),
    ("document", "Library"),
)
_STATUS_WORDS = {"current": "Current", "superseded": "Corrected", "pending": "Waiting for your review",
                 "inferred": "Inferred, not confirmed by you"}


class BadRequest(ValueError):
    """A parameter outside its bounds; the message is safe to show."""


# ── parameters ────────────────────────────────────────────────────────────────
def check_params(query: Any, kinds: Any = None, since: Any = None, until: Any = None,
                 fmt: Any = None) -> Tuple[str, Tuple[str, ...], Optional[str], Optional[str], str]:
    """Normalised (query, kinds, since, until, format), or BadRequest. The query may be empty (the whole
    memory); otherwise it, the kinds and the dates are held to exactly the search's bounds."""
    f = str(fmt or "json").strip().lower()
    if f == "markdown":
        f = "md"
    if f not in FORMATS:
        raise BadRequest("format is json or md.")
    q = " ".join(str(query or "").split())
    try:
        # The search's own check, so an export can never ask for more than a search could. With no query
        # it still checks the kinds and the dates (a stand-in query that is never used).
        _, ks, s, u, _ = ms.check_params(q or "all", kinds, since, until, None)
    except ms.BadRequest as e:
        raise BadRequest(str(e))
    return q, ks, s, u, f


# ── the whole memory, kind by kind ────────────────────────────────────────────
def _stored_messages(cid: str) -> List[Dict[str, Any]]:
    """The turns kept on disk for one conversation: never this session's off-record ones."""
    from agent_friday.services import conversations as cv
    fn = getattr(cv, "_stored_messages", None)
    if fn is not None:
        return fn(cid)
    from agent_friday.services import off_record
    held = {m.get("id") for m in off_record.recalled(cid)}
    return [m for m in cv.messages(cid) if m.get("id") not in held]


def _all_conversations(lo, hi, include_superseded, budget, notes) -> List[Dict[str, Any]]:
    from agent_friday.services import conversations as cv
    out: List[Dict[str, Any]] = []
    try:
        convs = cv.list_all(include_archived=True)
    except Exception:
        notes.append("Conversations are not available right now.")
        return out
    for c in convs:
        cid = c.get("id")
        if not cid or len(out) > budget:
            continue
        title = c.get("title") or ""
        try:
            msgs = _stored_messages(cid)
        except Exception:
            continue
        for m in msgs:
            text, role = m.get("text"), m.get("role")
            if not isinstance(text, str) or not text.strip() or role not in ("user", "assistant", "friday"):
                continue
            try:
                ts = float(m.get("ts")) if m.get("ts") else None
            except (TypeError, ValueError):
                ts = None
            if not ms._in_window(ts, lo, hi):
                continue
            date = ms._when(ts)
            out.append({"kind": "conversation", "id": str(m.get("id") or ""), "text": text, "ts": ts,
                        "when": date,
                        "source": f"Said by {ms._said_by(role)} in “{title or 'a conversation'}” on {date[:10]}",
                        "status": "current", "status_reason": ""})
            if len(out) > budget:
                break       # one past the budget, so the export knows it was cut short
    # Which of them were corrected is kept with the vector memory.
    try:
        mem = ms._memory()
        if mem is not None and mem.available() and out:
            by_id = {r["id"]: r for r in out if r["id"]}
            ids = list(by_id)
            for i in range(0, len(ids), 200):
                for g in mem.get(ids[i:i + 200]):
                    r = by_id.get(g.get("id"))
                    if r is not None and g.get("superseded"):
                        r["status"] = "superseded"
                        r["status_reason"] = g.get("superseded_reason") or ""
    except Exception:
        notes.append("Which turns were corrected is not available right now.")
    if len(out) > budget:
        notes.append(f"Conversations stop at the export's limit of {EXPORT_MAX} items; there are more.")
    if not include_superseded:
        out = [r for r in out if r["status"] != "superseded"]
    return out


def _all_card(lo, hi, include_superseded) -> List[Dict[str, Any]]:
    from agent_friday.services import core_memory as cm
    out = []
    for t in cm.TARGETS:
        for line in cm.entries(t):
            rows = cm.journal_for(line, target=t)
            row = rows[-1] if rows else None
            ts = float(row.get("ts")) if row and row.get("ts") else None
            if (lo is not None or hi is not None) and not ms._in_window(ts, lo, hi):
                continue
            card = "About you" if t == "user" else "Working with you"
            out.append({"kind": "card", "id": f"card:{t}:{cm.line_ref(line)}", "text": line, "ts": ts,
                        "when": ms._when(ts), "source": f"{card}: {ms._card_label(row)}",
                        "status": "current", "status_reason": ""})
    if include_superseded:
        current = {e.lower() for t in cm.TARGETS for e in cm.entries(t)}
        for r in cm.journal_rows():
            before = r.get("before") or ""
            if r.get("actor") == "owner" or r.get("op") not in ("replace", "remove") or not before:
                continue
            if r.get("undone") or before.lower() in current:
                continue
            ts = float(r.get("ts") or 0) or None
            if not ms._in_window(ts, lo, hi):
                continue
            reason = (f"{ms._who(r.get('actor'))} changed it to “{r.get('after')}” on {ms._when(ts)[:10]}"
                      if r.get("op") == "replace" else f"{ms._who(r.get('actor'))} removed it on {ms._when(ts)[:10]}")
            out.append({"kind": "card_change", "id": f"card_change:{r.get('id')}", "text": before, "ts": ts,
                        "when": ms._when(ts), "source": "Was on the memory card",
                        "status": "superseded", "status_reason": reason})
    return out


def _all_suggestions(lo, hi) -> List[Dict[str, Any]]:
    from agent_friday.services import core_memory as cm
    out = []
    for p in cm.pending():
        ts = float(p.get("ts") or 0) or None
        if not ms._in_window(ts, lo, hi):
            continue
        op, text = p.get("op"), p.get("text") or ""
        if op == "remove":
            text = "Remove: “" + (p.get("old") or "") + "”"
        elif op == "replace":
            text = "Change “" + (p.get("old") or "") + "” to “" + text + "”"
        out.append({"kind": "suggestion", "id": str(p.get("id")), "text": text, "ts": ts, "when": ms._when(ts),
                    "source": f"Suggested by {ms._from(p.get('source'))}",
                    "status": "pending", "status_reason": "Waiting for your Keep on the memory card"})
    return out


def _all_facts(lo, hi, budget) -> List[Dict[str, Any]]:
    from agent_friday.services import user_model
    out = []
    for f in user_model.all_facts(limit=max(1, budget + 1)):
        ts = float(f.get("ts") or 0) or None
        if not ms._in_window(ts, lo, hi):
            continue
        src = str(f.get("source") or "")
        confirmed = src.startswith(ms.CONFIRMED_SOURCES)
        out.append({"kind": "fact", "id": str(f.get("fact_id")), "text": f.get("text") or "", "ts": ts,
                    "when": ms._when(ts), "source": ms._fact_source(src),
                    "status": "current" if confirmed else "inferred",
                    "status_reason": "" if confirmed else "Friday concluded this; you have not confirmed it"})
    return out


def _all_proposals(lo, hi, budget) -> List[Dict[str, Any]]:
    from agent_friday.services import memory_proposals as mp
    out = []
    for p in mp.rows(("pending", "approved"), limit=max(1, min(2000, budget + 1))):
        ts = float(p.get("ts") or 0) or None
        if not ms._in_window(ts, lo, hi):
            continue
        meeting = (p.get("provenance") or {}).get("meeting_id")
        pending = p.get("status") == "pending"
        out.append({"kind": "proposal", "id": str(p.get("fact_id")), "text": p.get("text") or "", "ts": ts,
                    "when": ms._when(ts),
                    "source": (f"Suggested from a meeting on {p.get('day')}" if meeting
                               else f"Suggested from your conversations on {p.get('day')}"),
                    "status": "pending" if pending else "current",
                    "status_reason": ("Waiting for your review" if pending
                                      else f"You approved it on {ms._when(p.get('decided_ts'))[:10]}")})
    return out


def _all_documents() -> List[Dict[str, Any]]:
    from agent_friday.services.library.store import OWNER, store_for
    from agent_friday.services.library.tree import TreeBuilder
    out = []
    for _k, r in TreeBuilder(store_for(OWNER), OWNER).visible_docs().items():
        out.append({"kind": "document", "id": str(r.get("id")), "text": r.get("title") or "", "ts": None,
                    "when": "", "source": "A document in your Library",
                    "status": "current", "status_reason": ""})
    return out


def _whole(ks, since, until, include_superseded, notes) -> List[Dict[str, Any]]:
    lo, hi = ms._day_ts(since), ms._day_ts(until, end=True)
    items: List[Dict[str, Any]] = []
    for k in ks:
        budget = EXPORT_MAX - len(items)
        if budget < 0:
            break
        try:
            if k == "conversation":
                items.extend(_all_conversations(lo, hi, include_superseded, budget, notes))
            elif k == "card":
                items.extend(_all_card(lo, hi, include_superseded))
            elif k == "suggestion":
                items.extend(_all_suggestions(lo, hi))
            elif k == "fact":
                items.extend(_all_facts(lo, hi, budget))
            elif k == "proposal":
                items.extend(_all_proposals(lo, hi, budget))
            elif k == "document":
                if since or until:
                    notes.append("Library documents have no date here, so a date filter leaves them out.")
                    continue
                items.extend(_all_documents())
        except Exception:
            notes.append(f"Could not read {k} memory right now.")
    return items


def _from_search(q, ks, since, until, include_superseded, notes) -> List[Dict[str, Any]]:
    res = ms.search(q, kinds=ks, since=since, until=until, include_superseded=include_superseded,
                    limit=ms.LIMIT_MAX)
    if not res.get("ok"):
        notes.append(res.get("error") or "Could not search memory right now.")
        return []
    notes.extend(n for n in res.get("notes") or [] if n not in notes)
    out = []
    for r in res.get("results") or []:
        src = r.get("source") or {}
        out.append({"kind": r.get("kind"), "id": r.get("id"), "text": r.get("text") or "", "ts": r.get("ts"),
                    "when": r.get("when") or "", "source": (src.get("label") if isinstance(src, dict) else str(src)) or "",
                    "status": r.get("status") or "current", "status_reason": r.get("status_reason") or ""})
    return out


# ── build ─────────────────────────────────────────────────────────────────────
def build(query: str = "", kinds=None, since: Optional[str] = None, until: Optional[str] = None,
          include_superseded: bool = False) -> Dict[str, Any]:
    """The export's contents: {ok, query, kinds, items, count, truncated, notes, exported}."""
    try:
        q, ks, since, until, _ = check_params(query, kinds, since, until, "json")
    except BadRequest as e:
        return {"ok": False, "error": str(e)}
    notes: List[str] = []
    try:
        items = (_from_search(q, ks, since, until, include_superseded, notes) if q
                 else _whole(ks, since, until, include_superseded, notes))
    except Exception:
        return {"ok": False, "error": "Could not gather your memory right now."}
    # A forgotten person anywhere in an item (its text, where it came from, why it was corrected) drops it.
    names = ms._forgotten_filter()
    kept = [r for r in items if not names(r)]
    if len(kept) != len(items):
        notes.append("Items that name someone you asked Friday to forget are left out.")
    if not q:
        order = {k: i for i, (k, _) in enumerate(SECTIONS)}
        kept.sort(key=lambda r: (order.get(r.get("kind"), 99), -float(r.get("ts") or 0)))
    truncated = len(kept) > EXPORT_MAX
    kept = kept[:EXPORT_MAX]
    if truncated:
        notes.append(f"This export holds the first {EXPORT_MAX} items; there are more.")
    from agent_friday.services import pasted_secrets
    clean = []
    for r in kept:
        row = {"kind": r.get("kind"), "text": r.get("text") or "", "when": r.get("when") or "",
               "source": r.get("source") or "", "status": r.get("status") or "current"}
        if r.get("status_reason"):
            row["status_reason"] = r["status_reason"]
        # Keys and passwords are withheld in every string, whatever store the item came from.
        clean.append(pasted_secrets.scrub(row))
    return {"ok": True, "query": pasted_secrets.scrub(q), "kinds": list(ks),
            "include_superseded": bool(include_superseded), "items": clean, "count": len(clean),
            "truncated": truncated, "notes": notes,
            "exported": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ── writers ───────────────────────────────────────────────────────────────────
def filename(fmt: str) -> str:
    return "friday-memory-%s.%s" % (datetime.now().strftime("%Y-%m-%d"), "md" if fmt == "md" else "json")


def to_json(exp: Dict[str, Any]) -> Iterator[str]:
    """The JSON file, a piece at a time (one item per piece)."""
    head = {"app": "Agent Friday", "what": "What Friday keeps about you", "exported": exp.get("exported"),
            "query": exp.get("query") or "", "kinds": exp.get("kinds") or [],
            "include_corrected": bool(exp.get("include_superseded")), "count": exp.get("count", 0),
            "truncated": bool(exp.get("truncated")), "notes": exp.get("notes") or []}
    yield json.dumps(head, ensure_ascii=False, indent=1)[:-2] + ',\n "items": [\n'
    items = exp.get("items") or []
    for i, r in enumerate(items):
        yield "  " + json.dumps(r, ensure_ascii=False) + (",\n" if i < len(items) - 1 else "\n")
    yield " ]\n}\n"


def _md_line(text: str) -> str:
    # One list item per memory: inner line breaks become indented continuation lines.
    return "\n  ".join(str(text or "").splitlines() or [""])


def to_markdown(exp: Dict[str, Any]) -> Iterator[str]:
    """The Markdown file, grouped by kind, a piece at a time."""
    yield "# What Friday keeps about you\n\n"
    yield f"Exported {exp.get('exported')} from this PC.\n"
    if exp.get("query"):
        yield f"These are the results of a search for: {exp.get('query')}\n"
    else:
        yield "This is everything Friday keeps about you, kind by kind.\n"
    yield f"{exp.get('count', 0)} item{'s' if exp.get('count', 0) != 1 else ''}.\n"
    for n in exp.get("notes") or []:
        yield f"\n> {n}\n"
    items = exp.get("items") or []
    for kind, title in SECTIONS:
        rows = [r for r in items if r.get("kind") == kind]
        if not rows:
            continue
        yield f"\n## {title}\n\n"
        for r in rows:
            yield f"- {_md_line(r.get('text'))}\n"
            meta = [x for x in (r.get("when"), r.get("source"), _STATUS_WORDS.get(r.get("status"), r.get("status")))
                    if x]
            if r.get("status_reason"):
                meta.append(r["status_reason"])
            yield f"  - {_md_line(' · '.join(meta))}\n"
