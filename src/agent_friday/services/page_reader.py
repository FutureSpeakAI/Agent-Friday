"""Reading one web page: the blocks worth reading, its links, its metadata.

The one change point every article reader shares (the news deep dive,
Discuss with Friday, the podcast quote check, the research harness and
browse_web): what a page says is decided here, once.

* Blocks are scored, not cut by length: text density, link density, the
  element's kind, and whether it sits in navigation, a promo rail, a share
  bar or a comment thread. A short wire paragraph stays; a menu of links
  goes.
* `select` picks the passages a question needs (BM25 over the blocks, the
  lead always kept, page order restored) inside a token budget sized for the
  local seat's 8,192-token window, instead of a fixed cut.
* Links are kept as numbered references beside the text, never inside it:
  the model reads words, and code reads where they point (a court filing, a
  bill, a study).
* The page's own metadata (publisher, dates, author, kind) becomes one
  header line, and says whether the page is an article at all.

Ideas from Crawl4AI by UncleCode (see CREDITS.md); no code or dependency
from it. Every fetch goes through web_safety.safe_get.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

#: Bumped when extraction changes, so cached reads made the old way expire.
VERSION = 2

#: Characters per token, for budgets (English prose, local tokenizers).
CHARS_PER_TOKEN = 4

_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre", "figcaption",
               "td", "dd", "address")
_TAG_WEIGHT = {"p": 1.0, "blockquote": 1.0, "h1": 0.9, "h2": 0.9, "h3": 0.8, "h4": 0.7,
               "pre": 0.7, "li": 0.6, "dd": 0.6, "td": 0.5, "figcaption": 0.5,
               "address": 0.8, "byline": 0.8}
_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "form",
              "button", "select", "input", "dialog")
#: An element whose class or id names page furniture is not the story.
_CLUTTER_RE = re.compile(
    r"(?:^|[\s_-])(?:nav|navbar|menu|footer|sidebar|side-?bar|rail|related|recommended|"
    r"promo|advert|ads?|ad-slot|sponsor(?:ed)?|social|share|sharing|newsletter|subscribe|"
    r"signup|comments?|disqus|breadcrumbs?|cookie|consent|paywall|trending|popular|"
    r"most-read|outbrain|taboola|teaser|more-stories|tags?)(?:$|[\s_-])", re.I)
_BYLINE_RE = re.compile(r"(?:^|[\s_-])(?:byline|author|dateline|contributor)s?(?:$|[\s_-])", re.I)
#: Lines that are page furniture whatever element carries them.
_BOILERPLATE_RE = re.compile(
    r"^(?:advertisement|sponsored(?: content)?|read more\b.*|related(?: stories| articles)?:?|"
    r"sign up\b.*|subscribe\b.*|share (?:this|on)\b.*|click here\b.*|skip to (?:main )?content|"
    r"(?:copyright\s*)?©.*|all rights reserved\.?|follow us\b.*|story continues below.*)$",
    re.I)
#: A block scores at least this to be kept.
KEEP_SCORE = 0.5
#: Below this much kept text the page is thin markup: read all of it.
FALLBACK_CHARS = 200

#: JSON-LD types that are a page of links, not a story.
NOT_ARTICLE_TYPES = {"webpage", "collectionpage", "searchresultspage", "profilepage",
                     "itemlist", "website", "aboutpage", "contactpage", "faqpage"}
_ARTICLE_TYPES = ("article", "newsarticle", "reportagenewsarticle", "analysisnewsarticle",
                  "opinionnewsarticle", "reviewnewsarticle", "backgroundnewsarticle",
                  "blogposting", "liveblogposting", "report", "scholarlyarticle")


@dataclass
class Page:
    url: str = ""
    title: str = ""
    blocks: list = field(default_factory=list)   # [{"kind", "text"}] in page order
    links: list = field(default_factory=list)    # [{"n", "anchor", "url", "block"}]
    meta: dict = field(default_factory=dict)     # publisher, published, modified, author, type, section
    dropped: int = 0

    @property
    def text(self) -> str:
        return "\n\n".join(b["text"] for b in self.blocks)

    @property
    def header(self) -> str:
        return header_line(self.meta)


def tokens(text: str) -> int:
    return -(-len(text or "") // CHARS_PER_TOKEN)


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    # get_text(" ") puts a space where an inline tag ended: "a report ." -> "a report."
    return re.sub(r" (?=[.,;:!?)\]](?:\s|$))", "", s)


# ── metadata ────────────────────────────────────────────────────────────────

def _ld_nodes(data):
    if isinstance(data, list):
        for x in data:
            yield from _ld_nodes(x)
    elif isinstance(data, dict):
        yield data
        for k in ("@graph", "mainEntity", "mainEntityOfPage"):
            if isinstance(data.get(k), (list, dict)):
                yield from _ld_nodes(data[k])


def _name(v) -> str:
    if isinstance(v, str):
        return _clean(v)
    if isinstance(v, dict):
        return _clean(str(v.get("name") or ""))
    if isinstance(v, list):
        names = [n for n in (_name(x) for x in v) if n]
        return ", ".join(dict.fromkeys(names))
    return ""


def _types(node) -> list[str]:
    t = node.get("@type")
    return [str(x).lower() for x in (t if isinstance(t, list) else [t]) if x]


def read_meta(soup) -> dict:
    """Publisher, dates, author, kind and section from the page's own
    og:/article: tags and its JSON-LD; the article's JSON-LD wins."""
    meta: dict = {}

    def tag(*keys):
        for k in keys:
            el = soup.find("meta", attrs={"property": k}) or soup.find("meta", attrs={"name": k})
            if el and _clean(el.get("content")):
                return _clean(el.get("content"))
        return ""
    meta["publisher"] = tag("og:site_name", "application-name", "publisher")
    meta["published"] = tag("article:published_time", "og:published_time", "datePublished",
                            "date", "pubdate", "parsely-pub-date", "sailthru.date")
    meta["modified"] = tag("article:modified_time", "og:updated_time", "dateModified")
    meta["author"] = tag("author", "article:author", "parsely-author", "byl")
    meta["type"] = tag("og:type")
    meta["section"] = tag("article:section", "parsely-section")
    meta["headline"] = tag("og:title", "twitter:title")
    best, page_type = None, ""
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        for node in _ld_nodes(data):
            types = _types(node)
            if best is None and any(t in _ARTICLE_TYPES for t in types):
                best = node
            elif not page_type and any(t in NOT_ARTICLE_TYPES for t in types):
                page_type = next(t for t in types if t in NOT_ARTICLE_TYPES)
    if best is not None:
        meta["type"] = str(best.get("@type") if not isinstance(best.get("@type"), list)
                           else best["@type"][0])
        meta["publisher"] = _name(best.get("publisher")) or meta["publisher"]
        meta["published"] = _clean(str(best.get("datePublished") or "")) or meta["published"]
        meta["modified"] = _clean(str(best.get("dateModified") or "")) or meta["modified"]
        meta["author"] = _name(best.get("author")) or meta["author"]
        meta["section"] = _name(best.get("articleSection")) or meta["section"]
        meta["headline"] = _clean(str(best.get("headline") or "")) or meta["headline"]
    elif page_type:
        meta["type"] = page_type
    if re.match(r"https?://", meta.get("author") or ""):
        meta["author"] = ""          # a profile link, not a name
    return {k: v for k, v in meta.items() if v}


def _date(s: str) -> str:
    try:
        d = datetime.fromisoformat((s or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    return "%s %d, %d" % (d.strftime("%b"), d.day, d.year)


def header_line(meta: dict) -> str:
    """"Reuters · Oct 2, 2026 · by Jane Roe · NewsArticle": the page's own
    word on who published it, when and what it is."""
    parts = [meta.get("publisher") or ""]
    when = _date(meta.get("published") or "")
    if when:
        updated = _date(meta.get("modified") or "")
        parts.append(when + (" (updated %s)" % updated if updated and updated != when else ""))
    if meta.get("author"):
        parts.append("by " + meta["author"])
    if meta.get("type"):
        parts.append(meta["type"])
    return " · ".join(p for p in parts if p)


def is_article_meta(meta: dict) -> bool | None:
    """True or False when the page says what it is, None when it does not."""
    kind = (meta or {}).get("type", "").lower().replace(" ", "")
    if not kind:
        return None
    if kind in NOT_ARTICLE_TYPES:
        return False
    if kind in _ARTICLE_TYPES or kind == "article":
        return True
    return None


# ── blocks ──────────────────────────────────────────────────────────────────

def _attrs(el) -> str:
    cls = el.get("class") or []
    return " ".join([*(cls if isinstance(cls, list) else [cls]), str(el.get("id") or ""),
                     str(el.get("role") or ""), str(el.get("aria-label") or "")])


def _furniture(el, root) -> bool:
    """Whether the element, or an ancestor up to the page body, is page
    furniture: nav, aside, footer, a promo rail, a share bar. A header inside
    the story (headline, byline) is the story."""
    node = el
    while node is not None and getattr(node, "name", None) not in (None, "[document]", "body", "html"):
        if node.name in ("nav", "aside", "footer"):
            return True
        if node.name == "header" and not _inside(node, root):
            return True
        if node is not el and node.name == "article" and node is not root:
            # A teaser card for another story.
            return True
        a = _attrs(node)
        if a.strip() and _CLUTTER_RE.search(a) and not _BYLINE_RE.search(a):
            return True
        if node is root:
            # Above the story's container, only real furniture counts.
            root = None
        node = node.parent
    return False


def _inside(node, root) -> bool:
    if root is None:
        return False
    p = node
    while p is not None:
        if p is root:
            return True
        p = p.parent
    return False


def _story_root(soup):
    """The element that holds the story: the <article> with the most
    paragraph text (a page carries teaser cards as <article> too), else
    <main>, else the body."""
    best, size = None, 0
    for art in soup.find_all("article"):
        n = sum(len(p.get_text(" ", strip=True)) for p in art.find_all("p"))
        if n > size:
            best, size = art, n
    if best is not None and size >= FALLBACK_CHARS:
        return best
    return soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup


def _score(el, text: str, kind: str) -> float:
    html_len = max(len(str(el)), 1)
    density = min(1.0, len(text) / html_len * 1.25)
    link_chars = sum(len(a.get_text(" ", strip=True)) for a in el.find_all("a"))
    link_density = min(1.0, link_chars / max(len(text), 1))
    length = min(1.0, math.log1p(len(text)) / math.log1p(400))
    s = (0.3 * density + 0.3 * (1 - link_density) + 0.25 * _TAG_WEIGHT.get(kind, 0.5)
         + 0.15 * length)
    if link_density > 0.6 and len(text) < 160:
        s -= 0.3                     # a link standing alone: a menu entry or a teaser
    return s


def _candidates(root):
    """Block elements, innermost first-class: a <li> or <blockquote> holding
    <p>s yields the paragraphs; a byline <div>/<span> is a block too."""
    def is_block(t):
        if t.name in _BLOCK_TAGS:
            return True
        return t.name in ("div", "span", "time") and bool(_BYLINE_RE.search(_attrs(t))) \
            and not t.find(_BLOCK_TAGS)
    for el in root.find_all(is_block):
        if el.name in _BLOCK_TAGS and el.find(_BLOCK_TAGS):
            continue
        yield el


def read_html(html: str, url: str = "") -> Page:
    """Read one page's HTML. Never raises for odd markup; an empty page is an
    empty Page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    page = Page(url=url)
    page.meta = read_meta(soup)
    page.title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    for t in soup(_DROP_TAGS):
        t.decompose()
    root = _story_root(soup)
    seen: set = set()
    blocks, raw = [], []
    for el in _candidates(root):
        text = _clean(el.get_text(" ", strip=True))
        if not text:
            continue
        kind = el.name if el.name in _BLOCK_TAGS else "byline"
        if el.name == "p" and el.find_parent("blockquote") is not None:
            kind = "blockquote"
        raw.append(text)
        if (text in seen or _BOILERPLATE_RE.match(text) or _furniture(el, root)
                or _score(el, text, kind) < KEEP_SCORE):
            page.dropped += 1
            continue
        seen.add(text)
        blocks.append((el, {"kind": kind, "text": text}))
    if sum(len(b["text"]) for _el, b in blocks) < FALLBACK_CHARS:
        # Thin markup (text in bare <div>s): every line of the story's container.
        lines = [_clean(x) for x in root.get_text("\n", strip=True).split("\n")]
        page.blocks = [{"kind": "text", "text": x} for x in dict.fromkeys(lines) if x]
        page.dropped = 0
        return page
    page.blocks = [b for _el, b in blocks]
    links: dict = {}
    for i, (el, _b) in enumerate(blocks):
        for a in el.find_all("a", href=True):
            href = urljoin(url, a["href"].strip()) if url else a["href"].strip()
            if not re.match(r"https?://", href) or href.split("#")[0] == url.split("#")[0]:
                continue
            if href not in links:
                links[href] = {"n": len(links) + 1, "anchor": _clean(a.get_text(" ", strip=True)),
                               "url": href, "block": i}
    page.links = list(links.values())
    return page


# ── passages by query ───────────────────────────────────────────────────────

_STOP = set("""a an and are as at be been but by for from had has have he her his i if in into is it
its of on or our she so that the their them they this to was we were what when which who will with
you your said says""".split())


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in _STOP]


def _trim(text: str, budget_chars: int) -> str:
    """A block longer than the room left: whole sentences up to it."""
    if len(text) <= budget_chars:
        return text
    out = ""
    for sent in re.split(r"(?<=[.!?”\"])\s+", text):
        if len(out) + len(sent) + 1 > budget_chars:
            break
        out = (out + " " + sent).strip()
    return out or text[:budget_chars]


@dataclass
class Selection:
    text: str
    kept: list
    dropped: int


_GAP = "\n\n[…]\n\n"


def select_blocks(blocks: list, query: str, budget_tokens: int, *, lead: int = 2) -> Selection:
    """The passages `query` needs, inside `budget_tokens`, in page order.

    The lead (the first `lead` paragraphs, and any heading above them) is
    always read, inside a third of the budget: a story's who-what-when lives
    there. Then blocks by BM25
    against the query, each with its neighbours, while the budget lasts.
    Gaps are marked "[…]" so the reader knows text was left out."""
    texts = [b["text"] if isinstance(b, dict) else str(b) for b in blocks]
    budget = max(1, budget_tokens) * CHARS_PER_TOKEN
    if sum(len(t) + 2 for t in texts) <= budget:
        return Selection("\n\n".join(texts), list(range(len(texts))), 0)
    docs = [_terms(t) for t in texts]
    n = len(docs)
    avg = (sum(len(d) for d in docs) / n) if n else 0
    df: dict = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    q = list(dict.fromkeys(_terms(query)))
    scores = []
    for i, d in enumerate(docs):
        s = 0.0
        for w in q:
            tf = d.count(w)
            if tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * len(d) / (avg or 1)))
        scores.append(s)
    chosen: dict = {}
    used = 0

    def take(i, room=budget):
        nonlocal used
        if i in chosen or not (0 <= i < n) or used >= room:
            return
        piece = _trim(texts[i], room - used)
        if len(piece) < min(len(texts[i]), 80):
            return
        chosen[i] = piece
        used += len(piece) + len(_GAP)       # room for a separator and a gap mark
    paras = [i for i, b in enumerate(blocks)
             if not (isinstance(b, dict) and str(b.get("kind", "")).startswith("h"))]
    first = paras[:lead]
    for i in range(0, (first[-1] + 1) if first else 0):
        take(i, budget // 3)
    for i in sorted((i for i in range(n) if scores[i] > 0), key=lambda i: -scores[i]):
        for j in (i, i - 1, i + 1):
            take(j)
        if used >= budget:
            break
    for i in range(n):              # room left: the story in order
        if used >= budget:
            break
        take(i)
    order = sorted(chosen)
    out, prev = [], None
    for i in order:
        if prev is not None and i != prev + 1:
            out.append("[…]")
        out.append(chosen[i])
        prev = i
    return Selection("\n\n".join(out), order, n - len(order))


def select(page: Page, query: str, budget_tokens: int, **kw) -> Selection:
    return select_blocks(page.blocks, query, budget_tokens, **kw)


def select_text(text: str, query: str, budget_tokens: int, **kw) -> str:
    """`select` over plain text whose paragraphs are blank-line separated."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(paras) <= 1:
        paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    return select_blocks(paras, query, budget_tokens, **kw).text


# ── links ───────────────────────────────────────────────────────────────────

_PRIMARY_HOST_RE = re.compile(
    r"(?:^|\.)(?:[a-z0-9-]+\.gov|[a-z0-9-]+\.mil|gov\.[a-z]{2}|congress\.gov|courtlistener\.com|"
    r"supremecourt\.gov|uscourts\.gov|sec\.gov|justia\.com|arxiv\.org|doi\.org|nih\.gov|"
    r"europa\.eu|[a-z0-9-]+\.int|federalregister\.gov|regulations\.gov)$", re.I)
_PRIMARY_ANCHOR_RE = re.compile(
    r"\b(?:filing|filed|complaint|lawsuit|indictment|ruling|opinion|order|judgment|bill|"
    r"statute|regulation|rule|study|paper|report|data|dataset|transcript|statement|"
    r"press release|letter|memo|audit)\b", re.I)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def primary_links(page_links: list, *, limit: int = 3) -> list[dict]:
    """The page's links to primary documents: a government, court, filing or
    research host first, then links whose own words name a document
    ("the filing", "the ruling", "the study"). Never a link to the
    publisher's own site."""
    if not page_links:
        return []
    def rank(link):
        host = _host(link["url"])
        if _PRIMARY_HOST_RE.search(host):
            return 0
        if _PRIMARY_ANCHOR_RE.search(link.get("anchor") or "") and \
                re.search(r"\.pdf(?:$|\?)", link["url"], re.I):
            return 1
        return 9
    ranked = sorted((link for link in page_links if rank(link) < 9), key=lambda x: (rank(x), x["n"]))
    return ranked[:limit]


def link_refs(page_links: list, *, limit: int = 20) -> str:
    """Links as numbered references, for a reader that may name them by
    number: "[1] the filing — courtlistener.com"."""
    return "\n".join("[%d] %s — %s" % (x["n"], x["anchor"] or "link", _host(x["url"]))
                     for x in page_links[:limit])


# ── fetching, and what was learned about a page ─────────────────────────────

_RECENT: dict = {}
_RECENT_MAX = 64
_LOCK = threading.Lock()
META_TTL_S = 14 * 86400
META_MAX = 2000


def fetch(url: str, *, timeout: float = 15) -> Page:
    """Fetch and read one page through the SSRF-guarded fetcher. Raises on a
    refused URL (web_safety.UnsafeURLError), a network error or an HTTP
    error status."""
    from agent_friday.services.web_safety import safe_get
    resp = safe_get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 FridayAgent/1.0"})
    resp.raise_for_status()
    page = read_html(resp.text, getattr(resp, "url", None) or url)
    remember(url, page)
    return page


def recent(url: str) -> Page | None:
    """The page read for `url` in this process, if it still is held."""
    with _LOCK:
        return _RECENT.get(url)


def remember(url: str, page: Page) -> None:
    with _LOCK:
        _RECENT.pop(url, None)
        _RECENT[url] = page
        while len(_RECENT) > _RECENT_MAX:
            _RECENT.pop(next(iter(_RECENT)))
    if page.meta:
        remember_meta([url, page.url], page.meta)


def _meta_path() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "news" / "page_meta.json"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return off_record.skip("web_fetch_cache")
    except Exception:
        return False


def remember_meta(urls, meta: dict) -> None:
    """Keep what a page said about itself (publisher, kind, dates) under every
    URL it was reached by, so the edition filter and outlet naming can use it
    without fetching again. Off the record nothing is kept."""
    if _off_record():
        return
    keep = {k: meta[k] for k in ("publisher", "type", "published", "modified", "author")
            if meta.get(k)}
    if not keep:
        return
    with _LOCK:
        path = _meta_path()
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            store = {}
        now = time.time()
        for u in dict.fromkeys(u for u in urls if u):
            store[u] = dict(keep, at=now)
        store = {u: v for u, v in store.items() if now - float(v.get("at") or 0) < META_TTL_S}
        if len(store) > META_MAX:
            store = dict(sorted(store.items(), key=lambda kv: kv[1].get("at", 0))[-META_MAX:])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(store), encoding="utf-8")
            _STORE_CACHE.update(path=str(path), stamp=path.stat().st_mtime_ns, store=store)
        except OSError:
            pass


_STORE_CACHE: dict = {}


def _meta_store() -> dict:
    """The kept metadata, re-read only when the file changed: an edition asks
    about every item it considers."""
    path = _meta_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _LOCK:
        if _STORE_CACHE.get("path") == str(path) and _STORE_CACHE.get("stamp") == stamp:
            return _STORE_CACHE["store"]
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            store = {}
        _STORE_CACHE.update(path=str(path), stamp=stamp, store=store)
        return store


def known_meta(url: str) -> dict:
    """What a page read earlier said about itself, or {}."""
    if not url:
        return {}
    rec = _meta_store().get(url) or {}
    if time.time() - float(rec.get("at") or 0) >= META_TTL_S:
        return {}
    return {k: v for k, v in rec.items() if k != "at"}
