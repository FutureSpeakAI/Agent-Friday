"""The owner's memory search, and "why do you know this" (north star NS-15.11-1,
NS-36.2-11).

``search()`` looks across everything Friday keeps about the owner and returns
each match as it is stored, with where it came from and whether it still
counts:

* ``conversation``  a turn from a past conversation: the full-text index
  (services/conversation_fts) and the vector memory (conversation_memory).
* ``card``          a line on the memory card (services/core_memory).
* ``card_change``   with ``include_superseded``, a line Friday replaced or
  removed (asked for with the ``card`` kind).
* ``suggestion``    a change waiting in the memory card's review queue.
* ``fact``          a user-model fact (services/user_model), with its source.
* ``proposal``      a suggested fact from the Memory keeper, pending or kept
  (services/memory_proposals).
* ``document``      a Library document whose title matches.

Each result carries ``kind``, ``id`` (what ``why()`` takes), ``text``,
``when``, ``source`` and ``status``: ``current``, ``superseded`` (with the
reason), ``pending`` (not used until the owner keeps it) or ``inferred``
(Friday concluded it; nobody confirmed it).

``why(kind, id)`` says where Friday knows it from: the original message and
its conversation and date; for a card line, the change record that put it
there (the owner or Friday, and which suggestion it was kept from); for a
fact, its source and confidence; for a proposal, the quote it rests on. Then
the correction history, and how it was used lately: which turns recalled it
(services/memory_influence, ids and dates only).

Owner-only, local: the routes are login_required and serve only the owner's
own principal ("user"). Searching is the owner reading their own data, so
nothing here is gated by off-record (off-record turns are never stored to
begin with). A result, or a why, in which any text names a person the owner
asked Friday to forget, or carries one of their addresses
(services/forget_person), is never returned. Nothing here logs a query or a
result.

Leaf module: no Flask; returns envelopes, never raises to a caller.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

KINDS = ("conversation", "card", "suggestion", "fact", "proposal", "document")
WHY_KINDS = KINDS + ("card_change",)

QUERY_MAX = 200
LIMIT_MAX = 50
LIMIT_DEFAULT = 20
ID_MAX = 120
#: How many of the Memory keeper's newest suggestions one search opens (each is unsealed to be read).
PROPOSALS_MAX = 200

#: A semantic match below this is noise for a search the owner reads.
SEMANTIC_FLOOR = 0.35

def _confirmed(src) -> bool:
    """A user-model fact source that means the owner entered or approved it: user_model's own rule
    (exact names, prefixes only for the colon forms), so an inferred "setup_research" is not the owner's."""
    try:
        from agent_friday.services.user_model import is_confirmed_source
        return is_confirmed_source(src)
    except Exception:
        return False

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STOP = {"the", "and", "for", "with", "that", "this", "what", "when", "where", "who",
         "did", "was", "were", "you", "your", "our", "about", "have", "has", "had",
         "from", "they", "them", "then", "there", "their", "are", "its", "into"}


class BadRequest(ValueError):
    """A parameter outside its bounds; the message is safe to show."""


# ── parameters ────────────────────────────────────────────────────────────────
def check_params(query: Any, kinds: Any = None, since: Any = None, until: Any = None,
                 limit: Any = None) -> Tuple[str, Tuple[str, ...], Optional[str], Optional[str], int]:
    """Normalised (query, kinds, since, until, limit), or BadRequest."""
    q = " ".join(str(query or "").split())
    if not q:
        raise BadRequest("Type something to search for.")
    if len(q) > QUERY_MAX:
        raise BadRequest(f"A search is at most {QUERY_MAX} characters.")
    if kinds in (None, "", [], ()):
        ks: Tuple[str, ...] = KINDS
    else:
        raw = kinds.split(",") if isinstance(kinds, str) else list(kinds)
        if len(raw) > len(KINDS):
            raise BadRequest("Too many kinds.")
        ks = tuple(k for k in KINDS if k in {str(x).strip() for x in raw})
        if not ks or len(ks) != len({str(x).strip() for x in raw if str(x).strip()}):
            raise BadRequest("Kinds are: " + ", ".join(KINDS) + ".")
    for name, v in (("since", since), ("until", until)):
        if v in (None, ""):
            continue
        # The shape and a real calendar day: 2026-13-45 and 2026-02-30 are refused, not read as no date.
        try:
            if not _DAY.match(str(v)):
                raise ValueError(v)
            datetime.strptime(str(v), "%Y-%m-%d")
        except ValueError:
            raise BadRequest(f"{name} is a date like 2026-10-24.")
    try:
        n = LIMIT_DEFAULT if limit in (None, "") else int(limit)
    except (TypeError, ValueError):
        raise BadRequest("limit is a number.")
    if not 1 <= n <= LIMIT_MAX:
        raise BadRequest(f"limit is between 1 and {LIMIT_MAX}.")
    return q, ks, (str(since) if since else None), (str(until) if until else None), n


def check_why(kind: Any, item_id: Any) -> Tuple[str, str]:
    k, i = str(kind or "").strip(), str(item_id or "").strip()
    if k not in WHY_KINDS:
        raise BadRequest("kind is one of: " + ", ".join(WHY_KINDS) + ".")
    if not i or len(i) > ID_MAX or not re.match(r"^[A-Za-z0-9:_\-\.]+$", i):
        raise BadRequest("That id is not one this search gave.")
    return k, i


# ── small helpers ─────────────────────────────────────────────────────────────
def _terms(query: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9']+", query.lower()) if len(w) >= 3 and w not in _STOP][:12]


def _score(text: str, terms: List[str], phrase: str) -> float:
    low = " ".join(str(text or "").lower().split())
    if not low:
        return 0.0
    if phrase and phrase in low:
        return 2.0
    if not terms:
        return 0.0
    return sum(1 for t in terms if t in low) / len(terms)


def _day_ts(day: Optional[str], end: bool = False) -> Optional[float]:
    if not day:
        return None
    try:
        return datetime.strptime(day, "%Y-%m-%d").timestamp() + (86400 if end else 0)
    except ValueError:
        return None


def _in_window(ts: Optional[float], lo: Optional[float], hi: Optional[float]) -> bool:
    if lo is None and hi is None:
        return True
    if ts is None:
        return False
    return (lo is None or ts >= lo) and (hi is None or ts < hi)


def _when(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M") if ts else ""
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _iso_ts(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").timestamp()
        except ValueError:
            return None


def _who(actor: str) -> str:
    return {"owner": "You", "friday": "Friday", "review": "The background review",
            "dream": "Nightly dreaming"}.get(str(actor or ""), "Friday")


def _from(source: str) -> str:
    s = str(source or "")
    if s.startswith("dream"):
        return "nightly dreaming" + (" on " + s.split(":", 1)[1] if ":" in s else "")
    if s == "review":
        return "the background review"
    if s.startswith("proposed:"):
        return "the Memory keeper's suggestions from " + s.split(":", 1)[1]
    if s.startswith("meeting:"):
        return "a meeting"
    if s == "friday":
        return "Friday in a conversation"
    if s in ("manual", "setup"):
        return {"manual": "you", "setup": "setup"}[s]
    return "an older note" if not s else s


def _fact_source(source: str) -> str:
    s = str(source or "")
    if s == "manual":
        return "You entered this"
    if s == "setup":
        return "You told Friday this during setup"
    if s == "user_stated":
        return "You told Friday this"
    if s.startswith("proposed:"):
        return "Approved suggestion from " + s.split(":", 1)[1]
    if s.startswith("meeting:"):
        return "Approved suggestion from a meeting"
    if s.startswith("dream"):
        return "Inferred by nightly dreaming" + (" on " + s.split(":", 1)[1] if ":" in s else "")
    return "Inferred" + (" (" + s + ")" if s else "")


# ── store access (each its own function, so a test can stand one in) ──────────
def _memory():
    from agent_friday.services.model_router import _get_conversation_memory
    return _get_conversation_memory()


def _forgotten_names() -> set:
    try:
        from agent_friday.services import forget_person
        return set(forget_person.forgotten_names())
    except Exception:
        return set()


def _forgotten_emails() -> set:
    try:
        from agent_friday.services import forget_person
        return set(forget_person.forgotten_emails())
    except Exception:
        return set()


def _titles() -> Dict[str, str]:
    try:
        from agent_friday.services import conversations as cv
        return {c.get("id"): c.get("title") or "" for c in cv.list_all(include_archived=True)}
    except Exception:
        return {}


class _Titles:
    """Conversation titles, read at most once for one request (a search lists them once, not per hit)."""

    def __init__(self):
        self._map: Optional[Dict[str, str]] = None

    def all(self) -> Dict[str, str]:
        if self._map is None:
            self._map = _titles()
        return self._map

    def get(self, cid: str, default: str = "") -> str:
        return self.all().get(cid, default) if cid else default


def _titles_of(cids) -> Dict[str, str]:
    """Titles of just these conversations (a why lists at most a few), without listing every one."""
    out: Dict[str, str] = {}
    try:
        from agent_friday.services import conversations as cv
    except Exception:
        return out
    for cid in {str(c) for c in cids if c}:
        try:
            conv = cv.load(cid)
        except Exception:
            conv = None
        if conv is not None:
            out[cid] = conv.get("title") or ""
    return out


def _documents(query: str) -> List[Dict[str, Any]]:
    from agent_friday.services.library.cards import find_documents
    from agent_friday.services.library.store import OWNER
    return find_documents(OWNER, query)


def _document(doc_id: str) -> Optional[Dict[str, Any]]:
    from agent_friday.services.library.store import OWNER, store_for
    from agent_friday.services.library.tree import TreeBuilder
    for k, r in TreeBuilder(store_for(OWNER), OWNER).visible_docs().items():
        if str(k) == str(doc_id) or str(r.get("id")) == str(doc_id):
            return {"doc_id": r.get("id"), "title": r.get("title"), "pages": r.get("pages")}
    return None


def _strings(obj, out: List[str], depth: int = 0) -> List[str]:
    """Every string in a result or a why, keys and values, at any depth: what the forgotten-people check
    reads. The parsed strings themselves, not a JSON dump, so an escaped quote or a newline between two
    words cannot hide a name."""
    if depth > 12:
        return out
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _strings(v, out, depth + 1)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            _strings(v, out, depth + 1)
    elif obj is not None and not isinstance(obj, (int, float, bool)):
        out.append(str(obj))
    return out


def _forgotten_filter():
    """A predicate over a whole result (or a whole why): does any text in it name someone the owner asked
    Friday to forget, or carry one of their addresses?

    The same rule forget_person uses for names (redact_forgotten_names) and the stores' purgers
    (memory_proposals, meeting_capture): the tombstoned name and its aliases, whole words, any case, any
    run of spaces between the words. An address recorded against a tombstoned person
    (forget_person.forgotten_emails) matches in any case, not as part of a longer address."""
    pats = []
    for name in sorted(_forgotten_names(), key=len, reverse=True):
        words = [re.escape(w) for w in str(name).split() if w]
        if words:
            pats.append(re.compile(r"\b" + r"\s+".join(words) + r"\b", re.IGNORECASE))
    for email in _forgotten_emails():
        e = str(email or "").strip()
        if "@" in e:
            pats.append(re.compile(r"(?<![\w.+-])" + re.escape(e) + r"(?![\w-]|\.\w)", re.IGNORECASE))
    if not pats:
        return lambda *objs: False

    def names(*objs) -> bool:
        hay = "\n".join(_strings(list(objs), []))
        return any(p.search(hay) for p in pats)
    return names


# ── search ────────────────────────────────────────────────────────────────────
def _said_by(role: str) -> str:
    return "you" if role == "user" else "Friday"


def _conversation(query, terms, since, until, lo, hi, include_superseded, n, notes, titles=None):
    from agent_friday.services import conversation_fts as fts
    out: Dict[str, Dict[str, Any]] = {}
    titles = titles if titles is not None else _Titles()
    try:
        for h in fts.search(query, since=since, until=until, limit=n,
                            include_superseded=include_superseded, titles=titles.all):
            mid = str(h.get("message_id") or "")
            if not mid:
                continue
            title = h.get("title") or ""
            date = h.get("date") or ""
            out[mid] = {
                "kind": "conversation", "id": mid, "text": h.get("text") or "",
                "ts": h.get("ts"), "when": date,
                "source": {"label": f"Said by {_said_by(h.get('role'))} in “{title or 'a conversation'}” on {date[:10]}",
                           "conversation_id": h.get("conversation_id"), "conversation_title": title,
                           "date": date[:10], "message_id": mid, "via": h.get("via") or "chat"},
                "status": "superseded" if h.get("superseded") else "current", "status_reason": "",
                "_score": 1.0 + max(0.0, float(h.get("score") or 0)) / 100.0}
    except Exception:
        notes.append("Conversation full-text search is not available right now.")
    mem = None
    try:
        mem = _memory()
        if mem is not None and mem.available():
            for h in mem.search(query, n=n, include_superseded=include_superseded):
                did = str(h.get("id") or "")
                rel = h.get("relevance")
                if not did or did in out or (rel is not None and rel < SEMANTIC_FLOOR):
                    continue
                ts = _iso_ts(h.get("timestamp") or h.get("date"))
                if not _in_window(ts, lo, hi):
                    continue
                loc = None
                try:
                    loc = fts.locate(did)
                except Exception:
                    loc = None
                cid = (loc or {}).get("conversation_id") or ""
                title = titles.get(cid, "")
                date = str(h.get("date") or "")[:10]
                out[did] = {
                    "kind": "conversation", "id": did, "text": h.get("text") or "",
                    "ts": ts, "when": _when(ts) or date,
                    "source": {"label": f"Said by {_said_by(h.get('role'))} in “{title or 'a conversation'}” on {date}",
                               "conversation_id": cid or None, "conversation_title": title,
                               "date": date, "message_id": did, "session": h.get("session_id") or ""},
                    "status": "superseded" if h.get("superseded") else "current",
                    "status_reason": h.get("superseded_reason") or "",
                    "_score": float(rel or 0)}
    except Exception:
        notes.append("Friday's conversation memory is not available right now.")
    # The reason a turn was corrected is kept with the vector memory.
    need = [k for k, r in out.items() if r["status"] == "superseded" and not r["status_reason"]]
    if need and mem is not None:
        try:
            for g in mem.get(need):
                if g.get("id") in out:
                    out[g["id"]]["status_reason"] = g.get("superseded_reason") or ""
        except Exception:
            pass
    return list(out.values())


def _origin(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A card change record's "origin" (where an owner-approved line came from), or {}."""
    o = (row or {}).get("origin")
    return o if isinstance(o, dict) else {}


def _card_label(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return "On the memory card (no change record says who added it)"
    when = _when(row.get("ts"))[:10]
    origin = _origin(row)
    # core_memory journals how a line was kept as "via" (e.g. "edited_suggestion": the owner's own
    # wording of a suggestion) and where it came from as "origin" (the suggestion or proposal).
    edited = row.get("via") == "edited_suggestion"
    if row.get("actor") == "owner":
        if origin.get("suggestion"):
            if edited:
                return (f"You kept this, in your own words, from a suggestion by "
                        f"{_from(origin.get('source'))}, on {when}")
            return f"You kept this from a suggestion by {_from(origin.get('source'))}, on {when}"
        if origin.get("proposal"):
            if edited:
                return (f"You kept this suggestion ({_from(origin.get('source'))}) in your own words "
                        f"on {when}")
            return f"You approved this suggestion ({_from(origin.get('source'))}) on {when}"
        return f"You wrote this on {when}"
    if row.get("actor") == "friday":
        return f"Friday saved this in a conversation on {when}"
    return f"{_who(row.get('actor'))} added this on {when}"


def _card(terms, phrase, lo, hi, include_superseded):
    from agent_friday.services import core_memory as cm
    out = []
    for t in cm.TARGETS:
        for line in cm.entries(t):
            sc = _score(line, terms, phrase)
            if not sc:
                continue
            rows = cm.journal_for(line, target=t)
            row = rows[-1] if rows else None
            ts = float(row.get("ts")) if row and row.get("ts") else None
            if (lo is not None or hi is not None) and not _in_window(ts, lo, hi):
                continue
            out.append({"kind": "card", "id": f"card:{t}:{cm.line_ref(line)}", "text": line,
                        "ts": ts, "when": _when(ts),
                        "source": {"label": _card_label(row), "card": t,
                                   "card_title": "About you" if t == "user" else "Working with you"},
                        "status": "current", "status_reason": "", "_score": sc})
    if include_superseded:
        current = {e.lower() for t in cm.TARGETS for e in cm.entries(t)}
        for r in cm.journal_rows():
            before = r.get("before") or ""
            if r.get("actor") == "owner" or r.get("op") not in ("replace", "remove") or not before:
                continue
            if r.get("undone") or before.lower() in current:
                continue
            sc = _score(before, terms, phrase)
            ts = float(r.get("ts") or 0) or None
            if not sc or not _in_window(ts, lo, hi):
                continue
            reason = (f"{_who(r.get('actor'))} changed it to “{r.get('after')}” on {_when(ts)[:10]}"
                      if r.get("op") == "replace" else f"{_who(r.get('actor'))} removed it on {_when(ts)[:10]}")
            # Its own kind, so the id goes back to why() as it came: why("card_change", "card_change:<id>").
            out.append({"kind": "card_change", "id": f"card_change:{r.get('id')}", "text": before,
                        "ts": ts, "when": _when(ts),
                        "source": {"label": "Was on the memory card", "card": r.get("target"),
                                   "change_id": r.get("id")},
                        "status": "superseded", "status_reason": reason, "_score": sc})
    return out


def _suggestions(terms, phrase, lo, hi):
    from agent_friday.services import core_memory as cm
    out = []
    for p in cm.pending():
        text = p.get("text") or p.get("old") or ""
        sc = max(_score(p.get("text") or "", terms, phrase), _score(p.get("old") or "", terms, phrase))
        ts = float(p.get("ts") or 0) or None
        if not sc or not _in_window(ts, lo, hi):
            continue
        op = p.get("op")
        out.append({"kind": "suggestion", "id": str(p.get("id")), "text": text,
                    "ts": ts, "when": _when(ts),
                    "source": {"label": f"Suggested by {_from(p.get('source'))}",
                               "op": op, "card": p.get("target"),
                               "old": p.get("old") if op in ("replace", "remove") else ""},
                    "status": "pending", "status_reason": "Waiting for your Keep on the memory card",
                    "_score": sc})
    return out


def _facts(terms, phrase, lo, hi):
    from agent_friday.services import user_model
    out = []
    for f in user_model.all_facts():
        sc = _score(f.get("text"), terms, phrase)
        ts = float(f.get("ts") or 0) or None
        if not sc or not _in_window(ts, lo, hi):
            continue
        src = str(f.get("source") or "")
        confirmed = _confirmed(src)
        out.append({"kind": "fact", "id": str(f.get("fact_id")), "text": f.get("text") or "",
                    "ts": ts, "when": _when(ts),
                    "source": {"label": _fact_source(src), "origin": src, "category": f.get("category"),
                               "confidence": f.get("confidence")},
                    "status": "current" if confirmed else "inferred",
                    "status_reason": "" if confirmed else "Friday concluded this; you have not confirmed it",
                    "_score": sc})
    return out


def _proposals(terms, phrase, lo, hi):
    from agent_friday.services import memory_proposals as mp
    out = []
    # Each row is opened once, and its evidence only when its text matches (memory_proposals.rows).
    for p in mp.rows(("pending", "approved"), limit=PROPOSALS_MAX,
                     match=lambda text: _score(text, terms, phrase) > 0):
        sc = _score(p.get("text"), terms, phrase)
        ts = float(p.get("ts") or 0) or None
        if not sc or not _in_window(ts, lo, hi):
            continue
        meeting = (p.get("provenance") or {}).get("meeting_id")
        label = (f"Suggested from a meeting on {p.get('day')}" if meeting
                 else f"Suggested from your conversations on {p.get('day')}")
        pending = p.get("status") == "pending"
        out.append({"kind": "proposal", "id": str(p.get("fact_id")), "text": p.get("text") or "",
                    "ts": ts, "when": _when(ts),
                    "source": {"label": label, "day": p.get("day"), "model": p.get("model")},
                    "status": "pending" if pending else "current",
                    "status_reason": ("Waiting for your review" if pending
                                      else f"You approved it on {_when(p.get('decided_ts'))[:10]}"),
                    "_score": sc})
    return out


def search(query: str, kinds=None, since: Optional[str] = None, until: Optional[str] = None,
           include_superseded: bool = False, limit: int = LIMIT_DEFAULT) -> Dict[str, Any]:
    """Matches across the kinds asked for, best first. See the module docstring."""
    try:
        q, ks, since, until, n = check_params(query, kinds, since, until, limit)
    except BadRequest as e:
        return {"ok": False, "error": str(e)}
    terms, phrase = _terms(q), q.lower()
    lo, hi = _day_ts(since), _day_ts(until, end=True)
    notes: List[str] = []
    results: List[Dict[str, Any]] = []
    titles = _Titles()
    steps = {
        "conversation": lambda: _conversation(q, terms, since, until, lo, hi, include_superseded, n, notes,
                                              titles=titles),
        "card": lambda: _card(terms, phrase, lo, hi, include_superseded),
        "suggestion": lambda: _suggestions(terms, phrase, lo, hi),
        "fact": lambda: _facts(terms, phrase, lo, hi),
        "proposal": lambda: _proposals(terms, phrase, lo, hi),
    }
    for k in ks:
        if k == "document":
            if since or until:
                notes.append("Library documents have no date here, so a date filter leaves them out.")
                continue
            try:
                for d in _documents(q):
                    results.append({"kind": "document", "id": str(d.get("doc_id")),
                                    "text": d.get("title") or "", "ts": None, "when": "",
                                    "source": {"label": "A document in your Library (its title matches)",
                                               "title": d.get("title"), "pages": d.get("pages")},
                                    "status": "current", "status_reason": "", "_score": 0.5})
            except Exception:
                notes.append("The Library is not available right now.")
            continue
        try:
            results.extend(steps[k]())
        except Exception:
            notes.append(f"Could not search {k} memory right now.")
    if not include_superseded:
        results = [r for r in results if r["status"] != "superseded"]
    # Every string a result carries (its text, its source and title, the reason it was corrected, the old
    # line a suggestion would change, ...), not a chosen few: a forgotten name anywhere drops the result.
    names = _forgotten_filter()
    kept = [r for r in results if not names(r)]
    if len(kept) != len(results):
        notes.append("Results that name someone you asked Friday to forget are not shown.")
    kept.sort(key=lambda r: (-float(r.get("_score") or 0), -float(r.get("ts") or 0)))
    for r in kept:
        r.pop("_score", None)
    return {"ok": True, "results": kept[:n], "kinds": list(ks),
            "include_superseded": bool(include_superseded), "notes": notes}


# ── why ───────────────────────────────────────────────────────────────────────
def _event(ts: Any, text: str) -> Dict[str, Any]:
    t = None
    try:
        t = float(ts) if ts not in (None, "") else None
    except (TypeError, ValueError):
        t = _iso_ts(ts)
    return {"ts": t, "when": _when(t), "text": text}


def _influence_conversation(mid: str) -> Dict[str, Any]:
    try:
        from agent_friday.services import memory_influence
        u = memory_influence.uses("conversation", mid)
    except Exception:
        return {"recorded": False, "summary": "How it was used is not available right now."}
    titles = _titles_of(r["session"] for r in u["recent"])
    recent = [{"ts": r["ts"], "when": _when(r["ts"]),
               "conversation_id": r["session"] if r["session"] in titles else None,
               "conversation_title": titles.get(r["session"], "")} for r in u["recent"]]
    if not u["count"]:
        # The record keeps only its newest rows (memory_influence.KEEP), so "never" would overclaim.
        summary = "No reply has recalled it recently."
    else:
        summary = (f"Recalled into {u['count']} {'reply' if u['count'] == 1 else 'replies'} recently; "
                   f"most recently on {_when(u['last'])[:10]}.")
    return {"recorded": True, "count": u["count"], "first": _when(u["first"]), "last": _when(u["last"]),
            "recent": recent, "summary": summary}


def _why_conversation(mid: str) -> Dict[str, Any]:
    from agent_friday.services import conversation_fts as fts
    stored = None
    try:
        mem = _memory()
        got = mem.get([mid]) if mem is not None and mem.available() else []
        stored = got[0] if got else None
    except Exception:
        stored = None
    loc = None
    try:
        loc = fts.locate(mid)
    except Exception:
        loc = None
    if not stored and not loc:
        return {"ok": False, "error": "Friday does not keep that turn now."}
    cid = (loc or {}).get("conversation_id") or ""
    original, title = None, ""
    if cid:
        try:
            from agent_friday.services import conversations as cv
            original = cv.find(cid, mid)
            title = (cv.load(cid) or {}).get("title") or ""
        except Exception:
            original = None
    text = (original or {}).get("text") if original else None
    text = text if isinstance(text, str) and text.strip() else (stored or {}).get("text") or ""
    role = (original or {}).get("role") or (loc or {}).get("role") or (stored or {}).get("role") or ""
    ts = (float(original.get("ts")) if original and original.get("ts") else None) \
        or (loc or {}).get("ts") or _iso_ts((stored or {}).get("timestamp"))
    date = _when(ts) or str((stored or {}).get("date") or "")
    superseded = bool((stored or {}).get("superseded") or (loc or {}).get("superseded"))
    history = [_event(ts, f"Said by {_said_by(role)}" + (f" in “{title}”" if title else ""))]
    if superseded:
        reason = (stored or {}).get("superseded_reason") or "no reason was recorded"
        by = (stored or {}).get("superseded_by") or ""
        history.append(_event((stored or {}).get("superseded_at") or None,
                              f"Marked as corrected: {reason}" + (f" (what counts now: {by})" if by else "")))
    summary = (f"{'You' if role == 'user' else 'Friday'} said this"
               + (f" in “{title}”" if title else " in a conversation") + (f" on {date[:10]}." if date else "."))
    if superseded:
        summary += " It has since been corrected, so Friday no longer uses it as evidence."
    return {"ok": True, "kind": "conversation", "id": mid, "text": text, "summary": summary,
            "status": "superseded" if superseded else "current",
            "source": {"conversation_id": cid or None, "conversation_title": title, "message_id": mid,
                       "date": date[:10], "said_by": _said_by(role), "via": (loc or {}).get("via") or "",
                       "session": (stored or {}).get("session_id") or ""},
            "history": history, "influence": _influence_conversation(mid)}


def _why_card(item_id: str) -> Dict[str, Any]:
    from agent_friday.services import core_memory as cm
    parts = item_id.split(":")
    if len(parts) != 3 or parts[1] not in cm.TARGETS:
        return {"ok": False, "error": "That id is not one this search gave."}
    target, ref = parts[1], parts[2]
    if not ref:
        return {"ok": False, "error": "That id is not one this search gave."}
    line = next((e for e in cm.entries(target) if cm.line_ref(e) == ref), None)
    if line is None:
        return {"ok": False, "error": "That line is no longer on the memory card."}
    rows = cm.journal_for(line, target=target)
    history = []
    added_by = None
    for r in rows:
        origin = _origin(r)
        ev = _card_label(r)
        if r.get("op") == "replace" and r.get("before"):
            ev += f" (it replaced “{r.get('before')}”)"
        if origin.get("reason"):
            ev += f". The suggestion said: {origin.get('reason')}"
        history.append(_event(r.get("ts"), ev))
    last = rows[-1] if rows else None
    if last:
        origin = _origin(last)
        added_by = {"actor": last.get("actor"), "who": _who(last.get("actor")), "when": _when(last.get("ts")),
                    "change_id": last.get("id"), "op": last.get("op")}
        if origin:
            added_by["suggestion"] = {k: origin.get(k) for k in ("suggestion", "proposal", "source", "reason")
                                      if origin.get(k)}
            added_by["suggestion"]["from"] = _from(origin.get("source"))
            if last.get("via") == "edited_suggestion":
                added_by["suggestion"]["edited"] = True
            if origin.get("queued_ts"):
                added_by["suggestion"]["suggested_on"] = _when(origin.get("queued_ts"))
            if origin.get("proposal"):
                try:
                    from agent_friday.services import memory_proposals as mp
                    pr = mp.rows(fact_id=origin.get("proposal"))
                    if pr and pr[0].get("evidence"):
                        added_by["suggestion"]["evidence"] = pr[0]["evidence"]
                except Exception:
                    pass
    summary = _card_label(last) + "."
    return {"ok": True, "kind": "card", "id": item_id, "text": line, "summary": summary,
            "status": "current",
            "source": {"card": target, "card_title": "About you" if target == "user" else "Working with you",
                       "added_by": added_by},
            "history": history,
            "influence": {"recorded": True, "always": True,
                          "summary": "Friday reads this line at the start of every conversation while it is on the card."}}


def _why_card_change(item_id: str) -> Dict[str, Any]:
    from agent_friday.services import core_memory as cm
    cid = item_id.split(":", 1)[1] if ":" in item_id else item_id
    r = cm.journal_row(cid)
    if not r or r.get("actor") == "owner" or not r.get("before"):
        return {"ok": False, "error": "That change is no longer on record."}
    when = _when(r.get("ts"))
    if r.get("op") == "replace":
        reason = f"{_who(r.get('actor'))} changed it to “{r.get('after')}” on {when[:10]}"
    else:
        reason = f"{_who(r.get('actor'))} removed it on {when[:10]}"
    hist = [_event(r.get("ts"), reason + (" (you undid this change)" if r.get("undone") else ""))]
    return {"ok": True, "kind": "card_change", "id": item_id, "text": r.get("before"),
            "summary": f"This was on the memory card. {reason}.",
            "status": "current" if r.get("undone") else "superseded",
            "source": {"card": r.get("target"), "change_id": cid, "replaced_by": r.get("after")},
            "history": hist,
            "influence": {"recorded": False, "summary": "It is no longer on the card, so Friday does not read it."}}


def _why_suggestion(item_id: str) -> Dict[str, Any]:
    from agent_friday.services import core_memory as cm
    p = next((x for x in cm.pending() if str(x.get("id")) == item_id), None)
    if not p:
        return {"ok": False, "error": "That suggestion is no longer waiting."}
    hist = [_event(p.get("ts"), f"Suggested by {_from(p.get('source'))}")]
    return {"ok": True, "kind": "suggestion", "id": item_id, "text": p.get("text") or p.get("old") or "",
            "summary": f"{_from(p.get('source')).capitalize()} suggested this on {_when(p.get('ts'))[:10]}. "
                       "It is not on the card until you keep it.",
            "status": "pending",
            "source": {"from": _from(p.get("source")), "origin": p.get("source"), "reason": p.get("reason") or "",
                       "op": p.get("op"), "card": p.get("target"), "old": p.get("old") or ""},
            "history": hist,
            "influence": {"recorded": False, "summary": "Not used in any reply until you keep it."}}


def _why_fact(item_id: str) -> Dict[str, Any]:
    from agent_friday.services import user_model
    f = next((x for x in user_model.all_facts() if str(x.get("fact_id")) == item_id), None)
    if not f:
        return {"ok": False, "error": "Friday does not keep that fact now."}
    src = str(f.get("source") or "")
    confirmed = _confirmed(src)
    hist = [_event(f.get("ts"), _fact_source(src))]
    evidence = ""
    if src.startswith(("proposed:", "meeting:")):
        try:
            from agent_friday.services import memory_proposals as mp
            low = str(f.get("text") or "").strip().lower()
            same = lambda text: str(text or "").strip().lower() == low      # noqa: E731
            pr = next(iter(mp.rows(("approved",), limit=PROPOSALS_MAX, match=same)), None)
            if pr:
                evidence = pr.get("evidence") or ""
                hist.insert(0, _event(pr.get("ts"), f"Suggested from your conversations on {pr.get('day')}"))
        except Exception:
            pass
    on_card = False
    try:
        from agent_friday.services import core_memory as cm
        on_card = any(e.lower() == str(f.get("text") or "").strip().lower() for e in cm.entries("user"))
    except Exception:
        pass
    try:
        conf = round(float(f.get("confidence") or 0), 2)
    except (TypeError, ValueError):
        conf = None
    summary = _fact_source(src) + (f" (confidence {conf})." if conf is not None else ".")
    if not confirmed:
        summary += " Nobody has confirmed it."
    return {"ok": True, "kind": "fact", "id": item_id, "text": f.get("text") or "", "summary": summary,
            "status": "current" if confirmed else "inferred",
            "source": {"origin": src, "label": _fact_source(src), "confidence": conf,
                       "category": f.get("category"), "evidence": evidence, "on_card": on_card},
            "history": hist,
            "influence": {"recorded": False, "on_card": on_card,
                          "summary": ("It is also on the memory card, which Friday reads at the start of every "
                                      "conversation." if on_card else
                                      "Uses of user-model facts are not recorded one by one.")}}


def _why_proposal(item_id: str) -> Dict[str, Any]:
    from agent_friday.services import memory_proposals as mp
    got = mp.rows(fact_id=item_id)
    if not got:
        return {"ok": False, "error": "That suggestion is no longer on record."}
    p = got[0]
    prov = p.get("provenance") or {}
    hist = [_event(p.get("ts"), ("Suggested from a meeting" if prov.get("meeting_id")
                                 else f"Suggested from your conversations on {p.get('day')}")
                   + (f" by {p.get('model')}" if p.get("model") else ""))]
    if p.get("status") == "approved":
        hist.append(_event(p.get("decided_ts"), "You approved it"))
    elif p.get("status") == "edited":
        # Kept in the owner's own words (memory_proposals.approve edits): this wording is not what was kept.
        hist.append(_event(p.get("decided_ts"), "You kept it in your own words"))
    elif p.get("status") == "rejected":
        hist.append(_event(p.get("decided_ts"), "You discarded it"))
    summary = (f"The Memory keeper read your conversations from {p.get('day')} and suggested this"
               + (f", from the words: “{p.get('evidence')}”." if p.get("evidence") else "."))
    return {"ok": True, "kind": "proposal", "id": item_id, "text": p.get("text") or "", "summary": summary,
            "status": {"pending": "pending", "approved": "current"}.get(p.get("status"), "superseded"),
            "source": {"day": p.get("day"), "evidence": p.get("evidence") or "", "model": p.get("model"),
                       "meeting_id": prov.get("meeting_id"),
                       "segment": (prov.get("span") or {}).get("segment")},
            "history": hist,
            "influence": {"recorded": False,
                          "summary": ("Not used in any reply until you approve it." if p.get("status") == "pending"
                                      else "Kept as a fact; uses of facts are not recorded one by one.")}}


def _why_document(item_id: str) -> Dict[str, Any]:
    d = _document(item_id)
    if not d:
        return {"ok": False, "error": "That document is not in your Library now."}
    return {"ok": True, "kind": "document", "id": item_id, "text": d.get("title") or "",
            "summary": f"“{d.get('title')}” is a document in your Library. Friday reads it on this PC "
                       "when a question needs it and cites the passage it used.",
            "status": "current", "source": {"title": d.get("title"), "pages": d.get("pages")},
            "history": [],
            "influence": {"recorded": False, "summary": "Uses of Library documents show as citations in the replies."}}


def why(kind: str, item_id: str) -> Dict[str, Any]:
    """Where Friday knows this from, its correction history, and its recent use."""
    try:
        kind, item_id = check_why(kind, item_id)
    except BadRequest as e:
        return {"ok": False, "error": str(e)}
    fn = {"conversation": _why_conversation, "card": _why_card, "card_change": _why_card_change,
          "suggestion": _why_suggestion, "fact": _why_fact, "proposal": _why_proposal,
          "document": _why_document}[kind]
    try:
        out = fn(item_id)
    except Exception:
        return {"ok": False, "error": "Could not look that up right now."}
    # The whole answer (its text, source, evidence, history and influence), not a few fields: a forgotten
    # name or address anywhere in it hides the lot.
    if out.get("ok") and _forgotten_filter()(out):
        return {"ok": False, "hidden": True, "kind": kind, "id": item_id,
                "error": "That names someone you asked Friday to forget."}
    return out
