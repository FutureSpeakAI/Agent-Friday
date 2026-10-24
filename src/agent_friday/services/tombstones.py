"""tombstones -- what the owner deleted stays deleted (NS-30.20-1).

Friday rebuilds much of what she knows from sources that do not change when
the owner deletes something: the nightly dreaming pass reads the same turns,
the knowledge graph re-reads the same pages, the review pass and her own tool
see the same conversation. A delete that the next derived pass quietly undoes
is not a delete. A tombstone is the record that says "the owner removed this;
do not make it again", consulted at every point that would re-create it.

ONE FACADE, SEVEN KINDS
-----------------------
Three tombstone lists already existed, each written by the store that owns the
deletion. They keep their files and their behaviour; this module reads them so
every caller asks one place:

  person             ``forgotten-people.json`` (services/forget_person). The
                     owner typed these names, and the indexer needs them as
                     names to redact them from prose, so that file keeps names
                     (and the addresses recorded against them) BY DESIGN.
                     Written only by forget_person.forget (the full purge);
                     ``remove`` lifts it (forget_person.unforget).
  conversation_turn  ``forgotten-turns.json`` (services/conversation_fts):
                     transcript message ids only, no text. ``add`` appends;
                     there is no undo (the turn was purged from every index).
  library_document   the Library's own ``tombstones`` table (content hash and
                     path, per principal; services/library/forget). Written by
                     forget_document; ``remove`` is the Library's un-forget.

and four new kinds live in ``tombstones.json`` beside them:

  card_line          a memory-card line the owner deleted, edited away, or
                     undid after Friday wrote it (services/core_memory)
  kg_entity          a graph entity the owner deleted (knowledge_graph)
  user_fact          an approved/stored fact the owner deleted (user_model)
  proposal           a proposed fact the owner rejected (memory_proposals)

A graph entity is named by WHAT it is, not by its title alone
(``entity_ident``): its type and its title ("person Mercury" and "page
Mercury" are two things), and a learned fact node (``fact_<hash>``) by its
fact id, which is a hash of the fact's whole text (never an 8-word prefix:
two facts that open the same way are two facts). A short title keeps ``+``,
``#`` and ``.`` (``normalise_title``), so C, C++, C# and .NET stay distinct.

MINIMAL PII
-----------
The new kinds never store the text. Each entry is ``{kind, h, day}``: ``h`` is
HMAC-SHA256 over the kind and the NORMALISED text (NFKC, invisible format
characters dropped, case-folded, punctuation turned to spaces, whitespace
collapsed, a leading bullet dropped), under a 32-byte random key made once per
install (``tombstones.key``, beside the list, owner-only permissions where the
OS honours them: on Windows the 0o600 mode has no effect, and the key is
protected by the permissions of the user-profile folder Friday's home lives
in). ``day`` is a date, not a timestamp. So the list says how many
things of which kind were deleted on which day, and can answer "is THIS text
tombstoned?", but cannot be read back into the text, and a short phrase cannot
be confirmed by hashing guesses without the key.

The key is this module's own, not the governance key: that one may live in the
OS keychain (a test run or a practice task must never touch it) and rotating
it on a governance reset would silently unhook every tombstone. A key that
exists but cannot be read is never replaced (that would orphan every entry);
writes then fail with a reason and checks fail open (see below). Nor is a
MISSING key made again while hashed entries exist (they would all be
orphaned, silently): a key is only ever created for an empty list, ``add``
refuses with a reason and ``list()`` reports the problem.

FAIL OPEN, NEVER RAISE
----------------------
Every check here is cheap (the list is cached on its mtime) and never raises
into its caller: a store that cannot be read logs a warning and answers "not
tombstoned". A tombstone check is a guard on a derived path, and memory has to
keep working; the owner's own deletion already ran. (The strict, refuse-the-
rebuild handling of the forgotten-turn list stays in conversation_fts, which
owns it.)

THE OWNER CAN ALWAYS SAY IT AGAIN
---------------------------------
A tombstone binds Friday, never the owner. The owner adding the line, the fact
or approving the proposal themselves lifts the tombstone for that text
(``owner_affirmed``), so their own word always wins.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple, Union

from agent_friday.paths import friday_home

_log = logging.getLogger("friday.tombstones")

KINDS = ("person", "library_document", "conversation_turn",
         "card_line", "kg_entity", "user_fact", "proposal")

#: Kinds kept in tombstones.json as keyed hashes of the normalised text.
HASHED = ("card_line", "kg_entity", "user_fact", "proposal")

#: Kinds that are all "a statement about the owner the owner said no to": a
#: derived path that would write such a statement (a card suggestion, a fact,
#: a proposal) checks all three, so a fact the owner deleted is not re-proposed
#: and a rejected proposal is not re-suggested for the card.
STATEMENT_KINDS = ("card_line", "user_fact", "proposal")

#: What each kind keeps, for the Settings screen and the receipt.
STORES = {
    "person": "names and aliases the owner typed (forgotten-people.json, by design)",
    "conversation_turn": "message ids only (forgotten-turns.json)",
    "library_document": "content hash and path (the Library's own tombstones table)",
    "card_line": "a keyed hash and a date (tombstones.json)",
    "kg_entity": "a keyed hash and a date (tombstones.json)",
    "user_fact": "a keyed hash and a date (tombstones.json)",
    "proposal": "a keyed hash and a date (tombstones.json)",
}

FILE = "tombstones.json"
KEY_FILE = "tombstones.key"
VERSION = 1
#: Entries kept. Old ones go first past this; a cap keeps every check cheap.
KEEP = 20000

_LOCK = threading.RLock()
_CACHE: Dict[str, Any] = {"path": None, "stamp": None, "entries": (), "index": {}}
_KEYS: Dict[str, bytes] = {}

KindArg = Union[str, Iterable[str]]


class Unreadable(RuntimeError):
    """The tombstone list (or its key) exists but cannot be read."""


# ── paths and the key ────────────────────────────────────────────────────────
def _path() -> Path:
    return friday_home() / FILE


def _key_path() -> Path:
    return friday_home() / KEY_FILE


def _key(create: bool) -> Optional[bytes]:
    """The per-install HMAC key. None when there is none yet (and `create` is
    False) or it cannot be read. An existing key is never overwritten."""
    p = _key_path()
    sp = str(p)
    if sp in _KEYS:
        return _KEYS[sp]
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raw = None
    except OSError as e:
        _log.warning("tombstones: the key could not be read: %s", type(e).__name__)
        return None
    if raw is not None:
        if len(raw) < 32:
            _log.warning("tombstones: the key file is damaged; it is not replaced")
            return None
        _KEYS[sp] = raw[:32]
        return _KEYS[sp]
    if not create:
        return None
    key = secrets.token_bytes(32)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(sp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        return _key(False)                 # another writer made it first
    except OSError as e:
        _log.warning("tombstones: no key could be made: %s", type(e).__name__)
        return None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        _log.warning("tombstones: the key could not be saved: %s", type(e).__name__)
        try:
            p.unlink()
        except OSError:
            pass
        return None
    _KEYS[sp] = key
    return key


# ── normalising and hashing ──────────────────────────────────────────────────
def normalise(ident: Any) -> str:
    """The comparable form of an identifying text: NFKC, invisible format
    characters dropped, case-folded, a leading bullet dropped, punctuation as
    spaces, whitespace collapsed. "- The user likes TEA!" and "the user likes
    tea" are the same thing."""
    s = unicodedata.normalize("NFKC", str(ident or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    s = s.casefold()
    s = re.sub(r"^[\s\-*•]+", "", s)
    s = re.sub(r"[\W_]+", " ", s)
    return " ".join(s.split())


#: A title this long or shorter keeps + # . (C++, C#, .NET); a longer one is
#: normalised like any statement.
SHORT_TITLE = 32

#: Entity types tried when the owner restores by title alone (the route
#: should send the type the delete returned).
RESTORE_TYPES = ("page", "soul", "document", "person", "organization", "project",
                 "tool", "concept", "event", "place")


def normalise_title(title: Any) -> str:
    """The comparable form of a graph entity's title. Like `normalise`, but a
    short title keeps ``+``, ``#`` and ``.`` (trailing dots dropped), so "C",
    "C++", "C#" and ".NET" stay four things while "Project-Atlas" and
    "PROJECT ATLAS" stay one."""
    s = unicodedata.normalize("NFKC", str(title or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf").casefold().strip()
    if len(s) > SHORT_TITLE:
        return normalise(s)
    s = re.sub(r"[^\w+#.]+|_+", " ", s)
    return " ".join((t.rstrip(".") or t) for t in s.split())


def entity_ident(entity: Any) -> str:
    """What a graph entity IS, as the text its kg_entity tombstone hashes.
    A learned fact node ("fact_<hash of its whole text>") is its fact id;
    any other entity is its type and its title. "" when it names nothing."""
    e = entity if isinstance(entity, dict) else {}
    eid = str(e.get("id") or "").strip()
    if eid.startswith("fact_"):
        return "fact\x1f" + eid.casefold()
    title = normalise_title(e.get("title"))
    if not title:
        return ""
    return (normalise(e.get("type")) or "entity") + "\x1f" + title


def entity_kind(entity: Any) -> str:
    """The bucket a dropped entity is counted under (never its content)."""
    e = entity if isinstance(entity, dict) else {}
    if str(e.get("id") or "").startswith("fact_"):
        return "fact"
    return normalise(e.get("type")) or "entity"


def _ident(kind: str, ident: Any) -> str:
    """The text `kind`'s hash is taken over. A kg_entity is named by a record
    ({id, type, title}: `entity_ident`); a bare string names no entity."""
    if kind == "kg_entity":
        return entity_ident(ident) if isinstance(ident, dict) else ""
    return normalise(ident)


def _digest(key: bytes, kind: str, norm: str) -> str:
    return hmac.new(key, ("%s\0%s" % (kind, norm)).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _kinds(kind: KindArg) -> Tuple[str, ...]:
    if isinstance(kind, str):
        return (kind,)
    return tuple(k for k in (kind or ()) if isinstance(k, str))


# ── the hashed list ──────────────────────────────────────────────────────────
def _stamp(p: Path):
    try:
        st = p.stat()
        return (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return None


def _read(strict: bool = False) -> Tuple[Dict[str, Any], ...]:
    """The hashed entries. No file: none. A file that cannot be read: with
    `strict` Unreadable, otherwise logged and none (checks fail open)."""
    p = _path()
    stamp = _stamp(p)
    if stamp is None:
        _CACHE.update(path=str(p), stamp=None, entries=(), index={})
        return ()
    if _CACHE["path"] == str(p) and _CACHE["stamp"] == stamp:
        return _CACHE["entries"]
    _CACHE.update(path=None, stamp=None, entries=(), index={})
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(rows, (list, tuple)):
            raise ValueError("no entry list")
        entries = tuple(r for r in rows if isinstance(r, dict)
                        and r.get("kind") in HASHED and isinstance(r.get("h"), str))
    except Exception as e:
        if strict:
            raise Unreadable("the tombstone list could not be read (%s)" % type(e).__name__) from e
        _log.warning("tombstones: the list could not be read: %s", type(e).__name__)
        return ()
    index: Dict[str, set] = {}
    for r in entries:
        index.setdefault(r["kind"], set()).add(r["h"])
    _CACHE.update(path=str(p), stamp=stamp, entries=entries, index=index)
    return entries


def _write(entries) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%s.tmp" % secrets.token_hex(4))
    body = json.dumps({"version": VERSION, "entries": [dict(e) for e in entries][-KEEP:]}, indent=0)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    _CACHE.update(path=None, stamp=None)


def _hashes(kind: str) -> set:
    _read()
    return _CACHE["index"].get(kind, set()) if _CACHE["path"] == str(_path()) else set()


def _has_any(kinds: Tuple[str, ...]) -> bool:
    return any(_hashes(k) for k in kinds if k in HASHED)


# ── the facade's public API ─────────────────────────────────────────────────
def add(kind: str, ident: Any) -> Dict[str, Any]:
    """Tombstone `ident` as `kind`. Returns {"ok", "added"} or {"ok": False,
    "error"}. Never raises."""
    try:
        if kind not in KINDS:
            return {"ok": False, "error": "unknown kind"}
        if kind == "person":
            return {"ok": False, "error": "a person is tombstoned by forget-person (it purges what is held "
                                          "about them first): services/forget_person.forget"}
        if kind == "library_document":
            return {"ok": False, "error": "a Library document is tombstoned by the Library's forget: "
                                          "services/library/forget.forget_document"}
        if kind == "conversation_turn":
            mid = str(ident or "").strip()
            if not mid:
                return {"ok": False, "error": "no message id"}
            from agent_friday.services import conversation_fts as _fts
            _fts._add_durable([mid])
            return {"ok": True, "kind": kind, "added": True}
        norm = _ident(kind, ident)
        if not norm:
            return {"ok": False, "error": ("an entity is named by its record {id, type, title}"
                                           if kind == "kg_entity" else "nothing to tombstone")}
        with _LOCK:
            entries = _read(strict=True)
            # A key is only ever made for an empty list: a new key beside
            # existing entries would orphan every one of them, silently.
            key = _key(create=not entries)
            if key is None and entries and not _key_path().exists():
                _log.warning("tombstones: the key is missing while %d tombstones exist; no new key is "
                             "made (it would orphan them) and nothing was recorded", len(entries))
                return {"ok": False, "key_missing": True,
                        "error": ("the tombstone key is missing while %d tombstones exist; a new key "
                                  "would orphan them, so nothing was recorded" % len(entries))}
            if key is None:
                return {"ok": False, "error": "the tombstone key is not available; nothing was recorded"}
            h = _digest(key, kind, norm)
            if any(e.get("kind") == kind and e.get("h") == h for e in entries):
                return {"ok": True, "kind": kind, "added": False}
            _write(tuple(entries) + ({"kind": kind, "h": h, "day": time.strftime("%Y-%m-%d")},))
        return {"ok": True, "kind": kind, "added": True}
    except Exception as e:
        _log.warning("tombstones: %s could not be recorded: %s", kind, type(e).__name__)
        return {"ok": False, "error": "the tombstone could not be recorded (%s)" % type(e).__name__}


def is_tombstoned(kind: KindArg, ident: Any) -> bool:
    """Whether `ident` is tombstoned as `kind` (one kind, or any of several).
    Cheap, and never raises: a store that cannot be read answers False."""
    return tombstoned_as(kind, ident) is not None


def tombstoned_as(kind: KindArg, ident: Any) -> Optional[str]:
    """The first of `kind` that `ident` is tombstoned as, or None. Never raises."""
    for k in _kinds(kind):
        try:
            if k in HASHED:
                if not _hashes(k):
                    continue
                key = _key(create=False)
                norm = _ident(k, ident)
                if key is not None and norm and _digest(key, k, norm) in _hashes(k):
                    return k
            elif k == "person":
                from agent_friday.services import forget_person as _fp
                if _fp.is_forgotten(str(ident or "")):
                    return k
            elif k == "conversation_turn":
                if ident:
                    from agent_friday.services import conversation_fts as _fts
                    if _fts.is_forgotten(str(ident)):
                        return k
            elif k == "library_document":
                if ident and _library_has(str(ident)):
                    return k
        except Exception as e:
            _log.warning("tombstones: the %s check failed (treated as not tombstoned): %s",
                         k, type(e).__name__)
    return None


def remove(kind: str, ident: Any, *, principal: Optional[str] = None) -> Dict[str, Any]:
    """The owner's undo: lift the tombstone (nothing deleted is restored).
    A forgotten conversation turn has no undo. Never raises."""
    try:
        if kind not in KINDS:
            return {"ok": False, "error": "unknown kind"}
        if kind == "person":
            from agent_friday.services import forget_person as _fp
            r = _fp.unforget(str(ident or ""))
            return {"ok": True, "kind": kind, "removed": int(r.get("lifted") or 0)}
        if kind == "conversation_turn":
            return {"ok": False, "error": "a forgotten conversation turn has no undo: it was purged from every "
                                          "index, and the transcript itself was never changed"}
        if kind == "library_document":
            from agent_friday.services.library import forget as _lf
            n = 0
            for p in ([principal] if principal else _lf._principals()):
                n += int(_lf.unforget(p, str(ident or "")) or 0)
            return {"ok": True, "kind": kind, "removed": n}
        norm = _ident(kind, ident)
        if not norm:
            return {"ok": True, "kind": kind, "removed": 0}
        with _LOCK:
            entries = _read(strict=True)
            if not any(e.get("kind") == kind for e in entries):
                return {"ok": True, "kind": kind, "removed": 0}
            key = _key(create=False)
            if key is None:
                return {"ok": False, "error": "the tombstone key is not available"}
            h = _digest(key, kind, norm)
            keep = tuple(e for e in entries if not (e.get("kind") == kind and e.get("h") == h))
            if len(keep) != len(entries):
                _write(keep)
        return {"ok": True, "kind": kind, "removed": len(entries) - len(keep)}
    except Exception as e:
        _log.warning("tombstones: %s could not be lifted: %s", kind, type(e).__name__)
        return {"ok": False, "error": "the tombstone could not be lifted (%s)" % type(e).__name__}


def owner_affirmed(text: Any, kinds: KindArg = STATEMENT_KINDS) -> int:
    """The owner said `text` themselves (added the line, the fact, approved
    the proposal): every statement tombstone for it is lifted, so their own
    word always wins. Returns how many went. Never raises; cheap when nothing
    is tombstoned."""
    n = 0
    ks = _kinds(kinds)
    try:
        if not _has_any(ks):
            return 0
    except Exception:
        return 0
    for k in ks:
        if k in HASHED and is_tombstoned(k, text):
            n += int(remove(k, text).get("removed") or 0)
    return n


def filter_entities_detail(entities):
    """Drop graph entities tombstoned as kg_entity (by `entity_ident`: type
    and title, or a fact node's own fact id, so an owner-approved fact goes
    only when that exact fact was deleted). Returns (kept, {bucket: count})
    where the bucket is the entity type or "fact", never its content. Never
    raises: on any failure the input comes back whole (a warning is logged)."""
    items = [e for e in (entities or [])]
    try:
        gone = _hashes("kg_entity")
        if not gone:
            return items, {}
        key = _key(create=False)
        if key is None:
            _log.warning("tombstones: %d graph tombstones but no readable key (nothing filtered)", len(gone))
            return items, {}
        kept, counts = [], {}
        for e in items:
            ident = entity_ident(e)
            if ident and _digest(key, "kg_entity", ident) in gone:
                b = entity_kind(e)
                counts[b] = counts.get(b, 0) + 1
            else:
                kept.append(e)
        return kept, counts
    except Exception as e:
        _log.warning("tombstones: the graph check failed (nothing filtered): %s", type(e).__name__)
        return items, {}


def filter_entities(entities):
    """`filter_entities_detail` as (kept, how many were dropped)."""
    kept, counts = filter_entities_detail(entities)
    return kept, sum(counts.values())


def list_tombstones() -> Dict[str, Any]:
    """Counts, kinds and dates only: never a name, a text, a hash or an id.
    {"kinds": {kind: {"count", "first", "last", "keeps"}}, "total"}."""
    out: Dict[str, Any] = {}
    for k in HASHED:
        out[k] = {"count": 0, "first": None, "last": None}
    try:
        for e in _read():
            row = out[e["kind"]]
            row["count"] += 1
            d = str(e.get("day") or "")[:10] or None
            if d:
                row["first"] = d if not row["first"] or d < row["first"] else row["first"]
                row["last"] = d if not row["last"] or d > row["last"] else row["last"]
    except Exception:
        pass
    try:
        from agent_friday.services import forget_person as _fp
        days = sorted(str(e.get("forgotten_at") or "")[:10] for e in _fp.list_forgotten()
                      if isinstance(e, dict))
        days_set = [d for d in days if d]
        out["person"] = {"count": len(days), "first": days_set[0] if days_set else None,
                         "last": days_set[-1] if days_set else None}
    except Exception:
        out["person"] = {"count": 0, "first": None, "last": None}
    try:
        from agent_friday.services import conversation_fts as _fts
        out["conversation_turn"] = {"count": len(_fts._read_durable()), "first": None, "last": None}
    except Exception:
        out["conversation_turn"] = {"count": 0, "first": None, "last": None}
    out["library_document"] = _library_counts()
    for k in out:
        out[k]["keeps"] = STORES.get(k, "")
    res = {"kinds": out, "total": sum(int(v.get("count") or 0) for v in out.values())}
    try:
        hashed = sum(int(out[k]["count"]) for k in HASHED)
        if hashed and _key(create=False) is None:
            missing = not _key_path().exists()
            res["key"] = "missing" if missing else "unreadable"
            res["problem"] = ("the tombstone key is %s while %d tombstones exist: they cannot be matched "
                              "(checks fail open) and no new tombstone is recorded until it is back"
                              % ("missing" if missing else "unreadable", hashed))
        else:
            res["key"] = "ok" if hashed else "none needed"
    except Exception:
        pass
    return res


# ── the Library's own table ─────────────────────────────────────────────────
def _library_has(ident: str) -> bool:
    from agent_friday.services.library import forget as _lf
    from agent_friday.services.library import store as _ls
    for p in _lf._principals():
        if _ls.store_for(p).one("SELECT 1 FROM tombstones WHERE sha256=? OR path=? LIMIT 1", (ident, ident)):
            return True
    return False


def _library_counts() -> Dict[str, Any]:
    row = {"count": 0, "first": None, "last": None}
    try:
        from agent_friday.services.library import forget as _lf
        from agent_friday.services.library import store as _ls
        lo = hi = None
        for p in _lf._principals():
            r = _ls.store_for(p).one("SELECT COUNT(*) AS n, MIN(ts) AS a, MAX(ts) AS b FROM tombstones")
            if not r or not r["n"]:
                continue
            row["count"] += int(r["n"])
            lo = r["a"] if lo is None or (r["a"] is not None and r["a"] < lo) else lo
            hi = r["b"] if hi is None or (r["b"] is not None and r["b"] > hi) else hi
        if lo is not None:
            row["first"] = time.strftime("%Y-%m-%d", time.localtime(float(lo)))
        if hi is not None:
            row["last"] = time.strftime("%Y-%m-%d", time.localtime(float(hi)))
    except Exception:
        pass
    return row


def _reset_for_tests() -> None:
    _CACHE.update(path=None, stamp=None, entries=(), index={})
    _KEYS.clear()


#: The facade's list, by the name the spec uses. It shadows the builtin inside
#: this module, so nothing here calls the builtin list() (comprehensions only).
list = list_tombstones  # noqa: A001
