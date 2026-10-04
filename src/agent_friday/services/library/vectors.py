"""Vectors for the Library's routing profiles and passages.

A vector is topic, not text, so vault-shelf documents keep theirs (stated in
Settings). They are computed from the plaintext at index time, while it is in
memory, and never again from sealed text.
"""
from __future__ import annotations

from agent_friday.services.library import embed, structure
from agent_friday.services.library.store import Store

PASSAGE_EMBED_CHARS = 900


def index_document_vectors(store: Store, doc_id: int, *, sections: list[dict], passages: list[dict],
                           doc_profile: str, section_profiles: dict[int, str], sec_ids: dict[int, int]) -> int:
    """Embed the document profile, each section profile and each passage.
    Returns the number of vectors stored (0 when the encoder is unavailable)."""
    if not embed.available():
        return 0
    head = {s["id"]: s["heading"] for s in sections}
    texts, meta = [doc_profile], [("document", doc_id)]
    for local, text in section_profiles.items():
        texts.append(text)
        meta.append(("section", sec_ids[local]))
    for p in passages:
        texts.append(f"{head.get(p['section'], '')}. {p['text']}"[:PASSAGE_EMBED_CHARS])
        meta.append(("passage", p["id"]))
    vecs = embed.embed(texts)
    if vecs is None:
        return 0
    store.put_vectors([(k, nid, doc_id, embed.to_blob(v)) for (k, nid), v in zip(meta, vecs)])
    return len(meta)


def refresh_folders(store: Store) -> int:
    """Profiles and vectors for every folder that holds a document: its name,
    its subfolders and the titles it holds. Deterministic text, no model."""
    rows = store.q("SELECT id, name, parent_id FROM folders")
    docs = store.q("SELECT folder_id, title FROM documents WHERE state='indexed'")
    titles: dict[int, list[str]] = {}
    for d in docs:
        titles.setdefault(d["folder_id"], []).append(d["title"])
    subs: dict[int, list[str]] = {}
    for r in rows:
        if r["parent_id"] is not None:
            subs.setdefault(r["parent_id"], []).append(r["name"])
    ids, texts = [], []
    for r in rows:
        items = subs.get(r["id"], []) + titles.get(r["id"], [])
        if not items:
            continue
        text = structure.folder_profile(r["name"], items)
        store.x("INSERT OR REPLACE INTO profiles(node_kind, node_id, text) VALUES('folder',?,?)", (r["id"], text))
        ids.append(r["id"])
        texts.append(text)
    if not ids or not embed.available():
        return 0
    vecs = embed.embed(texts)
    if vecs is None:
        return 0
    store.put_vectors([("folder", i, None, embed.to_blob(v)) for i, v in zip(ids, vecs)])
    return len(ids)


def missing_vectors(store: Store) -> list[int]:
    """Indexed open-shelf documents with no document vector (indexed before the
    encoder was available)."""
    return [r["id"] for r in store.q(
        "SELECT d.id FROM documents d WHERE d.state='indexed' AND d.shelf='open' AND NOT EXISTS "
        "(SELECT 1 FROM vectors v WHERE v.node_kind='document' AND v.node_id=d.id)")]


def backfill(store: Store) -> int:
    """Embed open-shelf documents that were indexed without vectors."""
    if not embed.available():
        return 0
    n = 0
    for doc_id in missing_vectors(store):
        doc = store.get_document(doc_id)
        secs = store.sections_of(doc_id)
        psgs = store.q("SELECT id, section_id, text FROM passages WHERE doc_id=?", (doc_id,))
        sec_of = {s["id"]: s for s in secs}
        texts, meta = [], []
        dprof = store.profile("document", doc_id) or doc["title"]
        texts.append(dprof)
        meta.append(("document", doc_id))
        for s in secs:
            sp = store.profile("section", s["id"])
            if sp:
                texts.append(sp)
                meta.append(("section", s["id"]))
        for p in psgs:
            texts.append(f"{sec_of[p['section_id']]['heading']}. {p['text']}"[:PASSAGE_EMBED_CHARS])
            meta.append(("passage", p["id"]))
        vecs = embed.embed(texts)
        if vecs is None:
            return n
        store.put_vectors([(k, nid, doc_id, embed.to_blob(v)) for (k, nid), v in zip(meta, vecs)])
        n += 1
    return n
