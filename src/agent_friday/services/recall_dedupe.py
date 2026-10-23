"""Near-duplicate suppression for recalled items (north star NS-14.6-2: "Avoid
returning near-duplicate memories that crowd out distinct evidence").

The memory card dedupes at write time; this is the READ side. When a turn's
context is filled with recalled items (conversation recall, the past-
conversation search tool, Library passages, wiki excerpts), the caller
over-fetches a little (`overfetch`) and then `select`s the slots: items that
say the same thing as one already chosen are left out, so the freed slots go
to distinct evidence. Nothing is deleted from any store; this only decides
what one turn is shown.

The rule throughout: WHEN IN DOUBT, TWO ITEMS ARE NOT DUPLICATES. Hiding a
distinct piece of evidence is worse than showing a repeat.

Deterministic, no model calls. Two texts are near-duplicates only when ALL of
these hold:

  * their DISTINGUISHING tokens are the same, in the same order: numbers
    (400 = 400.00 = 1,200 -> 1200; versions such as v1.20 / 1.2.3 and
    decimals such as 1.20 kept exactly), a number with its unit or currency
    (9am / 9pm, 5 mg / 5 g, 400 / 400 pounds / GBP400), names and other words
    capitalised mid-sentence in either text (Alice, Harwich, Postgres),
    weekdays, months, people nouns (wife, son, tenant, landlord, boss...) and
    negators (not, no, never, n't, without, against, instead...), each with
    the two content words after it (what is negated). So
    "Alice pays Bob" / "Bob pays Alice" and "decided to use Postgres" /
    "decided against Postgres" are distinct;
  * where both name a person by pronoun, the pronouns are the same, in order
    ("I paid her" / "she paid me" are distinct; "Our fee" / "The fee" are
    not told apart);
  * their content-word SEQUENCES are identical once pure filler words
    (really, just, also, actually...) are left out. Any other difference -
    one word swapped (benign / malignant, left / right, to / from, is / was,
    tom / jim), added or dropped - makes them distinct. There is no
    similarity score: "almost the same words" is not "the same thing".
    Texts of more than `MAX_FUZZY_WORDS` content words must match exactly,
    fillers included.

Content words are lower-cased words (any script: fiancé, Zoë, 北京) minus a
small stoplist of articles; prepositions, copulas, pronouns, possessives,
negators and numbers are always kept.

`select` groups items against each group's FIRST (anchor) member only, so
A~B, B~C, C~D never chain A and D together. Items from different speakers
(the user vs Friday) are never one group. Within a group the item kept is:
not superseded first (superseded items should already be excluded upstream),
then the caller's preference (`prefer`, e.g. "this cloud provider already
received it"), then the newest timestamp, then the best score, then the
earliest rank. The group takes the rank of its anchor, so relevance still
orders the slots. The same id twice is always one item.

`SUPPRESSED` counts, per path, the items that would have filled one of the
slots had there been no suppression but were left out (counts only, never
text), for local diagnostics. Over-fetched extras that were never going to be
shown are not counted.
"""
from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections import namedtuple
from datetime import datetime
from typing import Any, Callable, Iterable, List, Optional

THRESHOLD = 0.8          # kept for callers; the rule is exact (see is_near_duplicate)
MAX_FUZZY_WORDS = 300    # longer texts: exact content-word sequence only
OVERFETCH_FACTOR = 3
OVERFETCH_MAX_EXTRA = 20

# Ignored everywhere: articles only (and "per", as in "400 per year" / "400 a
# year"). Prepositions (to/from/in/on/by...), copulas and tense (is/was/been),
# conjunctions (and/or), deictics (this/that/here/there) and possessives all
# change what a sentence says, so they are content words.
_STOP = frozenset("a an the per".split())

# Pure fillers: two texts may differ by adding or dropping these and nothing
# else. Never a word that changes who, what, when, where or whether.
_FILLER = frozenset("""really just also please actually basically very so ok
okay""".split())

# Negators. not / no / n't / cannot all read as "not"; the rest keep their own
# word. Every one is a distinguishing token.
_NOT = frozenset("not no cannot".split())
_NEG_OTHER = frozenset("""never none nobody nothing neither nor nowhere without
against instead""".split())

_PRONOUNS = frozenset("""i me my mine myself we us our ours ourselves you your
yours yourself yourselves he him his himself she her hers herself they them
their theirs themselves""".split())

_WEEKDAYS = frozenset("""monday tuesday wednesday thursday friday saturday sunday
mondays tuesdays wednesdays thursdays fridays saturdays sundays""".split())
# "may" and "march" are months only when written with a capital (in either text).
_MONTHS = frozenset("""january february april june july august september october
november december""".split())
_MONTHS_CAPITAL_ONLY = frozenset({"may", "march"})

_PEOPLE = frozenset("""wife husband son daughter mum mom mother mummy mommy dad
father daddy brother sister partner spouse friend boss colleague coworker
tenant landlord landlady child kid baby parent grandma grandpa granny grandad
grandmother grandfather grandson granddaughter aunt uncle cousin nephew niece
girlfriend boyfriend fiance fiancee fiancé fiancée neighbour neighbor manager client customer
doctor dentist lawyer solicitor accountant teacher stepson stepdaughter
stepmother stepfather""".split())
_PEOPLE = _PEOPLE | frozenset(w + "s" for w in _PEOPLE) | frozenset(
    "children kids wives grandchildren".split())

# Words that, right after a number, are its unit (merged: "5 mg" -> "5mg").
_UNITS = frozenset("""am pm mg g kg ml l km m cm mm mi miles mile kmh mph
h hr hrs hour hours min mins minute minutes s sec secs second seconds
day days week weeks month months year years yr yrs lb lbs oz ft
pound pounds quid pence p gbp usd eur euro euros dollar dollars cent cents
k x percent pct gb mb tb kb mbps kw kwh w v""".split())
_UNIT_ALIAS = {"percent": "%", "pct": "%", "quid": "pounds", "pound": "pounds",
               "gbp": "pounds", "dollar": "dollars", "usd": "dollars",
               "euro": "euros", "eur": "euros"}
_CURRENCY = {"£": "pounds", "$": "dollars", "€": "euros", "¥": "yen"}

# Words in any script (letters only: [^\W\d_]), so fiancé / fiancée, Zoë and
# 北京 / 上海 are words of their own, not dropped or split.
_TOKEN = re.compile(r"[£$€¥%]|[vV]\d+(?:\.\d+)+|\d+(?:[.,]\d+)*|[^\W\d_]+(?:'[^\W\d_]+)*")
_CONTRACTION = {"m": "am", "re": "are", "ve": "have", "ll": "will", "d": "would"}
_NT_BASE = {"wo": "will", "ca": "can", "sha": "shall", "ai": "is"}
_BOUNDARY_SKIP = " \t\"'“”‘’([{*_"
_BOUNDARY = ".!?:;\n\r-#>•|"

#: path -> items left out that would otherwise have filled a slot (no text).
SUPPRESSED: dict = {}
_LOCK = threading.Lock()

#: words: content words in order; caps: those written capitalised mid-
#: sentence here; fixed: positions of always-distinguishing tokens; raw: the
#: normalised raw text (for texts with no content words at all).
Signature = namedtuple("Signature", "words caps fixed raw")


def overfetch(n: int, factor: int = OVERFETCH_FACTOR, max_extra: int = OVERFETCH_MAX_EXTRA) -> int:
    """How many candidates to fetch for `n` slots: up to `factor`x, at most
    `max_extra` more than `n`."""
    n = max(1, int(n))
    return n + min((factor - 1) * n, max_extra)


def _number(tok: str) -> str:
    """400 / 400.00 / 0400 -> 400, 1,200 -> 1200. Versions (1.2.3) and
    decimals with a non-zero fraction (1.20) are kept exactly."""
    t = tok.replace(",", "")
    if t.count(".") > 1:
        return t                                   # a version: exact
    whole, dot, frac = t.partition(".")
    whole = whole.lstrip("0") or "0"
    if dot and frac.strip("0"):
        return whole + "." + frac                  # 1.20 stays 1.20 (not 1.2)
    return whole


def _sentence_start(text: str, start: int) -> bool:
    j = start - 1
    while j >= 0 and text[j] in _BOUNDARY_SKIP:
        j -= 1
    return j < 0 or text[j] in _BOUNDARY


def signature(text: str) -> Signature:
    """The comparable form of a text (see the module docstring)."""
    src = unicodedata.normalize("NFC", str(text or "")).replace("’", "'").replace("‘", "'")
    toks = [(m.group(0), m.start()) for m in _TOKEN.finditer(src)]
    words: List[str] = []
    caps = set()
    fixed = set()                     # indexes into `words` that always distinguish
    pending_cur = None
    i = 0
    while i < len(toks):
        tok, pos = toks[i]
        i += 1
        if tok in _CURRENCY:
            pending_cur = _CURRENCY[tok]
            continue
        if tok == "%":
            if words and (len(words) - 1) in fixed and words[-1][0].isdigit() and not words[-1].endswith("%"):
                words[-1] += "%"
            continue
        if tok[0] in "vV" and len(tok) > 1 and tok[1].isdigit():
            fixed.add(len(words))
            words.append(tok.lower())                       # a version, exact
            pending_cur = None
            continue
        if tok[0].isdigit():
            val = _number(tok)
            unit = pending_cur
            pending_cur = None
            if i < len(toks) and toks[i][0].lower() in _UNITS:
                u = toks[i][0].lower()
                unit = unit or _UNIT_ALIAS.get(u, u)
                i += 1
            if i < len(toks) and toks[i][0] == "%":
                unit = "%"
                i += 1
            fixed.add(len(words))
            words.append(val + (unit or ""))
            continue
        pending_cur = None
        low = tok.lower()
        parts: List[str]
        if low.endswith("n't"):
            base = low[:-3]
            parts = [_NT_BASE.get(base, base), "not"]
        elif "'" in low:
            head, _, tail = low.partition("'")
            parts = [head] + ([_CONTRACTION[tail]] if tail in _CONTRACTION else [])
        elif low == "cannot":
            parts = ["can", "not"]
        else:
            parts = [low]
        first = True
        for w in parts:
            if w in _NOT:
                w = "not"
            if w in _STOP and w != "not":
                first = False
                continue
            idx = len(words)
            words.append(w)
            if (w == "not" or w in _NEG_OTHER or w in _WEEKDAYS or w in _MONTHS
                    or w in _PEOPLE):
                fixed.add(idx)
            if first and tok[0].isupper() and w != "i":
                if w in _MONTHS_CAPITAL_ONLY or not _sentence_start(src, pos):
                    caps.add(w)
            first = False
    raw = " ".join(src.lower().split())
    return Signature(tuple(words), frozenset(caps), frozenset(fixed), raw)


def _sig(x) -> Signature:
    return x if isinstance(x, Signature) else signature(x)


def _distinguishing(s: Signature, entities: frozenset) -> list:
    """The distinguishing tokens in order. A negator carries the two content
    words after it, so what is negated counts ("not like cats" is not
    "not like dogs")."""
    out = []
    for k, w in enumerate(s.words):
        if w == "not" or w in _NEG_OTHER:
            out.append(tuple(s.words[k:k + 3]))
        elif k in s.fixed or w in entities:
            out.append(w)
    return out


def _pronouns(s: Signature) -> list:
    return [w for w in s.words if w in _PRONOUNS]


def similarity(a, b) -> float:
    """Jaccard of the content-word sets (texts or signatures)."""
    wa, wb = set(_sig(a).words), set(_sig(b).words)
    if not wa and not wb:
        return 1.0
    return len(wa & wb) / max(1, len(wa | wb))


def is_near_duplicate(a, b, threshold: float = THRESHOLD) -> bool:
    """Do two texts (or signatures) say the same thing? Any doubt -> False.

    Same content words in the same order, allowing only pure fillers
    (`_FILLER`) to be added or dropped; texts longer than `MAX_FUZZY_WORDS`
    content words must match exactly. Linear time, no similarity score;
    `threshold` is accepted for callers and not used."""
    sa, sb = _sig(a), _sig(b)
    ca = tuple(w for w in sa.words if w not in _FILLER)
    cb = tuple(w for w in sb.words if w not in _FILLER)
    if not ca or not cb:
        if ca or cb:
            return False
        # Nothing but articles / fillers on both sides: equal only if the raw text is.
        return bool(sa.raw) and sa.raw == sb.raw
    if len(sa.words) > MAX_FUZZY_WORDS or len(sb.words) > MAX_FUZZY_WORDS:
        if sa.words != sb.words:
            return False
    elif ca != cb:
        return False
    # Second guard (the sequences already agree): distinguishing tokens,
    # including what each negator governs, and pronouns.
    entities = sa.caps | sb.caps
    if _distinguishing(sa, entities) != _distinguishing(sb, entities):
        return False
    pa, pb = _pronouns(sa), _pronouns(sb)
    if pa and pb and pa != pb:
        return False
    return True


def _finite(value) -> Optional[float]:
    """A finite float, else None (NaN / inf never sort as newest or best)."""
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _ts(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return _finite(value)
    s = str(value).strip()
    for parse in (lambda: datetime.fromisoformat(s.replace("Z", "+00:00")),
                  lambda: datetime.strptime(s[:16], "%Y-%m-%d %H:%M"),
                  lambda: datetime.strptime(s[:10], "%Y-%m-%d")):
        try:
            return _finite(parse().timestamp())
        except (ValueError, OverflowError, OSError):
            continue
    return None


def _num(value) -> Optional[float]:
    return None if value is None else _finite(value)


def _default_text(item) -> str:
    return str(item.get("text") or "") if isinstance(item, dict) else str(item or "")


def _default_id(item):
    if not isinstance(item, dict):
        return None
    return item.get("id") or item.get("message_id") or None


def _default_ts(item):
    if not isinstance(item, dict):
        return None
    for k in ("timestamp", "ts", "date"):
        t = _ts(item.get(k))
        if t is not None:
            return t
    return None


def _default_score(item):
    if not isinstance(item, dict):
        return None
    for k in ("relevance", "score"):
        s = _num(item.get(k))
        if s is not None:
            return s
    return None


def _default_superseded(item) -> bool:
    return bool(item.get("superseded")) if isinstance(item, dict) else False


_ROLE_ALIAS = {"user": "user", "owner": "user", "human": "user", "me": "user",
               "assistant": "assistant", "friday": "assistant", "model": "assistant",
               "ai": "assistant", "bot": "assistant", "you": "assistant"}


def _default_partition(item):
    """Who said it: items from different speakers are never one group."""
    if not isinstance(item, dict):
        return None
    r = item.get("role") or item.get("speaker")
    if r is None or r == "":
        return None
    r = str(r).strip().lower()
    return _ROLE_ALIAS.get(r, r)


def select(items: Iterable[Any], limit: int, *, threshold: float = THRESHOLD,
           text: Callable[[Any], str] = _default_text,
           ident: Callable[[Any], Any] = _default_id,
           when: Callable[[Any], Optional[float]] = _default_ts,
           score: Callable[[Any], Optional[float]] = _default_score,
           superseded: Callable[[Any], bool] = _default_superseded,
           partition: Callable[[Any], Any] = _default_partition,
           prefer: Optional[Callable[[Any], bool]] = None,
           keep: str = "current", path: str = "") -> List[Any]:
    """Up to `limit` items, best-ranked first, with near-duplicates collapsed.

    `items` are in rank order (best first). An item joins a group only if it
    is a near-duplicate of the group's anchor (its first member) and has the
    same `partition` (by default the speaker role); members never become
    anchors, so near-duplicates do not chain. Each group yields one item:
    with keep="current" the not-superseded, preferred (`prefer`), newest,
    best-scored, best-ranked one; with keep="first" the first preferred one,
    else the best-ranked (for sources with no "said when"). Groups keep the
    rank of their anchor. Stable: equal inputs give equal outputs.
    `limit=None` means no limit; an unhashable id counts as no id."""
    try:
        lim: Optional[int] = None if limit is None else max(0, int(limit))
    except (TypeError, ValueError, OverflowError):
        lim = None
    seen_ids = set()
    groups: List[dict] = []          # {"anchor": sig, "part": key, "members": [(rank, item)]}
    n = 0
    for rank, it in enumerate(items):
        n = rank + 1
        iid = ident(it)
        try:
            hash(iid)
        except TypeError:
            iid = None
        if iid is not None and iid != "":
            if iid in seen_ids:
                continue
            seen_ids.add(iid)
        sig = signature(text(it))
        part = partition(it)
        home = None
        for g in groups:
            if g["part"] == part and is_near_duplicate(sig, g["anchor"], threshold):
                home = g
                break
        if home is None:
            groups.append({"anchor": sig, "part": part, "members": [(rank, it)]})
        else:
            home["members"].append((rank, it))

    def _pref(it) -> int:
        try:
            return 0 if (prefer is not None and prefer(it)) else 1
        except Exception:
            return 1

    def _pick(members):
        if keep == "first":
            return sorted(members, key=lambda m: (_pref(m[1]), m[0]))[0]

        def k(m):
            r, it = m
            t, s = _finite(when(it)), _finite(score(it))
            return (1 if superseded(it) else 0,
                    _pref(it),
                    -(t if t is not None else float("-inf")),
                    -(s if s is not None else float("-inf")),
                    r)
        return sorted(members, key=k)[0]

    if lim is None:
        lim = n
    chosen = [_pick(g["members"]) for g in groups][:lim]
    if path:
        shown = {r for r, _ in chosen}
        # Only what would have filled a slot with no suppression is counted.
        note_suppressed(path, sum(1 for r in range(min(lim, n)) if r not in shown))
    return [it for _, it in chosen]


def note_suppressed(path: str, count: int) -> None:
    """Count items a path left out that would otherwise have filled a slot
    (no text is kept)."""
    if path and count:
        with _LOCK:
            SUPPRESSED[path] = SUPPRESSED.get(path, 0) + int(count)


def suppressed_counts() -> dict:
    """Items left out per path since start (counts only)."""
    with _LOCK:
        return dict(SUPPRESSED)
