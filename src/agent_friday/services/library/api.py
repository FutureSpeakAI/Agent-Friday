"""What the Library workspace reads: status, the tree, one block, a receipt.

Pure functions over the store, so the routes are thin and the principal is
always an argument the route took from the request context. Nothing here
returns a file path as a label: documents are named by their title, folders by
their folder name.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from agent_friday.services.library import embed, grants, route, tree
from agent_friday.services.library.store import Store, VaultLocked

MAX_NODES = 6000


def _scope_row(store: Store, a: dict) -> dict:
    root = str(Path(a["path"]).resolve()) if Path(a["path"]).exists() else a["path"]
    n = store.one("SELECT count(*) n FROM documents WHERE scope=? AND state='indexed'", (root,))["n"] \
        if a.get("type") == "folder" else store.one("SELECT count(*) n FROM documents WHERE path=?", (root,))["n"]
    return {"id": a["id"], "name": Path(a["path"]).name or a["path"], "type": a.get("type"),
            "recursive": bool(a.get("recursive", True)), "source": a.get("source"), "documents": n,
            "created_ts": a.get("created_ts")}


def _kg_choice() -> str:
    """"" until the owner chooses, then "on" or "off" (settings.library_kg_learn)."""
    try:
        from agent_friday.core import _load_settings
        v = str((_load_settings() or {}).get("library_kg_learn") or "")
        return v if v in ("on", "off") else ""
    except Exception:
        return ""


def status(store: Store, principal: str, indexer_pending: int = 0) -> dict:
    from agent_friday.services.library import runtime
    c = store.counts()
    scopes = [_scope_row(store, a) for a in grants.active_scopes(principal)]
    failed = [{"id": r["id"], "title": r["title"], "reason": (r["state"].split(":", 1) + [""])[1],
               "kind": "failed"} for r in store.list_documents("failed")]
    skipped = [{"id": r["id"], "title": r["title"], "reason": (r["state_detail"] or ""), "kind": "skipped"}
               for r in store.list_documents("skipped")
               if r["state"] != "skipped:removed"]
    vault_docs = store.one("SELECT count(*) n FROM documents WHERE shelf='vault' AND state='indexed'")["n"]
    free, why = runtime.machine_is_free()
    return {
        "counts": c, "scopes": scopes, "failures": failed, "skipped": skipped, "reading": indexer_pending,
        "paused": grants.suspended(), "waiting_because": None if free else why,
        "vault": {"documents": vault_docs, "unlocked": store.vault_open()},
        "encoder": {"available": embed.available(), **embed.status()},
        "laya": {"loaded": route.laya_available()},
        "index_bytes": store.size_bytes(),
        "empty": not scopes,
        "kg_learn": _kg_choice(),
    }


def _doc_row(tb: tree.TreeBuilder, r) -> dict:
    return {"id": "d:%d" % r["id"], "kind": "document", "title": r["title"], "ext": r["ext"], "pages": r["pages"],
            "shelf": r["shelf"], "year": time.gmtime(r["mtime"] or 0).tm_year, "size": r["size"],
            "ocr": bool(r["ocr_pages"]), "indexed_at": r["indexed_at"]}


def nodes(store: Store, principal: str, node: str = "", depth: int = 1) -> dict:
    """Children of `node` ("" = the root, "f:3", "d:7", "s:12"), to `depth`."""
    tb = tree.TreeBuilder(store, principal)
    vis = tb.visible_docs()
    out: list[dict] = []
    budget = [MAX_NODES]

    def add(n: dict):
        if budget[0] > 0:
            budget[0] -= 1
            out.append(n)

    def folders_under(parent_id, d):
        memo: dict = {}
        sql = "SELECT * FROM folders WHERE parent_id IS NULL ORDER BY name COLLATE NOCASE" if parent_id is None \
            else "SELECT * FROM folders WHERE parent_id=? ORDER BY name COLLATE NOCASE"
        for f in store.q(sql, () if parent_id is None else (parent_id,)):
            if not tb._folder_has_visible(f["id"], memo):
                continue
            n_docs = sum(1 for r in vis.values() if r["folder_id"] == f["id"])
            add({"id": "f:%d" % f["id"], "kind": "folder", "title": f["name"], "parent": ("f:%d" % parent_id) if parent_id else "",
                 "documents": n_docs})
            if d > 1:
                folders_under(f["id"], d - 1)
                docs_in(f["id"], d - 1)

    def docs_in(folder_id, d):
        for r in sorted((r for r in vis.values() if r["folder_id"] == folder_id), key=lambda r: (r["title"] or "").lower()):
            row = _doc_row(tb, r)
            row["parent"] = "f:%d" % folder_id
            add(row)
            if d > 1:
                sections_of(r["id"], None, d - 1)

    def sections_of(doc_id, parent_id, d):
        r = vis.get(doc_id)
        if not r:
            return
        for s in store.sections_of(doc_id):
            if s["parent_id"] != parent_id:
                continue
            npass = store.one("SELECT count(*) n FROM passages WHERE section_id=?", (s["id"],))["n"]
            add({"id": "s:%d" % s["id"], "kind": "section", "title": s["heading"], "level": s["level"],
                 "page_from": s["page_from"], "page_to": s["page_to"], "passages": npass,
                 "parent": ("s:%d" % parent_id) if parent_id else "d:%d" % doc_id, "doc": "d:%d" % doc_id})
            if d > 1:
                sections_of(doc_id, s["id"], d - 1)

    depth = max(1, min(int(depth or 1), 4))
    kind, _, raw = (node or "").partition(":")
    if not node:
        folders_under(None, depth)
    elif kind == "f" and raw.isdigit():
        folders_under(int(raw), depth)
        docs_in(int(raw), depth)
    elif kind == "d" and raw.isdigit():
        sections_of(int(raw), None, depth)
    elif kind == "s" and raw.isdigit():
        r = store.one("SELECT doc_id FROM sections WHERE id=?", (int(raw),))
        if r:
            sections_of(r["doc_id"], int(raw), depth)
    return {"nodes": out, "truncated": budget[0] <= 0, "locked": tb.locked}


def document_detail(store: Store, principal: str, doc_id: int) -> dict | None:
    tb = tree.TreeBuilder(store, principal)
    r = tb.visible_docs().get(doc_id)
    if not r:
        return None
    cited = store.one("SELECT count(DISTINCT conversation_id) n FROM cited_in WHERE doc_id=?", (doc_id,))["n"]
    from agent_friday.services import file_grants as fg
    cloud = fg.check_grant(Path(r["path"])).state == "active"
    return {"id": "d:%d" % r["id"], "title": r["title"], "kind": r["kind"], "ext": r["ext"], "pages": r["pages"],
            "shelf": r["shelf"], "size": r["size"], "indexed_at": r["indexed_at"], "ocr_pages": r["ocr_pages"],
            "cloud_grant": cloud, "cited_in": cited, "has_page_images": r["kind"] == "pdf",
            "folder": (store.one("SELECT name FROM folders WHERE id=?", (r["folder_id"],)) or {"name": ""})["name"]}


def block(store: Store, principal: str, block_id: int) -> dict | None:
    """One block with its document, page, box and neighbours, for the Reader."""
    row = store.block(block_id)
    if not row:
        return None
    tb = tree.TreeBuilder(store, principal)
    doc = tb.visible_docs().get(row["doc_id"])
    if not doc:
        return None
    try:
        text = store.dec(row["text"], doc["shelf"])
    except VaultLocked:
        return None
    near = []
    for r in store.q("SELECT * FROM blocks WHERE doc_id=? AND ord BETWEEN ? AND ? ORDER BY ord",
                     (row["doc_id"], row["ord"] - 2, row["ord"] + 2)):
        try:
            near.append({"id": r["id"], "kind": r["kind"], "text": store.dec(r["text"], doc["shelf"]),
                         "page": r["page"], "current": r["id"] == block_id})
        except VaultLocked:
            pass
    bbox = None if row["x0"] is None else [row["x0"], row["y0"], row["x1"], row["y1"]]
    sect = store.one("SELECT heading FROM sections WHERE id=?", (row["section_id"],)) if row["section_id"] else None
    heading = None
    if sect:
        try:
            heading = store.dec(sect["heading"], doc["shelf"])
        except VaultLocked:
            heading = None
    from agent_friday.services.library import search as _search
    return {"id": row["id"], "doc": "d:%d" % row["doc_id"], "doc_id": row["doc_id"], "title": doc["title"],
            "kind": row["kind"], "page": row["page"], "para": _search._para_number(store, row), "pages": doc["pages"], "bbox": bbox, "text": text,
            "t_start": row["t_start"], "t_end": row["t_end"], "section": heading, "neighbours": near,
            "page_image": doc["kind"] == "pdf", "doc_kind": doc["kind"]}


def receipt(store: Store, search_id: str) -> dict | None:
    row = store.one("SELECT data FROM receipts WHERE search_id=?", (search_id,))
    if not row:
        return None
    data = json.loads(row["data"])
    return data


def section_passages(store: Store, principal: str, section_id: int) -> dict | None:
    """A section's passages as plain text with the block each one starts at, for
    the 3D reading plane."""
    sec = store.one("SELECT * FROM sections WHERE id=?", (section_id,))
    if not sec:
        return None
    doc = tree.TreeBuilder(store, principal).visible_docs().get(sec["doc_id"])
    if not doc:
        return None
    try:
        heading = store.dec(sec["heading"], doc["shelf"])
    except VaultLocked:
        return None
    out = []
    for p in store.q("SELECT id, block_ids, text FROM passages WHERE section_id=? ORDER BY id", (section_id,)):
        try:
            text = store.dec(p["text"], doc["shelf"])
        except VaultLocked:
            return None
        ids = json.loads(p["block_ids"] or "[]")
        first = store.one("SELECT id, page, t_start FROM blocks WHERE id=?", (ids[0],)) if ids else None
        out.append({"id": p["id"], "text": text, "block": first["id"] if first else None,
                    "page": first["page"] if first else None, "t_start": first["t_start"] if first else None})
    return {"id": "s:%d" % section_id, "doc": "d:%d" % sec["doc_id"], "doc_id": sec["doc_id"], "heading": heading,
            "page_from": sec["page_from"], "page_to": sec["page_to"], "passages": out}
