"""Links in the News routines are attached by code, never typed by the model.

A small local model drops links on some days and invents them on others
(a real briefing linked "mail.google.com" and "linkedin.com/jobs/"). So the
model never sees a URL. It sees each fetched story as

    [N3] Headline (Outlet): snippet

and cites stories by id. `attach_links` then turns each id into a link to
the URL that story was fetched from, removes any link or address the model
wrote itself, and ends the text with the stories it cited. The podcast's
source chips resolve from the same ids.

Used by all four routines: the Briefing and the Editorial (markdown), the
Weekly Digest (stories chosen by id), and the Front Page, whose stories were
already chosen by index and linked by code.
"""

from __future__ import annotations

import re

#: Matches [N3], [N3, N5], [N3][N5]: citations by story id.
_CITE_RE = re.compile(r"\[\s*([A-Z]\d{1,3}(?:\s*[,;]\s*[A-Z]\d{1,3})*)\s*\]")
_MD_LINK_RE = re.compile(r"\[([^\]\n]{1,300})\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"(?<![(\[])\bhttps?://[^\s)\]>\"'\x00]+")
#: marked's own rule (9.1.6, inline._backpedal, applied until it stops changing) for where an address it links
#: ends: closing punctuation and a ")" that opens nothing are trimmed off the end, a "(" with no ")" ends it, and
#: so does an "&" that starts an entity at the end. ui_parts/app.html renders a briefing with marked (gfm).
_BACKPEDAL = re.compile(r"(?:[^?!.,:;*_'\"~()&]+|\([^)]*\)|&(?![a-zA-Z0-9]+;\Z)|[?!.,:;*_'\"~)]+(?!\Z))+")


def _linked(run: str) -> str:
    """The address marked links when it meets `run`: an address and everything glued to it, up to a space."""
    while True:
        found = _BACKPEDAL.match(run)
        linked = found.group(0) if found else ""
        if linked == run:
            return run
        run = linked


_MARK = re.compile("\x00(\\d+)\x00")


def set_aside_known(text: str, known, kept: list) -> str:
    """The text with every known link replaced by a mark, its spelling saved in `kept`: first each exact "](known)"
    target, then each known address standing on its own up to a natural end. Every pass that removes links runs
    on the marked text, so no pattern of its own can cut a known address short (a ")" or a "." in it, or after it)
    and take it for an unknown one; put_back restores them. The one place both link passes decide what is known
    (services/briefing_links.strip_unknown_links and attach_links); tests/unit/test_briefing_links.py runs every
    address shape through both."""
    def mark(spelling: str) -> str:
        kept.append(spelling)
        return "\x00%d\x00" % (len(kept) - 1)
    ordered = sorted(set(known), key=len, reverse=True)
    for url in ordered:
        span = "](%s)" % url
        if span in text:
            text = text.replace(span, mark(span))
    for url in ordered:
        # Everything glued to the address up to a space is what marked would link, a link set aside before it
        # included (put_back restores it, glued as it was); the address stands alone only when that is exactly
        # the address. "K)evil.example", "K<@evil.example", "K]x" and K glued to another known link are other
        # addresses, and go.
        text = re.sub(re.escape(url) + "\\S*",
                      lambda m, url=url: mark(m.group(0)) if _linked(m.group(0)) == url else m.group(0), text)
    return text


def put_back(text: str, kept: list) -> str:
    return _MARK.sub(lambda m: kept[int(m.group(1))], text)

CITE_RULE = (
    "Each story below has an id in square brackets, like [N3]. Cite a story by "
    "writing its id in square brackets right after the sentence that uses it, "
    "for example: \"The council passed the budget. [N3]\". Never write a web "
    "address or a markdown link; the links are added for you from the ids.")


def working(url: str) -> bool:
    """A link a reader can open: an http(s) address with a host."""
    return bool(re.match(r"^https?://[^\s/]+\.[^\s/]+", url or ""))


def number(items: list[dict], prefix: str = "N") -> list[dict]:
    """Give each fetched story an id (N1, N2, ...). Keeps title, outlet, url,
    snippet; the id is what the model cites and what the podcast resolves."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not (it.get("title") or "").strip():
            continue
        out.append({"id": "%s%d" % (prefix, len(out) + 1),
                    "title": str(it.get("title")).strip(),
                    "source": str(it.get("source") or it.get("domain") or "").strip(),
                    "url": str(it.get("url") or "").strip(),
                    "snippet": str(it.get("snippet") or "").strip(),
                    **({"category": it["category"]} if it.get("category") else {})})
    return out


def prompt_lines(stories: list[dict], *, snippet_chars: int = 200) -> str:
    """The stories as the model sees them: id, headline, outlet, snippet. No URL."""
    lines = []
    for s in stories:
        line = "[%s] %s" % (s["id"], s["title"])
        if s.get("source"):
            line += " (%s)" % s["source"]
        if s.get("snippet"):
            line += ": " + s["snippet"][:snippet_chars]
        lines.append(line)
    return "\n".join(lines)


def outlet_name(story: dict) -> str:
    from agent_friday.services.podcast_quality import spoken_outlet
    return spoken_outlet({"outlet": story.get("source") or "", "title": story.get("title") or ""}) \
        or story.get("source") or "source"


def cited_ids(text: str) -> list[str]:
    seen = []
    for m in _CITE_RE.finditer(text or ""):
        for sid in re.split(r"\s*[,;]\s*", m.group(1)):
            if sid not in seen:
                seen.append(sid)
    return seen


def attach_links(text: str, stories: list[dict], *, sources_heading: str = "Sources", keep=()) -> str:
    """Turn story ids into links from the fetched URLs, and remove any link the
    model wrote. Ends the text with the cited stories, each linked. `keep` are
    links code already verified from the run's own sources (the calendar and
    email links services/briefing_links put in); they are not removed."""
    by_id = {s["id"]: s for s in stories}
    known = {s["url"] for s in stories if s.get("url")} | set(keep)

    # 1. Links and addresses the model typed: keep the words, drop the address,
    #    unless it is exactly a fetched story's URL (set aside first: set_aside_known).
    kept: list = []
    text = set_aside_known((text or "").replace("\x00", ""), known, kept)
    text = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = put_back(_BARE_URL_RE.sub("", text), kept)

    # 2. Ids to links.
    def cite(m):
        links = []
        for sid in re.split(r"\s*[,;]\s*", m.group(1)):
            s = by_id.get(sid)
            if s and working(s.get("url")):
                links.append("[%s](%s)" % (outlet_name(s), s["url"]))
            elif s:
                links.append(outlet_name(s))
        return "(%s)" % ", ".join(links) if links else ""
    body = _CITE_RE.sub(cite, text)
    body = re.sub(r"[ \t]+\(", " (", body)
    body = re.sub(r" +([.,;:])", r"\1", body)

    cited = [by_id[i] for i in cited_ids(text) if i in by_id]
    if cited:
        body = body.rstrip() + "\n\n## %s\n" % sources_heading + "\n".join(
            "- [%s](%s) — %s" % (s["title"], s["url"], outlet_name(s)) if working(s.get("url"))
            else "- %s — %s (no link)" % (s["title"], outlet_name(s)) for s in cited) + "\n"
    return body


def link_problems(text: str, stories: list[dict]) -> list[str]:
    """Why a routine's text fails the link rule: no story cited, or a cited
    story without a working link. Empty when every story used is linked."""
    ids = [i for i in cited_ids(text) if i in {s["id"] for s in stories}]
    if stories and not ids:
        return ["no story is cited by id, so none can be linked"]
    by_id = {s["id"]: s for s in stories}
    return ["%s \"%s\" has no working link" % (i, by_id[i]["title"][:80])
            for i in ids if not working(by_id[i].get("url"))]


def for_speech(context: str) -> str:
    """The same stories for a spoken briefing: no citation rule and no ids,
    which a voice would read out. The outlet stays, to be named aloud."""
    text = (context or "").replace(CITE_RULE, "")
    return re.sub(r"(?m)^(\W{0,3})\[[A-Z]\d{1,3}\]\s*", r"\1- ", text)


# ── Google News redirects ───────────────────────────────────────────────────
#
# A Google News feed item links to news.google.com/rss/articles/<id>, not to
# the publisher. The id is either the publisher's URL, base64-encoded (older
# items), or an opaque id Google exchanges for the URL on request. Links put
# in front of a reader are the publisher's; one that cannot be resolved is
# kept, so the story still opens.

_GN_RE = re.compile(r"^https?://news\.google\.com/(?:rss/)?(?:articles|read)/([A-Za-z0-9_\-]+)")
_GN_EXEC = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_RESOLVED: dict = {}


def _decode_old(gid: str) -> str:
    import base64
    try:
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
    except (ValueError, TypeError):
        return ""
    m = re.search(rb"https?://[\x21-\x7e]+", raw)
    if not m:
        return ""
    url = m.group(0).decode("ascii", "ignore")
    # The protobuf framing byte after the URL is not part of it.
    return re.sub(r"[^\w/%&=?#.:~+\-]+$", "", url)


def _record(url: str, result, content=None) -> None:
    """A news link lookup in the egress ledger and on the privacy map (§26.7).
    Never raises."""
    try:
        from agent_friday.services import egress_gate as _eg
        _eg.record_address("news_feed", url, result, content=content, prefix="news")
    except Exception:
        pass


def _fetch(url: str, data: str | None = None) -> str:
    """GET through the SSRF guard; POST only to Google's fixed exchange endpoint."""
    from agent_friday.services.cloud_intent import hold_outbound
    hold_outbound("web_fetch", "news link")         # A16a: raises RouteNotChosen
    if data is None:
        from agent_friday.services.web_safety import safe_get
        try:
            text = safe_get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).text
        except Exception as e:
            if type(e).__name__ != "UnsafeURLError":     # refused before anything was sent
                _record(url, e)
            raise
        _record(url, "sent")
        return text
    if url != _GN_EXEC:
        raise ValueError("refusing to post anywhere but Google's link exchange")
    import requests
    from agent_friday.services.web_safety import pinned
    try:
        with pinned(url):
            text = requests.post(url, data={"f.req": data}, timeout=10,
                                 headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                                          "User-Agent": "Mozilla/5.0"}).text
    except Exception as e:
        if type(e).__name__ != "UnsafeURLError":
            _record(url, e, {"url": url, "request": data})
        raise
    _record(url, "sent", {"url": url, "request": data})
    return text


def resolve_url(url: str, *, fetch=None) -> str:
    """The publisher's URL for a Google News link; any other URL unchanged."""
    m = _GN_RE.match(url or "")
    if not m:
        return url
    if url in _RESOLVED:
        return _RESOLVED[url]
    gid = m.group(1)
    real = _decode_old(gid)
    if real and "news.google.com" not in real and not real.startswith("https://news.google"):
        _RESOLVED[url] = real
        return real
    fetch = fetch or _fetch
    try:
        import json as _json
        page = fetch("https://news.google.com/rss/articles/" + gid)
        sig = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        aid = re.search(r'data-n-a-id="([^"]+)"', page)
        if not (sig and ts):
            return url
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,'
                 'null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]'
                 % (aid.group(1) if aid else gid, ts.group(1), sig.group(1)))
        body = fetch(_GN_EXEC, _json.dumps([[["Fbv4je", inner]]]))
        found = re.search(r'garturlres\\",\\"(https?://[^"\\]+)', body)
        if found:
            _RESOLVED[url] = found.group(1)
            return found.group(1)
    except Exception:
        pass
    return url


def resolve_links(stories: list[dict], ids=None, *, fetch=None) -> list[dict]:
    """Replace Google News redirects with publisher URLs for the given story ids
    (all when None). Only the stories a routine used are looked up."""
    want = set(ids) if ids is not None else None
    for s in stories:
        if want is None or s.get("id") in want:
            s["url"] = resolve_url(s.get("url") or "", fetch=fetch)
    return stories
