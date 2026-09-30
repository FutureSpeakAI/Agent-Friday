"""Turning "anything" into numbered podcast sources.

A reference is a small dict naming where the material lives:

    {"kind": "file", "path": "C:/…/notes.pdf"}
    {"kind": "wiki", "path": "projects/atlas.md"}
    {"kind": "kg_node", "id": "person:ada"}
    {"kind": "conversation", "id": "<conversation id>"}
    {"kind": "creation", "filename": "story-20260929T101500.md"}
    {"kind": "dataset", "path": "C:/…/sales.csv"}          (data mode)
    {"kind": "text", "text": "…", "title": "Pasted notes"}
    {"kind": "url", "url": "https://…"}                     (fetched: outward)
    {"kind": "news_run", "routine": "front_page", "run_id": "2026-09-29-morning"}

`resolve(ref)` returns a list of documents (a news run expands to one document
per story), each `{title, kind, text, origin, url, private}`.

`private` is True for everything that is the owner's own material: files,
wiki, the knowledge graph, conversations, creations, datasets, pasted text,
and a Briefing (which carries mail and calendar). Only public articles and
fetched web pages are not private. One private document makes the whole
episode private.
"""

from __future__ import annotations

from pathlib import Path

from agent_friday.user_errors import UserFacingValueError

#: Per-document cap on text handed to the writer, and a per-episode cap.
DOC_CHARS = 8000
EPISODE_CHARS = 60000

DATA_EXTS = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".parquet"}

KINDS = ("file", "wiki", "kg_node", "conversation", "creation", "dataset",
         "text", "url", "news_run")


class SourceError(UserFacingValueError):
    """A reference that could not be read; the message says which and why."""


def _doc(title, kind, text, origin="", url="", private=True, **extra):
    d = {"title": (title or kind).strip()[:200], "kind": kind,
         "text": (text or "").strip(), "origin": str(origin or ""),
         "url": url or "", "private": bool(private)}
    d.update(extra)
    return d


def is_dataset(ref: dict) -> bool:
    if (ref or {}).get("kind") == "dataset":
        return True
    if (ref or {}).get("kind") == "file":
        return Path(str(ref.get("path") or "")).suffix.lower() in DATA_EXTS
    return False


def _file(ref):
    raw = str(ref.get("path") or "").strip()
    if not raw:
        raise SourceError("a file source needs a path")
    p = Path(raw).expanduser().resolve()
    if not p.is_file():
        raise SourceError("file not found: %s" % p.name)
    from agent_friday.services.file_extraction import extract_text
    r = extract_text(p)
    if r.text is None:
        raise SourceError("could not read %s: %s" % (p.name, r.error))
    return [_doc(p.stem, "file", r.text, origin=str(p))]


def _wiki(ref):
    from agent_friday.services import wiki_engine as we
    rel = str(ref.get("path") or "").strip()
    p = we._safe_wiki_path(rel if rel.endswith(".md") else rel + ".md") if rel else None
    if p is None or not Path(p).is_file():
        raise SourceError("wiki page not found: %s" % rel)
    text = we.wiki_read_text(p)
    if text == getattr(we, "VAULT_LOCKED_PLACEHOLDER", object()):
        raise SourceError("that wiki page is in the vault, which is locked")
    return [_doc(Path(p).stem.replace("-", " "), "wiki", text, origin="wiki:" + rel)]


def _kg_node(ref):
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    nid = str(ref.get("id") or "").strip()
    store = KnowledgeGraphStore()
    ents = store.load("entities")
    node = next((e for e in ents if e.get("id") == nid), None)
    if node is None:
        raise SourceError("knowledge-graph entry not found: %s" % nid)
    by_id = {e.get("id"): e for e in ents}
    lines = ["%s (%s)" % (node.get("title") or node.get("name") or nid,
                          node.get("type") or "entity")]
    if node.get("description"):
        lines.append(str(node["description"]))
    for r in store.load("relationships"):
        if nid not in (r.get("source"), r.get("target")):
            continue
        other = r["target"] if r.get("source") == nid else r.get("source")
        o = by_id.get(other) or {}
        lines.append("- %s: %s%s" % (
            r.get("type") or r.get("label") or "related to",
            o.get("title") or o.get("name") or other,
            (" — " + str(r.get("description"))) if r.get("description") else ""))
    return [_doc(node.get("title") or node.get("name") or nid, "kg_node",
                 "\n".join(lines), origin="kg:" + nid)]


def _conversation(ref):
    from agent_friday.services import conversations as conv
    from agent_friday.services.conversation_provenance import is_off_record
    cid = str(ref.get("id") or "").strip()
    meta = conv.load(cid) if cid else None
    if not meta:
        raise SourceError("conversation not found")
    msgs = [m for m in conv.messages(cid) if not is_off_record(m)]
    lines = []
    for m in msgs:
        role = m.get("role") or ""
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(c.get("text") or "") for c in content
                               if isinstance(c, dict))
        content = str(content or "").strip()
        if content and role in ("user", "assistant"):
            lines.append("%s: %s" % ("Owner" if role == "user" else "Friday", content))
    if not lines:
        raise SourceError("that conversation has nothing on the record to use")
    return [_doc(meta.get("title") or "Conversation", "conversation",
                 "\n".join(lines), origin="conversation:" + cid)]


def _creation(ref):
    from agent_friday.core import CREATIONS_DIR
    from agent_friday.paths import contained
    name = str(ref.get("filename") or "").strip()
    try:
        p = contained(CREATIONS_DIR, name)
    except ValueError as e:
        raise SourceError(str(e)) from e
    if not p.is_file():
        raise SourceError("creation not found: %s" % name)
    parts = []
    try:
        from agent_friday.services.creative_engine import creation_metadata
        meta = creation_metadata(p.name) or {}
        for k in ("title", "prompt", "description", "summary"):
            if meta.get(k):
                parts.append("%s: %s" % (k, meta[k]))
    except Exception:
        pass
    if p.suffix.lower() in DATA_EXTS:
        raise SourceError("%s is a dataset; use data mode" % p.name)
    from agent_friday.services.file_extraction import extract_text
    r = extract_text(p)
    if r.text:
        parts.append(r.text)
    if not parts:
        raise SourceError("could not read anything from %s" % p.name)
    return [_doc(p.stem, "creation", "\n\n".join(parts), origin="creation:" + p.name)]


def _text(ref):
    text = str(ref.get("text") or "").strip()
    if not text:
        raise SourceError("the text source is empty")
    return [_doc(ref.get("title") or "Notes", "text", text, origin="pasted")]


def _url(ref):
    url = str(ref.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise SourceError("a web source needs an http(s) address")
    from agent_friday.services.news_engine import _extract_article_text
    try:
        title, text = _extract_article_text(url)
    except Exception as e:
        raise SourceError("could not fetch %s (%s)" % (url, str(e)[:100])) from e
    return [_doc(title or url, "url", text, origin=url, url=url, private=False)]


def _news_run(ref):
    from agent_friday.services import podcast_news
    return podcast_news.run_documents(str(ref.get("routine") or ""),
                                      str(ref.get("run_id") or ""))


_READERS = {"file": _file, "wiki": _wiki, "kg_node": _kg_node,
            "conversation": _conversation, "creation": _creation,
            "text": _text, "url": _url, "news_run": _news_run}


def resolve(ref: dict) -> list[dict]:
    kind = (ref or {}).get("kind")
    if kind == "dataset":
        raise SourceError("datasets are read by data mode")
    fn = _READERS.get(kind)
    if fn is None:
        raise SourceError("unknown source kind %r (expected one of %s)"
                          % (kind, ", ".join(KINDS)))
    return fn(ref)


def number(docs: list[dict]) -> list[dict]:
    """Give each document its citation id S1…Sn and trim it to the budget."""
    out, used = [], 0
    for i, d in enumerate(docs, 1):
        d = dict(d, sid="S%d" % i)
        room = max(0, min(DOC_CHARS, EPISODE_CHARS - used))
        if len(d["text"]) > room:
            d["text"] = d["text"][:room]
            d["trimmed"] = True
        used += len(d["text"])
        out.append(d)
    return out


def find_topic(topic: str, limit: int = 5) -> list[dict]:
    """Wiki pages about `topic` ("my notes on the Atlas project"), best first.

    Scores each page by how many of the topic's words appear in its name and
    text. Reads the wiki through `wiki_read_text`, so vault pages are read only
    while the vault is open.
    """
    import re as _re
    from agent_friday.core import WIKI_DIR
    from agent_friday.services import wiki_engine as we
    stop = {"my", "notes", "note", "on", "about", "the", "a", "an", "of", "and",
            "for", "from", "podcast", "episode", "make", "me"}
    words = [w for w in _re.findall(r"[a-z0-9]+", (topic or "").lower())
             if w not in stop and len(w) > 1]
    if not words:
        return []
    root = Path(WIKI_DIR)
    scored = []
    for f in root.rglob("*.md") if root.exists() else []:
        try:
            text = we.wiki_read_text(f)
        except Exception:
            continue
        if text == getattr(we, "VAULT_LOCKED_PLACEHOLDER", None):
            continue
        name, low = f.stem.lower(), text.lower()
        score = sum(3 for w in words if w in name) + sum(1 for w in words if w in low)
        if all(w in name or w in low for w in words):
            score += 5
        if score >= len(words) + 1:
            scored.append((score, str(f.relative_to(root)).replace("\\", "/")))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"kind": "wiki", "path": rel} for _s, rel in scored[:limit]]


def describe(ref: dict) -> str:
    """A one-line label for a reference, for the episode's source list."""
    k = (ref or {}).get("kind")
    for key in ("title", "path", "filename", "id", "url", "run_id"):
        if ref.get(key):
            v = str(ref[key])
            return "%s: %s" % (k, Path(v).name if key == "path" else v[:120])
    return str(k)
