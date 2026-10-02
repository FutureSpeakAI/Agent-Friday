"""Stories that already ran, across editions, and what an edition may carry.

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
import time
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
            rec["last_ran_at"] = _when(ed.get("generated_at"))
    return out


def _when(iso) -> float | None:
    try:
        return time.mktime(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return None


def _find(seen: list[dict], story: dict) -> dict | None:
    key = _url_key(story.get("url"))
    head = _headline(story.get("title"))
    for rec in seen:
        if key and key in rec["urls"]:
            return rec
        if head and rec["headline"] and len(head & rec["headline"]) / len(head | rec["headline"]) >= SAME_HEADLINE:
            return rec
    return None


def _development(story: dict, rec: dict) -> list[str]:
    """What a story brings that its earlier run did not: new facts (names,
    figures, days) and new outlets. Empty: nothing new."""
    text = "%s. %s" % (story.get("title") or "", story.get("snippet") or "")
    # Compared in lower case; said as written ("Ledgerline", not "ledgerline").
    written = {w.strip(".,;:!?()\"'“”‘’").lower(): w.strip(".,;:!?()\"'“”‘’") for w in text.split()}
    # A word of the story's own headline is its identity, not a development;
    # nor is anything published before the edition it last ran in.
    ts = float(story.get("ts") or 0)
    if ts and rec.get("last_ran_at") and ts <= rec["last_ran_at"]:
        return []
    new_facts = [written.get(f, f) for f in sorted(_facts(text) - rec["facts"])
                 if f[:6] not in rec["headline"]]
    source = (story.get("source") or "").lower()
    what = []
    if new_facts:
        what.append("new: " + ", ".join(new_facts[:5]))
    if source and source not in rec["sources"]:
        what.append("newly reported by " + source)
    return what


def _day(ts) -> str:
    try:
        t = time.localtime(float(ts))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return "%s %d" % (time.strftime("%b", t), t.tm_mday)


def _update_note(story: dict, rec: dict, what: list[str]) -> str:
    day = _day(story.get("ts"))
    return "Update%s on a story that ran in %s (%s)." % ((" (%s)" % day) if day else "", rec["last_ran"],
                                                         "; ".join(what))


def filter_pool(pool: list[dict], seen: list[dict]) -> tuple[list[dict], list[dict]]:
    """(stories to offer, stories held back). A seen story is offered only
    with something new, marked `update` with an `update_note` saying what."""
    kept, held = [], []
    for story in pool:
        rec = _find(seen, story)
        if rec is None:
            kept.append(story)
            continue
        what = _development(story, rec)
        if not what:
            held.append({"title": story.get("title") or "", "url": story.get("url") or "",
                         "first_ran": rec["first_ran"], "last_ran": rec["last_ran"], "why": "ran before, nothing new"})
            continue
        kept.append(dict(story, update=True, update_note=_update_note(story, rec, what)))
    return kept, held


# ── what an edition may carry ───────────────────────────────────────────────

#: Hours a story stays eligible for an edition, by routine (settings
#: `news_edition_window_hours` overrides).
DEFAULT_WINDOW_H = 36

_NOT_ARTICLE_RE = re.compile(
    r"^(?:your (?:latest|daily|local|morning|evening|weekly) (?:forecast|headlines|news|weather|updates?)"
    r"|(?:latest|top) (?:headlines|stories|news)|(?:weather )?forecast|weather|headlines)$", re.I)
_PROMO_RE = re.compile(r"^[^:]{2,40}:\s.*\b(?:keeping you|stay (?:safe|informed)|download|get the app|"
                       r"sign up|subscribe)\b", re.I)


# ── news value ──────────────────────────────────────────────────────────────

#: Hard news first, then analysis, service pieces, and promotion last.
VALUE_ORDER = ("hard", "analysis", "service", "promo")
_SPONSORED_RE = re.compile(r"^(?:sponsored|partner content|paid content|advertisement|presented by|"
                           r"brought to you by)\b|\bsponsored content\b", re.I)
_ADVERTORIAL_RE = re.compile(r"\b(?:discusses|explains|shares) how\b.*\b(?:can|could)\s+(?:help|partner|support|save)\b",
                             re.I)
_EVENT_RE = re.compile(r"\b(?:summit|disrupt|conference|festival|expo|awards|tickets|session|speakers?|"
                       r"agenda|guide to)\b", re.I)
_HARD_RE = re.compile(
    r"\b(?:council|legislat\w*|senate|congress|court|judge|ruling|lawsuit|indict\w*|charged|police|"
    r"sheriff|fbi|governor|mayor|budget|votes?|voted|election|ballot|bill|law|ordinance|tax(?:es)?|"
    r"jobs report|unemployment|inflation|tariffs?|strike|evacuat\w*|wildfire|flood\w*|outage|recall)\b", re.I)
_SERVICE_RE = re.compile(
    r"\b(?:things to do|guide to|how to|tips|list of|events (?:across|this|in|around)|weekend (?:check|guide)|"
    r"where to|recipes?|deals?|gear up)\b", re.I)
_MARKETS_RE = re.compile(r"\b(?:stocks?|bonds?|bond market|yields?|nasdaq|s&p|dow|wall street|markets?|"
                         r"investors?|shares|earnings|ipo)\b", re.I)
_FIRST_PERSON_RE = re.compile(r"\b(?:I|I've|I'm|I'd|I'll)\b|\bmy\b|^opinion\b|^column\b", re.I)


def _brand(item: dict) -> str:
    return re.sub(r"[^a-z0-9]", "", (item.get("source") or "").lower().removeprefix("www.").split(".")[0])


def _promotes(item: dict) -> bool:
    """Sponsored or advertorial, or an outlet promoting its own event."""
    title = item.get("title") or ""
    if _SPONSORED_RE.search(title) or _ADVERTORIAL_RE.search(title):
        return True
    brand = _brand(item)
    squashed = re.sub(r"[^a-z0-9]", "", title.lower())
    return bool(brand and len(brand) > 3 and brand in squashed and _EVENT_RE.search(title))


def news_value(item: dict) -> str:
    """"hard", "analysis", "service" or "promo"."""
    if _promotes(item):
        return "promo"
    title = item.get("title") or ""
    from agent_friday.services.podcast_quality import is_safety_story
    if is_safety_story({"title": title, "text": ""}) or _HARD_RE.search(title):
        return "hard"
    if _SERVICE_RE.search(title):
        return "service"
    return "analysis"


def rank(items: list[dict]) -> list[dict]:
    """Hard news first, keeping the order within each kind."""
    return sorted(items, key=lambda i: VALUE_ORDER.index(news_value(i)))


def cap_per_outlet(items: list[dict], per_outlet: int = 2) -> list[dict]:
    seen: dict = {}
    out = []
    for i in items:
        src = (i.get("source") or "").lower()
        seen[src] = seen.get(src, 0) + 1
        if seen[src] <= per_outlet:
            out.append(i)
    return out


def is_opinion(item: dict) -> bool:
    """A column or opinion piece, labelled as such on the card."""
    url = (item.get("url") or "").lower()
    if re.search(r"/(?:opinion|opinions|op-ed|oped|column|columns|commentary)(?:/|$)", url):
        return True
    return bool(_FIRST_PERSON_RE.search(item.get("title") or ""))


def section_for(item: dict) -> str:
    """The section a story belongs in, whatever feed it came from: a markets
    story is Business."""
    if _MARKETS_RE.search(item.get("title") or ""):
        return "Business"
    return item.get("category") or ""


def window_hours(routine: str) -> float:
    try:
        from agent_friday.core import _load_settings
        v = ((_load_settings() or {}).get("news_edition_window_hours") or {}).get(routine)
        return float(v) if v else float(DEFAULT_WINDOW_H)
    except Exception:
        return float(DEFAULT_WINDOW_H)


def is_article(item: dict) -> bool:
    """A story, not a page: no forecast, headline index, app promo, homepage,
    untitled or cut-off post ("Yet More …")."""
    title = (item.get("title") or "").strip()
    if not title:
        return False
    if _NOT_ARTICLE_RE.match(title.rstrip(".! ")) or _PROMO_RE.match(title) or _promotes(item):
        return False
    # A death notice is a private person's page, not front-page news.
    if re.search(r"\bobituary\b|\bobituaries\b", title, re.I):
        return False
    words = re.findall(r"[A-Za-z0-9']+", title)
    if (title.endswith("\u2026") or title.endswith("...")) and len(words) <= 3:
        return False
    try:
        path = urlsplit(item.get("url") or "").path
    except ValueError:
        path = ""
    if item.get("url") and not path.strip("/"):
        return False
    return True


def merge_events(pool: list[dict]) -> list[dict]:
    """One event reported by several outlets is one story: the freshest report
    leads, and `also` lists every other outlet's report."""
    def head(p):
        # The outlet's own name ("TechCrunch Disrupt") is not what the story is about.
        site = re.sub(r"[^a-z0-9]", "", (p.get("source") or "").lower().removeprefix("www.").split(".")[0])
        words = [w for w in re.findall(r"[a-z0-9]+", (p.get("title") or "").lower()) if w != site]
        return {w[:6] for w in words if w not in _STOP and len(w) > 2}
    heads = [head(p) for p in pool]
    parent = list(range(len(pool)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            a, b = heads[i], heads[j]
            if not a or not b:
                continue
            shared = len(a & b)
            if (shared >= 3 and shared / len(a | b) >= 0.2) or shared / len(a | b) >= 0.5:
                parent[root(i)] = root(j)
    groups: dict = {}
    for i in range(len(pool)):
        groups.setdefault(root(i), []).append(i)
    out = []
    for i in range(len(pool)):
        g = groups.get(root(i))
        if g is None or g[0] != i:
            continue
        members = sorted((pool[k] for k in g), key=lambda p: -(float(p.get("ts") or 0)))
        # One outlet once: its other report of the same event is dropped,
        # never listed as another outlet.
        seen_src, uniq = set(), []
        for m in members:
            src = (m.get("source") or "").lower()
            if src in seen_src:
                continue
            seen_src.add(src)
            uniq.append(m)
        members = uniq
        lead = dict(members[0])
        if len(members) > 1:
            lead["also"] = [{"source": m.get("source") or "", "url": m.get("url") or "",
                             "title": m.get("title") or ""} for m in members[1:]]
        out.append(lead)
    return out


def current(items: list[dict], routine: str, *, now: float | None = None) -> list[dict]:
    """The items a routine may use: articles inside its window (an item with
    no date is kept; nothing says it is old)."""
    now = time.time() if now is None else now
    limit = window_hours(routine) * 3600
    return [i for i in items if is_article(i)
            and not (float(i.get("ts") or 0) and now - float(i.get("ts") or 0) > limit)]


def edition_pool(pool: list[dict], past: list[dict], *, now: float | None = None,
                 window_h: float = DEFAULT_WINDOW_H) -> tuple[list[dict], list[dict]]:
    """(stories an edition may carry, stories held back with why).

    A story is eligible inside the window; one that ran before (or one older
    than the window) only with a material new development, as a dated update.
    Pages that are not articles are never stories, and one event from several
    outlets is one story.
    """
    now = time.time() if now is None else now
    seen = index([e for e in past if e])
    kept, held = [], []
    for story in pool:
        title = story.get("title") or ""
        if not is_article(story):
            held.append({"title": title, "url": story.get("url") or "", "why": "not an article"})
            continue
        ts = float(story.get("ts") or 0) or None
        old = ts is not None and now - ts > window_h * 3600
        rec = _find(seen, story)
        what = _development(story, rec) if rec else []
        if rec and what and not old:
            kept.append(dict(story, update=True, update_note=_update_note(story, rec, what)))
        elif rec is None and not old:
            kept.append(story)
        else:
            held.append({"title": title, "url": story.get("url") or "",
                         "why": "older than the edition window" if old else "ran before, nothing new"})
    return merge_events(kept), held
