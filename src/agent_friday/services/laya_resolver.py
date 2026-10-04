"""The item resolver: a request for one specific thing becomes one exact command.

"Open the go-live email from Dana", "show my wiki page on the Neurow pitch",
"pull up that story about the school board": each names ONE item in one of
Friday's workspaces. This turns such a request into a command built from a
template and that item's stable id, or asks which one was meant, or hands the
request to the brain. It never writes a command as free text, so a command
can only point at something that exists.

THE PIPELINE (resolve)

  1. Is this a request to open one thing? An opening verb (deterministic),
     then `open_request` and `item_kind` in ONE Laya pass. No verb, "other",
     or kind "other": the brain takes it. (laya_questions has the measured
     reasons: `direct_command`, worded for device commands, caught 45% of
     item requests on the owner's phrasing.)
  2. Candidates from the workspace's own index, by id (CANDIDATE_SOURCES):
       email      the Messages cache, then Gmail's own search      thread_id
       wiki_page  the knowledge graph's pages and nodes            wiki path / node id
       file       Friday's file search over the Studio roots       path
       news       the local news archive                           url hash
       workspace  the workspace alias table                        workspace id
       calendar,  desktop_targets' own deterministic parsers       date / name
       contact
  3. Shortlist to at most SHORTLIST_K: words first, then cosine similarity
     on Laya's own encoder (laya.shortlist). A choice over more than ten
     options lands in laya's `choice:11+` bucket, which is clamped and
     uncalibrated on every build of this checkpoint (gate_status says so).
  4. A second Laya choice picks among the shortlist.
  5. Confident (top >= CONFIDENT and a clear margin): the command. Otherwise
     an ask-back naming two or three candidates. It never guesses; a missing
     Laya answer is "brain", not a default.

THE COMMAND is `navigate_to {kind, id}` (services/desktop_targets), which
opens the exact item and asks the desktop to confirm it, or `open_url` for a
news story until the desktop has a news-item target. Opening and reading are
internal; anything that changes an outside service goes through the gate as
it always does. This module decides; it does not run the command.

Measured by tools/laya_resolver_eval.py against the owner's own phrasing.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

_log = logging.getLogger("friday.laya_resolver")

SHORTLIST_K = 8
#: Narrow with cosine similarity on Laya's encoder (True) or keep the index's
#: own word ranking (False). Measured on the owner's 50 real items: word
#: ranking 43/50 top-1 at p50 0.62 s / p95 3.4 s, cosine 42/50 at 1.0 s /
#: 5.9 s (it embeds up to 30 titles on the CPU per request). The index's
#: ranking already put the right item first or second. Both are measured by
#: tools/laya_resolver_eval.py (--lexical).
COSINE_SHORTLIST = False
#: Retrieval keeps this many before the cosine shortlist.
LEXICAL_K = 30
#: A choice is acted on only at or above this probability, and with at least
#: MARGIN over the runner-up; below, the owner is asked.
CONFIDENT = 0.55
MARGIN = 0.20
#: Every Laya pass is bounded; a missing answer sends the request to the brain.
GATE_BUDGET_MS = 1500.0
PICK_BUDGET_MS = 1500.0
#: Gmail's search is network; the Messages cache is tried first.
MAIL_BUDGET_S = 3.0

STATUSES = ("command", "ask", "brain", "not_found")

#: The deterministic half of the gate: a request to open one thing says so.
_OPEN_VERB = re.compile(
    r"\b(open|opening|show|display|pull\s+up|bring\s+up|go\s+to|take\s+me\s+to|"
    r"find|get\s+me|look\s+at|read\s+me|view|launch)\b", re.I)


@dataclass
class Candidate:
    kind: str                 # a desktop_targets kind, or "news"
    id: str                   # the stable id the command is built from
    title: str                # how it is named, on screen and aloud
    detail: str = ""          # sender, date, section: what tells two apart
    score: float = 0.0        # retrieval score, for ordering only
    extra: Dict[str, str] = field(default_factory=dict)   # e.g. account, url


@dataclass
class Resolution:
    status: str                               # one of STATUSES
    text: str
    kind: Optional[str] = None
    command: Optional[dict] = None            # {"tool": ..., "input": {...}}
    chosen: Optional[Candidate] = None
    choices: List[Candidate] = field(default_factory=list)   # the ask-back
    question: str = ""                        # the ask-back, as spoken
    confidence: Optional[float] = None
    reason: str = ""
    timings_ms: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
#  COMMANDS: a template plus a stable id, never free text
# ---------------------------------------------------------------------------

def command_for(c: Candidate) -> dict:
    """The one command that opens exactly this candidate."""
    if c.kind == "news":
        return {"tool": "open_url", "input": {"url": c.extra["url"]}}
    if c.kind == "workspace":
        # A workspace that folded into another (Studio into Media) keeps its old
        # name as an alias: the command opens the workspace that exists today.
        try:
            from agent_friday.services import agent
            ws = agent._resolve_workspace(c.id) or c.id
        except Exception:
            ws = c.id
        return {"tool": "navigate_to", "input": {"kind": "workspace", "workspace": ws}}
    if c.kind == "library":
        return {"tool": "library_show", "input": {"target": c.title}}
    inp = {"kind": c.kind, "id": c.id}
    if c.kind == "email" and c.extra.get("account"):
        inp["account"] = c.extra["account"]
    return {"tool": "navigate_to", "input": inp}


# ---------------------------------------------------------------------------
#  CANDIDATES: each workspace's own index, by stable id
# ---------------------------------------------------------------------------

#: Words that say what KIND of thing or what to DO with it. They are not in
#: the item's title, and counting them capped a perfect title match below the
#: full-match line ("open that story about ..." scored 0.8 at best).
_CUE_WORDS = frozenset("""
open opening show display pull bring up go take view launch find get look read
please me my that this the story stories article articles headline news item
email emails mail message messages thread inbox wiki page pages note notes entry
entries file files document doc folder about on from called named titled
calendar calendars event events schedule agenda meeting new tab tabs chrome browser window
in for""".split())


def _words(text: str) -> list:
    from agent_friday.services import desktop_targets as dt
    return [w for w in dt._content(text) if w not in _CUE_WORDS]


def _dedupe(cands: List[Candidate]) -> List[Candidate]:
    """One candidate per title: the archive keeps one story under several
    ids, and two copies of the same title can only split a choice. The
    best-scored copy (the list is best-first) is kept."""
    seen, out = set(), []
    for c in cands:
        key = (c.kind, re.sub(r"[^a-z0-9]+", " ", c.title.lower()).strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _email_candidates(text: str) -> List[Candidate]:
    from agent_friday.services import desktop_targets as dt
    q = _words(text)
    if not q:
        return []
    ranked = dt._rank_cards(q, dt._cached_cards())
    if not ranked or ranked[0][0] < 0.5:
        from agent_friday.services import message_triage as mt
        _done, res = dt._within(lambda: mt.collect(limit_per_account=10,
                                                   query=" ".join(q)), MAIL_BUDGET_S)
        ranked = dt._rank_cards(q, list((res or {}).get("messages") or []),
                                keep_all=True) or ranked
    out = []
    for s, _t, c in ranked[:LEXICAL_K]:
        tid = c.get("thread_id") or c.get("id")
        if not tid:
            continue
        out.append(Candidate("email", str(tid), (c.get("subject") or "(no subject)")[:90],
                             "from %s, %s" % (c.get("sender") or c.get("sender_email") or "someone",
                                              str(c.get("timestamp") or "")[:10]),
                             float(s), {"account": str(c.get("account_id") or "")}))
    return out


def _wiki_candidates(text: str) -> List[Candidate]:
    from agent_friday.services import desktop_targets as dt
    q = _words(text)
    if not q:
        return []
    ranked = []
    for e in dt._entities():
        path = dt._entity_path(e)
        title = str(e.get("title") or "")
        key = (path or "").lower().replace("-", " ").replace("_", " ").replace("/", " ")
        s = max(dt._score(q, title.lower()), dt._score(q, key),
                0.8 * dt._score(q, (title + " " + str(e.get("description") or "")).lower()))
        if s > 0:
            ranked.append((s, bool(path), e, path))
    ranked.sort(key=lambda x: (round(x[0], 2), x[1]), reverse=True)
    out = []
    for s, _is_page, e, path in ranked[:LEXICAL_K]:
        out.append(Candidate("wiki_page" if path else "graph_node", path or e["id"],
                             (e.get("title") or e.get("id"))[:90],
                             str(e.get("description") or "")[:120], float(s)))
    return out


def _file_candidates(text: str) -> List[Candidate]:
    from agent_friday.services import desktop_targets as dt
    from agent_friday.services import file_search
    q = _words(text)
    if not q:
        return []
    _done, res = dt._within(lambda: file_search.search_files(
        query=" ".join(q), newest_first=True, limit=LEXICAL_K), dt.FILE_BUDGET_S)
    hits = (res or {}).get("results") if isinstance(res, dict) else res
    out = []
    for h in (hits or [])[:LEXICAL_K]:
        path = h.get("path") if isinstance(h, dict) else str(h)
        if path:
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            out.append(Candidate("file", path, name[:90], path[:120],
                                 float(dt._score(q, name.lower()))))
    return out


def _library_candidates(text: str) -> List[Candidate]:
    """Documents the owner added to the Library, by title."""
    from agent_friday.services import desktop_targets as dt
    from agent_friday.services.library import principal as lib_principal
    from agent_friday.services.library.store import store_for
    q = _words(text)
    principal = lib_principal.current()
    if not q or principal is None:
        return []
    try:
        from agent_friday.services.library import tree
        st = store_for(principal)
        rows = list(tree.TreeBuilder(st, principal).visible_docs().values())     # consent and the vault, as everywhere
    except Exception:
        return []
    out = []
    for r in rows:
        title = r["title"] or ""
        sc = float(dt._score(q, title.lower()))
        if sc > 0:
            out.append(Candidate("library", str(r["id"]), title[:90],
                                 ("%s pages" % r["pages"]) if r["pages"] else "in your Library", sc))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:LEXICAL_K]


def _news_candidates(text: str) -> List[Candidate]:
    from agent_friday.services import desktop_targets as dt
    from agent_friday.services import news_engine
    q = _words(text)
    if not q:
        return []
    ranked = []
    for n, a in enumerate(news_engine._iter_archive()):
        if n >= 3000:            # the newest few thousand; the archive is huge
            break
        hay = ("%s %s %s" % (a.get("title") or "", a.get("source") or "",
                              a.get("snippet") or "")).lower()
        s = dt._score(q, hay)
        if s > 0 and a.get("url"):
            ranked.append((s, a))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [Candidate("news", str(a.get("id") or news_engine._news_url_hash(a["url"])),
                      (a.get("title") or "")[:90],
                      "%s, %s" % (a.get("source") or "", str(a.get("published_at") or "")[:10]),
                      float(s), {"url": a["url"]}) for s, a in ranked[:LEXICAL_K]]


def _workspace_candidates(text: str) -> List[Candidate]:
    from agent_friday.services import agent
    ws = agent._resolve_workspace(text)
    if not ws:
        for w in _words(text):
            ws = agent._resolve_workspace(w)
            if ws:
                break
    if not ws:
        return []
    label = agent._WORKSPACE_LABELS.get(ws, ws.title())
    return [Candidate("workspace", ws, label, "workspace", 1.0)]


_LEAD = re.compile(
    r"^\s*(?:(?:yeah|ok(?:ay)?|so|um+|uh+|please|can you|could you|would you|i mean|go ahead and|just)[\s,]+)*"
    r"(?:open|opening|show|display|pull\s+up|bring\s+up|go\s+to|take\s+me\s+to|launch|view)\s+(?:up\s+)?", re.I)


_KIND_CUES = (
    ("email", re.compile(r"\b(e-?mails?|inbox message|thread)\b", re.I)),
    ("wiki_page", re.compile(r"\b(wiki|page|notes?|entry|entries|article I wrote|my archive)\b", re.I)),
    ("news", re.compile(r"\b(story|stories|article|headline|news item)\b", re.I)),
    ("file", re.compile(r"\b(file|document|doc|pdf|spreadsheet|folder)\b", re.I)),
)


def _kind_named(text: str) -> Optional[str]:
    for kind, rx in _KIND_CUES:
        if rx.search(text or ""):
            return kind
    return None


def _workspace_named(text: str) -> Optional[Candidate]:
    """"open news", "Open the Studio workspace for me": the words after the
    opening verb ARE a workspace name. Friday's alias table decides it, the
    way chat's navigate intent already does; no model is needed."""
    from agent_friday.services import agent
    rest = _LEAD.sub("", text or "", count=1)
    if rest == (text or ""):
        return None
    rest = re.sub(r"\s+(?:for me|for us|now|please|again|workspace|window|screen|tab)\b.*$", "",
                  rest.strip().rstrip(".!?"), flags=re.I)
    ws = agent._resolve_workspace(rest)
    if not ws:
        return None
    return Candidate("workspace", ws, agent._WORKSPACE_LABELS.get(ws, ws.title()), "workspace", 1.0)


def _delegated(kind: str) -> Callable[[str], List[Candidate]]:
    """Calendar and contacts: desktop_targets' own deterministic parsers."""
    def fn(text: str) -> List[Candidate]:
        from agent_friday.services import desktop_targets as dt
        r = dt.resolve(kind, query=text)
        if not r.get("ok"):
            return []
        t = r["target"]
        ident = t.get("date") or t.get("meeting_id") or t.get("name") or ""
        return [Candidate(kind, str(ident), r.get("label") or ident, "", 1.0)] if ident else []
    return fn


#: Google Calendar's own search is network; a day named outright is parsed
#: first and needs none.
CALENDAR_BUDGET_S = 3.0


def _calendar_candidates(text: str) -> List[Candidate]:
    """A day ("tomorrow", "Friday") or an event named by its title.

    The owner's own misses were events by title ("show me the concert on my
    calendar"), which the date parser cannot read. The desktop has no target
    for one event yet, so an event opens its DAY; its event id rides along in
    `extra` for when the registry gains an event target.
    """
    day = _delegated("calendar")(text)
    if day:
        return day
    from agent_friday.services import calendar_write
    from agent_friday.services import desktop_targets as dt
    q = _words(text)
    if not q:
        return []
    def _search_all():
        # Every connected calendar account, not only the primary one.
        from agent_friday.services import google_accounts
        found = []
        ids = [a.get("id") for a in google_accounts._accounts_with("calendar")] or [None]
        for aid in ids:
            r = calendar_write.find_events(" ".join(q), include_series=False, account_id=aid)
            found += (r or {}).get("events") or []
        return {"events": found}

    _done, res = dt._within(_search_all, CALENDAR_BUDGET_S)
    out = []
    for ev in ((res or {}).get("events") or []):
        start = str(ev.get("start") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
            continue
        title = str(ev.get("title") or "(untitled)")
        out.append(Candidate("calendar", start, title[:90], "on %s" % start,
                             float(dt._score(q, title.lower())), {"event_id": str(ev.get("id") or "")}))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:LEXICAL_K]


CANDIDATE_SOURCES: Dict[str, Callable[[str], List[Candidate]]] = {
    "email": _email_candidates,
    "wiki_page": _wiki_candidates,
    "file": lambda text: _file_candidates(text) + _library_candidates(text),
    "library": _library_candidates,
    "news": _news_candidates,
    "workspace": _workspace_candidates,
    "calendar": _calendar_candidates,
    "contact": _delegated("contact"),
}


# ---------------------------------------------------------------------------
#  SHORTLIST AND CHOICE
# ---------------------------------------------------------------------------

def _label(c: Candidate) -> str:
    return ("%s (%s)" % (c.title, c.detail)) if c.detail else c.title


def shortlist(text: str, cands: List[Candidate], k: int = SHORTLIST_K) -> List[Candidate]:
    """At most k, by Laya-encoder cosine over the lexical top; order kept on a tie.

    Falls back to the lexical order when the encoder is unavailable: the
    shortlist narrows, it never decides.
    """
    if len(cands) <= k:
        return list(cands)
    if not COSINE_SHORTLIST:
        return list(cands[:k])
    try:
        from laya.shortlist import embed_fn_from_agent, shortlist_choice
        from agent_friday.services import laya_backend
        agent = laya_backend._agent
        if agent is None:
            raise RuntimeError("laya not loaded")
        crit = {str(i): _label(c) for i, c in enumerate(cands)}
        keep = shortlist_choice(text, crit, embed_fn_from_agent(agent), k=k,
                                instructions="Which item does the user mean?")
        return [cands[int(i)] for i in keep]
    except Exception as e:
        _log.debug("cosine shortlist unavailable (%s); keeping the lexical order", e)
        return list(cands[:k])


_KEYS = "ABCDEFGHIJ"


def _pick(text: str, cands: List[Candidate]) -> dict:
    from agent_friday.services import laya_runtime
    crit = {_KEYS[i]: _label(c) for i, c in enumerate(cands)}
    q = {"item": {"type": "choice", "instructions": "Which item does the user mean?",
                  "criteria": crit}}
    r = laya_runtime.predict_bounded(text, q, budget_ms=PICK_BUDGET_MS)
    if r["status"] != "ok":
        return {"ok": False, "reason": r["reason"], "ms": r["elapsed_ms"]}
    probs = ((r["result"].get("answers") or {}).get("item") or {}).get("probabilities") or {}
    ranked = sorted(((float(probs.get(_KEYS[i], 0.0)), i) for i in range(len(cands))),
                    reverse=True)
    return {"ok": True, "ranked": ranked, "ms": r["elapsed_ms"]}


def _ask(kind, choices: List[Candidate], conf, why, timings) -> Resolution:
    names = [c.title for c in choices]
    if len(names) == 1:
        question = "Did you mean %s?" % names[0]
    else:
        question = "Did you mean %s, or %s?" % (", ".join(names[:-1]), names[-1])
    return Resolution("ask", question, kind=kind, choices=choices, question=question,
                      confidence=conf, reason=why, timings_ms=timings)


# ---------------------------------------------------------------------------
#  RESOLVE
# ---------------------------------------------------------------------------

def resolve(text: str, *, sources: Optional[Dict[str, Callable]] = None) -> Resolution:
    """Decide what `text` should open. Never runs the command. Never raises."""
    from agent_friday.services import laya_questions, laya_runtime
    t0 = time.monotonic()
    timings: Dict[str, float] = {}
    sources = sources or CANDIDATE_SOURCES
    try:
        if not _OPEN_VERB.search(text or ""):
            return Resolution("brain", "not a request to open something", reason="no_open_verb",
                              timings_ms=timings)
        ws = _workspace_named(text)
        if ws is not None and "workspace" in sources:
            return Resolution("command", "Opening %s." % ws.title, kind="workspace",
                              command=command_for(ws), chosen=ws,
                              reason="a workspace by name", timings_ms=timings)
        g = laya_runtime.ask(text, ["open_request", "item_kind"], budget_ms=GATE_BUDGET_MS)
        timings["gate"] = g["elapsed_ms"]
        if g["status"] != "ok":
            return Resolution("brain", "Laya did not answer: %s" % g["reason"],
                              reason=g["reason"], timings_ms=timings)
        if not laya_questions.holds("open_request", g["answers"].get("open_request")):
            return Resolution("brain", "not a request to open one thing", reason="not_command",
                              timings_ms=timings)
        kind = (g["answers"].get("item_kind") or {}).get("choice")
        if kind not in sources:
            # The request names the kind of thing outright ("my wiki page on",
            # "that story about"): the words decide where Laya said "other".
            kind = _kind_named(text) or kind
        if kind not in sources:
            return Resolution("brain", "not a request for one item", kind=kind,
                              reason="not_an_item", timings_ms=timings)

        t = time.monotonic()
        cands = _dedupe(sources[kind](text) or [])
        timings["retrieve"] = round((time.monotonic() - t) * 1000.0, 2)
        if not cands:
            return Resolution("not_found", "Nothing matched in %s." % kind, kind=kind,
                              reason="no_candidates", timings_ms=timings)
        # One candidate matches every word of the request and no other does:
        # the index has already decided, and a model choice adds only time.
        strong = [c for c in cands if c.score >= 0.99]
        if len(strong) == 1:
            c = strong[0]
            return Resolution("command", "Opening %s." % c.title, kind=kind,
                              command=command_for(c), chosen=c,
                              reason="the only full match", timings_ms=timings)
        t = time.monotonic()
        short = shortlist(text, cands)
        timings["shortlist"] = round((time.monotonic() - t) * 1000.0, 2)

        if len(short) == 1:
            c = short[0]
            if c.score >= 0.99:
                return Resolution("command", "Opening %s." % c.title, kind=kind,
                                  command=command_for(c), chosen=c, confidence=None,
                                  reason="the only match", timings_ms=timings)
            return _ask(kind, short, None, "one partial match", timings)

        p = _pick(text, short)
        timings["pick"] = p["ms"]
        if not p["ok"]:
            # Never a default: without Laya's choice, ask among the best matches.
            return _ask(kind, short[:3], None, "laya did not choose: %s" % p["reason"], timings)
        (p1, i1), (p2, _i2) = p["ranked"][0], p["ranked"][1]
        if p1 >= CONFIDENT and (p1 - p2) >= MARGIN:
            c = short[i1]
            return Resolution("command", "Opening %s." % c.title, kind=kind,
                              command=command_for(c), chosen=c, confidence=round(p1, 4),
                              reason="confident", timings_ms=timings)
        # Unsure: offer the index's own best matches first, then Laya's. On
        # the owner's items Laya's runner-up order held the right item in 3
        # of 21 ask-backs; the retrieval order is the better first guess.
        by_score = sorted(short, key=lambda c: c.score, reverse=True)
        top = []
        for c in by_score[:2] + [short[i] for _p, i in p["ranked"][:2]]:
            if c not in top:
                top.append(c)
        return _ask(kind, top[:3], round(p1, 4), "not confident", timings)
    except Exception as e:
        _log.warning("item resolver failed: %s", e)
        return Resolution("brain", "the resolver failed", reason="error: %s" % type(e).__name__,
                          timings_ms=timings)
    finally:
        timings["total"] = round((time.monotonic() - t0) * 1000.0, 2)
