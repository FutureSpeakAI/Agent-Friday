"""Offline retrieval evaluation for conversation memory (north star §36.2-12).

    python -m agent_friday.services.memory_retrieval_eval [--k 5]

A small fixed set of synthetic conversations (invented; nobody's real words)
is indexed into a real ``ConversationMemory`` and every labelled question is
asked through ``ConversationMemory.search``, the same call the chat's memory
block makes. It reports:

  recall@k          share of questions whose expected message is in the top k
  MRR               mean of 1/rank of the expected message (0 when missed)
  superseded leaks  questions where a superseded (corrected) message came back;
                    it must be zero, whatever the scorer

The vector store under ``ConversationMemory`` is, by default, a deterministic
in-memory collection with Chroma's interface that scores by word overlap
(stop words dropped, light suffix stemming): no model, no network, no Chroma,
so CI can run it on every change and a regression in the search path (ranking
order, over-fetching, supersession filtering, result shape) shows as a drop.
The real encoder's semantic quality is a separate measurement: pass a real
Chroma collection as ``run(collection=...)`` on a machine that has one, as
tools/library_eval.py does for the Library.

The library's evaluation (tools/library_eval.py, tests/rig/library_golden) is
the model for this one; it measures documents, this measures past turns.
Nothing here writes to the Friday home: supersession is set on the in-memory
rows directly, not through the full-text index.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The floor CI holds the default (lexical) run to.
FLOOR = {"recall_at_k": 0.8, "mrr": 0.6, "superseded_leaks": 0}

#: (id, role, session/date, text). Synthetic.
MESSAGES = [
    ("ev-01", "user", "2026-03-02", "My sailing boat is called Wren and she is moored at Harwich."),
    ("ev-02", "friday", "2026-03-02", "Wren sounds lovely. Harwich is a good harbour for a small boat."),
    ("ev-03", "user", "2026-03-09", "My sister Priya lives in Leeds and works as a radiographer."),
    ("ev-04", "user", "2026-03-09", "Priya's birthday is on the fourteenth of July."),
    ("ev-05", "user", "2026-04-01", "I take my coffee black with no sugar, always."),
    ("ev-06", "user", "2026-04-01", "For tea I prefer oolong in the afternoon."),
    ("ev-07", "user", "2026-04-15", "The dentist appointment is booked for Thursday at nine in Ipswich."),
    ("ev-08", "user", "2026-04-15", "My car is a green Skoda Octavia estate."),
    ("ev-09", "user", "2026-05-03", "The project codename for the warehouse migration is Bluebell."),
    ("ev-10", "user", "2026-05-03", "Bluebell has to be finished before the September audit."),
    ("ev-11", "user", "2026-05-20", "My running target this year is a half marathon under two hours."),
    ("ev-12", "user", "2026-05-20", "I injured my left knee playing football in May."),
    ("ev-13", "user", "2026-06-11", "The spare key for the cottage is under the blue flowerpot."),
    ("ev-14", "user", "2026-06-11", "The cottage in Aldeburgh is rented for the first week of August."),
    ("ev-15", "user", "2026-07-02", "The mooring fee for Wren is 400 pounds a year."),
    ("ev-16", "user", "2026-07-09", "Correction: the mooring fee for Wren is now 520 pounds a year."),
    ("ev-17", "user", "2026-07-20", "Our cat Biscuit needs her flea treatment every month."),
    ("ev-18", "user", "2026-07-20", "The vet for Biscuit is Dr Okafor on Station Road."),
    ("ev-19", "user", "2026-08-05", "I am learning Portuguese with lessons on Tuesday evenings."),
    ("ev-20", "user", "2026-08-05", "My Portuguese teacher is called Marta."),
    ("ev-21", "friday", "2026-08-05", "Good luck with the lessons this week."),
    ("ev-22", "friday", "2026-04-15", "I have noted the appointment."),
    ("ev-23", "user", "2026-08-12", "Remind me to buy bread and milk on the way home."),
    ("ev-24", "user", "2026-08-12", "The football results this weekend were disappointing."),
]

#: Messages the owner corrected: they must never come back as evidence.
SUPERSEDED = ("ev-15",)

#: (question id, question, expected message ids, ids that must not appear).
QUERIES = [
    ("q01", "what is my boat called", ("ev-01",), ()),
    ("q02", "where does my sister live", ("ev-03",), ()),
    ("q03", "when is Priya's birthday", ("ev-04",), ()),
    ("q04", "how do I take my coffee", ("ev-05",), ()),
    ("q05", "when is the dentist appointment", ("ev-07",), ()),
    ("q06", "what car do I drive", ("ev-08",), ()),
    ("q07", "what is the codename of the warehouse project", ("ev-09",), ()),
    ("q08", "deadline for the Bluebell audit", ("ev-10",), ()),
    ("q09", "which knee did I injure", ("ev-12",), ()),
    ("q10", "where is the spare key for the cottage", ("ev-13",), ()),
    ("q11", "how much is the mooring fee for Wren", ("ev-16",), ("ev-15",)),
    ("q12", "who is the vet for Biscuit", ("ev-18",), ()),
    ("q13", "when are my Portuguese lessons", ("ev-19",), ()),
    ("q14", "my half marathon target", ("ev-11",), ()),
    ("q15", "which week is the Aldeburgh cottage rented", ("ev-14",), ()),
    ("q16", "what tea do I like in the afternoon", ("ev-06",), ()),
]


def _stopwords() -> frozenset:
    try:
        from agent_friday.conversation_memory import _STOPWORDS
        return frozenset(_STOPWORDS)
    except Exception:
        return frozenset()


_STOP = None


def _stem(w: str) -> str:
    w = w[:-2] if w.endswith("'s") else w
    for suf in ("ing", "ed", "es", "e", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def terms(text: Any) -> set:
    """The words a lexical score compares: lower-cased, stop words dropped,
    lightly stemmed."""
    global _STOP
    if _STOP is None:
        _STOP = _stopwords()
    out = set()
    for w in re.findall(r"[a-z0-9']+", str(text or "").lower()):
        w = w.strip("'")
        if len(w) < 3 or w in _STOP:
            continue
        out.add(_stem(w))
    return out


def _match(meta: Dict[str, Any], where: Optional[Dict[str, Any]]) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_match(meta, c) for c in where["$and"])
    for k, v in where.items():
        if isinstance(v, dict) and "$in" in v:
            if meta.get(k) not in v["$in"]:
                return False
        elif meta.get(k) != v:
            return False
    return True


class LexicalCollection:
    """Chroma's collection interface, as ConversationMemory uses it, scoring by
    word overlap (cosine over binary term sets). Deterministic: ties go to the
    earlier-stored row."""

    def __init__(self):
        self.rows: Dict[str, list] = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = terms((query_texts or [""])[0])
        scored = []
        for order, (i, (d, m)) in enumerate(self.rows.items()):
            if not _match(m, where):
                continue
            t = terms(d)
            sim = (len(q & t) / ((len(q) * len(t)) ** 0.5)) if q and t else 0.0
            scored.append((1.0 - sim, order, i, d, m))
        scored.sort(key=lambda s: (s[0], s[1]))
        top = scored[:max(1, int(n_results))]
        return {"ids": [[s[2] for s in top]], "documents": [[s[3] for s in top]],
                "metadatas": [[dict(s[4]) for s in top]], "distances": [[s[0] for s in top]]}

    def get(self, ids=None, include=None, where=None, limit=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else list(self.rows)
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


def _memory(collection, tmp: Path):
    from agent_friday.conversation_memory import ConversationMemory
    mem = ConversationMemory(persist_dir=tmp / "conv_mem")
    mem._collection = collection if collection is not None else LexicalCollection()
    mem._assert_embedding_space = lambda: None          # no model is loaded to check against
    mem._shared_query_ok = lambda: False               # the collection embeds (or scores) the query itself
    return mem


def run(k: int = 5, collection=None) -> Dict[str, Any]:
    """Index the synthetic set, ask every question, return the scores. The result
    holds question ids and ranks only, never the text."""
    k = max(1, int(k))
    with tempfile.TemporaryDirectory(prefix="memory_eval_") as tmp:
        mem = _memory(collection, Path(tmp))
        for mid, role, day, text in MESSAGES:
            mem.index(text, role, session_id=day, timestamp=day + "T10:00:00", msg_id=mid)
        # The corrected message keeps its row and loses its authority, as
        # ConversationMemory.supersede() leaves it, without touching the
        # full-text index on disk.
        got = mem._collection.get(ids=list(SUPERSEDED), include=["metadatas"])
        metas = []
        for m in got.get("metadatas") or []:
            m = dict(m or {})
            m.update({"superseded": "1", "superseded_reason": "corrected by the owner",
                      "superseded_at": "2026-07-09T10:00:00", "superseded_by": ""})
            metas.append(m)
        if got.get("ids"):
            mem._collection.update(ids=list(got["ids"]), metadatas=metas)

        per: List[Dict[str, Any]] = []
        for qid, question, expected, forbidden in QUERIES:
            ids = [h.get("id") for h in mem.search(question, n=k)]
            rank = next((i + 1 for i, x in enumerate(ids) if x in expected), None)
            per.append({"id": qid, "rank": rank, "returned": len(ids),
                        "superseded_leak": any(x in forbidden or x in SUPERSEDED for x in ids)})
    n = len(per)
    hits = sum(1 for p in per if p["rank"] is not None and p["rank"] <= k)
    mrr = sum(1.0 / p["rank"] for p in per if p["rank"]) / n if n else 0.0
    leaks = sum(1 for p in per if p["superseded_leak"])
    out = {"k": k, "queries": n, "messages": len(MESSAGES),
           "recall_at_k": round(hits / n, 4) if n else 0.0, "mrr": round(mrr, 4),
           "superseded_leaks": leaks, "per_query": per,
           "scorer": "lexical" if collection is None else type(collection).__name__}
    out["meets_floor"] = (out["recall_at_k"] >= FLOOR["recall_at_k"] and out["mrr"] >= FLOOR["mrr"]
                          and leaks <= FLOOR["superseded_leaks"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args(argv)
    res = run(k=a.k)
    print(json.dumps(res, indent=2))
    return 0 if res["meets_floor"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
