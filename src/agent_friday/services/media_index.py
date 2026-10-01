"""Media: one card per piece of work, over the stores that already exist.

The index is a small sqlite file, ``<friday home>/media/index.sqlite``. The
indexer walks only Friday's own output roots and the content store, never the
owner's home directory, and writes one card per thing it finds:

    creations   ~/Desktop/friday-creations (recursively), with the creative
                sidecar and the provenance manifest
    documents   ~/.friday/documents, with each render as a relation, not a card
    podcasts    ~/.friday/podcasts, user episodes only (routine shows are News)
    drafts      ~/.friday/wiki/content/draft-*.html, the Draft workspace's copies
    legacy      ~/.friday/content/pipeline.json, the Ideas kanban
    posts       ~/.friday/content_pipeline.db, the v2 content store (read through;
                a post card is that post)
    media       cards made here (ideas, drafts, articles), whose body is a
                Markdown file under ~/.friday/media/cards/

Nothing the indexer reads is moved or rewritten. What the owner changes on a
card (status, project, title, when) lives in the ``overrides`` table and
survives every re-index, so a migration never loses a draft or a Content item:
the originals stay where they were and the card points at them.

One status vocabulary. Every source status maps onto five words, with badges
for the rest (docs/design/media-workspace.md §4.3). Moving a card into
Published is never done here: ``publish()`` raises the one approval card and
``complete_publish()`` runs only from the approval's decision hook.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import agent_friday.core as core

STATUSES = ("idea", "draft", "review", "scheduled", "published")
STATUS_WORD = {"idea": "Idea", "draft": "Draft", "review": "In review", "scheduled": "Scheduled", "published": "Published"}

#: The legacy Ideas kanban's stages, onto the five.
LEGACY_STAGE = {"idea": "idea", "drafting": "draft", "review": "review", "scheduled": "scheduled", "published": "published"}
#: The v2 content store's post statuses, onto the five plus a badge.
V2_STATUS: Dict[str, Tuple[str, Optional[str]]] = {
    "DRAFT": ("draft", None), "SCHEDULED": ("scheduled", None), "PUBLISHING": ("scheduled", "publishing"),
    "PUBLISHED": ("published", None), "PARTIAL": ("published", "partial"), "HELD": ("review", "held"),
    "FAILED": ("published", "failed"), "CANCELLED": ("draft", "cancelled"),
}
#: A podcast episode's status, onto the five plus a badge.
EPISODE_STATUS: Dict[str, Tuple[str, Optional[str]]] = {
    "ready": ("published", None), "failed": ("draft", "failed"), "cancelled": ("draft", "cancelled"),
}

KIND_BY_SUFFIX = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image", ".svg": "image",
    ".mp4": "video", ".webm": "video", ".mov": "video",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio",
    ".md": "doc", ".txt": "doc", ".pdf": "doc", ".html": "page", ".htm": "page",
    ".pptx": "deck", ".docx": "doc", ".xlsx": "sheet", ".csv": "chart",
    ".glb": "model3d", ".gltf": "model3d", ".obj": "model3d",
}
KIND_WORD = {
    "draft": "Draft", "article": "Article", "episode": "Episode", "image": "Image", "imageset": "Image set",
    "video": "Video", "page": "Page", "chart": "Chart", "doc": "Document", "deck": "Deck", "sheet": "Sheet",
    "post": "Post", "code": "Codebase", "music": "Music", "audio": "Audio", "model3d": "3D", "file": "File",
}
TEXT_KINDS = ("draft", "article", "doc")
_LOCK = threading.RLock()


# ── paths ───────────────────────────────────────────────────────────────────

def media_dir() -> Path:
    """``<friday home>/media``, read lazily so a redirected home moves it."""
    return Path(core.FRIDAY_DIR) / "media"


def db_path() -> Path:
    return media_dir() / "index.sqlite"


def cards_dir() -> Path:
    return media_dir() / "cards"


def _connect() -> sqlite3.Connection:
    media_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path()), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY, kind TEXT, title TEXT, path TEXT, source_kind TEXT, source_ref TEXT,
            origin TEXT, created REAL, modified REAL, when_ts REAL, status TEXT, badges TEXT, held INTEGER,
            maker TEXT, on_this_pc INTEGER, sources TEXT, signed INTEGER, hash TEXT, privacy TEXT,
            published_at TEXT, targets TEXT, project TEXT, text TEXT, extra TEXT, updated REAL, present INTEGER
        );
        CREATE TABLE IF NOT EXISTS overrides (
            id TEXT PRIMARY KEY, status TEXT, project TEXT, title TEXT, when_ts REAL, body_path TEXT,
            privacy TEXT, published_at TEXT, updated REAL
        );
        CREATE TABLE IF NOT EXISTS relations (
            from_id TEXT, to_id TEXT, how TEXT, PRIMARY KEY (from_id, to_id, how)
        );
        CREATE INDEX IF NOT EXISTS cards_status ON cards(status);
        CREATE INDEX IF NOT EXISTS cards_when ON cards(when_ts);
        """
    )
    return con


# ── helpers ─────────────────────────────────────────────────────────────────

def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _dj(s: Any, default: Any) -> Any:
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def _id_for(source_kind: str, ref: str) -> str:
    return source_kind + ":" + hashlib.sha1(f"{source_kind}|{ref}".encode("utf-8")).hexdigest()[:12]


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(ts)))


def _parse_when(s: Any) -> Optional[float]:
    """'YYYY-MM-DD HH:MM', ISO, or epoch -> epoch seconds (local)."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(s[:19] if fmt.endswith("%S") else s[:16] if fmt.endswith("%M") else s[:10], fmt))
        except Exception:
            continue
    return None


def _title_from_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"^friday-(image|video|music|text|deck|site|code-art)-?", "", stem)
    stem = re.sub(r"[-_]+", " ", stem).strip()
    return stem[:1].upper() + stem[1:] if stem else name


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s).strip()


# ── one card ─────────────────────────────────────────────────────────────────

def _card(**f: Any) -> Dict[str, Any]:
    c = {
        "id": "", "kind": "file", "title": "", "path": "", "source_kind": "", "source_ref": "", "origin": "",
        "created": None, "modified": None, "when_ts": None, "status": "draft", "badges": [], "held": False,
        "maker": "", "on_this_pc": True, "sources": [], "signed": False, "hash": "", "privacy": "private",
        "published_at": None, "targets": [], "project": None, "text": "", "extra": {},
    }
    c.update(f)
    if c["held"]:
        c["badges"] = [b for b in c["badges"] if b != "held"] + ["held"]
    return c


def _upsert(con: sqlite3.Connection, c: Dict[str, Any]) -> None:
    con.execute(
        """INSERT INTO cards (id, kind, title, path, source_kind, source_ref, origin, created, modified, when_ts,
             status, badges, held, maker, on_this_pc, sources, signed, hash, privacy, published_at, targets, project,
             text, extra, updated, present)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
           ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title=excluded.title, path=excluded.path,
             source_kind=excluded.source_kind, source_ref=excluded.source_ref, origin=excluded.origin,
             created=excluded.created, modified=excluded.modified, when_ts=excluded.when_ts, status=excluded.status,
             badges=excluded.badges, held=excluded.held, maker=excluded.maker, on_this_pc=excluded.on_this_pc,
             sources=excluded.sources, signed=excluded.signed, hash=excluded.hash, privacy=excluded.privacy,
             published_at=excluded.published_at, targets=excluded.targets, project=excluded.project,
             text=excluded.text, extra=excluded.extra, updated=excluded.updated, present=1""",
        (c["id"], c["kind"], c["title"], c["path"], c["source_kind"], c["source_ref"], c["origin"], c["created"],
         c["modified"], c["when_ts"], c["status"], _j(c["badges"]), 1 if c["held"] else 0, c["maker"],
         1 if c["on_this_pc"] else 0, _j(c["sources"]), 1 if c["signed"] else 0, c["hash"], c["privacy"],
         c["published_at"], _j(c["targets"]), c["project"], c["text"] or "", _j(c["extra"]), time.time()),
    )


def _relate(con: sqlite3.Connection, a: str, b: str, how: str) -> None:
    con.execute("INSERT OR IGNORE INTO relations (from_id, to_id, how) VALUES (?,?,?)", (a, b, how))


# ── sources ──────────────────────────────────────────────────────────────────

def _provenance(path: Path) -> Tuple[bool, str, Optional[str]]:
    """(signed, content hash, where it was published if the manifest says so)."""
    try:
        from agent_friday.services import provenance
        m = provenance.manifest_for_file(str(path))
    except Exception:
        m = None
    if not m:
        return False, "", None
    sig = (m.get("signature") or {}).get("value") or ""
    signed = bool(sig) and sig != "ed25519_unavailable"
    h = ((m.get("artifact") or {}).get("content_hash")) or ""
    pubs = m.get("publications") or []
    where = None
    if pubs:
        last = pubs[-1] if isinstance(pubs, list) else pubs
        if isinstance(last, dict):
            where = str(last.get("where") or last.get("platform") or last.get("url") or "").strip() or None
    return signed, h, where


def _sidecar(name: str) -> Dict[str, Any]:
    try:
        from agent_friday.services.creative_engine import creation_metadata
        return creation_metadata(name) or {}
    except Exception:
        return {}


def _scan_creations(con: sqlite3.Connection) -> int:
    root = Path(core.CREATIONS_DIR)
    if not root.exists():
        return 0
    n = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith(".") or p.suffix.lower() in (".json", ".jsonl", ".tmp", ".part"):
            continue
        suffix = p.suffix.lower()
        kind = KIND_BY_SUFFIX.get(suffix, "file")
        name = p.name
        if kind == "audio" and name.startswith("friday-music"):
            kind = "music"
        if kind == "doc" and name.startswith("friday-text"):
            kind = "article"
        if name.startswith("friday-code-art"):
            kind = "image"
        meta = _sidecar(name)
        signed, h, where = _provenance(p)
        st = p.stat()
        sources: List[str] = []
        if meta.get("prompt"):
            sources.append("prompt: " + str(meta["prompt"])[:120])
        if meta.get("seed_image"):
            sources.append(str(meta["seed_image"]))
        maker = str(meta.get("model") or meta.get("api_model") or "").strip()
        rel = p.relative_to(root).as_posix()
        c = _card(
            id=_id_for("creation", rel), kind=kind, title=_title_from_name(name), path=str(p),
            source_kind="creation", source_ref=rel, origin="daily" if "daily" in name.lower() else "create",
            created=st.st_ctime, modified=st.st_mtime, when_ts=st.st_mtime,
            status="published", badges=[], maker=(maker + " · this PC") if maker else "Friday · this PC",
            sources=sources, signed=signed, hash=h,
            privacy="published" if where else "private", published_at=where or "Kept on this PC",
            text=(p.read_text(encoding="utf-8", errors="ignore")[:20000] if suffix in (".md", ".txt") else ""),
            extra={"suffix": suffix, "bytes": st.st_size, "meta": {k: v for k, v in meta.items() if k in ("kind", "model", "aspect_ratio", "duration_seconds")}},
        )
        _upsert(con, c)
        n += 1
    return n


def _scan_documents(con: sqlite3.Connection) -> int:
    try:
        from agent_friday.services import office_engine
        root = Path(office_engine.DOCUMENTS_DIR)
        render_dir = root / getattr(office_engine, "RENDER_DIR", "_renders")
    except Exception:
        return 0
    if not root.exists():
        return 0
    n = 0
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".pptx", ".docx", ".xlsx", ".pdf"):
            continue
        st = p.stat()
        renders = sorted(str(r) for r in render_dir.glob(p.stem + "*.png")) if render_dir.exists() else []
        made_by = {}
        rec = root / ".made-by-friday.json"
        try:
            made_by = (json.loads(rec.read_text(encoding="utf-8")) or {}).get(p.name) or {}
        except Exception:
            made_by = {}
        signed, h, where = _provenance(p)
        c = _card(
            id=_id_for("document", p.name), kind=KIND_BY_SUFFIX.get(p.suffix.lower(), "doc"), title=_title_from_name(p.name),
            path=str(p), source_kind="document", source_ref=p.name, origin="office",
            created=st.st_ctime, modified=st.st_mtime, when_ts=st.st_mtime, status="published",
            maker="Office engine · this PC", sources=[str(made_by.get("from") or made_by.get("prompt") or "")[:120]] if made_by else [],
            signed=signed or bool(made_by), hash=h, privacy="published" if where else "private",
            published_at=where or "Kept on this PC",
            extra={"renders": renders, "pages": len(renders) or None},
        )
        _upsert(con, c)
        n += 1
    return n


def _scan_podcasts(con: sqlite3.Connection) -> int:
    try:
        from agent_friday.services import podcast_engine as pe
        eps = pe.list_episodes(limit=1000)
    except Exception:
        return 0
    n = 0
    for ep in eps:
        if (ep.get("origin") or "user") != "user":
            continue  # the routine shows belong to News, on their runs
        status, badge = EPISODE_STATUS.get(ep.get("status") or "", ("draft", "working"))
        srcs = [str(s.get("title") or s.get("kind") or "") for s in (ep.get("sources") or [])] or []
        if not srcs and ep.get("source_count"):
            srcs = [f"{ep['source_count']} source(s)"]
        created = float(ep.get("created_at") or 0) or None
        try:
            full = pe.load(ep["id"]) or {}
        except Exception:
            full = {}
        if not srcs:
            srcs = [str(s.get("title") or s.get("kind") or "") for s in (full.get("sources") or [])]
        prov = full.get("provenance") or {}
        text = " ".join(str(l.get("text") or "") for l in (full.get("lines") or [])[:80])[:20000]
        c = _card(
            id=_id_for("episode", ep["id"]), kind="episode", title=ep.get("title") or ep.get("show") or "Episode",
            path=str(pe.root() / ep["id"]), source_kind="episode", source_ref=ep["id"], origin="create",
            created=created, modified=float(ep.get("updated_at") or created or 0) or None, when_ts=created,
            status=status, badges=[badge] if badge else [], maker=("Local model · " + str(ep.get("voice_engine") or "local") + " · this PC"),
            sources=[s for s in srcs if s], signed=bool(prov.get("signed")), hash=str(prov.get("content_hash") or ""),
            privacy="published" if ep.get("privacy") == "public" else "private",
            published_at="Kept on this PC" if status == "published" else None, text=text,
            extra={"duration_s": ep.get("duration_s"), "show": ep.get("show"), "stage": ep.get("stage_detail"), "chapters": ep.get("chapters") or []},
        )
        _upsert(con, c)
        n += 1
    return n


_DRAFT_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_DRAFT_MODE = re.compile(r'<div class="mode-tag">(.*?)</div>', re.S | re.I)
_DRAFT_PROMPT = re.compile(r'<div class="prompt-ctx">Prompt:\s*(.*?)</div>', re.S | re.I)
_DRAFT_BODY = re.compile(r'<div class="draft-body">(.*?)</div>\s*(?:</div>|</body>)', re.S | re.I)


def parse_draft_html(raw: str) -> Dict[str, str]:
    """The Draft workspace's HTML copy: mode, prompt and the text, nothing else."""
    body_m = _DRAFT_BODY.search(raw)
    body = _strip_html(body_m.group(1)) if body_m else _strip_html(raw)
    mode = _html.unescape(_DRAFT_MODE.search(raw).group(1)).strip() if _DRAFT_MODE.search(raw) else ""
    prompt = _html.unescape(_DRAFT_PROMPT.search(raw).group(1)).strip() if _DRAFT_PROMPT.search(raw) else ""
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return {"mode": mode, "prompt": prompt, "body": body, "first_line": first}


def _scan_drafts(con: sqlite3.Connection) -> int:
    root = Path(core.FRIDAY_DIR) / "wiki" / "content"
    if not root.exists():
        return 0
    n = 0
    for p in sorted(root.glob("draft-*.html")):
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        d = parse_draft_html(raw)
        st = p.stat()
        title = (d["prompt"][:80] if d["prompt"] else d["first_line"][:80]) or _title_from_name(p.name)
        c = _card(
            id=_id_for("draft_html", p.name), kind="draft", title=title, path=str(p), source_kind="draft_html",
            source_ref=p.name, origin="draft", created=st.st_mtime, modified=st.st_mtime, when_ts=st.st_mtime,
            status="draft", maker="Local model · this PC", sources=[("prompt: " + d["prompt"][:120])] if d["prompt"] else [],
            signed=False, privacy="private", text=d["body"][:20000], extra={"channel": d["mode"]},
        )
        _upsert(con, c)
        n += 1
    return n


def _scan_legacy(con: sqlite3.Connection) -> int:
    try:
        from agent_friday.services.misc_engine import _load_content_pipeline
        pipe = _load_content_pipeline() or {}
    except Exception:
        return 0
    n = 0
    for it in pipe.get("items") or []:
        iid = str(it.get("id") or "")
        if not iid:
            continue
        kind = "post" if str(it.get("type") or "").lower() in ("post", "social", "thread") else "draft"
        status = LEGACY_STAGE.get(str(it.get("stage") or "idea"), "idea")
        created = _parse_when(it.get("created")) or None
        updated = _parse_when(it.get("updated")) or created
        when = _parse_when(it.get("scheduled_for")) if it.get("scheduled_for") else updated
        c = _card(
            id=_id_for("legacy_item", iid), kind=kind, title=str(it.get("title") or "Untitled")[:160], path="",
            source_kind="legacy_item", source_ref=iid, origin="ideas", created=created, modified=updated, when_ts=when,
            status=status, maker="You", sources=[s for s in [str(it.get("template") or "")] if s],
            signed=False, privacy="published" if status == "published" else "private",
            published_at=(str(it.get("channel") or "") or None) if status == "published" else None,
            targets=[str(it.get("channel"))] if it.get("channel") else [], project=None,
            text=str(it.get("draft") or it.get("notes") or "")[:20000],
            extra={"channel": it.get("channel"), "tags": it.get("tags") or [], "post_id": it.get("post_id")},
        )
        _upsert(con, c)
        if it.get("post_id"):
            _relate(con, c["id"], _id_for("post", str(it["post_id"])), "turned_into")
        n += 1
    return n


def _scan_posts(con: sqlite3.Connection) -> int:
    try:
        from agent_friday.services import content_pipeline as cp
        res = cp.list_posts(limit=2000)
    except Exception:
        return 0
    if not res or not res.get("ok"):
        return 0
    n = 0
    for p in res.get("posts") or []:
        status, badge = V2_STATUS.get(str(p.get("status") or "DRAFT").upper(), ("draft", None))
        targets = p.get("targets") or []
        plats = sorted({str(t.get("platform") or "") for t in targets if t.get("platform")})
        urls = [str(t.get("post_url")) for t in targets if t.get("post_url")]
        sched = p.get("schedule") or {}
        when = _parse_when(p.get("published_at")) if status == "published" else (_parse_when(sched.get("publish_at")) or _parse_when(p.get("updated_at")))
        src = p.get("source") or {}
        sources = []
        if src.get("kind"):
            sources.append(str(src.get("kind")) + (": " + str(src.get("ref")) if src.get("ref") else ""))
        sources += [str(a.get("filename")) for a in (p.get("assets") or []) if a.get("filename")]
        c = _card(
            id=_id_for("post", str(p["id"])), kind="post", title=str(p.get("title") or (p.get("body") or "")[:60] or "Post"),
            path="", source_kind="post", source_ref=str(p["id"]), origin="compose",
            created=_parse_when(p.get("created_at")), modified=_parse_when(p.get("updated_at")), when_ts=when,
            status=status, badges=[badge] if badge else [], held=(badge == "held"), maker="Composer · this PC",
            sources=sources, signed=bool(p.get("provenance_hash")), hash=str(p.get("provenance_hash") or ""),
            privacy="published" if status == "published" and not badge == "failed" else "private",
            published_at=(", ".join(plats) + (" · " + urls[0] if urls else "")) if status == "published" and plats else None,
            targets=plats, project=None, text=str(p.get("body") or "")[:20000],
            extra={"post": {"id": p["id"], "status": p.get("status"), "schedule": sched}, "tags": p.get("tags") or []},
        )
        _upsert(con, c)
        for a in p.get("assets") or []:
            if a.get("filename"):
                _relate(con, c["id"], _id_for("creation", str(a["filename"])), "made_from")
        if src.get("kind") in ("creation",) and src.get("ref"):
            _relate(con, c["id"], _id_for("creation", str(src["ref"])), "made_from")
        if src.get("kind") == "card" and src.get("ref"):
            _relate(con, c["id"], str(src["ref"]), "made_from")
        n += 1
    return n


def _scan_media_cards(con: sqlite3.Connection) -> int:
    root = cards_dir()
    if not root.exists():
        return 0
    n = 0
    for p in sorted(root.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        body_p = root / (p.stem + ".md")
        text = body_p.read_text(encoding="utf-8", errors="ignore") if body_p.exists() else ""
        st = p.stat()
        file_p = Path(rec["file"]) if rec.get("file") else None     # a Media-owned file (read-aloud audio)
        path = str(file_p) if file_p and file_p.exists() else (str(body_p) if body_p.exists() else "")
        signed, h, _where = _provenance(Path(path)) if path else (False, "", None)
        status = rec.get("status") or "idea"
        c = _card(
            id=rec["id"], kind=rec.get("kind") or "draft", title=rec.get("title") or "Untitled",
            path=path, source_kind="media", source_ref=p.stem,
            origin=rec.get("origin") or "media", created=rec.get("created") or st.st_ctime,
            modified=Path(path).stat().st_mtime if path else st.st_mtime, when_ts=rec.get("when_ts") or st.st_mtime,
            status=status, badges=list((rec.get("extra") or {}).get("badges") or []),
            maker=rec.get("maker") or "You", sources=rec.get("sources") or [],
            signed=signed, hash=h, privacy="private",
            published_at="Kept on this PC" if (file_p and status == "published") else None,
            project=rec.get("project"), text=text[:20000], extra=rec.get("extra") or {},
        )
        _upsert(con, c)
        for r in rec.get("relations") or []:
            _relate(con, c["id"], r["to"], r["how"])
        n += 1
    return n


def reindex() -> Dict[str, int]:
    """Walk every root and refresh the cards. Overrides are reapplied; nothing is lost."""
    with _LOCK:
        con = _connect()
        try:
            con.execute("UPDATE cards SET present=0")
            counts = {
                "creations": _scan_creations(con), "documents": _scan_documents(con), "podcasts": _scan_podcasts(con),
                "drafts": _scan_drafts(con), "legacy": _scan_legacy(con), "posts": _scan_posts(con), "media": _scan_media_cards(con),
            }
            con.execute("DELETE FROM cards WHERE present=0")
            con.commit()
        finally:
            con.close()
        return counts


# ── reading ──────────────────────────────────────────────────────────────────

def _row_to_card(r: sqlite3.Row, ov: Optional[sqlite3.Row]) -> Dict[str, Any]:
    c = {
        "id": r["id"], "kind": r["kind"], "title": r["title"], "path": r["path"], "source_kind": r["source_kind"],
        "source_ref": r["source_ref"], "origin": r["origin"], "created": _iso(r["created"]), "modified": _iso(r["modified"]),
        "when": _iso(r["when_ts"]), "when_ts": r["when_ts"], "status": r["status"], "badges": _dj(r["badges"], []),
        "held": bool(r["held"]), "maker": r["maker"], "on_this_pc": bool(r["on_this_pc"]), "sources": _dj(r["sources"], []),
        "signed": bool(r["signed"]), "hash": r["hash"], "privacy": r["privacy"], "published_at": r["published_at"],
        "targets": _dj(r["targets"], []), "project": r["project"], "extra": _dj(r["extra"], {}),
    }
    if ov is not None:
        for k in ("status", "project", "title", "privacy", "published_at"):
            if ov[k] not in (None, ""):
                c[k] = ov[k]
        if ov["when_ts"]:
            c["when"] = _iso(ov["when_ts"]); c["when_ts"] = ov["when_ts"]
        if ov["body_path"]:
            c["body_path"] = ov["body_path"]
    ex = c["extra"] or {}
    if ex.get("duration_s"):
        s = int(ex["duration_s"]); c["duration"] = f"{s // 60}:{s % 60:02d}"
    if ex.get("pages"):
        c["pages"] = ex["pages"]
    if r["text"] and c["kind"] in TEXT_KINDS:
        c["words"] = len(r["text"].split())
    if ex.get("renders"):
        c["renders"] = ["/api/media/" + c["id"] + "/render/" + str(i) for i in range(len(ex["renders"]))]
    if c["path"] and c["source_kind"] in ("creation", "document", "media"):
        c["file_url"] = "/api/media/" + c["id"] + "/file"
        if c["kind"] in ("image", "imageset", "chart") and c["source_kind"] == "creation":
            c["thumb"] = c["file_url"]
    c["editable_text"] = c["kind"] in TEXT_KINDS or (c["source_kind"] in ("draft_html", "legacy_item"))
    return c


def _today_bounds() -> Tuple[float, float]:
    lt = time.localtime()
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return start, start + 86400


def _view_sql(view: str) -> Tuple[str, List[Any]]:
    s, e = _today_bounds()
    if view == "today":
        return "(c.status='review' OR c.held=1 OR (c.when_ts>=? AND c.when_ts<?) OR (c.modified>=? AND c.modified<?))", [s, e, s, e]
    if view == "progress":
        return "(c.status IN ('draft','review'))", []
    if view == "review":
        return "(c.status='review' OR c.held=1)", []
    if view == "published":
        return "(c.status='published')", []
    return "1=1", []


def query(view: str = "all", q: str = "", kind: Optional[str] = None, project: Optional[str] = None,
          privacy: Optional[str] = None, unsigned: bool = False, status: Optional[str] = None,
          sort: str = "next", limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute("SELECT c.*, o.status AS o_status, o.project AS o_project, o.title AS o_title, o.when_ts AS o_when, o.body_path AS o_body, o.privacy AS o_privacy, o.published_at AS o_pub FROM cards c LEFT JOIN overrides o ON o.id=c.id").fetchall()
        finally:
            con.close()
    cards = []
    for r in rows:
        ov = {"status": r["o_status"], "project": r["o_project"], "title": r["o_title"], "when_ts": r["o_when"], "body_path": r["o_body"], "privacy": r["o_privacy"], "published_at": r["o_pub"]}
        c = _row_to_card(r, _Ov(ov))
        c["_text"] = (r["text"] or "")
        cards.append(c)
    s, e = _today_bounds()

    def in_view(c: Dict[str, Any]) -> bool:
        if view == "today":
            return c["status"] == "review" or c["held"] or (c["when_ts"] and s <= c["when_ts"] < e) or (r_mod(c) and s <= r_mod(c) < e)
        if view == "progress":
            return c["status"] in ("draft", "review")
        if view == "review":
            return c["status"] == "review" or c["held"]
        if view == "published":
            return c["status"] == "published"
        return True

    def r_mod(c: Dict[str, Any]) -> Optional[float]:
        m = c.get("modified")
        return _parse_when(m) if m else None

    # counts over everything, for the rail
    counts: Dict[str, Any] = {"all": len(cards), "kinds": {}, "private": 0, "shared": 0, "unsigned": 0, "no_project": 0}
    for v in ("today", "progress", "review", "published"):
        counts[v] = 0
    projects: Dict[str, int] = {}
    for c in cards:
        for v in ("today", "progress", "review", "published"):
            saved = view
            view_local = v
            if (view_local == "today" and (c["status"] == "review" or c["held"] or (c["when_ts"] and s <= c["when_ts"] < e) or (r_mod(c) and s <= r_mod(c) < e))) \
               or (view_local == "progress" and c["status"] in ("draft", "review")) \
               or (view_local == "review" and (c["status"] == "review" or c["held"])) \
               or (view_local == "published" and c["status"] == "published"):
                counts[v] += 1
        kg = _kind_group(c["kind"])
        counts["kinds"][kg] = counts["kinds"].get(kg, 0) + 1
        if c["privacy"] == "private":
            counts["private"] += 1
        else:
            counts["shared"] += 1
        if not c["signed"]:
            counts["unsigned"] += 1
        if c["project"]:
            projects[c["project"]] = projects.get(c["project"], 0) + 1
        else:
            counts["no_project"] += 1

    out = [c for c in cards if in_view(c)]
    if kind:
        out = [c for c in out if _kind_group(c["kind"]) == kind or c["kind"] == kind]
    if project is not None:
        out = [c for c in out if (c["project"] or "") == (project or "")]
    if privacy == "private":
        out = [c for c in out if c["privacy"] == "private"]
    elif privacy in ("shared", "published"):
        out = [c for c in out if c["privacy"] != "private"]
    if unsigned:
        out = [c for c in out if not c["signed"]]
    if status:
        out = [c for c in out if c["status"] == status]
    if q:
        ql = q.lower().strip()
        out = [c for c in out if ql in (c["title"] + " " + c["maker"] + " " + " ".join(c["sources"]) + " " + (c["project"] or "") + " " + c["_text"]).lower()]
    order = {"review": 0, "draft": 1, "idea": 2, "scheduled": 3, "published": 4}
    if sort == "newest":
        out.sort(key=lambda c: -(c["when_ts"] or 0))
    elif sort == "title":
        out.sort(key=lambda c: c["title"].lower())
    elif sort == "status":
        out.sort(key=lambda c: (order.get(c["status"], 9), -(c["when_ts"] or 0)))
    else:  # next: what needs the owner first, then what is soonest
        out.sort(key=lambda c: (0 if c["held"] else order.get(c["status"], 9), -(c["when_ts"] or 0)))
    total = len(out)
    out = out[offset: offset + limit] if limit else []
    for c in out:
        c.pop("_text", None)
    return {"cards": out, "counts": counts, "projects": [{"name": k, "n": v} for k, v in sorted(projects.items())], "total": total}


class _Ov:
    def __init__(self, d: Dict[str, Any]) -> None:
        self._d = d

    def __getitem__(self, k: str) -> Any:
        return self._d.get(k)


def _kind_group(kind: str) -> str:
    if kind in ("image", "imageset"):
        return "imageset"
    if kind in ("audio", "music"):
        return "audio"
    if kind in ("deck", "doc", "sheet"):
        return "deck"
    if kind == "timeline":
        return "video"
    return kind


def get(card_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        con = _connect()
        try:
            r = con.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            if r is None:
                return None
            ov = con.execute("SELECT * FROM overrides WHERE id=?", (card_id,)).fetchone()
            c = _row_to_card(r, ov)
            rels = []
            for rr in con.execute("SELECT from_id, to_id, how FROM relations WHERE from_id=? OR to_id=?", (card_id, card_id)).fetchall():
                other = rr["to_id"] if rr["from_id"] == card_id else rr["from_id"]
                how = rr["how"] if rr["from_id"] == card_id else {"made_from": "turned_into", "turned_into": "made_from", "rendered_from": "render_of"}.get(rr["how"], rr["how"])
                t = con.execute("SELECT title FROM cards WHERE id=?", (other,)).fetchone()
                rels.append({"id": other, "how": how, "title": t["title"] if t else other})
            c["relations"] = rels
            c["body"] = _read_body(c, r["text"] or "")
            return c
        finally:
            con.close()


def _read_body(c: Dict[str, Any], text: str) -> Optional[str]:
    bp = c.get("body_path")
    if bp and Path(bp).exists():
        return Path(bp).read_text(encoding="utf-8", errors="ignore")
    if c["source_kind"] == "media" and c.get("path"):
        if c["kind"] not in TEXT_KINDS:
            return None            # a spoken file or an image Media owns is not text
        return Path(c["path"]).read_text(encoding="utf-8", errors="ignore") if Path(c["path"]).exists() else ""
    if c["kind"] in TEXT_KINDS or c["source_kind"] in ("draft_html", "legacy_item"):
        return text
    if c["source_kind"] == "post":
        return text
    return None


def calendar(frm: str, to: str) -> List[Dict[str, Any]]:
    a = _parse_when(frm) or 0
    b = (_parse_when(to) or time.time()) + 86400
    res = query(view="all", limit=0)
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute("SELECT c.*, o.status AS o_status, o.project AS o_project, o.title AS o_title, o.when_ts AS o_when, o.body_path AS o_body, o.privacy AS o_privacy, o.published_at AS o_pub FROM cards c LEFT JOIN overrides o ON o.id=c.id").fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        c = _row_to_card(r, _Ov({"status": r["o_status"], "project": r["o_project"], "title": r["o_title"], "when_ts": r["o_when"], "body_path": r["o_body"], "privacy": r["o_privacy"], "published_at": r["o_pub"]}))
        if c["status"] not in ("published", "scheduled", "review"):
            continue
        if c["status"] == "published" and c["source_kind"] in ("creation", "document", "episode") and not (c["published_at"] and c["published_at"] != "Kept on this PC"):
            continue  # finished-and-kept things are the Library's, not the calendar's
        if c["when_ts"] and a <= c["when_ts"] < b:
            out.append(c)
    out.sort(key=lambda c: c["when_ts"])
    return out


# ── writing ──────────────────────────────────────────────────────────────────

def _set_override(card_id: str, **fields: Any) -> None:
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute("SELECT * FROM overrides WHERE id=?", (card_id,)).fetchone()
            rec = dict(cur) if cur else {"id": card_id, "status": None, "project": None, "title": None, "when_ts": None, "body_path": None, "privacy": None, "published_at": None}
            rec.update(fields)
            rec["updated"] = time.time()
            con.execute("INSERT OR REPLACE INTO overrides (id, status, project, title, when_ts, body_path, privacy, published_at, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                        (card_id, rec["status"], rec["project"], rec["title"], rec["when_ts"], rec["body_path"], rec["privacy"], rec["published_at"], rec["updated"]))
            con.commit()
        finally:
            con.close()


def create_card(kind: str = "draft", title: str = "", body: str = "", project: Optional[str] = None,
                sources: Optional[List[str]] = None, maker: str = "You", status: str = "idea",
                relations: Optional[List[Dict[str, str]]] = None, origin: str = "media") -> Dict[str, Any]:
    """A card Media owns: a JSON record and a Markdown body under ~/.friday/media/cards."""
    if kind not in KIND_WORD:
        kind = "draft"
    cid = "media:" + uuid.uuid4().hex[:12]
    root = cards_dir()
    root.mkdir(parents=True, exist_ok=True)
    rec = {"id": cid, "kind": kind, "title": (title or "Untitled")[:200], "project": project or None, "sources": sources or [],
           "maker": maker, "status": status if status in STATUSES and status != "published" else "idea", "created": time.time(),
           "origin": origin, "relations": relations or [], "extra": {}}
    (root / (cid.split(":")[1] + ".json")).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    body_p = root / (cid.split(":")[1] + ".md")
    body_p.write_text(body or "", encoding="utf-8")
    if (body or "").strip():
        _sign(body_p, kind, [{"kind": "card", "ref": r["to"], "title": title} for r in (relations or []) if r.get("how") == "made_from"], "media.card")
    with _LOCK:
        con = _connect()
        try:
            _scan_media_cards(con)
            con.commit()
        finally:
            con.close()
    return get(cid) or rec


def set_body(card_id: str, text: str) -> Dict[str, Any]:
    """Edit a card's text. Media-owned bodies are written in place; a Draft HTML
    copy or a legacy item gets a Media-owned body on first edit and the
    original stays untouched."""
    c = get(card_id)
    if c is None:
        return {"status": "not_found"}
    if c["source_kind"] == "media":
        Path(c["path"]).write_text(text, encoding="utf-8")
        if text.strip():
            _sign(Path(c["path"]), c["kind"], [], "media.card")
    elif c["source_kind"] == "post":
        from agent_friday.services import content_pipeline as cp
        r = cp.update_post(c["source_ref"], {"body": text})
        if not r.get("ok"):
            return {"status": "error", "message": r.get("error") or "Could not save the post."}
        reindex_posts_only()
    else:
        if c["kind"] not in TEXT_KINDS and c["source_kind"] not in ("draft_html", "legacy_item"):
            return {"status": "denied", "message": "This kind has no text to edit."}
        root = cards_dir(); root.mkdir(parents=True, exist_ok=True)
        bp = root / ("body-" + hashlib.sha1(card_id.encode()).hexdigest()[:12] + ".md")
        bp.write_text(text, encoding="utf-8")
        _set_override(card_id, body_path=str(bp))
        if text.strip():
            _sign(bp, c["kind"], [{"kind": c["source_kind"], "ref": c["source_ref"]}], "media.card")
    with _LOCK:
        con = _connect()
        try:
            con.execute("UPDATE cards SET text=?, modified=? WHERE id=?", (text[:20000], time.time(), card_id))
            con.commit()
        finally:
            con.close()
    return {"status": "ok", "card": get(card_id)}


def reindex_posts_only() -> None:
    with _LOCK:
        con = _connect()
        try:
            _scan_posts(con); con.commit()
        finally:
            con.close()


def patch(card_id: str, status: Optional[str] = None, project: Optional[str] = None, title: Optional[str] = None,
          when: Any = None) -> Dict[str, Any]:
    """Change what the owner may change by hand. Published is refused here: it
    goes through publish() and the approval card."""
    c = get(card_id)
    if c is None:
        return {"status": "not_found"}
    fields: Dict[str, Any] = {}
    if status is not None:
        if status not in STATUSES:
            return {"status": "error", "message": "Unknown status."}
        if status == "published":
            return {"status": "denied", "message": "Publishing asks you first: use the publish action, which raises a card."}
        if c["source_kind"] == "post":
            from agent_friday.services import content_pipeline as cp
            pid = c["source_ref"]
            if status == "scheduled":
                ts = _parse_when(when) if when else (c.get("when_ts") or time.time() + 3600)
                iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "Z"
                r = cp.schedule_post(pid, cp.new_schedule_config(publish_at=iso))
                if not r.get("ok"):
                    return {"status": "error", "message": r.get("error") or "Could not schedule the post."}
                _set_override(card_id, status=None, when_ts=None)
                reindex_posts_only()
                return {"status": "ok", "card": get(card_id)}
            if status in ("draft", "idea") and c["status"] == "scheduled":
                r = cp.cancel_post(pid)
                if not r.get("ok"):
                    return {"status": "error", "message": r.get("error") or "Could not pull the post back."}
                reindex_posts_only()
            if status == "review" and c["held"]:
                pass  # a held post is already in review
        fields["status"] = status
        if status == "scheduled":
            ts = _parse_when(when)
            if not ts:
                return {"status": "error", "message": "Scheduling needs a date and time."}
            fields["when_ts"] = ts
        elif c["status"] == "scheduled":
            fields["when_ts"] = None
    if when is not None and status is None:
        ts = _parse_when(when)
        if ts:
            fields["when_ts"] = ts
    if project is not None:
        fields["project"] = project.strip() or None
    if title is not None and title.strip():
        fields["title"] = title.strip()[:200]
        if c["source_kind"] == "media":
            _rewrite_media_record(c, title=title.strip()[:200])
    if c["source_kind"] == "media":
        _rewrite_media_record(c, **{k: v for k, v in fields.items() if k in ("status", "project")})
    if fields:
        _set_override(card_id, **fields)
    return {"status": "ok", "card": get(card_id)}


def _rewrite_media_record(c: Dict[str, Any], **changes: Any) -> None:
    p = cards_dir() / (c["source_ref"] + ".json")
    if not p.exists():
        return
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec.update({k: v for k, v in changes.items() if v is not None})
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def delete(card_id: str) -> Dict[str, Any]:
    c = get(card_id)
    if c is None:
        return {"status": "not_found"}
    if c["source_kind"] == "media":
        for suf in (".json", ".md"):
            p = cards_dir() / (c["source_ref"] + suf)
            if p.exists():
                p.unlink()
    elif c["source_kind"] == "post":
        from agent_friday.services import content_pipeline as cp
        r = cp.delete_post(c["source_ref"])
        if not r.get("ok"):
            return {"status": "error", "message": r.get("error") or "Could not delete the post."}
    elif c["source_kind"] in ("draft_html", "legacy_item"):
        # The original is the owner's; the card goes, the file or item stays until they clear it.
        _set_override(card_id, status="idea")
    else:
        return {"status": "denied", "message": "A file is deleted from Files 3D, where a card asks first."}
    with _LOCK:
        con = _connect()
        try:
            con.execute("DELETE FROM cards WHERE id=?", (card_id,))
            con.execute("DELETE FROM overrides WHERE id=?", (card_id,))
            con.execute("DELETE FROM relations WHERE from_id=? OR to_id=?", (card_id, card_id))
            con.commit()
        finally:
            con.close()
    return {"status": "ok"}


# ── publishing: always the one approval card ─────────────────────────────────

PUBLISH_ACTION = "media: publish"
HANDLER = "media_publish"


def publish(card_id: str, requested_by: str = "user") -> Dict[str, Any]:
    """Raise the approval card. Nothing leaves this computer until it is approved;
    complete_publish() runs from the decision hook."""
    c = get(card_id)
    if c is None:
        return {"status": "not_found"}
    if c["status"] == "published" and c["source_kind"] not in ("creation", "document", "episode"):
        return {"status": "ok", "message": "Already published."}
    if c["source_kind"] == "post":
        return _publish_post(c)
    detail = {
        "handler": HANDLER, "card": card_id, "kind": c["kind"], "title": c["title"],
        "targets": c.get("targets") or [], "sources": c.get("sources") or [], "bytes": (c.get("extra") or {}).get("bytes"),
        "credentials": "signed" if c["signed"] else "unsigned",
    }
    try:
        from agent_friday.governance import action_gate
        v = action_gate.authorize_external(
            PUBLISH_ACTION, detail, requested_by=requested_by,
            title=f'Publish "{c["title"]}"',
            description=_publish_description(c),
            action_description="Publish it. The receipt stays on the card; unpublish from the card any time.",
        )
    except Exception as e:  # the gate is the rule; without it nothing is published
        return {"status": "error", "message": f"The approval gate is unavailable: {e}"}
    act = getattr(v, "action", "deny")
    if act == "allow":
        return complete_publish(card_id, approval_id=None)
    if act == "deny":
        return {"status": "denied", "message": getattr(v, "reason", "") or "Refused."}
    aid = _find_card_id(detail)
    return {"status": "pending", "approval_id": aid, "message": "A card is asking you first."}


def _publish_post(c: Dict[str, Any]) -> Dict[str, Any]:
    """A post card is a v2 post: arm it the way the content route does and let
    the content gate raise the owner's card for each target's exact words.
    Media raises no card of its own here, so there is one card per place it
    goes, and the publisher dispatches only once that card is approved."""
    from agent_friday.services import content_pipeline as cp
    from agent_friday.services import publisher as pub
    pid = c["source_ref"]
    r = cp.publish_now(pid)
    if not r.get("ok"):
        return {"status": "error", "message": r.get("error") or "The post could not be armed."}
    ap = pub.request_publish_approval(pid)
    try:
        pub.kick()
    except Exception:
        pass
    reindex_posts_only()
    targets = ap.get("targets") or []
    states = {str(t.get("state") or "") for t in targets}
    if targets and states <= {"approved", "covered by a grant"}:
        return {"status": "ok", "message": "Publishing.", "card": get(c["id"]), "targets": targets}
    return {"status": "pending", "message": "A card is asking you first, for each place it goes.",
            "targets": targets, "approval_id": next((t.get("approval_id") for t in targets if t.get("approval_id")), None)}


def _publish_description(c: Dict[str, Any]) -> str:
    where = ", ".join(c.get("targets") or []) or "this PC's publish host"
    leaves = KIND_WORD.get(c["kind"], "the file").lower()
    return (f"To: {where}. Leaves this PC: the {leaves} and its credential; nothing else. "
            f"Sources: {', '.join(c.get('sources') or []) or 'none'}. Undo: unpublish from the card any time; the receipt stays.")


def _find_card_id(detail: Dict[str, Any]) -> Optional[str]:
    try:
        from agent_friday.services import approvals as ap
        for rec in ap.list_approvals(kind="governed_action") or []:
            if (rec.get("payload") or {}).get("card") == detail["card"] and rec.get("status") in ("pending", "approved"):
                return rec.get("approval_id")
    except Exception:
        pass
    return None


def complete_publish(card_id: str, approval_id: Optional[str]) -> Dict[str, Any]:
    """Runs after approval. A post goes out through the content store's own
    publish; everything else is recorded as published on this PC."""
    c = get(card_id)
    if c is None:
        return {"status": "not_found"}
    if c["source_kind"] == "post":
        from agent_friday.services import content_pipeline as cp
        r = cp.publish_now(c["source_ref"])
        if not r.get("ok"):
            return {"status": "error", "message": r.get("error") or "The post could not be published."}
        reindex_posts_only()
        return {"status": "ok", "message": "Publishing.", "card": get(card_id)}
    where = ", ".join(c.get("targets") or []) or "This PC"
    stamp = where + " · " + time.strftime("%d %b %H:%M")
    if c.get("hash"):
        try:
            from agent_friday.services import provenance
            provenance.add_publication(c["hash"], {"where": where, "ts": time.time(), "card": card_id, "approval": approval_id})
        except Exception:
            pass
    _set_override(card_id, status="published", privacy="published", published_at=stamp)
    if c["source_kind"] == "media":
        _rewrite_media_record(c, status="published")
    return {"status": "ok", "message": "Published to " + where + ".", "card": get(card_id)}


def unpublish(card_id: str) -> Dict[str, Any]:
    c = get(card_id)
    if c is None:
        return {"status": "not_found"}
    if c["source_kind"] == "post":
        return {"status": "denied", "message": "A post that went out is taken down on the platform, not here; the receipt stays on the card."}
    _set_override(card_id, status="draft", privacy="private", published_at=None)
    if c["source_kind"] == "media":
        _rewrite_media_record(c, status="draft")
    return {"status": "ok", "card": get(card_id)}


def _on_decision(record: Dict[str, Any]) -> None:
    payload = record.get("payload") or {}
    if payload.get("handler") != HANDLER or record.get("status") != "approved":
        return
    try:
        from agent_friday.services import approvals as ap
        aid = record.get("approval_id")
        if not ap.claim_for_execution(aid):
            return
        res = complete_publish(payload.get("card"), approval_id=aid)
        ap.mark_used(aid, "media", {"ok": res.get("status") == "ok", "message": res.get("message")})
    except Exception:
        pass


_HOOKED = False


def register_hooks() -> None:
    global _HOOKED
    if _HOOKED:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook("governed_action", _on_decision)
        _HOOKED = True
    except Exception:
        pass


# ── turn this into… ──────────────────────────────────────────────────────────

TURNS = {
    "episode": "a podcast", "post": "a post", "page": "a page", "article": "an article", "draft": "a draft",
    "deck": "slides", "audio": "read aloud", "video": "a video",
}


def turn_into(card_id: str, kind: str) -> Dict[str, Any]:
    """A new card, status Draft, related made_from, using the tool that exists."""
    c = get(card_id)
    if c is None:
        return {"status": "not_found"}
    if kind not in TURNS:
        return {"status": "error", "message": "Not a kind Media can make."}
    body = c.get("body") or ""
    title = c["title"]
    if kind == "episode":
        try:
            from agent_friday.services import podcast_engine as pe
            if c["source_kind"] == "creation" and c["path"]:
                suffix = Path(c["path"]).suffix.lower()
                ref = {"kind": "dataset", "path": c["path"]} if suffix in (".csv", ".tsv", ".xlsx") else {"kind": "creation", "filename": c["source_ref"]}
            elif body.strip():
                ref = {"kind": "text", "text": body, "title": title}
            else:
                return {"status": "error", "message": "Nothing to read from yet."}
            ep = pe.create([ref], title=title, length="standard", origin="user", instructions=f"Made from the Media card \"{title}\".")
        except Exception as e:
            msg = getattr(e, "user_message", None) or str(e)
            return {"status": "error", "message": msg}
        with _LOCK:
            con = _connect()
            try:
                _scan_podcasts(con)
                new_id = _id_for("episode", ep["id"])
                _relate(con, new_id, card_id, "made_from")
                con.commit()
            finally:
                con.close()
        return {"status": "ok", "card": get(new_id)}
    if kind == "post":
        from agent_friday.services import content_pipeline as cp
        assets = None
        if c["source_kind"] == "creation" and c["kind"] in ("image", "imageset", "video", "audio", "music"):
            ak = "video" if c["kind"] == "video" else "audio" if c["kind"] in ("audio", "music") else "image"
            assets = [cp.new_asset_ref(c["source_ref"], c.get("hash") or "", ak, alt_text=title)]
        excerpt = (body.strip().split("\n\n")[0] if body.strip() else title)[:600]
        r = cp.create_post(title=title, body=excerpt, assets=assets, source={"kind": "card", "ref": card_id, "title": title})
        if not r.get("ok"):
            return {"status": "error", "message": r.get("error") or "Could not make the post."}
        reindex_posts_only()
        new_id = _id_for("post", str(r["post"]["id"]))
        with _LOCK:
            con = _connect()
            try:
                _relate(con, new_id, card_id, "made_from"); con.commit()
            finally:
                con.close()
        return {"status": "ok", "card": get(new_id)}
    if kind in ("article", "draft"):
        seed = body if body.strip() else ("\n".join(f"- {s}" for s in c.get("sources") or []) or "")
        if c["kind"] == "episode":
            seed = (c.get("body") or "") or seed
        new = create_card(kind=kind, title=title, body=seed, project=c.get("project"), sources=[title],
                          maker="You", status="draft", relations=[{"to": card_id, "how": "made_from"}], origin="turn")
        return {"status": "ok", "card": new}
    if kind == "page":
        try:
            from agent_friday.services import showcase_engine
            brief = (body.strip() or title)[:4000]
            r = showcase_engine.generate_website(brief, pages=None, style=None, workspace="media")
        except Exception as e:
            return {"status": "error", "message": getattr(e, "user_message", None) or str(e)}
        if not r or r.get("status") != "ok":
            return {"status": "error", "message": (r or {}).get("message") or "Could not make the page."}
        fn = (r.get("files") or [{}])[0].get("filename") or ""
        with _LOCK:
            con = _connect()
            try:
                _scan_creations(con)
                new_id = _id_for("creation", fn)
                _relate(con, new_id, card_id, "made_from")
                con.commit()
            finally:
                con.close()
        _set_override(new_id, status="draft")
        return {"status": "ok", "card": get(new_id)}
    if kind == "audio":
        return read_aloud(c, sync=bool(os.environ.get("FRIDAY_TESTING")))
    if kind == "deck":
        return make_deck(c)
    return {"status": "unavailable", "message": f"Making {TURNS[kind]} is not wired yet; ask Friday in chat and the result lands here."}


# ── slides: a deck from a card's text, through the office tool ───────────────

def _office(argv: List[str]) -> Dict[str, Any]:
    """One door to the office CLI (services/office_engine.run_command); tests stub this."""
    from agent_friday.services import office_engine
    return office_engine.run_command(argv)


def deck_outline(title: str, text: str, max_slides: int = 12) -> List[Tuple[str, str]]:
    """(heading, body) per slide: a title slide, then one slide per heading or
    paragraph, bodies kept to a few lines."""
    slides: List[Tuple[str, str]] = [(title, "")]
    current: Optional[str] = None
    buf: List[str] = []

    def flush() -> None:
        if current is not None or buf:
            body = "\n".join(buf).strip()
            slides.append((current or (body.split(". ")[0][:60] if body else "…"), body[:480]))

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            flush(); current = line.lstrip("#").strip()[:80]; buf = []
        elif current is None and not buf and len(slides) == 1 and line == title:
            continue
        else:
            buf.append(line)
            if current is None and len(buf) >= 1 and sum(len(b) for b in buf) > 300:
                flush(); current = None; buf = []
    flush()
    return slides[:max_slides]


def make_deck(c: Dict[str, Any]) -> Dict[str, Any]:
    """A .pptx in the documents folder from the card's text: one slide per
    heading or paragraph. Made by the office CLI on this computer; signed when
    the last command has run; a deck card linked made_from."""
    try:
        from agent_friday.services import office_engine
        if not office_engine.available():
            return {"status": "unavailable", "message": "The office tool is not installed on this computer, so slides cannot be made here yet."}
    except Exception as e:
        return {"status": "unavailable", "message": "The office tool is unavailable: %s" % e}
    body = (c.get("body") or "").strip()
    if not body and not c.get("title"):
        return {"status": "error", "message": "Nothing to make slides from yet."}
    slug = re.sub(r"[^a-z0-9]+", "-", c["title"].lower()).strip("-")[:40] or "deck"
    name = f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.pptx"
    slides = deck_outline(c["title"], body)
    cmds: List[List[str]] = [["create", name]]
    for i, (heading, text) in enumerate(slides, start=1):
        cmds.append(["add", name, "/", "--type", "slide"])
        cmds.append(["add", name, f"/slide[{i}]", "--type", "placeholder", "--prop", "phType=title", "--prop", "text=" + heading])
        if text:
            cmds.append(["add", name, f"/slide[{i}]", "--type", "shape", "--prop", "text=" + text,
                         "--prop", "x=2cm", "--prop", "y=5cm", "--prop", "width=29cm", "--prop", "height=12cm", "--prop", "size=18pt"])
    path: Optional[str] = None
    for argv in cmds:
        try:
            r = _office(argv)
        except Exception as e:
            return {"status": "error", "message": getattr(e, "user_message", None) or str(e)}
        if not r.get("ok"):
            return {"status": "error", "message": (r.get("stderr") or r.get("stdout") or "The office tool refused.")[:300]}
        if path is None:
            for f in r.get("files") or []:
                if f.get("path"):
                    path = f["path"]
    if not path or not Path(path).exists():
        return {"status": "error", "message": "The office tool made no file."}
    _sign(Path(path), "deck", [{"kind": "card", "ref": c["id"], "title": c["title"]}], "media.make_deck")
    with _LOCK:
        con = _connect()
        try:
            _scan_documents(con)
            new_id = _id_for("document", Path(path).name)
            _relate(con, new_id, c["id"], "made_from")
            con.commit()
        finally:
            con.close()
    _set_override(new_id, status="draft", title=c["title"] + " · slides")   # after the connection closes: one writer at a time
    return {"status": "ok", "card": get(new_id)}


# ── read aloud: one local voice, kept here ───────────────────────────────────

def _chunks(text: str, limit: int = 420) -> List[str]:
    """Paragraphs, then sentences, each short enough for one line of speech."""
    out: List[str] = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = re.sub(r"\s+", " ", para).strip().lstrip("#").strip()
        if not para:
            continue
        buf = ""
        for sent in re.split(r"(?<=[.!?])\s+", para):
            if len(buf) + len(sent) + 1 > limit and buf:
                out.append(buf.strip()); buf = sent
            else:
                buf = (buf + " " + sent).strip()
        if buf:
            out.append(buf.strip())
        out.append("")  # a paragraph break: a short silence
    return out


def _sign(path: Path, kind: str, sources: List[Dict[str, Any]], tool: str) -> None:
    """Content credentials on a file Media saved. Never raises: an unsigned
    file is shown as Unsigned, never pretended signed."""
    try:
        from agent_friday.services import provenance
        media_type = {"audio": "audio", "image": "image", "video": "video", "draft": "text", "article": "text", "doc": "text"}.get(kind, "document")
        provenance.write(str(path), tool_chain=[{"tool": tool, "version": "media"}], sources=sources, license=None, media_type=media_type)
    except Exception:
        pass


def _write_media_record(cid: str, rec: Dict[str, Any]) -> Path:
    root = cards_dir(); root.mkdir(parents=True, exist_ok=True)
    p = root / (cid.split(":")[1] + ".json")
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def read_aloud(c: Dict[str, Any], sync: bool = False) -> Dict[str, Any]:
    """A new audio card, spoken by the local voice on this computer, from a
    card's text. Returns at once with the card in Draft and "working"; the
    file and the credential land when the voice is done (sync=True waits)."""
    text = (c.get("body") or "").strip()
    if not text:
        return {"status": "error", "message": "Nothing to read yet."}
    try:
        from agent_friday.services import podcast_render as pr
        voices = pr.installed_voices()
    except Exception as e:
        return {"status": "unavailable", "message": "The local voice is not installed on this computer (%s)." % e}
    if not voices:
        return {"status": "unavailable", "message": "No local voice is installed on this computer; nothing is downloaded to read aloud."}
    voice = "af_heart" if "af_heart" in voices else voices[0]
    cid = "media:" + uuid.uuid4().hex[:12]
    audio_dir = media_dir() / "audio"; audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / (cid.split(":")[1] + ".wav")
    rec = {"id": cid, "kind": "audio", "title": "Read aloud: " + c["title"], "project": c.get("project"), "sources": [c["title"]],
           "maker": "Local voice (%s) · this PC" % voice, "status": "draft", "created": time.time(), "origin": "turn",
           "relations": [{"to": c["id"], "how": "made_from"}], "file": str(wav), "extra": {"badges": ["working"], "voice": voice}}
    _write_media_record(cid, rec)
    with _LOCK:
        con = _connect()
        try:
            _scan_media_cards(con); con.commit()
        finally:
            con.close()

    def work() -> None:
        try:
            from agent_friday.services import podcast_render as pr
            spk = pr.speaker()
            pcm = b""
            for chunk in _chunks(text)[:400]:
                if chunk == "":
                    pcm += pr._silence(0.35)
                    continue
                pcm += pr._to_pcm16(spk.speak(chunk, voice))
            pr.write_wav(pcm, wav)
            seconds = len(pcm) / 2 / pr.RATE
            mp3 = wav.with_suffix(".mp3")
            try:
                if pr.encode_mp3(wav, mp3, title=rec["title"], album="Read aloud by Agent Friday™", artist="Agent Friday™") and mp3.exists():
                    rec["file"] = str(mp3)
            except Exception:
                pass
            _sign(Path(rec["file"]), "audio", [{"kind": "card", "ref": c["id"], "title": c["title"]}], "media.read_aloud")
            rec["status"] = "published"; rec["extra"] = {"duration_s": seconds, "voice": voice}
        except Exception as e:
            rec["status"] = "draft"; rec["extra"] = {"badges": ["failed"], "error": str(e)[:200], "voice": voice}
        _write_media_record(cid, rec)
        with _LOCK:
            con = _connect()
            try:
                _scan_media_cards(con); con.commit()
            finally:
                con.close()

    if sync:
        work()
    else:
        threading.Thread(target=work, name="media-read-aloud", daemon=True).start()
    return {"status": "ok", "card": get(cid)}
