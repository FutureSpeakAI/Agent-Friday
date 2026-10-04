"""Removing a document from the Library, and forgetting it everywhere.

Remove stops indexing and deletes the document's rows, full-text entries,
vectors, profiles and cached images; the file on disk is untouched.

Forget does everything Remove does and also:
  * writes a tombstone (hash and path), so a folder scope never re-indexes it
    until the owner un-forgets it;
  * rewrites every saved chat that cited it: the footnote becomes
    "[forgotten source]" and any quotation of the document is removed;
  * is found by the people path (`forget_registry`), which purges Library
    blocks naming a forgotten person.
The file on disk stays; deleting it is a separate, governed action.
"""
from __future__ import annotations

import re
import shutil
import time

from agent_friday.services.library import store as lstore
from agent_friday.services.library.store import Store

FORGOTTEN = "[forgotten source]"
_MIN_QUOTE = 20


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[‘’“”\"'`*_]", "", t.lower())).strip()


def _doc_text(store: Store, doc_id: int) -> str:
    doc = store.get_document(doc_id)
    if not doc:
        return ""
    parts = []
    for r in store.q("SELECT text FROM blocks WHERE doc_id=? ORDER BY ord", (doc_id,)):
        try:
            parts.append(store.dec(r["text"], doc["shelf"]))
        except lstore.VaultLocked:
            return ""
    return _norm("\n".join(parts))


def _doc_plain(st: Store, doc_id: int) -> str:
    """The document's text as read (not lowercased or stripped), for fingerprinting."""
    doc = st.get_document(doc_id)
    parts = []
    for r in st.q("SELECT text FROM blocks WHERE doc_id=? ORDER BY ord", (doc_id,)):
        parts.append(st.dec(r["text"], doc["shelf"]))
    return "\n".join(parts)


def scrub_text(text: str, doc_id: int, doc_norm: str) -> str:
    """`text` with this document's footnotes and quotations removed."""
    text = re.sub(r"\[(?:unverified-)?lib:%d#\d+\]" % doc_id, FORGOTTEN, text)
    if doc_norm:
        def _is_quote(s: str) -> bool:
            n = _norm(s)
            return len(n) >= _MIN_QUOTE and n in doc_norm

        out = []
        for line in text.split("\n"):
            m = re.match(r"^(\s*>\s?)(.*)$", line)
            if m and _is_quote(m.group(2)):
                out.append(m.group(1) + FORGOTTEN)
                continue
            out.append(line)
        text = "\n".join(out)
        text = re.sub(r"“([^”]{%d,})”" % _MIN_QUOTE,
                      lambda m: "“" + FORGOTTEN + "”" if _is_quote(m.group(1)) else m.group(0), text)
        text = re.sub(r"\"([^\"\n]{%d,})\"" % _MIN_QUOTE,
                      lambda m: '"' + FORGOTTEN + '"' if _is_quote(m.group(1)) else m.group(0), text)
    return text


def _scrub_message(msg: dict, doc_id: int, doc_norm: str) -> dict:
    for key in ("text", "content"):
        if isinstance(msg.get(key), str):
            msg[key] = scrub_text(msg[key], doc_id, doc_norm)
    meta = msg.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("lib_refs"), list):
        meta["lib_refs"] = [r for r in meta["lib_refs"] if not (isinstance(r, dict) and r.get("doc") == doc_id)]
    return msg


def cited_conversations(store: Store, doc_id: int) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in store.q("SELECT DISTINCT conversation_id, message_id FROM cited_in WHERE doc_id=?", (doc_id,)):
        if r["conversation_id"]:
            out.setdefault(r["conversation_id"], set()).add(r["message_id"])
    return out


def _clear_cache(principal: str, doc_id: int) -> None:
    try:
        from agent_friday.services.library import pages
        pages.purge_document(lstore.store_for(principal).get_document(doc_id))
    except Exception:  # noqa: BLE001
        pass
    try:
        shutil.rmtree(lstore.principal_dir(principal) / "cache" / str(doc_id), ignore_errors=True)
    except ValueError:
        pass


def _purge_graph(principal: str, doc_id: int) -> None:
    """Facts the knowledge graph learned from this document go with it."""
    if principal != lstore.OWNER:
        return
    try:
        from agent_friday.services.knowledge_graph import indexer as kg
        kg.purge_library_document(doc_id)
    except Exception:  # noqa: BLE001 - a graph that is not built has nothing to purge
        pass


def left_the_open_shelf(principal: str, doc_id: int) -> None:
    """A document moved to the vault: what the graph learned from its open copy, the page
    renders and the remembered last search all go; the index rewrites its own rows."""
    from agent_friday.services.library import search
    _purge_graph(principal, doc_id)
    search.forget_last(doc_id)
    doc = lstore.store_for(principal).get_document(doc_id)
    if doc:
        _clear_cache(principal, doc_id)


def remove_document(principal: str, doc_id: int) -> dict:
    """Stop indexing and purge. The file on disk is untouched."""
    from agent_friday.services.library import search
    st = lstore.store_for(principal)
    doc = st.get_document(doc_id)
    if not doc:
        return {"ok": False, "error": "no such document"}
    _purge_graph(principal, doc_id)
    search.forget_last(doc_id)
    st.purge_document(doc_id, keep_row=False)
    _clear_cache(principal, doc_id)
    st.drop_empty_folders()
    return {"ok": True, "removed": doc["title"]}


def forget_document(principal: str, doc_id: int) -> dict:
    st = lstore.store_for(principal)
    doc = st.get_document(doc_id)
    if not doc:
        return {"ok": False, "error": "no such document"}
    if doc["shelf"] == "vault" and not st.vault_open():
        # Its words cannot be read to find every copy of them, so nothing is half-forgotten.
        return {"ok": False, "error": "Unlock the vault first: this document's text has to be read once to "
                                      "find every copy of it before it is forgotten."}
    from agent_friday.services import conversations
    from agent_friday.services.library import sweep
    doc_norm = _doc_text(st, doc_id)
    fp = sweep.fingerprint(_doc_plain(st, doc_id), doc_id)
    _purge_graph(principal, doc_id)
    changed = 0
    for cid in cited_conversations(st, doc_id):
        changed += conversations.rewrite(cid, lambda m, d=doc_id, n=doc_norm: _scrub_message(m, d, n))
    swept = sweep.sweep_all(fp, doc_id)
    # The tombstone goes in before the purge so a failure part-way leaves the document
    # forgotten (never re-read) rather than half-deleted and still searchable.
    st.x("INSERT OR REPLACE INTO tombstones(sha256, path, ts) VALUES(?,?,?)",
         (doc["sha256"], doc["path"], time.time()))
    st.x("DELETE FROM cited_in WHERE doc_id=?", (doc_id,))
    st.purge_document(doc_id, keep_row=False)
    _clear_cache(principal, doc_id)
    st.drop_empty_folders()
    return {"ok": True, "forgotten": doc["title"], "messages_rewritten": changed + swept["chats"], "swept": swept,
            "incomplete": swept.get("incomplete") or []}


def unforget(principal: str, path: str) -> int:
    st = lstore.store_for(principal)
    return st.x("DELETE FROM tombstones WHERE path=?", (path,)).rowcount


# ── the people path ──────────────────────────────────────────────────────────

def _name_hits(st: Store, names: set[str]) -> list[tuple[int, int, str]]:
    """(passage id, doc id, shelf) of passages that name any of `names`."""
    hits: dict[int, tuple[int, int, str]] = {}
    for n in names:
        n = _norm(n)
        if len(n) < 3:
            continue
        phrase = '"' + n.replace('"', " ") + '"'
        for r in st.q("SELECT p.id, p.doc_id FROM fts JOIN passages p ON p.id = fts.rowid "
                      "WHERE fts MATCH ?", (phrase,)):
            hits[r["id"]] = (r["id"], r["doc_id"], "open")
        if st.vault_open():
            for r in st.q("SELECT p.id, p.doc_id, p.text FROM passages p JOIN documents d ON d.id=p.doc_id "
                          "WHERE d.shelf='vault'"):
                try:
                    if n in _norm(st.dec(r["text"], "vault")):
                        hits[r["id"]] = (r["id"], r["doc_id"], "vault")
                except Exception:
                    continue
    return list(hits.values())


def people_find(names, emails) -> int:
    total = 0
    for principal in _principals():
        total += len(_name_hits(lstore.store_for(principal), set(names) | set(emails)))
    return total


def people_purge(names, emails) -> int:
    """Delete the passages (and the blocks they cover) that name the person."""
    import json
    total = 0
    for principal in _principals():
        st = lstore.store_for(principal)
        for pid, _doc, _shelf in _name_hits(st, set(names) | set(emails)):
            row = st.one("SELECT block_ids FROM passages WHERE id=?", (pid,))
            if not row:
                continue
            st.x("DELETE FROM fts WHERE rowid=?", (pid,))
            st.x("DELETE FROM vectors WHERE node_kind='passage' AND node_id=?", (pid,))
            for bid in json.loads(row["block_ids"] or "[]"):
                st.x("DELETE FROM blocks WHERE id=?", (bid,))
            st.x("DELETE FROM passages WHERE id=?", (pid,))
            total += 1
        st.compact()
    return total


def _principals() -> list[str]:
    root = lstore.library_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "library.sqlite").exists())


def register() -> None:
    from agent_friday.services import forget_registry
    forget_registry.register_store("library", people_find, people_purge)
