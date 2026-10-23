"""Near-duplicate suppression for recalled items (north star NS-14.6-2: "Avoid
returning near-duplicate memories that crowd out distinct evidence").

The memory card dedupes at write time; this is the READ side. When a turn's
context is filled with recalled items (conversation recall, the past-
conversation search tool, Library passages, wiki excerpts), the caller
over-fetches a little (`overfetch`) and then `select`s the slots: items that
say the same thing as one already chosen are left out, so the freed slots go
to distinct evidence. Nothing is deleted from any store; this only decides
what one turn is shown.

Deterministic, no model calls. Two texts are near-duplicates when

  * their content-word sets overlap by Jaccard >= `threshold` (0.8), or one's
    content words are wholly contained in the other's with Jaccard >= 0.6
    (a restatement with a word or two more), AND
  * they carry the SAME numbers ("fee is 400" and "fee is 520" are two
    pieces of evidence, never one), AND
  * they have the same polarity (one negated, the other not -> distinct).

Content words are lower-cased words minus a small stoplist of articles,
prepositions and copulas; pronouns, negations and numbers are always kept.

Within a near-duplicate group the CURRENT item is kept: not superseded first
(superseded items should already be excluded upstream), then the newest
timestamp, then the best score, then the earliest rank. The group takes the
rank of its best-ranked member, so relevance still orders the slots. The same
id twice is always one item.

`SUPPRESSED` counts how many items were left out per path (counts only, never
text), for local diagnostics.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

THRESHOLD = 0.8
CONTAIN_JACCARD = 0.6
OVERFETCH_FACTOR = 3
OVERFETCH_MAX_EXTRA = 20

_STOP = frozenset("""a an the of to in on at by for from with as and or but so
is are was were be been being am it its this that these those there here
per about into onto over than then just also very really please ok okay""".split())

_NEGATORS = frozenset("""no not never none nobody nothing neither nor cannot
without nowhere dont doesnt didnt isnt arent wasnt werent wont wouldnt cant
couldnt shouldnt hasnt havent hadnt mustnt aint""".split())

_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-z]+(?:'[a-z]+)?")

#: path -> items left out as near-duplicates or repeats (no text).
SUPPRESSED: dict = {}
_LOCK = threading.Lock()


def overfetch(n: int, factor: int = OVERFETCH_FACTOR, max_extra: int = OVERFETCH_MAX_EXTRA) -> int:
    """How many candidates to fetch for `n` slots: up to `factor`x, at most
    `max_extra` more than `n`."""
    n = max(1, int(n))
    return n + min((factor - 1) * n, max_extra)


def _number(tok: str) -> str:
    t = tok.replace(",", "")
    if "." in t:
        whole, _, frac = t.partition(".")
        frac = frac.rstrip("0")
        t = whole + ("." + frac if frac else "")
    return t.lstrip("0") or "0"


def signature(text: str) -> Tuple[frozenset, frozenset, bool]:
    """(content words, numbers, negated) for a text."""
    low = str(text or "").lower().replace("’", "'").replace("‘", "'")
    words, numbers, neg = set(), set(), 0
    for tok in _TOKEN.findall(low):
        if tok[0].isdigit():
            n = _number(tok)
            numbers.add(n)
            words.add(n)
            continue
        bare = tok.replace("'", "")
        if bare in _NEGATORS or tok.endswith("n't"):
            neg += 1
            words.add("not")
            continue
        if tok.endswith("'s"):
            tok = tok[:-2]
        if tok in _STOP:
            continue
        words.add(tok)
    return frozenset(words), frozenset(numbers), bool(neg)


def similarity(a, b) -> float:
    """Jaccard of the content-word sets (texts or signatures)."""
    sa = a if isinstance(a, tuple) else signature(a)
    sb = b if isinstance(b, tuple) else signature(b)
    wa, wb = sa[0], sb[0]
    if not wa and not wb:
        return 1.0
    return len(wa & wb) / max(1, len(wa | wb))


def is_near_duplicate(a, b, threshold: float = THRESHOLD) -> bool:
    """Do two texts (or signatures) say the same thing? Differing numbers or
    polarity always make them distinct."""
    sa = a if isinstance(a, tuple) else signature(a)
    sb = b if isinstance(b, tuple) else signature(b)
    if sa[1] != sb[1] or sa[2] != sb[2]:
        return False
    wa, wb = sa[0], sb[0]
    if not wa or not wb:
        if wa or wb:
            return False
        # Nothing but stopwords on both sides: equal only if the raw text is.
        return (not isinstance(a, tuple) and not isinstance(b, tuple)
                and " ".join(str(a).lower().split()) == " ".join(str(b).lower().split()))
    inter = len(wa & wb)
    jac = inter / len(wa | wb)
    if jac >= threshold:
        return True
    small = min(len(wa), len(wb))
    return small >= 3 and inter == small and jac >= CONTAIN_JACCARD


def _ts(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").timestamp()
    except ValueError:
        return None


def _num(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


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


def select(items: Iterable[Any], limit: int, *, threshold: float = THRESHOLD,
           text: Callable[[Any], str] = _default_text,
           ident: Callable[[Any], Any] = _default_id,
           when: Callable[[Any], Optional[float]] = _default_ts,
           score: Callable[[Any], Optional[float]] = _default_score,
           superseded: Callable[[Any], bool] = _default_superseded,
           keep: str = "current", path: str = "") -> List[Any]:
    """Up to `limit` items, best-ranked first, with near-duplicates collapsed.

    `items` are in rank order (best first). Each near-duplicate group yields
    one item: with keep="current" the not-superseded, newest, best-scored one;
    with keep="first" the best-ranked one (for sources with no "said when").
    Groups keep the rank of their best-ranked member. Stable: equal inputs give
    equal outputs."""
    limit = max(0, int(limit))
    seen_ids = set()
    groups: List[dict] = []          # {"sigs": [...], "members": [(rank, item)]}
    dropped = 0
    for rank, it in enumerate(items):
        iid = ident(it)
        if iid is not None and iid != "":
            if iid in seen_ids:
                dropped += 1
                continue
            seen_ids.add(iid)
        sig = signature(text(it))
        home = None
        for g in groups:
            if any(is_near_duplicate(sig, s, threshold) for s in g["sigs"]):
                home = g
                break
        if home is None:
            groups.append({"sigs": [sig], "members": [(rank, it)]})
        else:
            home["sigs"].append(sig)
            home["members"].append((rank, it))
            dropped += 1

    def _pick(members):
        if keep == "first":
            return members[0][1]

        def k(m):
            r, it = m
            t, s = when(it), score(it)
            return (1 if superseded(it) else 0,
                    -(t if t is not None else float("-inf")),
                    -(s if s is not None else float("-inf")),
                    r)
        return sorted(members, key=k)[0][1]

    out = [_pick(g["members"]) for g in groups][:limit]
    if path:
        note_suppressed(path, dropped)
    return out


def note_suppressed(path: str, count: int) -> None:
    """Count items a path left out (no text is kept)."""
    if path and count:
        with _LOCK:
            SUPPRESSED[path] = SUPPRESSED.get(path, 0) + int(count)


def suppressed_counts() -> dict:
    """Items left out per path since start (counts only)."""
    with _LOCK:
        return dict(SUPPRESSED)
