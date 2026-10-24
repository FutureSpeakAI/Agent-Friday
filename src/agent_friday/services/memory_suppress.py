"""Don't suggest things like this: the owner's suppression rules for memory
suggestions (NS-15.5-1).

A pattern Friday notices is a proposal, never a fact: it waits on the memory
card (core_memory.queue: nightly dreaming, the background review, Friday's own
tool) or in the proposals table (memory_proposals: a day's review, a meeting).
The owner keeps it, edits it and keeps their own words, discards it, or says
"Don't suggest things like this". The last discards it AND records a rule here,
so a later suggestion that says much the same thing is never queued again.

What a rule is
--------------
The suppressed suggestion's normalised content words (lower case, NFKC, a
possessive "'s" taken off, the card's small words and a few more that say
nothing about the subject -- "owner", "user", "friday", "has" -- dropped, a
trailing plural "s" taken off), at most ``MAX_WORDS`` of them, plus whether
the suggestion was negative ("not", "never", "no longer", "dislikes" ...). The
raw text is not stored.

A suggestion from a meeting (or any text that was sealed at rest) is about
other people and was kept encrypted, so its rule keeps no readable words:
each word is kept as a keyed hash (HMAC-SHA256 under a per-install random key,
``suppressed.key`` beside the rules, itself sealed by services/credential_store)
with a flag saying whether it is distinctive. A candidate's words are hashed
the same way, so matching is the same set arithmetic. The panel shows such a
rule as "From a meeting". Other rules are the owner's own data and their words
are shown, in a plain file next to the card on this PC
(``core_memory/suppressed.json``); the owner sees and removes every rule from
the Memory card panel.

What "similar" means
--------------------
Deterministic and conservative, so an unrelated suggestion is never dropped.
A candidate is similar to a rule when ALL of these hold:

* both are negative or neither is (a polarity change -- "likes" to
  "dislikes", "is" to "is not", "stopped" -- is a new fact, not a repeat);
* every capitalised name and every number in the candidate is among the
  rule's words ("Acme" is not "Globex", "June" is not "March");
* they share at least ``MIN_SHARED`` distinctive words (at least ``MIN_LEN``
  letters, not "day" or "use");
* the Jaccard overlap of their DISTINCTIVE words is at least ``JACCARD``.

A rule needs at least ``MIN_SHARED`` distinctive words itself; a suggestion
too short to match safely is discarded without a rule.

What is counted
---------------
Each drop is counted by the place it happened (``queue.dream``,
``proposals.day`` ...) and on the rule that matched; no text is kept.

Never suppressed: the owner's own adds and edits (core_memory.add with
actor="owner"), a queued line resting on the owner's own words
(``owner_stated``), anything queued from a source that means the owner entered
or approved it (user_model.CONFIRMED_SOURCES), and a suggested correction of a
line already on the card (a replace).

Forgetting a person (services/forget_registry, "memory_suppressions") drops
every rule whose words include a token of the forgotten name.

Leaf module: no Flask, never raises to a caller.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

#: Jaccard overlap of distinctive words at or above which a suggestion is similar.
JACCARD = 0.6

#: Distinctive words a candidate must share with a rule (and a rule must have).
MIN_SHARED = 2

#: A distinctive word has at least this many letters.
MIN_LEN = 4

#: Words kept on one rule (distinctive words first).
MAX_WORDS = 64

#: Rules kept (oldest dropped first).
RULES_KEEP = 200

#: What a rule is about: a line to add or rewrite, or a line to remove.
KINDS = ("change", "remove")

#: Words that say nothing about what a suggestion is about (besides the card's
#: own small words, core_memory._SMALL_WORDS).
STOPWORDS = frozenset({
    "s", "t", "owner", "owners", "user", "users", "friday", "has", "have", "had",
    "having", "were", "been", "being", "do", "does", "did", "also", "really", "very",
})

#: Words that make a statement negative (and any "n't").
NEGATIONS = frozenset({
    "not", "no", "never", "none", "nobody", "nothing", "nor", "neither", "without",
    "cannot", "anymore", "stopped", "stops", "quit", "quits", "dislike",
    "dislikes", "disliked", "hate", "hates", "hated", "avoid", "avoids", "avoided",
    "doesn", "didn", "isn", "aren", "wasn", "weren", "wouldn", "couldn", "shouldn",
    "hasn", "haven", "hadn",
})

#: Month names count as names even in lower case ("may" is left out: a verb).
_MONTHS = frozenset({"january", "february", "march", "april", "june", "july", "august",
                     "september", "october", "november", "december"})

_LOCK = threading.RLock()
_KEYS: Dict[str, bytes] = {}


def _path() -> Path:
    from agent_friday.services import core_memory
    return core_memory._dir() / "suppressed.json"


def _key_path() -> Path:
    return _path().with_name("suppressed.key")


# ── words ─────────────────────────────────────────────────────────────────────
def _stem(w: str) -> str:
    if len(w) > MIN_LEN and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _prep(text: Any) -> str:
    """NFKC, curly apostrophes made straight, a possessive "'s" taken off."""
    s = unicodedata.normalize("NFKC", str(text or "")).replace("’", "'").replace("‘", "'")
    return re.sub(r"'s\b", "", s, flags=re.IGNORECASE)


def _tokens(text: Any) -> List[str]:
    return re.findall(r"[a-z0-9]+", _prep(text).lower())


def _norm_word(w: str) -> str:
    return _stem(str(w or "").lower())


def _distinct_word(w: str) -> bool:
    return len(w) >= MIN_LEN and not w.isdigit()


def words(text: Any) -> List[str]:
    """The normalised content words of `text`, sorted (the stored form)."""
    try:
        from agent_friday.services.core_memory import content_words
        base = content_words(_prep(text))
    except Exception:
        base = set(_tokens(text))
    return sorted({_stem(w) for w in base if w and w not in STOPWORDS})


def negative(text: Any) -> bool:
    """Does `text` say something is NOT so (not, never, no longer, n't, dislikes ...)?"""
    low = _prep(text).lower()
    return bool(set(_tokens(low)) & NEGATIONS) or bool(re.search(r"n't\b", low))


def _negative_words(ws: Iterable[str]) -> bool:
    return bool({str(w) for w in ws} & {_stem(n) for n in NEGATIONS})


def entities(text: Any) -> Set[str]:
    """The capitalised names (not the first word of a sentence), numbers and
    month names in `text`, normalised like its words (and among them)."""
    s = _prep(text)
    out: Set[str] = set()
    for m in re.finditer(r"[A-Za-z0-9]+", s):
        tok = m.group(0)
        if tok.lower() in STOPWORDS:
            continue
        low = _norm_word(tok)
        if not low:
            continue
        if any(ch.isdigit() for ch in tok) or tok.lower() in _MONTHS:
            out.add(low)
            continue
        if len(tok) < 2 or not tok[0].isupper():
            continue
        before = s[:m.start()].rstrip()
        if not before or before[-1] in ".!?:;\n":
            continue                                    # a sentence's first word
        out.add(low)
    return out & set(words(text))


def _distinctive(ws: Iterable[str]) -> set:
    return {w for w in ws if _distinct_word(w)}


def _match(cand: Set[str], cand_dist: Set[str], cand_neg: bool, cand_ents: Set[str],
           rule: Set[str], rule_dist: Set[str], rule_neg: bool) -> bool:
    if not cand or not rule:
        return False
    if bool(cand_neg) != bool(rule_neg):
        return False                                    # a polarity change is a new fact
    if cand_ents - rule:
        return False                                    # a name or number the rule never had
    shared = cand_dist & rule_dist
    if len(shared) < MIN_SHARED:
        return False
    return len(shared) / len(cand_dist | rule_dist) >= JACCARD


def similar(a: Iterable[str], b: Iterable[str]) -> bool:
    """Two word lists (the stored form): same polarity, enough distinctive
    shared words, and Jaccard of distinctive words >= JACCARD."""
    sa, sb = set(a), set(b)
    return _match(sa, _distinctive(sa), _negative_words(sa), set(),
                  sb, _distinctive(sb), _negative_words(sb))


def similar_text(candidate: Any, suppressed: Any) -> bool:
    """Would a rule made from `suppressed` hold back `candidate`?"""
    c, r = set(words(candidate)), set(words(suppressed))
    return _match(c, _distinctive(c), negative(candidate), entities(candidate),
                  r, _distinctive(r), negative(suppressed))


def _cap(ws: List[str]) -> List[str]:
    if len(ws) <= MAX_WORDS:
        return ws
    dist = [w for w in ws if _distinct_word(w)]
    rest = [w for w in ws if not _distinct_word(w)]
    return sorted((dist + rest)[:MAX_WORDS])


# ── the keyed hash for rules that keep no readable words ─────────────────────
def _key(create: bool = False) -> Optional[bytes]:
    """This install's random key for hashed rules (sealed at rest), or None."""
    p = _key_path()
    k = _KEYS.get(str(p))
    if k:
        return k
    try:
        from agent_friday.services import credential_store
        with _LOCK:
            if p.is_file():
                raw = credential_store.read_secret(p)
            elif create:
                raw = secrets.token_bytes(32)
                credential_store.write_secret(p, raw)
            else:
                return None
    except Exception:
        return None
    if not raw or len(raw) < 16:
        return None
    _KEYS[str(p)] = raw
    return raw


def _h(key: bytes, w: str) -> str:
    return hmac.new(key, b"memory-suppress/v1\0" + w.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


# ── the store ─────────────────────────────────────────────────────────────────
def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    rules = data.get("rules")
    dropped = data.get("dropped")
    return {"rules": [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else [],
            "dropped": dict(dropped) if isinstance(dropped, dict) else {}}


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _rule_sets(r: Dict[str, Any], key: Optional[bytes]):
    """(word set, distinctive set, negative) of a stored rule, in its own space
    (hashes for a hashed rule), or None when it cannot be compared."""
    if r.get("hashed"):
        if not key:
            return None
        hw = [x for x in (r.get("hwords") or []) if isinstance(x, (list, tuple)) and len(x) == 2]
        return ({str(x[0]) for x in hw}, {str(x[0]) for x in hw if x[1]}, bool(r.get("neg")))
    ws = set(r.get("words") or [])
    neg = r.get("neg")
    return (ws, _distinctive(ws), _negative_words(ws) if neg is None else bool(neg))


def add_rule(text: str, *, kind: str = "change", origin: str = "",
             hashed: bool = False) -> Dict[str, Any]:
    """Record a rule from the suggestion the owner suppressed. Only its
    normalised content words are kept (as keyed hashes when `hashed`: a
    meeting's suggestion). A suggestion with too few distinctive words gets no
    rule (it could only match by accident)."""
    kind = kind if kind in KINDS else "change"
    ws = _cap(words(text))
    neg = negative(text)
    if len(_distinctive(ws)) < MIN_SHARED:
        return {"ok": True, "rule": None,
                "note": "That suggestion is too short to match others safely, so it is discarded "
                        "without a rule."}
    try:
        with _LOCK:
            body: Dict[str, Any]
            if hashed:
                key = _key(create=True)
                if not key:
                    return {"ok": False, "error": "the rule could not be kept safely on this PC"}
                hw = sorted([_h(key, w), 1 if _distinct_word(w) else 0] for w in ws)
                body = {"hashed": True, "hwords": hw}
            else:
                body = {"words": ws}
            data = _load()
            for r in data["rules"]:
                same = (r.get("hwords") == body.get("hwords") if hashed
                        else (not r.get("hashed") and r.get("words") == ws))
                if r.get("kind", "change") == kind and same and bool(r.get("neg")) == neg:
                    return {"ok": True, "rule": _public(r), "unchanged": True}
            rule = {"id": "sup-" + uuid.uuid4().hex[:10], "ts": time.time(), "kind": kind,
                    **body, "neg": neg, "origin": str(origin or "")[:20], "hits": 0}
            data["rules"] = (data["rules"] + [rule])[-RULES_KEEP:]
            _save(data)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": f"the rule could not be saved: {e}"}
    return {"ok": True, "rule": _public(rule)}


def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    hashed = bool(r.get("hashed"))
    return {"id": r.get("id"), "ts": r.get("ts"), "kind": r.get("kind", "change"),
            "words": [] if hashed else list(r.get("words") or []),
            "from_meeting": hashed, "label": "From a meeting" if hashed else "",
            "origin": r.get("origin") or "", "hits": int(r.get("hits") or 0)}


def rules() -> List[Dict[str, Any]]:
    """Every rule, newest first, for the panel."""
    return [_public(r) for r in reversed(_load()["rules"])]


def remove_rule(rule_id: str) -> Dict[str, Any]:
    try:
        with _LOCK:
            data = _load()
            keep = [r for r in data["rules"] if r.get("id") != rule_id]
            if len(keep) == len(data["rules"]):
                return {"ok": False, "error": "that rule is gone"}
            data["rules"] = keep
            _save(data)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": f"the rule could not be removed: {e}"}
    return {"ok": True, "removed": rule_id}


def matching(text: str, *, kind: str = "change") -> Optional[str]:
    """The id of the rule `text` is similar to, or None."""
    ws = set(words(text))
    if not ws:
        return None
    neg, ents = negative(text), entities(text)
    dist = _distinctive(ws)
    rs = [r for r in _load()["rules"] if r.get("kind", "change") == kind]
    key = _key() if any(r.get("hashed") for r in rs) else None
    hashed_cand = None
    for r in rs:
        sets = _rule_sets(r, key)
        if sets is None:
            continue
        if r.get("hashed"):
            if hashed_cand is None:
                hashed_cand = ({_h(key, w) for w in ws}, {_h(key, w) for w in dist},
                               {_h(key, e) for e in ents})
            c, cd, ce = hashed_cand
        else:
            c, cd, ce = ws, dist, ents
        if _match(c, cd, neg, ce, sets[0], sets[1], sets[2]):
            return r.get("id")
    return None


def check(text: str, where: str, *, kind: str = "change") -> Optional[str]:
    """For an inferring path about to queue `text`: the matching rule's id
    (and the drop is counted, by place and on the rule, with no text), or None.
    A store that cannot be read suppresses nothing (the owner still reviews)."""
    try:
        rid = matching(text, kind=kind)
    except Exception:
        return None
    if not rid:
        return None
    try:
        with _LOCK:
            data = _load()
            place = str(where or "unknown")[:40]
            data["dropped"][place] = int(data["dropped"].get(place) or 0) + 1
            for r in data["rules"]:
                if r.get("id") == rid:
                    r["hits"] = int(r.get("hits") or 0) + 1
            _save(data)
    except Exception:
        pass
    return rid


def counts() -> Dict[str, int]:
    """Suppressed drops by place (no text)."""
    return {k: int(v or 0) for k, v in _load()["dropped"].items()}


def summary() -> Dict[str, Any]:
    c = counts()
    return {"rules": rules(), "dropped": c, "dropped_total": sum(c.values())}


# ── forget a person (services/forget_registry: "memory_suppressions") ────────
def _name_tokens(names) -> Set[str]:
    out: Set[str] = set()
    for n in names or ():
        for t in _tokens(n):
            w = _norm_word(t)
            if len(w) >= 2 and w not in STOPWORDS:
                out.add(w)
    return out


def _rules_naming(rs: List[Dict[str, Any]], names) -> List[str]:
    toks = _name_tokens(names)
    if not toks:
        return []
    key = _key() if any(r.get("hashed") for r in rs) else None
    htoks = {_h(key, t) for t in toks} if key else set()
    hit: List[str] = []
    for r in rs:
        if r.get("hashed"):
            hw = {str(x[0]) for x in (r.get("hwords") or []) if isinstance(x, (list, tuple)) and x}
            if htoks & hw:
                hit.append(r.get("id"))
        elif toks & set(r.get("words") or []):
            hit.append(r.get("id"))
    return hit


def people_find(names, emails) -> int:
    """How many rules carry a token of a forgotten person's name."""
    return len(_rules_naming(_load()["rules"], names))


def people_purge(names, emails) -> int:
    """Drop every rule that carries a token of a forgotten person's name."""
    with _LOCK:
        data = _load()
        gone = set(_rules_naming(data["rules"], names))
        if not gone:
            return 0
        data["rules"] = [r for r in data["rules"] if r.get("id") not in gone]
        _save(data)
    return len(gone)
