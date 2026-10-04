"""The Library as a tree of nodes: folders, documents, sections.

One builder serves both the search (which walks it with menus) and the
workspace (which draws it). Consent is applied here, per request: a document
no active add covers, or a deny names, is not a node. A vault-shelf document is
a node only while the vault is unlocked.
"""
from __future__ import annotations

import time
from pathlib import Path

from agent_friday.services.library import embed, grants, route
from agent_friday.services.library.store import Store, VaultLocked


class TreeBuilder:
    def __init__(self, store: Store, principal: str):
        self.store = store
        self.principal = principal
        self._vec_cache: dict[str, dict] = {}
        self._visible: dict[int, bool] | None = None
        self.locked = 0

    # -- consent --------------------------------------------------------------

    def visible_docs(self) -> dict[int, object]:
        """doc id -> row, for every indexed document this principal may see now."""
        if self._visible is None:
            out = {}
            self.locked = 0
            for r in self.store.q("SELECT * FROM documents WHERE state='indexed'"):
                if r["shelf"] == "vault" and not self.store.vault_open():
                    self.locked += 1
                    continue
                if grants.allowed(self.principal, Path(r["path"])):
                    out[r["id"]] = r
            self._visible = out
        return self._visible

    # -- vectors --------------------------------------------------------------

    def vec(self, kind: str, node_id: int):
        d = self._vec_cache.get(kind)
        if d is None:
            d = {}
            if embed.available():
                rows = self.store.q("SELECT node_id, vec FROM vectors WHERE node_kind=?", (kind,))
                if rows:
                    mat = embed.from_blobs([r["vec"] for r in rows])
                    d = {r["node_id"]: mat[i] for i, r in enumerate(rows)}
            self._vec_cache[kind] = d
        return d.get(node_id)

    # -- nodes ----------------------------------------------------------------

    def _profile(self, kind: str, node_id: int, shelf: str, fallback: str) -> str:
        try:
            t = self.store.profile(kind, node_id, shelf)
        except VaultLocked:
            t = None
        return route.sanitize_profile(t or fallback)

    def doc_node(self, r) -> route.Node:
        n = route.Node("document", r["id"], self._profile("document", r["id"], r["shelf"], r["title"]),
                       vec=self.vec("document", r["id"]), doc_id=r["id"], shelf=r["shelf"],
                       extra={"title": r["title"], "ext": r["ext"], "year": time.gmtime(r["mtime"] or 0).tm_year,
                              "pages": r["pages"]})
        n.children = lambda i=r["id"]: self.document_children(i)
        return n

    def folder_node(self, r) -> route.Node:
        n = route.Node("folder", r["id"], self._profile("folder", r["id"], "open", r["name"]),
                       vec=self.vec("folder", r["id"]), extra={"title": r["name"]})
        n.children = lambda i=r["id"]: self.folder_children(i)
        return n

    def section_node(self, s, shelf: str, doc_id: int) -> route.Node:
        n = route.Node("section", s["id"], self._profile("section", s["id"], shelf, s["heading"]),
                       vec=self.vec("section", s["id"]), doc_id=doc_id, shelf=shelf,
                       extra={"title": s["heading"], "page_from": s["page_from"], "page_to": s["page_to"]})
        n.children = lambda i=s["id"], d=doc_id, sh=shelf: self.section_children(i, d, sh)
        return n

    def _folder_has_visible(self, folder_id: int, memo: dict) -> bool:
        if folder_id in memo:
            return memo[folder_id]
        vis = self.visible_docs()
        ok = any(d["folder_id"] == folder_id for d in vis.values())
        if not ok:
            for r in self.store.q("SELECT id FROM folders WHERE parent_id=?", (folder_id,)):
                if self._folder_has_visible(r["id"], memo):
                    ok = True
                    break
        memo[folder_id] = ok
        return ok

    def root_children(self, *, hints: list[int] | None = None) -> list[route.Node]:
        vis = self.visible_docs()
        if not vis:
            return []
        memo: dict = {}
        tops = [r for r in self.store.q("SELECT * FROM folders WHERE parent_id IS NULL ORDER BY name COLLATE NOCASE")
                if self._folder_has_visible(r["id"], memo)]
        nodes = [self.folder_node(r) for r in tops]
        if len(vis) <= route.MAX_MENU:
            nodes = [self.doc_node(r) for r in sorted(vis.values(), key=lambda r: (r["title"] or "").lower())]
        elif len(nodes) == 1:
            return nodes
        have = {n.key for n in nodes}
        for did in hints or []:
            r = vis.get(did)
            if r and ("document", did) not in have and len(nodes) < route.MAX_MENU:
                n = self.doc_node(r)
                n.hint = True
                nodes.append(n)
        return route.group_nodes(nodes)

    def folder_children(self, folder_id: int) -> list[route.Node]:
        memo: dict = {}
        subs = [self.folder_node(r) for r in self.store.q(
            "SELECT * FROM folders WHERE parent_id=? ORDER BY name COLLATE NOCASE", (folder_id,))
            if self._folder_has_visible(r["id"], memo)]
        docs = [self.doc_node(r) for r in self.visible_docs().values() if r["folder_id"] == folder_id]
        docs.sort(key=lambda n: n.extra["title"].lower())
        return route.group_nodes(subs + docs)

    def document_children(self, doc_id: int) -> list[route.Node]:
        r = self.visible_docs().get(doc_id)
        if not r:
            return []
        rows = self.store.q("SELECT * FROM sections WHERE doc_id=? AND parent_id IS NULL ORDER BY ord", (doc_id,))
        out = []
        for s in rows:
            d = dict(s)
            try:
                d["heading"] = self.store.dec(d["heading"], r["shelf"])
            except VaultLocked:
                continue
            out.append(self.section_node(d, r["shelf"], doc_id))
        return route.group_nodes(out)

    def section_children(self, section_id: int, doc_id: int, shelf: str) -> list[route.Node]:
        out = []
        for s in self.store.q("SELECT * FROM sections WHERE parent_id=? ORDER BY ord", (section_id,)):
            d = dict(s)
            try:
                d["heading"] = self.store.dec(d["heading"], shelf)
            except VaultLocked:
                continue
            out.append(self.section_node(d, shelf, doc_id))
        return route.group_nodes(out)
