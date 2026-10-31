"""forget_kinds -- forget a fact, a conversation or a source; see where something is first.

North star NS-15.7-1: "Forget fact/person/conversation/source and 'show
everywhere' requests supported." Forget-person (services/forget_person, M9)
and the Library's own forget (services/library/forget) already existed. This
module adds the other three forgets and the read-only look before any of them,
each with the same per-store receipt forget-person gives (one row per store:
``removed`` as a count and a kind, ``not_removable`` with why, ``retained``
with why; counts only, never the content).

``forget_fact(text)``
    One statement. Exact matching (``tombstones.normalise``: case, punctuation
    and a leading bullet do not count) for the stores that hold statements:
    memory-card lines, their pending suggestions and change history
    (core_memory), facts in the older user model, proposals (any status),
    the nightly dreaming log's consolidated facts, and the knowledge graph's
    fact nodes for it (``fact_<hash of its text>``). The statement is
    tombstoned FIRST (card_line, user_fact, proposal; the graph fact by its
    id), so no derived pass running meanwhile writes it back.
    Conversation turns are never taken by matching alone: show_everywhere
    lists CANDIDATES (TurnMatcher: the owner's own turns only, containing
    every content word of the statement less common verbs and negations, not
    much longer than it, and with no negation the statement lacks; only when
    the statement has at least three such words), each with a short snippet,
    and the owner ticks the ones that said it. forget_fact(turn_ids=...)
    takes exactly those out of recall and search the way a forgotten person's
    turns go (forget_derived.purge_turns); with no ids it takes none and the
    receipt says so. The transcript keeps the turns (the owner's words).

``forget_conversation(conversation_id, delete_transcript=False)``
    Stop using one conversation: every one of its turns leaves recall and
    search and joins the durable forgotten-turn list (so no rebuild brings
    them back), the day summaries made from them are removed and queued to be
    written again (memory_derived), and the graph retracts what those turns
    supported. The transcript is kept in History unless the owner also chose
    ``delete_transcript`` (owner ruling 9, 2026-10-28: a second, separate
    confirm): then conversations.delete removes the transcript, its record
    and its files, and every message from every index through the paths a
    per-message delete takes; the receipt lists the messages as removed
    (counts only). Signed action receipts that mention it are kept. Lines and
    facts learned from it carry no link back to it, so the receipt lists them
    as not removable by conversation.

``stop_using_source(source)``
    ``library:<doc id>``   the Library's own forget (tombstone, purge, cited
                           chats rewritten); the file on disk stays.
    ``meeting:<id>``       the meeting's proposals (tombstoned, then removed),
                           the facts and card lines it gave (tombstoned, then
                           removed), its graph fact nodes and its searchable
                           Library copies (meeting_archive, which refuses a
                           stopped meeting from then on, failing closed); the
                           meeting's own record stays in Meetings.
    ``web:<domain>``       graph facts whose only recorded sources are pages
                           on that site (setup research), and everything read
                           there in chat (owner ruling 10; services/
                           web_provenance records each reply's sites): those
                           replies leave recall and search (durable forgotten
                           list), the graph facts, memory-card suggestions and
                           memory suggestions carrying the site are removed
                           (tombstoned first), and from then on nothing read
                           there is remembered or proposed (Friday still opens
                           the site when asked). A URL or a bare domain is
                           read as its site. A public suffix alone (co.uk,
                           github.io) is refused: it is no one site.
    ``sender:<address>``   what the triage learned about the sender is
                           removed; mail content is not linked to its sender
                           in memory on this build, so that row says
                           "not supported yet".
    A meeting or a site is also recorded as stopped (``stopped-sources.json``
    beside the tombstones: keyed hashes under the tombstone key and a date,
    never the id or the domain in the clear), so a later meeting proposal or
    research fact from it is not taken in. Each entry also carries a random
    id and a display label SEALED with the credential store
    (credential_store.protect), so the owner can see what they stopped and
    use it again (``list_stopped``, ``unstop``); a label that cannot be
    sealed or opened shows as "a stopped site from <day>". Using a source
    again does not bring back what was removed.

``show_everywhere(query)``
    Read-only: where a statement, a person (forget_person.find) or a source
    appears, as counts and short locations (which card file and line, which
    conversations by title, which days). Nothing is written, so the owner can
    look before they forget.

Every operation is the owner's own explicit action (routes/memory_forget is
owner-only); none of them deletes a meeting record, a Library file or an
audit log, and only forget_conversation with ``delete_transcript`` (its own
confirm) deletes a transcript.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

_log = logging.getLogger("friday.forget_kinds")
_LOCK = threading.RLock()

#: Longest statement or query accepted (a card line is at most 400).
MAX_TEXT = 400
#: Longest source accepted.
MAX_SOURCE = 300
#: A statement needs this many content words before turns are matched by them.
MIN_CONTENT_WORDS = 3
#: Locations listed per store by show_everywhere.
LOCATIONS_MAX = 10
#: Candidate turns listed by show_everywhere for the owner to confirm.
CANDIDATES_MAX = 50
#: Most confirmed turn ids one forget_fact takes.
MAX_TURN_IDS = 200
#: A candidate turn has at most max(CANDIDATE_MIN_CAP, CANDIDATE_FACTOR x the statement's words) words.
CANDIDATE_MIN_CAP = 30
CANDIDATE_FACTOR = 4
#: A candidate the statement covers this much of is ticked by default.
CLOSE_RATIO = 0.4
#: Characters of a candidate turn shown to the owner.
SNIPPET = 120

STOPPED_FILE = "stopped-sources.json"

_TRANSCRIPT_REASON = ("your own conversation history, in your words: Friday does not rewrite it (you can delete "
                      "any message from History). Those messages are no longer recalled, searched, summarised "
                      "or replayed to the model as conversation context")
_CONVERSATION_REASON = ("the conversation itself stays in History, in your words. Its messages are no longer "
                        "recalled, searched, summarised or replayed to the model; to remove the transcript too, "
                        "choose Also delete the transcript, or clear the conversation in History")
_DERIVED_REASON = ("memory-card lines and saved facts carry no link to the conversation they were learned "
                   "from, so they cannot be found by it; use Forget this on the line or fact")

_DREAM_PREFIX = re.compile(r"^\s*[-*•]?\s*\*\*\[[^\]]*\]\*\*\s*")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
_MEETING_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FACT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TURN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
#: Negations: a turn with one the statement lacks says something else.
_NEGATION = re.compile(r"\b(?:no|not|never|nor|none|nothing|nobody|nowhere|neither|cannot|without|dont|doesnt|"
                       r"didnt|isnt|wasnt|werent|arent|cant|wont|wouldnt|shouldnt|couldnt|havent|hasnt|hadnt)\b"
                       r"|n['’]t\b")
#: Verbs (and contraction stubs) too common to say what a statement is about.
_COMMON_VERBS = frozenset((
    "am", "were", "been", "being", "have", "has", "had", "do", "does", "did", "done", "get", "gets", "got",
    "go", "goes", "went", "make", "makes", "made", "take", "takes", "took", "say", "says", "said", "think",
    "thinks", "know", "knows", "want", "wants", "wanted", "need", "needs", "like", "liked", "love", "loves",
    "prefer", "preferred", "use", "uses", "used", "will", "would", "can", "could", "should", "may", "might",
    "must", "also", "always", "usually", "really", "just", "don", "doesn", "didn", "isn", "wasn", "won",
    "ll", "ve", "re"))


# ── small helpers ────────────────────────────────────────────────────────────
def _row(store: str, n: int = 0, kind: str = "", not_removable=(), retained=()) -> Dict[str, Any]:
    return {"store": store, "removed": int(n or 0), "kind": kind,
            "not_removable": [dict(x) for x in not_removable], "retained": [dict(x) for x in retained]}


def _scrubbed(e: BaseException, what: str) -> str:
    from agent_friday.services.forget_derived import _scrubbed as s
    return s(e, what)


def _failed(store: str, kind: str, e: BaseException, what: str) -> Dict[str, Any]:
    return _row(store, 0, kind, [{"what": what, "reason": _scrubbed(e, "it could not be changed")}])


def _norm(text: Any) -> str:
    from agent_friday.services import tombstones
    return tombstones.normalise(text)


def _clip(text: Any, n: int = MAX_TEXT) -> str:
    return " ".join(str(text or "").split())[:n]


def statement_matcher(text: Any) -> Callable[[Any], bool]:
    """Exact statement equality after tombstones.normalise. A dreaming log's
    "- **[category]** text" line is compared without its category tag."""
    norm = _norm(text)

    def same(s) -> bool:
        if not norm or s is None:
            return False
        return _norm(_DREAM_PREFIX.sub("", str(s))) == norm
    return same


def _content(text: Any) -> frozenset:
    """A statement's or turn's meaning words: core_memory.content_words, less
    one-letter tokens, common verbs and negations."""
    from agent_friday.services import core_memory
    return frozenset(w for w in core_memory.content_words(text)
                     if len(w) >= 2 and w not in _COMMON_VERBS and not _NEGATION.fullmatch(w))


class TurnMatcher:
    """Which of the owner's turns may have stated this? Only CANDIDATES for the
    owner to confirm (show_everywhere lists them; forget_fact removes only the
    ids the owner ticks). A turn is a candidate when every content word of the
    statement (no common verbs or negations) is in it as a whole word, it is
    not much longer than the statement (CANDIDATE_FACTOR times its words, at
    least CANDIDATE_MIN_CAP), and it has no negation the statement lacks ("I
    do not drink green tea at breakfast" is not "drinks green tea at
    breakfast"). With fewer than MIN_CONTENT_WORDS words it matches nothing
    (False), and ``notes()`` says why."""

    def __init__(self, text: Any):
        from agent_friday.services import core_memory
        clean = core_memory._clean(text).lower()
        self.words = _content(text)
        self.negated = bool(_NEGATION.search(clean))
        self.max_words = max(CANDIDATE_MIN_CAP, CANDIDATE_FACTOR * len(re.findall(r"[a-z0-9]+", clean)))

    def __bool__(self) -> bool:
        return len(self.words) >= MIN_CONTENT_WORDS

    def score(self, text) -> Optional[float]:
        """None when the turn is not a candidate; else how much of the turn the
        statement covers (1.0: the turn says nothing else)."""
        if not self or not isinstance(text, str) or not text:
            return None
        from agent_friday.services import core_memory
        clean = core_memory._clean(text).lower()
        tokens = re.findall(r"[a-z0-9]+", clean)
        if len(tokens) > self.max_words or not self.words <= set(tokens):
            return None
        if not self.negated and _NEGATION.search(clean):
            return None
        return len(self.words) / max(1, len(_content(clean)))

    def __call__(self, text) -> bool:
        return self.score(text) is not None

    def notes(self) -> List[Dict[str, Any]]:
        if self:
            return []
        return [{"what": "conversation turns that said it",
                 "reason": ("the statement has fewer than %d meaningful words, so turns were not matched by "
                            "them (too likely to take unrelated ones); delete the message from History or forget "
                            "the conversation instead" % MIN_CONTENT_WORDS)}]


def _user_turns() -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(conversation, message) for every stored user turn with an id."""
    from agent_friday.services import conversations as cv
    if not cv._root().exists():
        return
    for conv in cv.list_all(include_archived=True):
        cid = conv.get("id")
        if not cid:
            continue
        for msg in cv._stored_messages(cid):
            if (isinstance(msg, dict) and msg.get("role") == "user" and msg.get("id")
                    and isinstance(msg.get("text"), str) and msg["text"].strip()):
                yield conv, msg


def turn_candidates(text: Any) -> List[Dict[str, Any]]:
    """The owner's own turns that may have stated this (TurnMatcher), not yet
    forgotten: {message_id, conversation_id, title, snippet, close}. Read-only.
    ``close`` (the statement covers at least CLOSE_RATIO of the turn) is the
    dialog's default tick."""
    tm = text if isinstance(text, TurnMatcher) else TurnMatcher(text)
    if not tm:
        return []
    out = []
    for conv, msg in _user_turns():
        s = tm.score(msg["text"])
        if s is None:
            continue
        out.append({"message_id": str(msg["id"]), "conversation_id": str(conv.get("id")),
                    "title": str(conv.get("title") or "")[:80], "snippet": _clip(msg["text"], SNIPPET),
                    "close": s >= CLOSE_RATIO})
    try:
        from agent_friday.services import conversation_fts as fts
        gone = fts.forgotten_ids([c["message_id"] for c in out])
    except Exception:
        gone = set()
    return [c for c in out if c["message_id"] not in gone][:CANDIDATES_MAX]


def _confirmed_turns(turn_ids: Iterable[Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """(conversation id, message) for each id the owner confirmed that names
    one of their own stored turns; anything else is ignored."""
    if isinstance(turn_ids, (str, bytes)):
        turn_ids = [turn_ids]
    want = {str(i) for i in list(turn_ids or [])[:MAX_TURN_IDS] if _TURN_ID.match(str(i or ""))}
    if not want:
        return []
    return [(str(conv.get("id")), msg) for conv, msg in _user_turns() if str(msg["id"]) in want]


class _ConversationTexts:
    """A conversation's own turns, by their exact text (for a vector-memory row
    stored before turns carried their message id). Only texts of at least 20
    characters, as conversation_fts.delete_message does, so a short "ok" said
    elsewhere is never taken. purge_turns(by_id=True) takes a row this matches
    only when it is a legacy row (random id) dated on one of this
    conversation's days: the same words under another conversation's message
    id stay."""

    def __init__(self, texts: Iterable[str], present: bool):
        self.texts = {t for t in (" ".join(str(x or "").split()) for x in texts) if len(t) >= 20}
        self.present = bool(present)

    def __bool__(self) -> bool:
        return self.present

    def __call__(self, text) -> bool:
        return isinstance(text, str) and " ".join(text.split()) in self.texts

    def notes(self) -> List[Dict[str, Any]]:
        return []


def _ro(db: Path, sql: str, args: tuple = ()) -> Optional[list]:
    """Rows from an SQLite file opened read-only; None when it does not exist."""
    if not Path(db).exists():
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % Path(db).as_posix(), uri=True, timeout=5)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _kg_store():
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    return KnowledgeGraphStore()


def _fact_node_id(text: str) -> str:
    """The id knowledge_graph.integration.ingest_fact gives a fact's node."""
    t = (text or "").strip()
    return "fact_" + hashlib.sha1(t.lower().encode()).hexdigest()[:12]


def _host(url: Any) -> str:
    try:
        h = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _on_site(host: str, domain: str) -> bool:
    return bool(host) and bool(domain) and (host == domain or host.endswith("." + domain))


# ── the stopped-sources record (keyed hashes, no ids or domains) ────────────
def _stopped_path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / STOPPED_FILE


def _key(create: bool) -> Optional[bytes]:
    """The tombstone key (services/tombstones). A key is only ever made while
    no hashed tombstone and no stopped-source entry exists: a new key beside
    existing entries would orphan every one of them (a stopped meeting or site
    would silently start again)."""
    from agent_friday.services import tombstones as t
    key = t._key(create=False)
    if key is not None or not create:
        return key
    try:
        if t._read(strict=True) or t._key_path().exists():
            return None
        if _stopped_path().exists() and _read_stopped():
            return None
    except Exception:
        return None
    return t._key(create=True)


def _digest(key: bytes, ident: str) -> str:
    from agent_friday.services import tombstones as t
    return t._digest(key, "source", ident)


def _read_stopped() -> List[Dict[str, Any]]:
    p = _stopped_path()
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    rows = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("no entry list")
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("h"), str)]


_SEALED_LABEL = "sealed:"
_STOP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _seal_label(ident: str) -> str:
    """The source as the owner named it, sealed with the credential store
    (the keystore Friday's own secrets use), or "" when it cannot be sealed:
    a stopped source's name is never written in the clear."""
    try:
        import base64
        from agent_friday.services import credential_store as cs
        blob, _method = cs.protect(str(ident).encode("utf-8"))
        if cs.looks_protected(blob) is None:
            return ""
        return _SEALED_LABEL + base64.b64encode(blob).decode("ascii")
    except Exception as e:
        _log.info("forget_kinds: a stopped source's label could not be sealed (%s)", type(e).__name__)
        return ""


def _open_label(label: Any) -> str:
    if not isinstance(label, str) or not label.startswith(_SEALED_LABEL):
        return ""
    try:
        import base64
        from agent_friday.services import credential_store as cs
        return cs.unprotect(base64.b64decode(label[len(_SEALED_LABEL):])).decode("utf-8")
    except Exception:
        return ""


def _entry_id(r: Dict[str, Any]) -> str:
    """An entry's id: its own random id, or (an entry written before ids)
    the start of its keyed hash."""
    rid = r.get("id")
    return rid if isinstance(rid, str) and _STOP_ID.match(rid) else "h" + str(r.get("h") or "")[:15]


def _write_stopped(rows: List[Dict[str, Any]]) -> None:
    p = _stopped_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%s.tmp" % secrets.token_hex(4))
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"version": 1, "entries": rows}, indent=0))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    _forget_stop_cache()


def record_stopped(ident: str) -> Dict[str, Any]:
    """Record a source as stopped: its keyed hash, the day, a random id and
    its sealed label (``k`` is one letter for its kind: w a site, m a
    meeting). {"ok"} or {"ok": False, "error"}. Never raises."""
    try:
        with _LOCK:
            rows = _read_stopped()
            key = _key(create=True)
            if key is None:
                return {"ok": False, "error": "the tombstone key is not available; the source was not recorded"}
            h = _digest(key, ident)
            if any(r.get("h") == h for r in rows):
                return {"ok": True, "added": False}
            row = {"h": h, "day": time.strftime("%Y-%m-%d"), "id": secrets.token_hex(8),
                   "k": "w" if str(ident).startswith("web:") else "m" if str(ident).startswith("meeting:") else "s"}
            label = _seal_label(ident)
            if label:
                row["label"] = label
            rows.append(row)
            _write_stopped(rows)
        return {"ok": True, "added": True}
    except Exception as e:
        _log.warning("forget_kinds: a stopped source could not be recorded: %s", type(e).__name__)
        return {"ok": False, "error": "the record could not be saved (%s)" % type(e).__name__}


_KIND_WORDS = {"w": "site", "m": "meeting"}


def list_stopped() -> Dict[str, Any]:
    """The sources the owner stopped, for the owner: {"ok", "sources":
    [{id, kind, label, day}]}. The label is the opened sealed name (a site's
    domain, "meeting <id>"), or "a stopped site from <day>" when it cannot be
    opened. Read-only."""
    try:
        rows = _read_stopped() if _stopped_path().exists() else []
    except Exception as e:
        return {"ok": False, "error": _scrubbed(e, "the stopped sources could not be read")}
    out = []
    for r in rows:
        kind = _KIND_WORDS.get(str(r.get("k") or ""), "source")
        day = str(r.get("day") or "")[:10]
        name = _open_label(r.get("label"))
        if name.startswith("web:"):
            name = name[4:]
        elif name.startswith("meeting:"):
            name = "meeting " + name[8:]
        out.append({"id": _entry_id(r), "kind": kind, "day": day, "named": bool(name),
                    "label": name or ("a stopped %s from %s" % (kind, day or "an earlier day"))})
    return {"ok": True, "sources": out}


def unstop(entry_id: str) -> Dict[str, Any]:
    """Use a stopped source again: its entry leaves the record, so it may be
    read and learned from from now on. Nothing it lost comes back."""
    eid = str(entry_id or "")
    if not _STOP_ID.match(eid):
        return {"ok": False, "error": "no such stopped source"}
    try:
        with _LOCK:
            rows = _read_stopped() if _stopped_path().exists() else []
            keep = [r for r in rows if _entry_id(r) != eid]
            if len(keep) == len(rows):
                return {"ok": False, "error": "no such stopped source"}
            _write_stopped(keep)
    except Exception as e:
        _log.warning("forget_kinds: a stopped source could not be removed: %s", type(e).__name__)
        return {"ok": False, "error": "the record could not be saved (%s)" % type(e).__name__}
    _log.info("unstop: one stopped source can be used again")
    return {"ok": True, "unstopped": 1, "note": UNSTOP_NOTE}


UNSTOP_NOTE = ("It can be used again from now on. What was removed when you stopped it does not come back: "
               "Friday learns from it again only from what it reads there next.")


#: The stopped hashes and the key, kept while the record file (and the key
#: file) are unchanged: every reply that read the web asks, per domain.
_STOP_CACHE: Dict[str, Any] = {"sig": None, "hashes": frozenset(), "key": None}
_STOP_CACHE_LOCK = threading.Lock()


def _file_sig(p: Path):
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    return (str(p), st.st_mtime_ns, st.st_size)


def _stopped_state() -> Tuple[frozenset, Optional[bytes]]:
    """(stopped hashes, key) from the record, cached on the record's and the
    key file's mtime and size. Raises when the record cannot be read."""
    from agent_friday.services import tombstones as t
    sig = (_file_sig(_stopped_path()), _file_sig(t._key_path()))
    if sig[0] is None:
        return frozenset(), None
    with _STOP_CACHE_LOCK:
        if _STOP_CACHE["sig"] == sig:
            return _STOP_CACHE["hashes"], _STOP_CACHE["key"]
    hashes = frozenset(r["h"] for r in _read_stopped())
    key = _key(create=False) if hashes else None
    if not hashes or key is not None:          # a missing key is asked again next time
        with _STOP_CACHE_LOCK:
            _STOP_CACHE.update(sig=sig, hashes=hashes, key=key)
    return hashes, key


def _forget_stop_cache() -> None:
    with _STOP_CACHE_LOCK:
        _STOP_CACHE.update(sig=None, hashes=frozenset(), key=None)


def source_stopped(ident: str, *, fail_closed: bool = False) -> bool:
    """Whether the owner stopped this source ("meeting:<id>", "web:<domain>").
    Cheap and never raises. A record that exists but cannot be read (or whose
    key is missing) answers False, logged (fail open: a proposal or graph fact
    from a source never stopped is not lost), or True with ``fail_closed``
    (meeting_archive: a stopped meeting's documents are never shown for want
    of the record; the learning paths through web_provenance.stopped)."""
    try:
        hashes, key = _stopped_state()
        if not hashes:
            return False
        if key is None:
            return bool(fail_closed)
        return _digest(key, str(ident)) in hashes
    except Exception as e:
        _log.warning("forget_kinds: the stopped-source check failed (%s): %s",
                     "fail closed" if fail_closed else "fail open", type(e).__name__)
        return bool(fail_closed)


def site_stopped(url_or_host: str, *, fail_closed: bool = False) -> bool:
    """Whether a page's site (or a parent domain of it) was stopped.
    ``fail_closed``: a record that cannot be read answers True."""
    host = _host(url_or_host) if "/" in str(url_or_host or "") else str(url_or_host or "").lower().strip(".")
    host = host[4:] if host.startswith("www.") else host
    parts = [p for p in host.split(".") if p]
    for i in range(0, max(0, len(parts) - 1)):
        if source_stopped("web:" + ".".join(parts[i:]), fail_closed=fail_closed):
            return True
    return False


# ── forget a fact ────────────────────────────────────────────────────────────
def _user_fact_text(fact_id: str) -> str:
    from agent_friday.services import user_model
    rows = _ro(user_model.DB_PATH, "SELECT text FROM facts WHERE fact_id=?", (str(fact_id),)) or []
    return str(rows[0][0] or "") if rows else ""


def _proposal_text(proposal_id: str) -> str:
    from agent_friday.services import memory_proposals as mp
    rows = _ro(mp.DB_PATH, "SELECT text FROM proposed_facts WHERE fact_id=?", (str(proposal_id),)) or []
    return mp._open(rows[0][0]) if rows else ""


def _tombstone_statement(text: str) -> Dict[str, Any]:
    from agent_friday.services import tombstones
    added, failed = 0, []
    for kind in tombstones.STATEMENT_KINDS:
        r = tombstones.add(kind, text)
        if r.get("ok"):
            added += int(bool(r.get("added")))
        else:
            failed.append(kind)
    nr = [{"what": "the record that stops Friday saving it again (%s)" % ", ".join(failed),
           "reason": "it could not be saved, so a later pass may learn it again; running forget again retries"}
          ] if failed else []
    return _row("tombstones", added, "records that stop Friday saving or suggesting it again "
                                     "(keyed hashes and a date, no text)", nr)


def _card_purge(match) -> List[Dict[str, Any]]:
    from agent_friday.services import core_memory
    c = core_memory.forget_matching(match)
    return [_row("memory_card", c["lines"], "memory card lines", c.get("not_removable") or ()),
            _row("memory_card_pending", c["pending"], "memory card suggestions awaiting review"),
            _row("memory_card_history", c["journal"], "copies in the memory card's change history")]


def _user_model_texts(match) -> List[Tuple[str, str]]:
    from agent_friday.services import user_model
    rows = _ro(user_model.DB_PATH, "SELECT fact_id, text FROM facts") or []
    return [(str(f), str(t)) for f, t in rows if t and match(str(t))]


def _proposals_purge(match) -> Dict[str, Any]:
    from agent_friday.services import memory_proposals as mp
    if not Path(mp.DB_PATH).exists():
        return _row("memory_proposals", 0, "memory suggestions (any status)")
    conn = mp._connect()
    try:
        doomed, locked = [], 0
        for fid, text in conn.execute("SELECT fact_id, text FROM proposed_facts").fetchall():
            opened = mp._open(text)
            if isinstance(text, str) and text.startswith(mp._SEALED) and not opened:
                locked += 1
                continue
            if match(opened):
                doomed.append(fid)
        for fid in doomed:
            conn.execute("DELETE FROM proposed_facts WHERE fact_id=?", (fid,))
        conn.commit()
    finally:
        conn.close()
    nr = [{"what": "meeting suggestions", "count": locked, "reason": mp.LOCKED_MESSAGE}] if locked else []
    return _row("memory_proposals", len(doomed), "memory suggestions (any status)", nr)


def _graph_fact_purge(match, forms: Iterable[str], *, extra=None) -> Dict[str, Any]:
    """The graph's fact nodes for this statement: by the id ingest_fact gives
    each surface form, by their text, or (``extra``) by any other rule; each
    deleted through owner_delete_entity (tombstoned first). Every form's node
    id is tombstoned too, so the same text is not ingested again."""
    from agent_friday.services import tombstones
    from agent_friday.services.knowledge_graph import integration
    store = _kg_store()
    ids = {_fact_node_id(f) for f in forms if str(f or "").strip()}
    doomed = [e for e in store.load("entities") if str(e.get("id") or "").startswith("fact_")
              and (e.get("id") in ids or match(e.get("description")) or (extra is not None and extra(e)))]
    n, nr = 0, []
    for e in doomed:
        out = integration.owner_delete_entity(e.get("id"), store=store)
        if out.get("ok") or out.get("tombstoned"):
            n += int(out.get("entities") or 0)
        else:
            nr.append({"what": "a knowledge-graph fact", "reason": str(out.get("warning") or "it could not be deleted")})
    for i in ids:
        if not tombstones.add("kg_entity", {"id": i}).get("ok"):
            nr.append({"what": "the record that stops the graph learning it again",
                       "reason": "it could not be saved; running forget again retries"})
            break
    return _row("kg_facts", n, "knowledge-graph fact nodes", nr)


def _turns_purge(m, transcript=None, **kw) -> List[Dict[str, Any]]:
    from agent_friday.services import forget_derived as fd
    out = fd.purge_turns(m, fd._transcript_hits(m) if transcript is None and m else (transcript or []), **kw)
    rows = [_row("conversation_recall", out.get("removed"), out.get("kind") or "",
                 out.get("not_removable") or (), out.get("retained") or ())]
    for extra in out.get("also") or []:
        rows.append(_row(str(extra.get("store")), extra.get("removed"), str(extra.get("kind") or ""),
                         extra.get("not_removable") or (), extra.get("retained") or ()))
    return rows


def forget_fact(text: str = "", *, fact_id: str = "", proposal_id: str = "",
                turn_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Forget one statement everywhere it was stored or derived, and stop it
    being learned again. `text` is the statement, or name a saved fact
    (`fact_id`) or a proposal (`proposal_id`) and its text is used.
    `turn_ids` are the owner's own turns they confirmed said it (from
    show_everywhere's candidates): only those leave recall and search; with
    none, no turn is touched and the receipt says so. Returns a receipt:
    {"ok", "stores": [rows], "note"}; never the statement itself."""
    text = _clip(text)
    try:
        if not text and fact_id and _FACT_ID.match(str(fact_id)):
            text = _clip(_user_fact_text(fact_id))
        if not text and proposal_id and _FACT_ID.match(str(proposal_id)):
            text = _clip(_proposal_text(proposal_id))
    except Exception as e:
        return {"ok": False, "error": _scrubbed(e, "that fact could not be read")}
    if not _norm(text):
        return {"ok": False, "error": "say which statement to forget"}
    match = statement_matcher(text)
    rows: List[Dict[str, Any]] = []
    with _LOCK:
        # 0. The one-time move of the old facts onto the card runs before the
        #    tombstones: it adds the owner's facts as the owner's own words,
        #    which would lift them again.
        try:
            from agent_friday.services import core_memory
            core_memory.ensure_migrated()
        except Exception as e:
            _log.warning("forget-fact: the card's one-time move did not run: %s", type(e).__name__)
        # 1. The tombstones first: a pass running meanwhile already skips it.
        try:
            rows.append(_tombstone_statement(text))
        except Exception as e:
            rows.append(_failed("tombstones", "records that stop Friday saving it again", e, "the tombstones"))
        forms = {text}
        # 2. The memory card: its lines, the review queue, the change history.
        try:
            from agent_friday.services import core_memory
            forms |= {e for t in core_memory.TARGETS for e in core_memory.entries(t) if match(e)}
            rows += _card_purge(match)
        except Exception as e:
            rows.append(_failed("memory_card", "memory card lines", e, "the memory card"))
        # 3. The older user model's facts.
        try:
            from agent_friday.services import user_model
            forms |= {t for _f, t in _user_model_texts(match)}
            rows.append(_row("user_model", user_model.forget_matching(match), "facts in the older user model"))
        except Exception as e:
            rows.append(_failed("user_model", "facts in the older user model", e, "the older user model"))
        # 4. Proposals, any status.
        try:
            rows.append(_proposals_purge(match))
        except Exception as e:
            rows.append(_failed("memory_proposals", "memory suggestions", e, "memory suggestions"))
        # 4b. The owner's correction history (services/corrections): an event
        #     whose old claim or new wording is this statement keeps a sealed
        #     copy of it, so it goes too.
        try:
            from agent_friday.services import corrections
            c = corrections.forget_matching(match)
            nr = ([{"what": "correction records", "count": c["locked"],
                    "reason": "they are sealed and the keystore is locked; running forget again retries"}]
                  if c.get("locked") else [])
            rows.append(_row("corrections", c["removed"], "records of your corrections", nr))
        except Exception as e:
            rows.append(_failed("corrections", "records of your corrections", e, "the correction history"))
        # 5. The nightly dreaming log.
        try:
            from agent_friday.services import memory_dreaming
            c = memory_dreaming.forget_matching(match)
            rows.append(_row("dreams", c["facts"] + c["topics"] + c["lines"],
                             "items in the nightly dreaming log", c.get("not_removable") or ()))
        except Exception as e:
            rows.append(_failed("dreams", "items in the nightly dreaming log", e, "the dreaming log"))
        # 6. The knowledge graph's fact nodes for it.
        try:
            rows.append(_graph_fact_purge(match, forms))
        except Exception as e:
            rows.append(_failed("kg_facts", "knowledge-graph fact nodes", e, "the knowledge graph"))
        # 7. The turns the owner confirmed said it (and only those): out of
        #    recall and search, the summaries made from them queued again, the
        #    graph's retraction requested.
        chosen: List[Tuple[str, Dict[str, Any]]] = []
        try:
            chosen = _confirmed_turns(turn_ids or [])
            if chosen:
                m = _ConversationTexts([msg["text"] for _c, msg in chosen], present=True)
                rows += _turns_purge(m, chosen, index_match=lambda _t: False, by_id=True,
                                     retained_what="the messages you chose")
            else:
                rows.append(_row("conversation_recall", 0, "conversation turns removed from recall and search", (),
                                 [{"what": "messages that may have said it",
                                   "reason": ("none were chosen, so no message was changed: tick them under "
                                              "Forget this (or delete them from History) to stop them being "
                                              "recalled")}]))
        except Exception as e:
            rows.append(_failed("conversation_recall", "conversation turns", e, "conversation recall"))
    _log.info("forget-fact: %d stores, %d items removed, %d turns chosen",
              len(rows), sum(r["removed"] for r in rows), len(chosen))
    turns_note = ("The messages you chose are no longer recalled or searched; your conversation history keeps "
                  "them, in your words." if chosen else
                  "No conversation messages were chosen, so none were changed; they are still recalled.")
    return {"ok": True, "kind": "fact", "stores": rows,
            "note": ("The statement is gone from the memory card, saved facts, suggestions, the dreaming log and "
                     "the knowledge graph, and Friday will not save or suggest it again unless you say it "
                     "yourself. " + turns_note)}


# ── forget a conversation ────────────────────────────────────────────────────
_RECEIPTS_REASON = ("signed records of what Friday did; they are kept unchanged so the chain of receipts stays "
                    "verifiable")


def _receipts_row(cid: str) -> Dict[str, Any]:
    """Signed action receipts that mention the conversation: kept, counted."""
    try:
        from agent_friday.services import action_journal as aj
        from agent_friday.services.forget_derived import _lines_naming
        n = _lines_naming(aj._path(), lambda s: cid in str(s or ""))
    except Exception:
        n = 0
    return _row("action_receipts", 0, "signed action receipts", (),
                [{"what": "signed action receipts that mention this conversation", "count": n,
                  "reason": _RECEIPTS_REASON}] if n else ())


def _delete_transcript(cid: str) -> Dict[str, Any]:
    """The transcript, its record and its files, through conversations.delete
    (every message leaves every index the way a per-message delete does)."""
    from agent_friday.services import conversations as cv
    try:
        out = cv.delete(cid)
    except Exception as e:
        return _failed("transcript", "messages deleted from History", e, "the conversation's transcript")
    if not out.get("ok"):
        nr = [{"what": "the conversation's transcript", "reason": str(out.get("error") or "not deleted")}]
        if out.get("leftovers"):
            # Part way: its indexes and some files are gone, its record is
            # kept so running it again finishes.
            nr[0]["count"] = int(out["leftovers"])
        return _row("transcript", 0, "messages deleted from History", nr)
    nr = [{"what": "some of its index entries",
           "reason": "an index could not be updated; the transcript itself is deleted"}] if out.get("index_errors") else []
    kind = "messages deleted from History (the transcript, its record and its files)"
    if cid == cv.MAIN_ID:
        kind = "messages deleted from History (Main is emptied; the conversation itself stays)"
    return _row("transcript", int(out.get("messages") or 0), kind, nr)


def forget_conversation(conversation_id: str, *, delete_transcript: bool = False) -> Dict[str, Any]:
    """Stop using one conversation in memory, and with `delete_transcript`
    delete its transcript too (the owner's separate confirm, owner ruling 9).
    Returns a receipt."""
    from agent_friday.services import conversations as cv
    cid = str(conversation_id or "")
    try:
        conv = cv.load(cid) if cid and len(cid) <= 80 else None
    except Exception:
        conv = None
    # Only the exact id: Windows opens "CONV-MAIN" or "conv-main." as Main's
    # folder, whose record says "conv-main" (conversations.delete checks too).
    if not conv or conv.get("id") != cid:
        return {"ok": False, "error": "no such conversation"}
    delete_transcript = delete_transcript is True
    msgs = [m for m in cv._stored_messages(cid)
            if isinstance(m, dict) and m.get("id") and isinstance(m.get("text"), str) and m["text"].strip()]
    transcript = [(cid, m) for m in msgs]
    m = _ConversationTexts([x["text"] for x in msgs], present=bool(msgs))
    with _LOCK:
        try:
            rows = _turns_purge(m, transcript, index_match=lambda _t: False, by_id=True,
                                retained_what="messages in this conversation", retained_reason=_CONVERSATION_REASON)
        except Exception as e:
            rows = [_failed("conversation_recall", "conversation turns", e, "conversation recall")]
        deleted, gone = None, False
        if delete_transcript:
            deleted = _delete_transcript(cid)
            gone = not any("transcript" in str(x.get("what") or "") for x in deleted["not_removable"])
            if gone:
                # The transcript is gone: the recall row no longer keeps it.
                for r in rows:
                    if r["store"] == "conversation_recall":
                        r["retained"] = []
            rows.append(deleted)
    rows.append(_row("learned_from_it", 0, "memory-card lines and saved facts learned from it",
                     [{"what": "lines and facts Friday may have learned from this conversation",
                       "reason": _DERIVED_REASON}] if msgs else ()))
    rows.append(_receipts_row(cid))
    _log.info("forget-conversation: %d turns, transcript %s", len(msgs),
              "kept" if deleted is None else "deleted" if gone else "not deleted")
    if gone:
        note = ("This conversation is no longer recalled, searched, summarised or used for Friday's memory, and "
                "its transcript is deleted from History; that cannot be undone. Signed receipts of what Friday "
                "did in it are kept.")
    elif deleted is not None:
        note = ("This conversation is no longer recalled, searched, summarised or used for Friday's memory, but "
                "its transcript could not be deleted; see below. Running it again retries.")
    else:
        note = ("This conversation is no longer recalled, searched, summarised or used for Friday's memory. "
                "It stays in History; what you say in it from now on is remembered as usual.")
    return {"ok": True, "kind": "conversation", "stores": rows, "transcript_deleted": gone, "note": note}


# ── stop using a source ──────────────────────────────────────────────────────
#: Common public suffixes (a small built-in subset of the Public Suffix List:
#: registries' second levels and shared hosting domains). Stopping one of
#: these would stop every unrelated site under it, so a site must have at
#: least one label beyond its suffix. A one-label suffix (com, io, uk) never
#: passes _DOMAIN, which needs a dot.
_PUBLIC_SUFFIXES = frozenset((
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "sch.uk", "nhs.uk",
    "police.uk", "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au", "co.nz", "org.nz",
    "net.nz", "govt.nz", "ac.nz", "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp", "co.kr", "or.kr", "com.br",
    "net.br", "org.br", "gov.br", "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "com.mx", "org.mx",
    "com.ar", "com.tr", "com.sg", "edu.sg", "com.hk", "com.tw", "co.in", "net.in", "org.in", "gov.in",
    "ac.in", "co.za", "org.za", "gov.za", "com.my", "co.id", "co.il", "org.il", "co.th", "com.ph",
    "com.vn", "com.pk", "com.ng", "com.eg", "com.sa", "co.ke", "com.ua", "com.pl", "co.at", "or.at",
    "com.es", "com.pt", "com.co", "com.pe", "com.ve", "gc.ca", "qc.ca", "on.ca", "bc.ca", "eu.org",
    "github.io", "gitlab.io", "githubusercontent.com", "blogspot.com", "herokuapp.com", "appspot.com",
    "netlify.app", "vercel.app", "pages.dev", "workers.dev", "web.app", "firebaseapp.com",
    "azurewebsites.net", "cloudfront.net", "s3.amazonaws.com", "readthedocs.io", "ngrok.io",
    "ngrok-free.app", "duckdns.org", "glitch.me", "onrender.com", "fly.dev", "r2.dev", "repl.co"))


def public_suffix_only(host: str) -> bool:
    """True when `host` is itself a public suffix (co.uk, github.io), so it
    names no one site."""
    h = str(host or "").lower().strip(".")
    return "." not in h or h in _PUBLIC_SUFFIXES


def parse_source(source: Any) -> Tuple[str, str]:
    """(kind, value): kind is library, meeting, web, sender or "" (not a source
    this build knows). The value is normalised (a URL is read as its site)."""
    s = str(source or "").strip()[:MAX_SOURCE]
    low = s.lower()
    for prefix, kind in (("library:", "library"), ("meeting:", "meeting"), ("web:", "web"),
                         ("domain:", "web"), ("site:", "web"), ("sender:", "sender"), ("mail:", "sender")):
        if low.startswith(prefix):
            value = s[len(prefix):].strip()
            break
    else:
        kind, value = "", s
        if low.startswith(("http://", "https://")):
            kind = "web"
        elif _EMAIL.match(s):
            kind = "sender"
        elif _DOMAIN.match(low.strip(".")):
            kind = "web"
    if kind == "library":
        return ("library", value) if value.isascii() and value.isdigit() and len(value) <= 12 else ("", s)
    if kind == "meeting":
        return ("meeting", value) if _MEETING_ID.match(value) else ("", s)
    if kind == "web":
        host = _host(value) if "/" in value else value.lower().strip(".")
        host = host[4:] if host.startswith("www.") else host
        return ("web", host) if _DOMAIN.match(host) and not public_suffix_only(host) else ("", s)
    if kind == "sender":
        return ("sender", value.lower()) if _EMAIL.match(value) else ("", s)
    return "", s


def _stop_row(ident: str) -> Dict[str, Any]:
    r = record_stopped(ident)
    if r.get("ok"):
        return _row("stopped_sources", int(bool(r.get("added"))),
                    "record that stops Friday taking in anything new from it (a keyed hash and a date)")
    return _row("stopped_sources", 0, "record that stops Friday taking in anything new from it",
                [{"what": "the record of this source", "reason": str(r.get("error") or "it could not be saved")}])


def _stop_library(doc_id: str) -> List[Dict[str, Any]]:
    from agent_friday.services.library import forget as lf
    from agent_friday.services.library import principal as pr
    p = pr.current()
    if p is None:
        return [_row("library", 0, "Library document",
                     [{"what": "the Library", "reason": "it is not available to this account"}])]
    out = lf.forget_document(p, int(doc_id))
    if not out.get("ok"):
        return [_row("library", 0, "Library document",
                     [{"what": "the document", "reason": str(out.get("error") or "it could not be forgotten")}])]
    nr = [{"what": str(x), "reason": "it could not be cleaned now; the document is hidden and the next sweep "
                                     "finishes it"} for x in (out.get("incomplete") or [])]
    return [_row("library", 1, "Library document forgotten (its passages, vectors, staged drafts and graph facts; "
                               "it is never indexed again)", nr,
                 [{"what": "the file on disk", "count": 1,
                   "reason": "your file: Friday does not delete it; deleting it is a separate action"}]),
            _row("saved_chats", int(out.get("messages_rewritten") or 0),
                 "saved chat messages that cited or quoted it, rewritten without it")]


def _stop_meeting(mid: str) -> List[Dict[str, Any]]:
    from agent_friday.services import core_memory
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    from agent_friday.services import user_model
    origin = "meeting:%s" % mid
    try:
        core_memory.ensure_migrated()           # before any tombstone: it adds as the owner (see forget_fact)
    except Exception as e:
        _log.warning("stop-using-source: the card's one-time move did not run: %s", type(e).__name__)
    rows = [_stop_row(origin)]
    texts: List[str] = []
    tomb_failed: List[str] = []

    def _tomb(kind: str, text: str) -> None:
        try:
            ok = bool(tombstones.add(kind, text).get("ok"))
        except Exception:
            ok = False
        if not ok and kind not in tomb_failed:
            tomb_failed.append(kind)
    # Its proposals: each statement tombstoned, then every row from it removed.
    try:
        n, locked = 0, 0
        if Path(mp.DB_PATH).exists():
            conn = mp._connect()
            try:
                found = [(t, p) for t, p in conn.execute(
                    "SELECT text, provenance FROM proposed_facts WHERE provenance IS NOT NULL AND provenance != ''")
                    if mp._meeting_id_of(p) == mid]
            finally:
                conn.close()
            for t, _p in found:
                opened = mp._open(t)
                if isinstance(t, str) and t.startswith(mp._SEALED) and not opened:
                    locked += 1
                elif (opened or "").strip():
                    _tomb("proposal", opened)
                    texts.append(opened)
            n = mp.purge_meeting(mid)
        rows.append(_row("memory_proposals", n, "memory suggestions from this meeting",
                         [{"what": "meeting suggestions", "count": locked,
                           "reason": "they could not be opened to record them as refused (the keystore is not "
                                     "available); they were removed"}] if locked else ()))
    except Exception as e:
        rows.append(_failed("memory_proposals", "memory suggestions from this meeting", e, "memory suggestions"))
    # The facts it gave (approved from it), and their card lines.
    try:
        facts = _ro(user_model.DB_PATH, "SELECT fact_id, text FROM facts WHERE source=?", (origin,)) or []
        n = 0
        for fid, t in facts:
            if t:
                _tomb("user_fact", str(t))          # before the delete: a pass meanwhile does not save it again
                texts.append(str(t))
            n += int(user_model.forget_fact(str(fid)).get("deleted") or 0)
        rows.append(_row("user_model", n, "facts kept from this meeting"))
    except Exception as e:
        rows.append(_failed("user_model", "facts kept from this meeting", e, "the saved facts"))
    try:
        norms = {_norm(t) for t in texts if _norm(t)}
        for t in texts:
            _tomb("card_line", t)
        rows += _card_purge(lambda s: bool(s) and _norm(s) in norms) if norms else [
            _row("memory_card", 0, "memory card lines")]
    except Exception as e:
        rows.append(_failed("memory_card", "memory card lines", e, "the memory card"))
    if tomb_failed:
        rows.append(_row("tombstones", 0, "records that stop Friday saving or suggesting its statements again",
                         [{"what": "the record that stops Friday saving them again (%s)" % ", ".join(tomb_failed),
                           "reason": "it could not be saved, so a later pass may learn them again; running stop "
                                     "again retries"}]))
    try:
        norms = {_norm(t) for t in texts if _norm(t)}
        rows.append(_graph_fact_purge(
            lambda s: bool(s) and _norm(s) in norms, texts,
            extra=lambda e: origin in ((e.get("provenance") or {}).get("conversations") or [])))
    except Exception as e:
        rows.append(_failed("kg_facts", "knowledge-graph fact nodes", e, "the knowledge graph"))
    # Its searchable copies in the Library (meeting_archive: the transcript and
    # notes as Library documents), through the Library's forget module; from
    # now on meeting_archive.covers and its indexing refuse a stopped meeting.
    try:
        from agent_friday.services import meeting_archive as ma
        rows.append(_row("meeting_archive", int(ma.remove_meeting(mid) or 0),
                         "searchable copies of its transcript and notes in the Library (never read in again)"))
    except Exception as e:
        rows.append(_failed("meeting_archive", "searchable copies of its transcript and notes", e,
                            "the meetings archive"))
    rows.append(_row("meeting_record", 0, "the meeting's own record", (),
                     [{"what": "the meeting's transcript and notes", "count": 1,
                       "reason": "the meeting's own record stays in Meetings, under its own protection; delete "
                                 "the meeting there to remove it"}]))
    return rows


#: Provenance that means the owner's own material also holds an entity (a
#: wiki page, a Library document, a cognitive note): stopping a site does not
#: delete such an entity, it says so.
_OWN_PROVENANCE = ("wiki_pages", "docs", "cognitive_keys")
_SITE_TURNS_REASON = ("your own conversation history: Friday does not rewrite it (you can delete any message from "
                      "History). These replies are no longer recalled, searched, summarised or replayed to the model")


def _site_matcher(domain: str):
    from agent_friday.services import web_provenance as wp
    return lambda ws: wp.on_site(ws, domain)


def _site_turns(on) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[Dict[str, Any]]]:
    """(transcript replies whose recorded sites ``on`` accepts, vector rows
    carrying them that no transcript message holds)."""
    from agent_friday.services import conversations as cv
    found: List[Tuple[str, Dict[str, Any]]] = []
    if cv._root().exists():
        for conv in cv.list_all(include_archived=True):
            cid = conv.get("id")
            if not cid:
                continue
            for msg in cv._stored_messages(cid):
                if not isinstance(msg, dict) or not msg.get("id"):
                    continue
                ws = (msg.get("meta") or {}).get("web_sources") or msg.get("web_sources")
                if ws and on(ws):
                    found.append((str(cid), msg))
    extra: List[Dict[str, Any]] = []
    try:
        from agent_friday.services.forget_derived import _memory
        mem, _why = _memory()
        rows = mem.with_web_sources(on) if mem is not None else None
        ids = {str(m.get("id")) for _c, m in found}
        extra = [r for r in (rows or []) if str(r.get("id")) not in ids]
    except Exception as e:
        _log.warning("stop-site: the vector memory was not checked for the site: %s", type(e).__name__)
    return found, extra


def _stop_site_turns(on) -> List[Dict[str, Any]]:
    found, extra = _site_turns(on)
    # A vector row with no transcript message is taken by its id, with the
    # same receipt (its "message" is the stored text, dated by the row).
    transcript = found + [("", {"id": r["id"], "text": r.get("text") or "", "ts": None}) for r in extra]
    if not transcript:
        return [_row("conversation_recall", 0, "replies that used pages from this site, removed from recall and "
                                               "search")]
    m = _ConversationTexts([str(msg.get("text") or "") for _c, msg in transcript], present=True)
    rows = _turns_purge(m, transcript, index_match=lambda _t: False, by_id=True,
                        retained_what="replies that used pages from this site", retained_reason=_SITE_TURNS_REASON)
    for r in rows:
        if r["store"] == "conversation_recall":
            r["kind"] = "replies that used pages from this site, removed from recall and search"
            r["retained"] = ([{"what": "replies that used pages from this site", "count": len(found),
                               "reason": _SITE_TURNS_REASON}] if found else [])
    try:
        from agent_friday.services import memory_review
        memory_review.forget_web_source(on)
    except Exception:
        pass
    return rows


def _stop_site_suggestions(on) -> List[Dict[str, Any]]:
    """Memory-card suggestions and memory suggestions read from the site:
    each text recorded as refused first, then removed."""
    from agent_friday.services import core_memory
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    rows, failed = [], []

    def _tomb(kind: str, text: str) -> None:
        try:
            ok = bool(tombstones.add(kind, text).get("ok"))
        except Exception:
            ok = False
        if not ok and kind not in failed:
            failed.append(kind)
    try:
        pend = core_memory.drop_pending(lambda r: bool(r.get("web_sources")) and on(r.get("web_sources")),
                                        apply=False)
        for r in pend:
            if (r.get("text") or "").strip():
                _tomb("card_line", r["text"])
        n = len(core_memory.drop_pending(lambda r: bool(r.get("web_sources")) and on(r.get("web_sources"))))
        rows.append(_row("memory_card_pending", n, "memory card suggestions read from this site"))
    except Exception as e:
        rows.append(_failed("memory_card_pending", "memory card suggestions read from this site", e,
                            "the memory card's review queue"))
    try:
        texts = mp.purge_web_source(on, apply=False)
        for t in texts:
            if (t or "").strip():
                _tomb("proposal", t)
        n = len(mp.purge_web_source(on))
        rows.append(_row("memory_proposals", n, "memory suggestions read from this site (any status)"))
    except Exception as e:
        rows.append(_failed("memory_proposals", "memory suggestions read from this site", e, "memory suggestions"))
    if failed:
        rows.append(_row("tombstones", 0, "records that stop Friday suggesting them again",
                         [{"what": "the record that stops Friday suggesting them again (%s)" % ", ".join(failed),
                           "reason": "it could not be saved, so a later pass may suggest them again; running stop "
                                     "again retries"}]))
    return rows


def _site_entities(domain: str, entities) -> Tuple[List[str], int]:
    """(ids of graph entities to delete, how many are also held by the owner's
    own material and kept). Deleted: a research fact whose only recorded
    sources are pages on the site, and an entity learned in chat from a reply
    that read the site."""
    on = _site_matcher(domain)
    doomed, mixed = [], 0
    for e in entities:
        prov = e.get("provenance") or {}
        urls = [s.get("url") for s in (prov.get("sources") or []) if isinstance(s, dict)]
        hosts = [_host(u) for u in urls if u]
        if hosts and all(_on_site(h, domain) for h in hosts):
            doomed.append(e.get("id"))
        elif prov.get("web_sources") and on(prov.get("web_sources")):
            if any(prov.get(k) for k in _OWN_PROVENANCE):
                mixed += 1
            else:
                doomed.append(e.get("id"))
    return doomed, mixed


def _stop_site(domain: str) -> List[Dict[str, Any]]:
    from agent_friday.services import web_provenance as wp
    from agent_friday.services.knowledge_graph import integration
    rows = [_stop_row("web:" + domain)]
    on = _site_matcher(domain)
    try:
        store = _kg_store()
        doomed, mixed = _site_entities(domain, store.load("entities"))
        n, nr = 0, []
        for eid in doomed:
            out = integration.owner_delete_entity(eid, store=store)
            if out.get("ok") or out.get("tombstoned"):
                n += int(out.get("entities") or 0)
            else:
                nr.append({"what": "a knowledge-graph fact", "reason": str(out.get("warning") or "not deleted")})
        if mixed:
            nr.append({"what": "knowledge-graph entries also learned from your own notes or Library", "count": mixed,
                       "reason": "they are kept because your own material holds them too; use Delete on one in the "
                                 "graph to remove it"})
        rows.append(_row("kg_facts", n, "knowledge-graph facts learned from this site (research, or read in chat)",
                         nr))
    except Exception as e:
        rows.append(_failed("kg_facts", "knowledge-graph facts learned from this site", e, "the knowledge graph"))
    try:
        rows += _stop_site_turns(on)
    except Exception as e:
        rows.append(_failed("conversation_recall", "replies that used pages from this site", e,
                            "conversation recall"))
    rows += _stop_site_suggestions(on)
    if wp.registered_domain(domain) and wp.registered_domain(domain) != domain:
        rows.append(_row("chat_reads", 0, "what Friday read on this site in chat", [
            {"what": "replies that read other pages of %s" % "its parent site",
             "reason": "chat records each reply's sites by their registered domain, so a part of a site cannot be "
                       "told apart there; stop the whole site to stop what was read in chat"}]))
    return rows


def _stop_sender(email: str) -> List[Dict[str, Any]]:
    rows = []
    try:
        from agent_friday.services import message_triage as mt
        r = mt.forget_sender(email)
        rows.append(_row("sender_signals", int(bool(r.get("removed"))), "what the triage learned about this sender"))
    except Exception as e:
        rows.append(_failed("sender_signals", "what the triage learned about this sender", e, "sender signals"))
    rows.append(_row("mail_content", 0, "mail from this sender in Friday's memory",
                     [{"what": "mail from this sender in other stores",
                       "reason": "not supported yet: mail is not linked to its sender in Friday's memory on this "
                                 "build. To remove what is held about a person, forget the person"}]))
    return rows


def stop_using_source(source: str) -> Dict[str, Any]:
    """Stop using one source; see the module docstring. Returns a receipt."""
    kind, value = parse_source(source)
    if not kind:
        return {"ok": True, "kind": "source", "source_kind": "unknown",
                "stores": [_row("source", 0, "source",
                                [{"what": "this kind of source", "reason": "not supported yet: a source is a Library "
                                  "document (library:<id>), a meeting (meeting:<id>), a web site (web:<domain>) or "
                                  "a mail sender (sender:<address>)"}])],
                "note": "Nothing was changed."}
    with _LOCK:
        try:
            rows = {"library": _stop_library, "meeting": _stop_meeting,
                    "web": _stop_site, "sender": _stop_sender}[kind](value)
        except Exception as e:
            rows = [_failed(kind, "source", e, "this source")]
    _log.info("stop-using-source: %s, %d stores", kind, len(rows))
    return {"ok": True, "kind": "source", "source_kind": kind, "stores": rows, "note": _SOURCE_NOTES[kind]}


#: The receipt's note for each kind of source: what stopping it reaches, and no more.
_SOURCE_NOTES = {
    "library": ("The document is forgotten from the Library and is never read in again; saved chats that cited "
                "it were rewritten without it. The file on disk is yours and stays."),
    "meeting": ("The meeting's suggestions, the facts and card lines it gave, its knowledge-graph facts and its "
                "searchable copies in the Library are removed, and nothing new is taken from it. The meeting's "
                "own record stays in Meetings."),
    "web": ("Replies that used pages from this site are no longer recalled or searched (History keeps them), and "
            "the knowledge-graph facts, memory card suggestions and memory suggestions learned from what Friday "
            "read there are removed. From now on Friday may still open the site when you ask, but nothing read "
            "there is remembered or suggested, and research findings from it are not taken in. Use it again from "
            "Stopped sources if you change your mind; that does not bring back what was removed."),
    "sender": ("What the mail triage learned about this sender is removed. Nothing else is changed: mail is not "
               "linked to its sender in Friday's memory on this build."),
}


# ── show everywhere (read-only) ──────────────────────────────────────────────
def _where(store: str, count: int, kind: str, locations=()) -> Dict[str, Any]:
    return {"store": store, "count": int(count or 0), "kind": kind, "locations": list(locations)[:LOCATIONS_MAX]}


def _unchecked(store: str, kind: str, e: BaseException) -> Dict[str, Any]:
    return dict(_where(store, 0, kind), unchecked=_scrubbed(e, "it could not be read"))


def _turn_locations(m) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(count, per-conversation locations, the candidate turns themselves)."""
    cands = turn_candidates(m)
    per: Dict[str, int] = {}
    titles: Dict[str, str] = {}
    for c in cands:
        per[c["conversation_id"]] = per.get(c["conversation_id"], 0) + 1
        titles[c["conversation_id"]] = c["title"]
    locs = [{"conversation_id": c, "title": titles[c], "messages": n}
            for c, n in sorted(per.items(), key=lambda kv: -kv[1])]
    return len(cands), locs, cands


def _statement_report(q: str) -> List[Dict[str, Any]]:
    from agent_friday.services import core_memory
    match = statement_matcher(q)
    out = []
    try:
        locs = []
        for t in core_memory.TARGETS:
            for i, e in enumerate(core_memory.entries(t)):
                if match(e):
                    locs.append({"file": "About you" if t == "user" else "Working with you", "line": i + 1})
        pend = sum(1 for r in core_memory._read_jsonl(core_memory._pending_path())
                   if match(r.get("text")) or match(r.get("old")))
        out.append(_where("memory_card", len(locs), "memory card lines", locs))
        out.append(_where("memory_card_pending", pend, "memory card suggestions awaiting review"))
    except Exception as e:
        out.append(_unchecked("memory_card", "memory card lines", e))
    try:
        out.append(_where("user_model", len(_user_model_texts(match)), "facts in the older user model"))
    except Exception as e:
        out.append(_unchecked("user_model", "facts in the older user model", e))
    try:
        from agent_friday.services import memory_proposals as mp
        rows = _ro(mp.DB_PATH, "SELECT text, status FROM proposed_facts") or []
        hit = [s for t, s in rows if match(mp._open(t))]
        out.append(_where("memory_proposals", len(hit), "memory suggestions",
                          [{"status": s, "count": hit.count(s)} for s in sorted(set(hit))]))
    except Exception as e:
        out.append(_unchecked("memory_proposals", "memory suggestions", e))
    try:
        from agent_friday.services import memory_dreaming
        rows = _ro(memory_dreaming.DB_PATH, "SELECT day, consolidated_json FROM dreams") or []
        days = []
        for day, cj in rows:
            try:
                facts = json.loads(cj) if cj else []
            except ValueError:
                facts = []
            n = sum(1 for f in facts if isinstance(f, dict) and match(f.get("text")))
            if n:
                days.append({"day": str(day), "count": n})
        out.append(_where("dreams", sum(d["count"] for d in days), "items in the nightly dreaming log", days))
    except Exception as e:
        out.append(_unchecked("dreams", "items in the nightly dreaming log", e))
    try:
        ids = {_fact_node_id(q)}
        n = sum(1 for e in _kg_store().load("entities") if str(e.get("id") or "").startswith("fact_")
                and (e.get("id") in ids or match(e.get("description"))))
        out.append(_where("kg_facts", n, "knowledge-graph fact nodes"))
    except Exception as e:
        out.append(_unchecked("kg_facts", "knowledge-graph fact nodes", e))
    tm = TurnMatcher(q)
    try:
        n, locs, cands = _turn_locations(tm)
        row = _where("conversation_turns", n, "your messages that may have said it (still recalled and searched; "
                                              "Forget this removes only the ones you tick)", locs)
        row["candidates"] = cands
        if not tm:
            row["note"] = tm.notes()[0]["reason"]
        out.append(row)
    except Exception as e:
        out.append(_unchecked("conversation_turns", "conversation messages that state it", e))
    try:
        from agent_friday.services import tombstones
        out.append(_where("tombstones", int(tombstones.is_tombstoned(tombstones.STATEMENT_KINDS, q)),
                          "already recorded as deleted (Friday does not save it again)"))
    except Exception as e:
        out.append(_unchecked("tombstones", "already recorded as deleted", e))
    return out


def _person_report(q: str) -> Optional[Dict[str, Any]]:
    if len(q.split()) > 4 or len(q) > 80:
        return None
    try:
        from agent_friday.services import forget_person as fp
        r = fp.find(q)
    except Exception:
        return None
    if not r.get("found"):
        return None
    return {"people_graph": bool((r.get("people_graph") or {}).get("present")),
            "knowledge_graph": dict(r.get("knowledge_graph") or {}),
            "relationships": dict(r.get("relationships") or {}),
            "wiki_pages": list(r.get("sources") or [])[:LOCATIONS_MAX],
            "forgotten": bool(r.get("forgotten"))}


def _source_report(kind: str, value: str) -> List[Dict[str, Any]]:
    out = []
    if kind == "library":
        try:
            from agent_friday.services.library import forget as lf
            from agent_friday.services.library import principal as pr
            from agent_friday.services.library import store as lstore
            p = pr.current()
            # Only a Library that exists is opened: opening one creates it.
            st = (lstore.store_for(p) if p and (lstore.library_root() / p / "library.sqlite").exists()
                  else None)
            doc = st.get_document(int(value)) if st else None
            out.append(_where("library", int(bool(doc)), "Library document"))
            out.append(_where("saved_chats", len(lf.cited_conversations(st, int(value))) if doc else 0,
                              "conversations that cited it"))
        except Exception as e:
            out.append(_unchecked("library", "Library document", e))
    elif kind == "meeting":
        origin = "meeting:" + value
        try:
            from agent_friday.services import memory_proposals as mp
            rows = _ro(mp.DB_PATH, "SELECT provenance FROM proposed_facts WHERE provenance IS NOT NULL") or []
            out.append(_where("memory_proposals", sum(1 for (p,) in rows if mp._meeting_id_of(p) == value),
                              "memory suggestions from this meeting"))
        except Exception as e:
            out.append(_unchecked("memory_proposals", "memory suggestions from this meeting", e))
        try:
            from agent_friday.services import user_model
            rows = _ro(user_model.DB_PATH, "SELECT COUNT(*) FROM facts WHERE source=?", (origin,)) or [(0,)]
            out.append(_where("user_model", rows[0][0], "facts kept from this meeting"))
        except Exception as e:
            out.append(_unchecked("user_model", "facts kept from this meeting", e))
        try:
            n = sum(1 for e in _kg_store().load("entities")
                    if origin in ((e.get("provenance") or {}).get("conversations") or []))
            out.append(_where("kg_facts", n, "knowledge-graph facts from this meeting"))
        except Exception as e:
            out.append(_unchecked("kg_facts", "knowledge-graph facts from this meeting", e))
        out.append(_where("stopped_sources", int(source_stopped(origin)), "already stopped"))
    elif kind == "web":
        on = _site_matcher(value)
        try:
            doomed, _mixed = _site_entities(value, _kg_store().load("entities"))
            out.append(_where("kg_facts", len(doomed), "knowledge-graph facts learned from this site"))
        except Exception as e:
            out.append(_unchecked("kg_facts", "knowledge-graph facts learned from this site", e))
        try:
            found, extra = _site_turns(on)
            per: Dict[str, int] = {}
            for c, _m in found:
                per[c] = per.get(c, 0) + 1
            titles = {}
            try:
                from agent_friday.services import conversations as cv
                titles = {c: str((cv.load(c) or {}).get("title") or "")[:80] for c in per}
            except Exception:
                pass
            out.append(_where("conversation_turns", len(found) + len(extra),
                              "replies that used pages from this site (still recalled and searched)",
                              [{"conversation_id": c, "title": titles.get(c, ""), "messages": n}
                               for c, n in sorted(per.items(), key=lambda kv: -kv[1])]))
        except Exception as e:
            out.append(_unchecked("conversation_turns", "replies that used pages from this site", e))
        try:
            from agent_friday.services import core_memory
            from agent_friday.services import memory_proposals as mp
            pend = core_memory.drop_pending(lambda r: bool(r.get("web_sources")) and on(r.get("web_sources")),
                                            apply=False)
            out.append(_where("memory_card_pending", len(pend), "memory card suggestions read from this site"))
            rows = _ro(mp.DB_PATH, "SELECT provenance FROM proposed_facts WHERE provenance IS NOT NULL") or []
            out.append(_where("memory_proposals", sum(1 for (p,) in rows if mp.web_sources_in(p)
                                                      and on(mp.web_sources_in(p))),
                              "memory suggestions read from this site"))
        except Exception as e:
            out.append(_unchecked("memory_proposals", "memory suggestions read from this site", e))
        out.append(_where("stopped_sources", int(source_stopped("web:" + value)), "already stopped"))
    elif kind == "sender":
        try:
            from agent_friday.services import message_triage as mt
            senders = (mt._load_signals() or {}).get("senders") or {}
            out.append(_where("sender_signals", int(value in senders), "what the triage learned about this sender"))
        except Exception as e:
            out.append(_unchecked("sender_signals", "what the triage learned about this sender", e))
    return out


def show_everywhere(query: str) -> Dict[str, Any]:
    """Where a statement, a person or a source appears: counts and short
    locations. Read-only: nothing is written anywhere."""
    q = _clip(query)
    if not _norm(q):
        return {"ok": False, "error": "say what to look for"}
    kind, value = parse_source(q)
    if kind:
        return {"ok": True, "read_only": True, "query_kind": "source", "source_kind": kind,
                "stores": _source_report(kind, value)}
    out: Dict[str, Any] = {"ok": True, "read_only": True, "query_kind": "statement",
                           "stores": _statement_report(q)}
    person = _person_report(q)
    if person:
        out["person"] = person
    out["total"] = sum(int(r.get("count") or 0) for r in out["stores"] if r["store"] != "tombstones")
    return out
