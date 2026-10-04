"""Library search: evidence first.

`search()` is a generator of events so the workspace can light the route as
each decision arrives; `run()` drains it for callers that want the result.

  question -> embed once + keyword search -> hint documents
  -> menus (folder, document, section) answered by the encoder, by Laya when it
     is unsure -> a beam of routes ranked by exp(mean log p) over the counted
     decisions -> passages ranked inside the sections reached
  -> low confidence widens, then searches every passage, then hands the brain
     the best snippets with an instruction to say what it could not find.

Nothing here calls a cloud model, and nothing here writes model text into the
index. The text returned is the owner's documents, labelled as data.
"""
from __future__ import annotations

import heapq
import itertools
import json
import math
import re
import time
import uuid
from pathlib import Path
from typing import Iterator

from agent_friday.services.library import embed, route, tree, versions
from agent_friday.services.library.store import OWNER, Store, VaultLocked, store_for

_STOP = frozenset("""a an and are as at be but by can did do does for from had has have how i if in into is it its me
my of on or our she so than that the their them then there these they this to us was we were what when where which
who whom why will with would you your about tell say said""".split())

_STATS = re.compile(r"\bhow many\s+(pages?|documents?|files?|words?|sections?|paragraphs?)\b", re.I)


def keywords(question: str) -> list[str]:
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]{1,}", question.lower())
    out = []
    for t in toks:
        t = t.strip("'-")
        if len(t) >= 3 and t not in _STOP and t not in out:
            out.append(t)
    return out[:12]


def fts_query(question: str) -> str | None:
    ks = keywords(question)
    if not ks:
        return None
    return " OR ".join('"%s"' % k.replace('"', "") for k in ks)


def fts_passages(store: Store, question: str, limit: int = 40) -> list[tuple[int, float]]:
    """(passage id, bm25) best first. Open-shelf passages only (vault text is not indexed)."""
    q = fts_query(question)
    if not q:
        return []
    try:
        rows = store.q("SELECT rowid, bm25(fts, 6.0, 3.0, 1.0) AS r FROM fts WHERE fts MATCH ? "
                       "ORDER BY r LIMIT ?", (q, limit))
    except Exception:
        return []
    return [(r["rowid"], float(r["r"])) for r in rows]


def coverage(question_keys: list[str], text: str) -> float:
    """The share of the question's distinctive words that a passage contains (by
    five-letter stem). A passage close in meaning that names none of them is a
    weak answer, and a question about something no document mentions finds none."""
    if not question_keys:
        return 1.0
    low = text.lower()
    hit = sum(1 for k in question_keys if k[:5] in low)
    return hit / len(question_keys)


def _rrf(*rankings: list, k: int = 60) -> dict:
    score: dict = {}
    for rk in rankings:
        for i, item in enumerate(rk):
            score[item] = score.get(item, 0.0) + 1.0 / (k + i + 1)
    return score


class _Ctx:
    def __init__(self, store: Store, principal: str, question: str, cfg: dict, scope: str | None):
        self.store, self.principal, self.question, self.cfg = store, principal, question, cfg
        self.tb = tree.TreeBuilder(store, principal)
        self.qvec = None
        if embed.available():
            v = embed.embed([question[:600]])
            self.qvec = v[0] if v is not None else None
        self.menus = 0
        self.laya_calls = 0
        self.laya_answered = False          # Laya actually answered something (it may be asked and not loaded)
        self.decisions: list[dict] = []
        self.cands: dict[int, dict] = {}        # passage id -> candidate
        self.seen_sections: set = set()
        if scope:
            s = scope.lower()
            vis = self.tb.visible_docs()
            self.tb._visible = {i: r for i, r in vis.items()
                                if s in (r["title"] or "").lower() or s in str(r["path"]).lower()}
        self.floor_tier = False
        self.keys = keywords(question)

    # -- the passage step -----------------------------------------------------

    def passages_of(self, section_id: int, doc, route_score: float) -> list[dict]:
        rows = self.store.q("SELECT id, block_ids, text FROM passages WHERE section_id=?", (section_id,))
        if not rows:
            return []
        import numpy as np
        vecs = None
        if self.qvec is not None:
            vr = self.store.q("SELECT node_id, vec FROM vectors WHERE node_kind='passage' AND doc_id=?", (doc["id"],))
            vm = {r["node_id"]: i for i, r in enumerate(vr)}
            mat = embed.from_blobs([r["vec"] for r in vr]) if vr else None
            vecs = (vm, mat)
        out = []
        for r in rows:
            if vecs and vecs[1] is not None and r["id"] in vecs[0]:
                sim = float(np.dot(self.qvec, vecs[1][vecs[0][r["id"]]]))
            else:
                sim = 0.0
            cov = self.cover(r["text"], doc["shelf"])
            out.append({"id": r["id"], "doc_id": doc["id"], "section_id": section_id, "score": sim * (0.6 + 0.4 * cov),
                        "cover": cov, "route": route_score, "block_ids": r["block_ids"], "raw": r["text"],
                        "shelf": doc["shelf"]})
        out.sort(key=lambda c: -c["score"])
        return out[:3]

    def cover(self, raw: str, shelf: str) -> float:
        try:
            return coverage(self.keys, self.store.dec(raw, shelf))
        except VaultLocked:
            return 0.0

    def judge_with_laya(self, cands: list[dict]) -> None:
        """Ask Laya whether each of the top passages answers the question, when
        the encoder is unsure and Laya's budget lasts."""
        for c in cands:
            if self.laya_calls >= self.cfg["max_laya"] or c["score"] >= self.cfg["p_strong"]:
                return
            try:
                text = self.store.dec(c["raw"], c["shelf"])
            except VaultLocked:
                continue
            p = route.laya_holds(self.question, text, self.cfg)
            self.laya_calls += 1
            if p is None:
                return
            c["noul"] = p
            self.laya_answered = True
            if p >= self.cfg["noul_stop"]:
                c["score"] = max(c["score"], 0.9)
            elif p >= self.cfg["noul_hold"]:
                c["score"] += 0.15
            elif p < 0.2:
                c["score"] -= 0.15

    def take(self, cands: list[dict]) -> None:
        for c in cands:
            old = self.cands.get(c["id"])
            if old is None or c["score"] + c["route"] * 0.25 > old["score"] + old["route"] * 0.25:
                self.cands[c["id"]] = c

    def best(self) -> float:
        return max((c["score"] for c in self.cands.values()), default=0.0)


def _doc_row(ctx: _Ctx, doc_id: int):
    return ctx.tb.visible_docs().get(doc_id)


def _decide(ctx: _Ctx, options: list[route.Node], level: int) -> route.Answer:
    kind = "group" if options[0].kind == "group" else options[0].kind
    a = route.answer_e(ctx.qvec, options, kind, ctx.cfg)
    if not route.e_is_sure(a, ctx.cfg) and ctx.laya_calls < ctx.cfg["max_laya"]:
        la = route.answer_l(ctx.question, options, ctx.cfg)
        ctx.laya_calls += 1
        if la is not None:
            a = la
            ctx.laya_answered = True
    return a


def _search_routes(ctx: _Ctx, hints: list[int]) -> Iterator[dict]:
    cfg = ctx.cfg
    beam = cfg["beam_floor"] if ctx.floor_tier else cfg["beam"]
    counter = itertools.count()
    frontier: list = []            # (-score, n, node, logps)
    parked: list = []
    ctx.parked = parked

    def score_of(logps):
        return math.exp(sum(logps) / len(logps)) if logps else 1.0

    def push(node, logps):
        heapq.heappush(frontier, (-score_of(logps), next(counter), node, logps))

    root = ctx.tb.root_children(hints=hints)
    if not root:
        return
    if len(root) == 1:
        push(root[0], [])
    else:
        a = _decide(ctx, root, 1)
        ctx.menus += 1
        yield from _apply(ctx, root, a, [], 1, push, parked, beam)
    level_of: dict = {}
    while frontier and ctx.menus < cfg["max_menus"]:
        negscore, _, node, logps = heapq.heappop(frontier)
        rs = -negscore
        lvl = level_of.get(node.key, len(logps) + 1)
        if node.kind == "section" and node.key not in ctx.seen_sections:
            ctx.seen_sections.add(node.key)
            doc = _doc_row(ctx, node.doc_id)
            if doc is not None:
                cs = ctx.passages_of(node.id, doc, rs)
                if cs and cs[0]["score"] < cfg["p_strong"]:
                    ctx.judge_with_laya(cs)
                    cs.sort(key=lambda c: -c["score"])
                ctx.take(cs)
                if cs and cs[0]["score"] >= cfg["p_strong"] and rs >= 0.4:
                    return                                    # one strong passage on a sure route
        kids = node.children() if node.children else []
        if node.kind == "document" and not kids:
            continue
        if not kids:
            continue
        if len(kids) == 1:
            level_of[kids[0].key] = lvl                       # a single child is free: no menu, no decision
            push(kids[0], logps)
            continue
        a = _decide(ctx, kids, lvl + 1)
        ctx.menus += 1
        yield from _apply(ctx, kids, a, logps, lvl + 1, push, parked, beam)


def _apply(ctx: _Ctx, options: list[route.Node], a: route.Answer, logps: list, level: int, push, parked, beam: int):
    cfg = ctx.cfg
    ranked = sorted(((a.probs.get(route.KEYS[i], 0.0), i) for i in range(len(options))), reverse=True)
    if a.top == route.ZERO and a.conf >= cfg["act_conf"]:
        take = []                                              # "none of these" is the sure answer here
    elif route.acts(a, cfg):
        take = ranked[:1]
        for p, i in ranked[1:3]:
            if p >= 0.08:
                parked.append((p, options[i], logps + [max(p, 1e-6)]))
    else:
        take = [(p, i) for p, i in ranked[:beam] if p >= 0.04]
        for p, i in ranked[beam:beam + 2]:
            if p >= 0.03:
                parked.append((p, options[i], logps + [max(p, 1e-6)]))
    for p, i in take:
        node = options[i]
        ev = {"event": "decision", "level": level, "kind": node.kind, "node_id": node.id,
              "doc_id": node.doc_id, "title": node.extra.get("title") or node.label[:60], "p": round(p, 4),
              "answerer": a.answerer, "hint": node.hint}
        ctx.decisions.append({k: ev[k] for k in ("level", "kind", "node_id", "p", "answerer")})
        yield ev
        push(node, logps + [max(p, 1e-6)])


# ── the fallbacks ────────────────────────────────────────────────────────────

def _widen(ctx: _Ctx, push_back) -> None:
    parked = getattr(ctx, "parked", [])
    parked.sort(key=lambda t: -t[0])
    while parked and ctx.menus < ctx.cfg["max_menus"] and ctx.best() < ctx.cfg["p_weak"] + 0.1:
        _p, node, logps = parked.pop(0)
        if node.kind == "section" and node.key not in ctx.seen_sections:
            ctx.seen_sections.add(node.key)
            doc = _doc_row(ctx, node.doc_id)
            if doc is not None:
                cs = ctx.passages_of(node.id, doc, math.exp(sum(logps) / len(logps)))
                ctx.take(cs)
        elif node.children:
            for kid in (node.children() or [])[:route.MAX_MENU]:
                if kid.kind == "section" and kid.key not in ctx.seen_sections:
                    ctx.seen_sections.add(kid.key)
                    doc = _doc_row(ctx, kid.doc_id)
                    if doc is not None:
                        ctx.take(ctx.passages_of(kid.id, doc, 0.3))
        ctx.menus += 1


def _full_search(ctx: _Ctx, limit: int = 12) -> None:
    """Every passage the principal may see: keyword rank fused with similarity."""
    vis = ctx.tb.visible_docs()
    fts = [pid for pid, _ in fts_passages(ctx.store, ctx.question, 60)]
    sims: list[tuple[int, float, int]] = []
    if ctx.qvec is not None:
        import numpy as np
        rows = ctx.store.q("SELECT node_id, doc_id, vec FROM vectors WHERE node_kind='passage'")
        rows = [r for r in rows if r["doc_id"] in vis]
        if rows:
            mat = embed.from_blobs([r["vec"] for r in rows])
            s = mat @ ctx.qvec
            order = np.argsort(-s)[:60]
            sims = [(rows[i]["node_id"], float(s[i]), rows[i]["doc_id"]) for i in order]
    fused = _rrf(fts, [p for p, _s, _d in sims])
    simmap = {p: s for p, s, _d in sims}
    top = sorted(fused, key=lambda p: -fused[p])[:limit]
    for rank, pid in enumerate(top):
        r = ctx.store.one("SELECT id, doc_id, section_id, block_ids, text FROM passages WHERE id=?", (pid,))
        if not r or r["doc_id"] not in vis:
            continue
        doc = vis[r["doc_id"]]
        score = simmap.get(pid)
        if ctx.qvec is None:
            score = max(0.3, 0.55 - 0.03 * rank) if pid in fts else 0.0     # keyword rank stands in for similarity
        elif score is None:
            v = ctx.store.one("SELECT vec FROM vectors WHERE node_kind='passage' AND node_id=?", (pid,))
            score = float(embed.from_blobs([v["vec"]])[0] @ ctx.qvec) if v else 0.0
        cov = ctx.cover(r["text"], doc["shelf"])
        ctx.take([{"id": pid, "doc_id": doc["id"], "section_id": r["section_id"],
                   "score": (score or 0.0) * (0.6 + 0.4 * cov), "cover": cov,
                   "route": 0.3, "block_ids": r["block_ids"], "raw": r["text"], "shelf": doc["shelf"]}])


# ── evidence ─────────────────────────────────────────────────────────────────

def _best_block(store: Store, block_ids: list[int], shelf: str, question: str):
    ks = set(keywords(question))
    best, best_n = None, -1
    for bid in block_ids:
        row = store.one("SELECT id, ord, page, kind, text FROM blocks WHERE id=?", (bid,))
        if not row:
            continue
        try:
            t = store.dec(row["text"], shelf).lower()
        except VaultLocked:
            continue
        n = sum(1 for k in ks if k in t)
        if n > best_n:
            best, best_n = row, n
    return best


def _para_number(store: Store, row) -> int:
    if row["page"] is not None:
        r = store.one("SELECT count(*) n FROM blocks WHERE doc_id=(SELECT doc_id FROM blocks WHERE id=?) "
                      "AND page=? AND ord<=? AND kind NOT IN ('heading')", (row["id"], row["page"], row["ord"]))
    else:
        r = store.one("SELECT count(*) n FROM blocks WHERE doc_id=(SELECT doc_id FROM blocks WHERE id=?) "
                      "AND ord<=? AND kind NOT IN ('heading')", (row["id"], row["ord"]))
    return int(r["n"]) if r else 1


def _block_time(store: Store, blk):
    if not blk:
        return None
    r = store.one("SELECT t_start FROM blocks WHERE id=?", (blk["id"],))
    return r["t_start"] if r else None


def confidence_word(score: float, cfg: dict) -> str:
    return "sure" if score >= cfg["p_strong"] else "fairly sure" if score >= cfg["p_weak"] + 0.1 else "a guess"


def build_evidence(ctx: _Ctx, limit: int, max_chars: int) -> list[dict]:
    ranked = sorted(ctx.cands.values(), key=lambda c: -(0.75 * c["score"] + 0.25 * c["route"]))
    doc_label: dict[int, int] = {}
    per_doc: dict[int, int] = {}
    out, chars = [], 0
    for c in ranked:
        if len(out) >= limit:
            break
        doc = ctx.tb.visible_docs().get(c["doc_id"])
        if doc is None:
            continue
        try:
            text = ctx.store.dec(c["raw"], c["shelf"])
        except VaultLocked:
            continue
        if out and chars + len(text) > max_chars:
            continue
        blk = _best_block(ctx.store, json.loads(c["block_ids"] or "[]"), c["shelf"], ctx.question)
        d = doc_label.setdefault(doc["id"], len(doc_label) + 1)
        per_doc[doc["id"]] = per_doc.get(doc["id"], 0) + 1
        out.append({
            "label": f"{d}.{per_doc[doc['id']]}", "doc": doc["title"], "doc_id": doc["id"],
            "page": blk["page"] if blk else None, "para": _para_number(ctx.store, blk) if blk else None,
            "t_start": _block_time(ctx.store, blk), 
            "text": text, "ref": f"lib:{doc['id']}#{blk['id']}" if blk else None,
            "block_id": blk["id"] if blk else None, "passage_id": c["id"], "section_id": c["section_id"],
            "score": round(c["score"], 3), "sure": confidence_word(c["score"], ctx.cfg),
        })
        chars += len(text)
    return out


def _stats_answer(ctx: _Ctx) -> dict | None:
    m = _STATS.search(ctx.question)
    if not m:
        return None
    unit = m.group(1).lower().rstrip("s")
    vis = ctx.tb.visible_docs()
    ql = ctx.question.lower()
    named = [r for r in vis.values() if r["title"] and r["title"].lower() in ql]
    if unit == "document" or unit == "file":
        return {"answer": f"{len(vis)} documents are in your Library.", "method": "counted from the index"}
    if named:
        r = named[0]
        if unit == "page" and r["pages"]:
            return {"answer": f"{r['title']} has {r['pages']} pages.", "method": "counted from the file"}
        if unit == "section":
            n = ctx.store.one("SELECT count(*) n FROM sections WHERE doc_id=?", (r["id"],))["n"]
            return {"answer": f"{r['title']} has {n} sections.", "method": "counted from its headings"}
    if unit == "page":
        total = sum(r["pages"] or 0 for r in vis.values())
        return {"answer": f"Your Library holds {total} pages in PDFs.", "method": "summed from the index"}
    return None


# ── the last result, for "next passage" and "show me that" (memory only) ────

_LAST: dict[str, dict] = {}


def remember(principal: str, evidence: list[dict]) -> None:
    _LAST[principal] = {"evidence": [dict(e) for e in evidence], "cursor": 0, "at": time.time()}


def last_result(principal: str) -> dict | None:
    return _LAST.get(principal)


# ── entry points ─────────────────────────────────────────────────────────────

def search(question: str, *, principal: str | None = None, scope: str | None = None,
           max_passages: int | None = None, floor_tier: bool = False) -> Iterator[dict]:
    """Yield decision events, then one `evidence` event, then `done`."""
    t0 = time.monotonic()
    principal = principal or OWNER
    question = (question or "").strip()
    if not question:
        yield {"event": "failed", "error": "ask a question"}
        return
    store = store_for(principal)
    from agent_friday.services.library import shelf as lshelf, grants
    lshelf.attach(store)
    cfg = route.config()
    if floor_tier:
        cfg["max_chars"] = 6000
    else:
        cfg["max_passages"], cfg["max_chars"] = 12, 12000
    ctx = _Ctx(store, principal, question, cfg, scope)
    ctx.floor_tier = floor_tier
    vis = ctx.tb.visible_docs()
    counts = store.counts()
    notes = []
    if grants.suspended():
        yield {"event": "failed", "error": "The Library is paused: the permissions ledger could not be verified."}
        return
    if ctx.tb.locked:
        notes.append(f"{ctx.tb.locked} document{'s' if ctx.tb.locked != 1 else ''} on your vault shelf "
                     f"{'are' if ctx.tb.locked != 1 else 'is'} locked.")
    if counts["queued"]:
        notes.append(f"The Library is still reading {counts['queued']} documents; results may be incomplete.")
    if not vis:
        yield {"event": "evidence", "evidence": [], "searched": {"folders": 0, "documents": 0, "fallback": "none"},
               "notes": notes + ["Your Library has no readable documents yet."]}
        yield {"event": "done", "result": _result([], 0, 0, "none", notes, {}, [])}
        return
    stat = _stats_answer(ctx)
    if stat:
        yield {"event": "evidence", "evidence": [], "searched": {"folders": 0, "documents": len(vis), "fallback": "none"},
               "stats": stat, "notes": notes}
        yield {"event": "done", "result": _result([], 0, len(vis), "none", notes, {}, [], stats=stat)}
        return
    fallback = "none"
    if ctx.qvec is None:
        fallback = "keyword"
        notes.append("Keyword search only: the sentence encoder is not available.")
        _full_search(ctx)
    else:
        hints = _hints(ctx)
        yield from _search_routes(ctx, hints)
        if ctx.best() < cfg["p_weak"]:
            _widen(ctx, None)
            fallback = "widened"
        if ctx.best() < cfg["p_weak"]:
            _full_search(ctx)
            fallback = "full"
        if ctx.best() < cfg["p_weak"]:
            fallback = "brain"          # the best snippets go to the brain, which says what it could not find
    ev = build_evidence(ctx, max_passages or cfg["max_passages"], cfg["max_chars"])
    searched = {"folders": ctx.store.q("SELECT count(*) n FROM folders")[0]["n"], "documents": len(vis),
                "fallback": fallback}
    remember(principal, ev)
    yield {"event": "evidence", "evidence": ev, "searched": searched, "notes": notes}
    stamp = versions.stamp(laya_used=ctx.laya_answered,
                           cfg=ctx.cfg)
    receipt = {"stamp": stamp, "menus": ctx.decisions, "fallback": fallback, "laya_calls": ctx.laya_calls,
               "passages": [{"id": e["passage_id"], "score": e["score"], "rank": i + 1} for i, e in enumerate(ev)],
               "ms": round((time.monotonic() - t0) * 1000)}
    sid = uuid.uuid4().hex[:12]
    store.add_receipt(sid, receipt)
    yield {"event": "done", "result": _result(ev, len(ctx.decisions), len(vis), fallback, notes, receipt, ev, sid=sid,
                                              stamp=stamp)}


def _hints(ctx: _Ctx) -> list[int]:
    """Up to eight documents proposed by title, heading and passage keywords fused
    with encoder similarity. They are extra options; routing still decides."""
    vis = ctx.tb.visible_docs()
    bm = []
    for pid, _ in fts_passages(ctx.store, ctx.question, 40):
        r = ctx.store.one("SELECT doc_id FROM passages WHERE id=?", (pid,))
        if r and r["doc_id"] in vis and r["doc_id"] not in bm:
            bm.append(r["doc_id"])
    sims: list[int] = []
    if ctx.qvec is not None and vis:
        rows = [(i, ctx.tb.vec("document", i)) for i in vis]
        scored = sorted(((float(ctx.qvec @ v), i) for i, v in rows if v is not None), reverse=True)
        sims = [i for _s, i in scored[:20]]
    fused = _rrf(bm, sims)
    return sorted(fused, key=lambda d: -fused[d])[:8]


def _result(evidence, menus, docs, fallback, notes, receipt, ev_full, *, sid=None, stats=None, stamp=None) -> dict:
    out = {"evidence": evidence, "searched": {"documents": docs, "menus": menus, "fallback": fallback},
           "notes": notes, "receipt": sid, "stamp": stamp or versions.stamp()}
    if stats:
        out["stats"] = stats
    return out


def run(question: str, **kw) -> dict:
    """Drain `search()`: the final result, plus the decision events."""
    events, result = [], None
    for ev in search(question, **kw):
        if ev["event"] == "done":
            result = ev["result"]
        elif ev["event"] == "failed":
            return {"evidence": [], "error": ev["error"], "searched": {}, "notes": []}
        else:
            events.append(ev)
    result = result or {"evidence": [], "searched": {}, "notes": []}
    result["events"] = events
    return result
