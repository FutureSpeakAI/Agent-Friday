"""Stories that already ran, across editions.

A story the Front Page already carried comes back only with a material new
development: a fact its earlier run did not have (a figure, a name, a day) or
an outlet that had not reported it. Kept, it is marked as an update with what
is new; otherwise it is held back, and the edition says so.

A story is the same story when its link is the same (tracking parameters and
fragments aside) or its headline is nearly the same, so a re-titled or
re-linked copy is still recognised.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

#: Headlines this alike (shared content words over all of them) are one story.
SAME_HEADLINE = 0.7

_STOP = set("""a an and are as at be by for from has have how in into is it its of on or that the
this to was were what when where which who why will with after over new says said but not now
then there they their these those she her his him our its while also more most than""".split())
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9$%.,'’-]*")
_FIGURE_RE = re.compile(r"\$?\d[\d,.]*[%BbMmKk]?")


def _url_key(url: str) -> str:
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if not p.netloc:
        return ""
    host = p.netloc.lower().removeprefix("www.")
    return urlunsplit(("", host, p.path.rstrip("/"), "", ""))


def _headline(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w[:6] for w in words if w not in _STOP and len(w) > 2}


def _facts(text: str) -> set:
    """Figures, and names written mid-sentence: what a development adds."""
    facts = {f.rstrip(".,").lower() for f in _FIGURE_RE.findall(text or "") if len(f.rstrip(".,")) > 1}
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        for tok in sent.split():
            w = re.sub(r"['’]s$", "", tok.strip(".,;:!?()\"'“”‘’"))
            # A capitalised word is a name, wherever it stands, unless it is
            # a common word that opens sentences ("The", "On").
            if len(w) >= 3 and w[0].isupper() and w.lower() not in _STOP:
                facts.add(w.lower())
    return facts


def _stories(edition: dict):
    if not isinstance(edition, dict):
        return
    if isinstance(edition.get("lead"), dict):
        yield edition["lead"]
    for sec in edition.get("sections") or []:
        for a in (sec or {}).get("articles") or []:
            if isinstance(a, dict):
                yield a


def index(editions: list[dict]) -> list[dict]:
    """What has already run: one record per story, oldest edition first."""
    out: list[dict] = []
    for ed in sorted((e for e in editions if isinstance(e, dict)), key=lambda e: str(e.get("id") or "")):
        for a in _stories(ed):
            rec = _find(out, a)
            if rec is None:
                rec = {"title": a.get("title") or "", "urls": set(), "headline": _headline(a.get("title")),
                       "facts": set(), "sources": set(), "first_ran": ed.get("id"), "last_ran": ed.get("id")}
                out.append(rec)
            rec["urls"].add(_url_key(a.get("url")))
            rec["facts"] |= _facts("%s. %s" % (a.get("title") or "", a.get("snippet") or ""))
            rec["sources"].add((a.get("source") or "").lower())
            rec["last_ran"] = ed.get("id")
    return out


def _find(seen: list[dict], story: dict) -> dict | None:
    key = _url_key(story.get("url"))
    head = _headline(story.get("title"))
    for rec in seen:
        if key and key in rec["urls"]:
            return rec
        if head and rec["headline"] and len(head & rec["headline"]) / len(head | rec["headline"]) >= SAME_HEADLINE:
            return rec
    return None


def filter_pool(pool: list[dict], seen: list[dict]) -> tuple[list[dict], list[dict]]:
    """(stories to offer, stories held back). A seen story is offered only
    with something new, marked `update` with an `update_note` saying what."""
    kept, held = [], []
    for story in pool:
        rec = _find(seen, story)
        if rec is None:
            kept.append(story)
            continue
        text = "%s. %s" % (story.get("title") or "", story.get("snippet") or "")
        # Compared in lower case; said as written ("Ledgerline", not "ledgerline").
        written = {w.strip(".,;:!?()\"'“”‘’").lower(): w.strip(".,;:!?()\"'“”‘’") for w in text.split()}
        new_facts = [written.get(f, f) for f in sorted(_facts(text) - rec["facts"])]
        source = (story.get("source") or "").lower()
        new_source = source and source not in rec["sources"]
        if not new_facts and not new_source:
            held.append({"title": story.get("title") or "", "url": story.get("url") or "",
                         "first_ran": rec["first_ran"], "last_ran": rec["last_ran"]})
            continue
        what = []
        if new_facts:
            what.append("new: " + ", ".join(new_facts[:5]))
        if new_source:
            what.append("newly reported by " + source)
        kept.append(dict(story, update=True,
                         update_note="Update on a story that ran in %s (%s)." % (rec["last_ran"], "; ".join(what))))
    return kept, held
