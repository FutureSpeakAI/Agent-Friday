"""Discuss with Friday: one story, evidence first (docs/design/active/news-discuss.md).

Seven modes: compare coverage, primary source, background, claim check,
follow, the local angle, and make something from it. Every answer is in two
parts: what the sources say (each claim citing numbered sources, linked here
from the fetched URLs, never typed by the model) and her read (labelled as
hers, no citations). A claim its cited source does not support moves to her
read, or is cut (podcast_quality.support). A story of violence or a threat
gets no read. The owner's media diet holds, with a receipt.

The model is the local seat only (podcast_engine._llm_json, behind the
local-only guard); when it is not serving, the answer says so. Web fetches
go through the SSRF-guarded fetcher.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

MODES = ("compare", "primary", "background", "claims", "follow", "local", "make")
#: Days of archive each mode reads.
ARCHIVE_DAYS = {"compare": 4, "claims": 4, "local": 7, "background": 30, "primary": 2}
MAX_RELATED = {"compare": 6, "claims": 6, "local": 6, "background": 12, "primary": 3}
STATUSES = ("confirmed", "disputed", "one side only")
_PRIMARY_RE = re.compile(
    r"https?://[^\s\"'<>)]*(?:\.gov|\.mil|congress\.gov|courtlistener\.com|supremecourt\.gov|sec\.gov"
    r"|arxiv\.org|doi\.org|nih\.gov|europa\.eu|\.int)(?:/[^\s\"'<>)]*)?", re.I)


def _dir() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "news"


def _domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/:]+)", url or "")
    return m.group(1).lower() if m else ""


# ── sources ─────────────────────────────────────────────────────────────────

def _archive(days: int) -> list[dict]:
    from agent_friday.services import news_engine as ne
    out = []
    today = datetime.now()
    for i in range(days):
        out += ne._load_archive_day((today - timedelta(days=i)).strftime("%Y-%m-%d")) or []
    return out


def _fetch(url: str) -> tuple[str, str]:
    from agent_friday.services import news_engine as ne
    return ne._extract_article_text(url)


def _related(title: str, url: str, items: list[dict], limit: int) -> list[dict]:
    """Other reports of the same story: headlines that share its words."""
    from agent_friday.services.news_seen import _headline, _url_key
    head = _headline(title)
    seen_urls = {_url_key(url)}
    scored = []
    for it in items:
        k = _url_key(it.get("url"))
        if not k or k in seen_urls:
            continue
        other = _headline(it.get("title"))
        shared = len(head & other)
        if head and other and (shared >= 3 or shared / len(head | other) >= 0.3):
            seen_urls.add(k)
            scored.append((shared, it))
    scored.sort(key=lambda x: -x[0])
    out, outlets = [], set()
    for _s, it in scored:
        o = (it.get("source") or _domain(it.get("url"))).lower()
        if o in outlets and len(out) >= 3:
            continue
        outlets.add(o)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def _doc(sid, title, text, url, outlet, when=""):
    return {"sid": sid, "kind": "news", "title": title or url, "text": text or "", "url": url or "",
            "outlet": outlet or _domain(url), "when": when}


# ── the local model ─────────────────────────────────────────────────────────

_SHAPES = {
    "compare": '"outlets": [{"id": "D2", "framing": "one line", "left_out": "what it omits that others report"}]',
    "primary": '"findings" cite the primary document (P ids) for what it says',
    "background": ('"timeline": [{"when": "YYYY-MM-DD", "what": "...", "cites": ["D1"]}], '
                   '"who": [{"name": "...", "role": "one line", "cites": ["D1"]}]'),
    "claims": ('"claims": [{"claim": "...", "status": "confirmed" | "disputed" | "one side only", '
               '"cites": ["D1"]}] (confirmed = two independent outlets or a primary document; '
               'disputed = sources disagree, cite both; one side only = only the party making it)'),
    "local": '"findings" about what the story means in %s',
}
_ASK = {
    "compare": "Compare how these outlets covered the same story: each one's framing, and what each left out that another reported.",
    "primary": "Say what the primary document (P sources) says on the point the story rests on, quoting its own words.",
    "background": "Give the background: a dated timeline of what led here and who's who, from these sources only.",
    "claims": "List each checkable claim in the story and mark it confirmed, disputed or one side only, from these sources only.",
    "local": "Say what this story means in %s, the listener's home, from these sources only. If nothing ties it there, say so in one finding.",
}
SYSTEM = (
    "You are Friday, answering the owner's question about one news story, evidence first. "
    "Use only the numbered sources. Every finding cites the ids it comes from. Your own analysis "
    "goes in \"read\", never in a finding, and cites nothing. Never type a link. Return JSON only.")


def _ask(mode, docs, title, city, llm):
    lines = []
    for d in docs:
        # Links come from code, never from the model: it never sees one.
        text = re.sub(r"https?://\S+", "[link]", d["text"][:3000])
        lines.append("[%s] %s (%s%s)\n%s" % (d["sid"], d["title"], d["outlet"],
                                             (", " + d["when"]) if d.get("when") else "", text))
    shape = _SHAPES[mode] % city if "%s" in _SHAPES[mode] else _SHAPES[mode]
    ask = _ASK[mode] % city if "%s" in _ASK[mode] else _ASK[mode]
    user = ("STORY: %s\n\nSOURCES:\n\n%s\n\n%s\nReturn {\"findings\": [{\"text\": \"...\", \"cites\": [\"D1\"]}], "
            "\"read\": [\"...\"], %s}." % (title, "\n\n".join(lines), ask, shape))
    raw, _model = llm(SYSTEM, user, max_tokens=2500)
    return raw if isinstance(raw, dict) else {}


def _links(cites, by):
    return [by[c]["url"] for c in cites or [] if c in by and by[c].get("url")]


def discuss(url: str, title: str = "", mode: str = "compare", *, fetch=None, archive=None,
            llm=None) -> dict:
    mode = mode if mode in MODES else "compare"
    if mode == "follow":
        follow(url, title)
        return {"status": "ok", "mode": mode, "note": "Following it: the next Front Page and Briefing "
                "say what changed, or that nothing did."}
    if mode == "make":
        return {"status": "error", "message": "say what to make: a podcast, notes or a draft"}
    from agent_friday.services import media_diet
    from agent_friday.services import podcast_engine as pe
    from agent_friday.services import podcast_quality as q
    fetch = fetch or _fetch
    archive = archive or _archive
    llm = llm or pe._llm_json
    city = ""
    if mode == "local":
        city = pe.home_city().split(",")[0].strip()
        if not city:
            return {"status": "ok", "mode": mode, "findings": [], "read": [], "sources": [],
                    "note": "No local angle: set your city in News, Local beat, and ask again."}
    try:
        page_title, text = fetch(url)
    except Exception:
        page_title, text = "", ""
    title = title or page_title or url
    docs = [_doc("D1", title, text, url, _domain(url))]
    related = _related(title, url, archive(ARCHIVE_DAYS.get(mode, 4)), MAX_RELATED.get(mode, 6))
    if mode == "local":
        related += [it for it in archive(ARCHIVE_DAYS["local"])
                    if city.lower() in ("%s %s" % (it.get("title"), it.get("snippet"))).lower()
                    and it not in related][:4]
    for i, it in enumerate(related, start=2):
        docs.append(_doc("D%d" % i, it.get("title"), it.get("snippet"), it.get("url"),
                         it.get("source"), (it.get("published_at") or "")[:10]))
    primary = []
    if mode == "primary":
        for i, link in enumerate(dict.fromkeys(_PRIMARY_RE.findall(text or "")), start=1):
            primary.append({"id": "P%d" % i, "url": link.rstrip(".,")})
            if len(primary) >= 3:
                break
        if not primary:
            return {"status": "ok", "mode": mode, "primary": [], "findings": [], "read": [],
                    "sources": _public(docs), "note": "Primary source not found in the article; "
                    "I won't guess one."}
        for p in primary:
            try:
                ptitle, ptext = fetch(p["url"])
            except Exception:
                ptitle, ptext = p["url"], ""
            docs.append(_doc(p["id"], ptitle, ptext, p["url"], _domain(p["url"])))
    before = len(docs)
    docs = media_diet.enforce_docs(docs, "discuss")
    diet_removed = before - len(docs)
    by = {d["sid"]: d for d in docs}
    story_list = q.stories(docs)
    hurt = bool(q._SAFETY_RE.search("%s %s" % (title, text or "")))
    try:
        raw = _ask(mode, docs, title, city, llm)
    except Exception as e:
        return {"status": "error", "mode": mode,
                "message": "The local model is not answering (%s). Nothing was sent anywhere else; "
                           "try again when it is serving." % str(e)[:120]}
    findings, read = [], [str(r) for r in raw.get("read") or [] if str(r).strip()]
    for f in raw.get("findings") or []:
        if not isinstance(f, dict) or not str(f.get("text") or "").strip():
            continue
        cites = [c for c in f.get("cites") or [] if c in by]
        verdict, _why = q.support(f["text"], cites, story_list, docs) if cites else ("own", [])
        if verdict == "ok":
            findings.append({"text": f["text"], "cites": cites, "links": _links(cites, by)})
        elif verdict == "own":
            read.append(f["text"])
    out = {"status": "ok", "mode": mode, "title": title, "findings": findings,
           "read": [] if hurt else read, "sources": _public(docs), "diet_removed": diet_removed,
           "note": "A story of violence or a threat: what is confirmed, and no read." if hurt else ""}
    if mode == "compare":
        out["outlets"] = [{"id": o["id"], "outlet": q.spoken_outlet(by[o["id"]]) or by[o["id"]]["outlet"],
                           "framing": str(o.get("framing") or ""), "left_out": str(o.get("left_out") or ""),
                           "url": by[o["id"]]["url"]}
                          for o in raw.get("outlets") or [] if isinstance(o, dict) and o.get("id") in by]
    if mode == "background":
        out["timeline"] = [{"when": str(t.get("when") or ""), "what": str(t.get("what") or ""),
                            "links": _links(t.get("cites"), by)}
                           for t in raw.get("timeline") or [] if isinstance(t, dict) and _links(t.get("cites"), by)]
        out["who"] = [{"name": str(w.get("name") or ""), "role": str(w.get("role") or ""),
                       "links": _links(w.get("cites"), by)}
                      for w in raw.get("who") or [] if isinstance(w, dict) and w.get("name")]
    if mode == "claims":
        out["claims"] = [{"claim": str(c.get("claim") or ""), "status": c["status"], "links": _links(c.get("cites"), by)}
                         for c in raw.get("claims") or []
                         if isinstance(c, dict) and c.get("status") in STATUSES and _links(c.get("cites"), by)]
    if mode == "primary":
        out["primary"] = primary
    return out


def _public(docs):
    from agent_friday.services import podcast_quality as q
    return [{"id": d["sid"], "title": d["title"], "outlet": q.spoken_outlet(d) or d["outlet"],
             "url": d["url"], "when": d.get("when") or ""} for d in docs]


# ── follow ──────────────────────────────────────────────────────────────────

def _follows_path() -> Path:
    return _dir() / "follows.json"


def follows() -> list[dict]:
    try:
        return json.loads(_follows_path().read_text(encoding="utf-8")).get("follows") or []
    except (OSError, ValueError):
        return []


def follow(url: str, title: str) -> dict:
    fs = [f for f in follows() if f.get("url") != url]
    rec = {"url": url, "title": title or url, "since": time.strftime("%Y-%m-%d")}
    fs.append(rec)
    p = _follows_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"follows": fs}, indent=2), encoding="utf-8")
    return rec


def unfollow(url: str) -> bool:
    fs = follows()
    left = [f for f in fs if f.get("url") != url]
    if len(left) == len(fs):
        return False
    _follows_path().write_text(json.dumps({"follows": left}, indent=2), encoding="utf-8")
    return True


def follow_report(stories: list[dict]) -> list[dict]:
    """For each followed story: the development in this edition, or no change."""
    out = []
    for f in follows():
        # A development usually has a new headline: the same story is any
        # report sharing three of its words (as for related coverage).
        hit = next(iter(_related(f["title"], f["url"], stories, 1)), None)
        if hit is None:
            hit = next((s for s in stories if s.get("url") == f["url"]), None)
        if hit is not None:
            out.append({"title": f["title"], "url": f["url"], "status": "update",
                        "note": hit.get("update_note") or hit.get("thread_update") or hit.get("title") or "",
                        "story_url": hit.get("url") or ""})
        else:
            out.append({"title": f["title"], "url": f["url"], "status": "no change", "note": ""})
    return out


# ── make something ──────────────────────────────────────────────────────────

def make(url: str, title: str, what: str) -> dict:
    """A two-host deep-dive podcast (queued, local voice), or notes in the wiki."""
    if what == "podcast":
        from agent_friday.services import podcast_engine as pe
        ep = pe.create([{"kind": "url", "url": url}], title="Deep dive: %s" % (title or url)[:120],
                       length="standard", origin="user", voice_engine="local",
                       instructions="A two-host deep dive on this one story: what happened, what is "
                                    "confirmed, what is disputed, and why it matters.")
        return {"status": "ok", "made": "podcast", "episode": {"id": ep.get("id"), "status": ep.get("status")}}
    if what == "notes":
        from agent_friday import core
        slug = re.sub(r"[^a-z0-9]+", "-", (title or url).lower()).strip("-")[:60] or "story"
        p = Path(core.FRIDAY_DIR) / "wiki" / "news-notes" / ("%s.md" % slug)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# %s\n\nSource: %s\n\nNoted %s.\n" % (title or url, url, time.strftime("%Y-%m-%d")),
                     encoding="utf-8")
        return {"status": "ok", "made": "notes", "path": "news-notes/%s.md" % slug}
    return {"status": "error", "message": "make a podcast or notes; a Media draft is made from the panel"}


# ── the tool ────────────────────────────────────────────────────────────────

def tool_discuss_story(inp: dict) -> str:
    url, title, mode = str(inp.get("url") or ""), str(inp.get("title") or ""), str(inp.get("mode") or "compare")
    if not url.startswith("http"):
        return json.dumps({"status": "error", "say": "Which story? Open it, or give me its link."})
    if mode == "make":
        out = make(url, title, str(inp.get("make") or "podcast"))
    else:
        out = discuss(url, title, mode)
    if out.get("status") != "ok":
        return json.dumps({"status": "error", "say": out.get("message") or "That didn't work."})
    top = [f["text"] for f in out.get("findings") or []][:2]
    say = " ".join(top + ["My read: " + out["read"][0]] if out.get("read") else top) or out.get("note") or "Done."
    return json.dumps({"status": "ok", "say": say + " The links are on screen.", "result": out})


TOOLS = [{
    "name": "discuss_story",
    "description": ("Discuss one news story with the user, evidence first: compare coverage across outlets, "
                    "find the primary source, give background, check its claims, follow it, find the "
                    "local angle, or make a deep-dive podcast or notes from it. Local model only."),
    "input_schema": {"type": "object", "properties": {
        "url": {"type": "string", "description": "the story's link"},
        "title": {"type": "string"},
        "mode": {"type": "string", "enum": list(MODES)},
        "make": {"type": "string", "enum": ["podcast", "notes"]}},
        "required": ["url", "mode"]},
}]
HANDLERS = {"discuss_story": tool_discuss_story}
#: Ring 1: it reads the web and writes only the owner's own files.
RINGS = {"discuss_story": 1}


def register(claude_tools, handlers, rings, workspace_tools=None):
    """Handlers and rings are always registered, so the tool runs wherever it
    is named (voice and the loader resolve it by name). Its schema is News's
    own when a workspace registry is given: it travels with a turn in News or
    on request, outside the always-on catalogue and its latency budget."""
    target = workspace_tools.setdefault("news", []) if workspace_tools is not None else claude_tools
    known = {t["name"] for t in target}
    for t in TOOLS:
        if t["name"] not in known:
            target.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
