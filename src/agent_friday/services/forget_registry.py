"""Forget means forget: every store that names a person registers here.

`forget_person.forget()` used to purge a hard-coded list of files and
missed the ones added since: the contacts research notes, the learned
sender signals, and now the trust evidence log. A store registers a finder
and a purger once; forgetting a person then asks every registered store,
and a store that is missing from this registry is a bug a guard can catch
rather than a silent survivor.

A store entry is ``(name, find_fn, purge_fn)`` where both functions take
``(names, emails)`` (normalised lowercase names and aliases, and lowercase
emails) and return a count. A store that fails reports its error in the
receipt instead of stopping the others.

THE DELETION RECEIPT (north star NS-15.7-3)
-------------------------------------------
``purge_all_detailed`` gives one row per store: what was removed (a count and
what kind of thing, never the content), what could not be removed and why,
and what is kept on purpose and why (retained for integrity: the owner's own
transcript, an append-only record). A purge function may return a plain count
or a dict ``{"removed", "kind", "not_removable", "retained", "also"}`` where
``also`` is a list of further rows (one purge reaching several stores in a
fixed order, e.g. the recall indexes before the summaries made from them).

NAME MATCHING IN FREE TEXT (``subject_matcher``)
-----------------------------------------------
Stores that hold sentences rather than records (conversation turns, day
summaries, memory-card lines) match the person's name case-sensitively, as a
whole word: the name as the owner gave it (the tombstone keeps it) or in
title case. Forgetting "Will" removes "Will said..." and never "I will call".
The case-insensitive whole-word rule the record stores use
(``forget_person.redact_forgotten_names``, memory_proposals) would remove
every ordinary "will", and a turn removed is a turn the owner cannot get back
into recall. What this cannot tell apart: the same name used as a word at the
start of a sentence ("Will you...?"), and a different person with the same
name. Names shorter than three characters are not matched in free text at
all and the receipt says so.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Tuple

_LOCK = threading.Lock()
_STORES: Dict[str, Tuple[Callable, Callable]] = {}
_DEFAULTS_LOADED = False

#: Shortest name matched inside free text (see the module docstring).
MIN_NAME = 3

#: What each store's count is a count of, for the receipt.
KINDS = {
    "contacts_research": "research notes",
    "sender_signals": "learned sender signals",
    "trust_log_people": "trust evidence events",
    "library": "Library passages and staged drafts",
    "laya_personal": "items Laya learned from",
    "meetings": "meeting records",
    "memory_proposals": "memory suggestions",
}


def register_store(name: str, find_fn: Callable, purge_fn: Callable) -> None:
    with _LOCK:
        _STORES[str(name)] = (find_fn, purge_fn)


def registered() -> List[str]:
    _ensure_defaults()
    with _LOCK:
        return sorted(_STORES)


def _count(value) -> int:
    if isinstance(value, dict):
        return int(value.get("removed") or 0)
    return int(value or 0)


def find_all(names, emails) -> Dict[str, int]:
    _ensure_defaults()
    out = {}
    for name, (find_fn, _purge) in list(_STORES.items()):
        try:
            out[name] = _count(find_fn(set(names), set(emails)))
        except Exception as e:
            out[name] = -1
            out[name + "_error"] = str(e)[:120]
    return out


def _row(store: str, value=None, error: str = "") -> Dict[str, Any]:
    """One receipt row. Never carries the removed content."""
    row = {"store": store, "removed": 0, "kind": KINDS.get(store, "records"),
           "not_removable": [], "retained": []}
    if error:
        row["not_removable"].append({"what": "this store", "reason": error})
        return row
    if isinstance(value, dict):
        row["removed"] = int(value.get("removed") or 0)
        row["kind"] = str(value.get("kind") or row["kind"])
        row["not_removable"] = [dict(x) for x in (value.get("not_removable") or [])]
        row["retained"] = [dict(x) for x in (value.get("retained") or [])]
    else:
        row["removed"] = int(value or 0)
    if store == "trust_log_people" and row["removed"] > 0:
        row["retained"].append({
            "what": "one marker in the trust log that carries only a count", "count": 1,
            "reason": "the log is hash-chained; the marker keeps the chain verifiable"})
    return row


def purge_all_detailed(names, emails) -> List[Dict[str, Any]]:
    """Purge every store; one receipt row per store (and per `also` row)."""
    _ensure_defaults()
    rows: List[Dict[str, Any]] = []
    for name, (_find, purge_fn) in list(_STORES.items()):
        try:
            value = purge_fn(set(names), set(emails))
        except Exception as e:
            rows.append(dict(_row(name, error=str(e)[:120]), error=str(e)[:120]))
            continue
        rows.append(_row(name, value))
        if isinstance(value, dict):
            for extra in value.get("also") or []:
                if isinstance(extra, dict) and extra.get("store"):
                    rows.append(_row(str(extra["store"]), extra))
    return rows


def counts(rows) -> Dict[str, Any]:
    """The old `{store: count, store_error: text}` shape from receipt rows."""
    out: Dict[str, Any] = {}
    for r in rows:
        if r.get("error"):
            out[r["store"]] = -1
            out[r["store"] + "_error"] = r["error"]
        else:
            out[r["store"]] = int(r.get("removed") or 0)
    return out


def purge_all(names, emails) -> Dict[str, int]:
    return counts(purge_all_detailed(names, emails))


# ── matching a name inside free text ─────────────────────────────────────────

def _given_forms(names) -> set:
    """The names as the owner gave them (tombstone), for the lowercased `names`."""
    wanted = {_norm(n) for n in names}
    out = set()
    try:
        from agent_friday.services import forget_person as fp
        for entry in fp.list_forgotten():
            if not isinstance(entry, dict):
                continue
            for n in [entry.get("name", "")] + list(entry.get("aliases") or []):
                n = " ".join(str(n or "").split())
                if n and _norm(n) in wanted:
                    out.add(n)
    except Exception:
        pass
    return out


def _title(name: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in str(name).split())


class _Matcher:
    def __init__(self, patterns, skipped):
        self.patterns = patterns
        self.skipped = skipped

    def __call__(self, text) -> bool:
        if not text or not isinstance(text, str):
            return False
        return any(p.search(text) for p in self.patterns)

    def __bool__(self) -> bool:
        return bool(self.patterns)


def subject_matcher(names, emails=()) -> "_Matcher":
    """A predicate: does this free text name the person? See the module
    docstring for the rule. `.skipped` lists names too short to match."""
    forms, skipped = set(), []
    lowered = {" ".join(str(n or "").split()).lower() for n in names if str(n or "").strip()}
    for n in lowered:
        if len(n) < MIN_NAME:
            skipped.append(n)
            continue
        forms.add(_title(n))
    for g in _given_forms(lowered):
        if len(g) >= MIN_NAME:
            forms.add(g)
    pats = []
    for form in sorted(forms, key=len, reverse=True):
        words = [re.escape(w) for w in form.split(" ") if w]
        if words:
            pats.append(re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)"))
    for e in emails or ():
        e = str(e or "").strip()
        if "@" in e:
            pats.append(re.compile(r"(?<![\w.+-])" + re.escape(e) + r"(?![\w-])", re.IGNORECASE))
    return _Matcher(pats, sorted(skipped))


# ── the stores that exist today ──────────────────────────────────────────────

def _ensure_defaults() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    _DEFAULTS_LOADED = True
    register_store("contacts_research", _research_find, _research_purge)
    register_store("sender_signals", _signals_find, _signals_purge)
    register_store("trust_log_people", _trust_log_find, _trust_log_purge)
    from agent_friday.services.library import forget as _library_forget
    _library_forget.register()
    # What Laya kept from the owner's corrections names people too (services/laya_personal): the rows that
    # name the person go, with every saved version built from them.
    from agent_friday.services import laya_personal as _laya_personal
    register_store("laya_personal", _laya_personal.people_find, _laya_personal.people_purge)
    # Proposals (and a meeting's, which carry a decision or a name) sit in a plain table of their own.
    from agent_friday.services import memory_proposals as _memory_proposals
    # A meeting's own encrypted record (title, transcript, notes, bullets) names people too.
    from agent_friday.services import meeting_capture as _meeting_capture
    register_store("meetings", _meeting_capture.people_find, _meeting_capture.people_purge)
    register_store("memory_proposals", _memory_proposals.people_find, _memory_proposals.people_purge)
    # What was derived from the owner's conversations (NS-15.7-2): the recall indexes (vector memory and
    # conversation search), the day summaries and the graph's conversation-sourced nodes made from them,
    # the memory card and its review queue; the transcript and action receipts are listed as kept.
    from agent_friday.services import forget_derived as _forget_derived
    _forget_derived.register()


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").replace("-", " ").split())


def _research_dir():
    from agent_friday.paths import friday_home
    return friday_home() / "contacts-research"


def _research_matches(names):
    d = _research_dir()
    if not d.exists():
        return []
    wanted = {_norm(n) for n in names}
    return [p for p in d.glob("*.md") if _norm(p.stem) in wanted]


def _research_find(names, emails) -> int:
    return len(_research_matches(names))


def _research_purge(names, emails) -> int:
    n = 0
    for p in _research_matches(names):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def _signals_find(names, emails) -> int:
    try:
        from agent_friday.services import message_triage as mt
        data = mt._load_signals()
    except Exception:
        return 0
    senders = (data or {}).get("senders") or {}
    return sum(1 for e in emails if str(e).lower() in senders)


def _signals_purge(names, emails) -> int:
    n = 0
    try:
        from agent_friday.services import message_triage as mt
    except Exception:
        return 0
    for e in emails:
        try:
            if mt.forget_sender(str(e)).get("removed"):
                n += 1
        except Exception:
            pass
    return n


def _trust_log_match(names, emails):
    wanted = {_norm(n) for n in names}

    def match(event):
        if event.get("entity_kind") != "person":
            return False
        ids = {_norm(event.get("entity_id"))}
        ids |= {_norm(a) for a in (event.get("provenance") or {}).get("names", [])}
        return bool(ids & wanted)
    return match


def _trust_log_find(names, emails) -> int:
    from agent_friday.trust import log as tlog
    m = _trust_log_match(names, emails)
    return sum(1 for r in tlog.read(tlog.people_path()) if m(r))


def _trust_log_purge(names, emails) -> int:
    from agent_friday.trust import log as tlog
    return tlog.forget_entity(tlog.people_path(), match=_trust_log_match(names, emails))
