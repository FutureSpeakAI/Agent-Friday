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
_BARE_URL_RE = re.compile(r"(?<![(\[])\bhttps?://[^\s)\]>\"']+")

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


def attach_links(text: str, stories: list[dict], *, sources_heading: str = "Sources") -> str:
    """Turn story ids into links from the fetched URLs, and remove any link the
    model wrote. Ends the text with the cited stories, each linked."""
    by_id = {s["id"]: s for s in stories}
    known = {s["url"] for s in stories if s.get("url")}

    # 1. Links and addresses the model typed: keep the words, drop the address,
    #    unless it is exactly a fetched story's URL.
    def md(m):
        return m.group(0) if m.group(2) in known else m.group(1)
    text = _MD_LINK_RE.sub(md, text or "")
    text = _BARE_URL_RE.sub(lambda m: m.group(0) if m.group(0) in known else "", text)

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
