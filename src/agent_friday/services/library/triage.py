"""What Laya notes about each document the Library reads: its kind, how sensitive it is, whether it is a copy.

All of it runs on the CPU from the vectors the index already made (the ONE shared MiniLM; see
services/minilm and library/embed): reading a document costs no second encoder load and no model call.

* **Document type** (note, contract, invoice, paper, receipt, CV, letter) by prototype heads: a few
  generic, invented sentences per type are embedded once, and a document takes the type whose centroid is
  closest to the mean of its profile vector and its first passages. Below `MIN_SCORE`, or too close to
  the runner-up, it is "other": a head that is not sure says so.
* **Sensitivity** is the existing local classifier's tier (services/sensitivity_classifier), read from
  the text the Library stored. A vault-shelf document is recorded as private at least; its sealed text is
  not opened.
* **Duplicates**: the same bytes (sha256), or a near copy (the mean of its passage vectors against every
  other document's, `NEAR_DUP`, a similar size, and the words themselves agreeing: a topic match alone is
  not a copy). The later document points at the earlier one. Nothing
  is deleted or merged: this is a note beside the document that a later "keep one" proposal can read.

Where it sits in the one Laya ladder: rung R1 (the shared embedding, no model), advisory only. It adds a
note; it never changes a shelf, lowers a tier, approves or removes anything. A missing encoder or a failure
records the document as `degraded` and carries on; one document never takes longer than `BUDGET_S`. Each
row carries the versions that made it (triage, encoder, heads), so a later change of heads or encoder is
visible and the row is made again. No user words are in the heads: they are generic exemplars written here.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from agent_friday.services.library import embed
from agent_friday.services.library.store import Store

VERSION = 1
DOC_TYPES = ("note", "contract", "invoice", "paper", "receipt", "cv", "letter")
OTHER = "other"

#: A document whose best type scores under this, or whose lead over the runner-up is under MARGIN, is "other".
MIN_SCORE = 0.22
MARGIN = 0.02
#: Cosine of two documents' mean passage vectors above which one is called a near copy of the other.
NEAR_DUP = 0.975
NEAR_SIZE_RATIO = 2.0
#: A topic match is confirmed by the words themselves: this much of their 3-word runs must agree.
NEAR_TEXT = 0.8
SHINGLE_WORDS = 3000
#: How many of a document's first passages join its profile in the type reading.
HEAD_PASSAGES = 3
#: One document's triage gives up past this (the encoder is already done; this guards a slow disk).
BUDGET_S = 2.0

#: Generic exemplars, invented for this module. They are what each type tends to say, not anyone's words.
PROTOTYPES: dict[str, tuple[str, ...]] = {
    "note": (
        "Meeting notes: agenda, discussion points and action items to follow up on.",
        "Ideas and reminders jotted down to remember later, with a to-do list.",
        "Daily journal entry about what I did today and what to do tomorrow.",
        "Brainstorm notes with bullet points, links and half-finished thoughts.",
        "Notes on a book or lecture: key takeaways and questions to look into.",
    ),
    "contract": (
        "This agreement is made between the parties and sets out the terms and conditions of the services.",
        "The term of this contract, termination, confidentiality, liability and governing law.",
        "The parties agree to the following obligations, payment terms and signatures below.",
        "Lease agreement between landlord and tenant covering rent, deposit and duration.",
        "Non-disclosure agreement: the receiving party shall keep the confidential information secret.",
    ),
    "invoice": (
        "Invoice number, invoice date, due date, bill to, line items, subtotal, tax and total amount due.",
        "Please remit payment within thirty days of the invoice date. Balance due.",
        "Amount payable for services rendered, quantity, unit price and total, purchase order number.",
        "Statement of account with outstanding balance, payment terms and bank transfer details.",
    ),
    "paper": (
        "Abstract. We propose a method and evaluate it on benchmark datasets. Keywords and introduction.",
        "Related work, methodology, experiments, results and discussion, conclusion and references.",
        "In this paper we show that the proposed approach improves accuracy over prior work.",
        "Theorem, proof and lemma; we derive the bound and cite the literature in the references.",
        "Participants, materials and procedure; the study found a significant effect, with limitations noted.",
    ),
    "receipt": (
        "Receipt: thank you for your purchase. Items, subtotal, sales tax, total paid by card.",
        "Order confirmation and payment received, transaction id, date, merchant and amount charged.",
        "Store receipt with item names and prices, change given and a return policy.",
        "Payment receipt: amount paid, payment method, reference number, paid in full.",
    ),
    "cv": (
        "Curriculum vitae: professional summary, work experience, education, skills and references.",
        "Resume. Experience: job titles, employers and dates, with achievements. Education and certifications.",
        "Objective, employment history, technical skills, languages, awards and publications.",
        "Career summary of a candidate listing roles held, responsibilities, degrees and skills.",
    ),
    "letter": (
        "Dear Sir or Madam, I am writing to you regarding the matter below. Yours sincerely.",
        "Dear friend, I hope this letter finds you well. Please write back soon. With warm regards.",
        "Thank you for your letter of the date mentioned; I wish to inform you of the following. Sincerely.",
        "Cover letter: I am writing to apply for the position advertised and enclose my details. Kind regards.",
        "Formal notice addressed to the recipient with a greeting, a body and a closing signature.",
    ),
}

_heads: dict = {"key": None, "types": (), "mat": None}


def heads_id() -> str:
    """A short id for the prototype set: changes when any exemplar or type does."""
    h = hashlib.sha256()
    for t in DOC_TYPES:
        h.update(t.encode())
        for s in PROTOTYPES[t]:
            h.update(s.encode())
    return h.hexdigest()[:12]


def _encoder_id() -> str:
    """The id of the encoder that made the vectors this reads: model, pinned hash and width."""
    return "%s/%d" % (embed.stamp_id(), embed.DIM)


def versions() -> dict:
    return {"triage": VERSION, "encoder": _encoder_id(), "heads": heads_id()}


def _unit(v):
    import numpy as np
    n = float(np.linalg.norm(v))
    return v / n if n else v


def prototype_matrix():
    """(types, matrix of unit centroids), embedded once per process and encoder. None if no encoder."""
    key = (_encoder_id(), heads_id())
    if _heads["key"] == key and _heads["mat"] is not None:
        return _heads["types"], _heads["mat"]
    import numpy as np
    texts, owner = [], []
    for t in DOC_TYPES:
        for s in PROTOTYPES[t]:
            texts.append(s)
            owner.append(t)
    vecs = embed.embed(texts)
    if vecs is None or not len(vecs):
        return None
    rows = []
    for t in DOC_TYPES:
        idx = [i for i, o in enumerate(owner) if o == t]
        rows.append(_unit(np.mean(vecs[idx], axis=0)))
    _heads.update(key=key, types=tuple(DOC_TYPES), mat=np.vstack(rows))
    return _heads["types"], _heads["mat"]


def reset_heads() -> None:
    _heads.update(key=None, types=(), mat=None)


def _vectors(store: Store, doc_id: int, kind: str, limit: int | None = None):
    rows = store.q("SELECT vec FROM vectors WHERE node_kind=? AND doc_id=? ORDER BY node_id"
                   + (" LIMIT %d" % int(limit) if limit else ""), (kind, doc_id))
    return embed.from_blobs([r["vec"] for r in rows])


def classify_type(store: Store, doc_id: int) -> tuple[str | None, float | None]:
    """(type, score) from the document's own stored vectors; (None, None) when it has none to read."""
    import numpy as np
    heads = prototype_matrix()
    if heads is None:
        return None, None
    types, mat = heads
    parts = [v for v in (_vectors(store, doc_id, "document"), _vectors(store, doc_id, "passage", HEAD_PASSAGES))
             if len(v)]
    if not parts:
        return None, None
    doc = _unit(np.vstack(parts).mean(axis=0))
    sims = mat @ doc
    order = np.argsort(-sims)
    best, second = float(sims[order[0]]), float(sims[order[1]]) if len(order) > 1 else -1.0
    if best < MIN_SCORE or best - second < MARGIN:
        return OTHER, round(best, 4)
    return types[int(order[0])], round(best, 4)


def signature(store: Store, doc_id: int):
    """The mean of a document's passage vectors, unit length; None when it has none."""
    v = _vectors(store, doc_id, "passage")
    return _unit(v.mean(axis=0)) if len(v) else None


class Signatures:
    """Every open-shelf document's signature, for near-copy lookups across one triage run."""

    def __init__(self, store: Store):
        import numpy as np
        self.ids: list[int] = []
        rows = []
        # One pass over the passage vectors of the open shelf, grouped by document.
        cur, acc = None, []

        def close():
            if cur is not None and acc:
                self.ids.append(cur)
                rows.append(_unit(np.vstack(acc).mean(axis=0)))
        for r in store.q("SELECT v.doc_id, v.vec FROM vectors v JOIN documents d ON d.id=v.doc_id "
                         "WHERE v.node_kind='passage' AND d.state='indexed' AND d.shelf='open' "
                         "ORDER BY v.doc_id, v.node_id"):
            if r["doc_id"] != cur:
                close()
                cur, acc = r["doc_id"], []
            acc.append(embed.from_blobs([r["vec"]])[0])
        close()
        self.mat = np.vstack(rows) if rows else np.zeros((0, embed.DIM), dtype=np.float32)

    def add(self, doc_id: int, sig) -> None:
        import numpy as np
        if doc_id in self.ids:
            self.mat[self.ids.index(doc_id)] = sig
            return
        self.ids.append(doc_id)
        self.mat = np.vstack([self.mat, sig[None, :]])

    def candidates_earlier(self, doc_id: int, sig) -> list:
        """[(id, cosine)] of documents with a smaller id whose signature is within NEAR_DUP, earliest first."""
        import numpy as np
        if not self.ids:
            return []
        sims = self.mat @ sig
        hits = [(self.ids[int(i)], float(sims[int(i)])) for i in np.where(sims >= NEAR_DUP)[0]
                if self.ids[int(i)] < doc_id]
        return sorted(hits)


def _shingles(store: Store, doc_id: int) -> set:
    """The document's first words as 3-word runs (text as stored, open shelf only)."""
    import re
    text = " ".join(r["text"] for r in store.q("SELECT text FROM blocks WHERE doc_id=? ORDER BY ord LIMIT 60", (doc_id,)))
    w = re.findall(r"[^\W_]+", text.lower())[:SHINGLE_WORDS]
    return {" ".join(w[i:i + 3]) for i in range(max(0, len(w) - 2))}


def same_text(store: Store, a: int, b: int) -> float:
    """How much of two documents' words run the same way (Jaccard of 3-word runs). A topic vector says two
    documents are about the same thing; this says they say the same thing."""
    sa, sb = _shingles(store, a), _shingles(store, b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def _sensitivity(store: Store, doc) -> int | None:
    """The classifier's tier: the one recorded when the document was read (the classifier ran then), else
    asked now for an open-shelf document. At least private for a vault-shelf one."""
    from agent_friday.services.library import shelf as lshelf
    have = store.get_doc_meta(doc["id"])
    if have is not None and have["sensitivity"] is not None:
        return max(int(have["sensitivity"]), 2) if doc["shelf"] == "vault" else int(have["sensitivity"])
    if doc["shelf"] == "vault":
        return 2
    rows = store.q("SELECT text FROM blocks WHERE doc_id=? ORDER BY ord LIMIT 40", (doc["id"],))
    sample = "\n".join(r["text"] for r in rows)[:4000]
    return int(lshelf.tier_of(doc["title"] or "", sample))


def _same_bytes(store: Store, doc):
    if not doc["sha256"]:
        return None
    return store.one("SELECT id FROM documents WHERE sha256=? AND id<?  AND state='indexed' AND shelf=? "
                     "AND path<>? ORDER BY id LIMIT 1", (doc["sha256"], doc["id"], doc["shelf"], doc["path"]))


def triage_document(store: Store, doc_id: int, sigs: Signatures | None = None) -> dict:
    """Note a document's type, tier and copies in `doc_meta`. Never raises, never changes the document."""
    t0 = time.monotonic()
    doc = store.get_document(doc_id)
    if doc is None or doc["state"] != "indexed":
        return {"ok": False}
    degraded = False
    doc_type = type_score = tier = dup_of = dup_score = dup_kind = None
    try:
        if embed.available():
            doc_type, type_score = classify_type(store, doc_id)
        degraded = doc_type is None
    except Exception:  # noqa: BLE001 - a head that cannot run is recorded as degraded, never as a type
        degraded = True
    try:
        tier = _sensitivity(store, doc)
    except Exception:  # noqa: BLE001 - a classifier that cannot answer is not evidence the text is public
        tier = 2
        degraded = True
    try:
        same = _same_bytes(store, doc)
        if same:
            dup_of, dup_kind, dup_score = same["id"], "exact", 1.0
        elif sigs is not None and doc["shelf"] == "open" and time.monotonic() - t0 < BUDGET_S:
            sig = signature(store, doc_id)
            if sig is not None:
                hits = sigs.candidates_earlier(doc_id, sig)
                sigs.add(doc_id, sig)
                for other_id, cos in hits[:5]:
                    other = store.get_document(other_id)
                    a, b = float(doc["size"] or 0), float(other["size"] or 0)
                    if other["kind"] == doc["kind"] and (not a or not b or max(a, b) / max(1.0, min(a, b)) <= NEAR_SIZE_RATIO) \
                            and same_text(store, doc_id, other_id) >= NEAR_TEXT:
                        dup_of, dup_kind, dup_score = other_id, "near", round(cos, 4)
                        break
    except Exception:  # noqa: BLE001
        degraded = True
    store.set_doc_meta(doc_id, doc_type=doc_type, type_score=type_score, sensitivity=tier, dup_of=dup_of,
                       dup_kind=dup_kind, dup_score=dup_score, degraded=degraded, versions=versions())
    return {"ok": True, "doc_type": doc_type, "sensitivity": tier, "dup_of": dup_of, "dup_kind": dup_kind,
            "degraded": degraded}


def triage_pending(store: Store, scope: str | None = None, *, before=None, allowed=None) -> int:
    """Triage every indexed document of `scope` that has no current row. `before()` runs ahead of each
    document (the job's pause and cancel point). `allowed(path)` is the owner's consent, read live: a
    document it refuses is left alone. Returns how many were done."""
    ids = store.untriaged(scope)
    if not ids:
        return 0
    sigs = None
    try:
        if embed.available():
            sigs = Signatures(store)
    except Exception:  # noqa: BLE001
        sigs = None
    n = 0
    for doc_id in ids:
        if before is not None:
            before()
        if allowed is not None:
            row = store.get_document(doc_id)
            if row is None or not allowed(Path(row["path"])):
                continue
        if triage_document(store, doc_id, sigs).get("ok"):
            n += 1
    return n


def summary_for(store: Store, doc_id: int) -> dict | None:
    """The triage row as plain data for the status read-outs, or None."""
    r = store.get_doc_meta(doc_id)
    if r is None:
        return None
    return {"type": r["doc_type"], "sensitivity": r["sensitivity"], "duplicate_of": r["dup_of"],
            "duplicate_kind": r["dup_kind"], "degraded": bool(r["degraded"])}
