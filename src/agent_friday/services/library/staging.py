"""Drafts from the local model, kept in staging until you review them.

After a folder's passages are in and Laya has noted what each document is, the local model may read the
documents and propose a short summary and a few facts for each. They are written to the staging area of
the wiki, `~/.friday/wiki/_staging/` (First Run spec section 7), one JSON file per proposed item, and
nowhere else: the wiki's pages, the knowledge graph and the Library's own index are not touched. The graph
builder already skips `_staging`. Publishing is the owner's decision, through review: an item is `proposed`
until you accept, reject or defer it, and nothing here writes a page.

Rules the code keeps:

* **A fact without a span is refused.** Every fact, and every summary, carries the exact sentence it came
  from, copied from the document's blocks and checked here against the stored text; the block it sits in is
  recorded. A quote that is not in the document, or is too short or too long to mean anything, drops the
  item. A summary needs at least one verified span.
* **Provenance on every item**: the Library document, its hash, the model that wrote the draft, the
  extractor version, `authority: model-inferred`, the file's own modified time, and the document's
  sensitivity as Laya noted it.
* **Only what is safe to read with a model**: open-shelf documents the classifier rates public. A vault
  document, a private one, a copy of another document, or a document already drafted from the same bytes
  is skipped. The text goes to the LOCAL model only (services/local_call); this module has no way to reach a
  cloud model, and its job waits for the Local AI queue like any other model job.
* **It follows your choices**: Settings > Library "learning" set to off means no drafts. With no local
  model installed it waits and says so.

Forgetting a document removes its drafts (`purge_document`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from agent_friday import paths
from agent_friday.services.library import store as lstore
from agent_friday.services.visible_jobs import visible_job

EXTRACTOR = "library-stage-1"
MAX_DOCS_PER_PASS = 300
MAX_CHARS = 4000          # what the sensitivity classifier reads (shelf.tier_of): the model reads no more
MAX_BLOCKS = 40
MAX_FACTS = 12
QUOTE_MIN, QUOTE_MAX = 12, 400
FIELD_MAX = 200
SUMMARY_MIN, SUMMARY_MAX = 20, 1200
STATUSES = ("proposed", "accepted", "rejected", "deferred")

SYSTEM = (
    "You read one document and propose notes for its owner to review. Reply with JSON only: "
    '{"summary": "two or three plain sentences", "evidence": ["a sentence copied exactly from the text"], '
    '"facts": [{"subject": "...", "predicate": "...", "object": "...", "quote": "a sentence copied exactly '
    'from the text"}]}. Every quote must be copied word for word from the text. A fact with no exact quote '
    "is not allowed. Do not add anything the text does not say. At most 12 facts.")

_lock = threading.RLock()


def staging_dir() -> Path:
    return paths.friday_home() / "wiki" / "_staging"


def _index_path() -> Path:
    return staging_dir() / "_library.json"


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def _read_index() -> dict:
    try:
        d = json.loads(_index_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _gone_ids(idx: dict | None = None) -> set:
    """Documents that were removed or forgotten. Ids are never reused (AUTOINCREMENT), so a draft that is
    finished after the forget is refused for good."""
    idx = _read_index() if idx is None else idx
    out = set()
    for x in idx.get("_gone") or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _is_meeting(row) -> bool:
    """Is this one of a meeting's archive documents (its transcript or notes)? The archive's yes is to search
    them: a draft for the wiki is never made from one, whatever else covers the path. Unreadable means yes."""
    try:
        from agent_friday.services import meeting_archive as ma
        return row["scope"] == ma.SCOPE or ma.is_archive_path(row["path"])
    except Exception:  # noqa: BLE001 - a row that cannot be read is not drafted from
        return True


def _still_ok(store, doc, principal: str, blocks=None) -> bool:
    """Is this document still one a draft may be made from? The row exists with the same bytes, indexed on the
    open shelf, the owner's consent still covers it (read live; an unverifiable ledger covers nothing), it is
    not forgotten, and its tier and copy marks still say public and original."""
    from agent_friday.services.library import grants
    try:
        if int(doc["id"]) in _gone_ids():
            return False
        row = store.get_document(doc["id"])
        if row is None or row["state"] != "indexed" or row["shelf"] != "open":
            return False
        if _is_meeting(row):
            return False
        if (row["sha256"] or "") != (doc["sha256"] or ""):
            return False
        if grants.suspended() or not grants.allowed(principal, Path(row["path"])):
            return False
        if row["sha256"] and store.tombstoned(row["sha256"], row["path"]):
            return False
        meta = store.get_doc_meta(doc["id"])
        if meta is None or meta["sensitivity"] is None or int(meta["sensitivity"]) > 1 or meta["dup_of"] is not None:
            return False
        # Forgetting a person deletes the passages and blocks that name them without touching the file's bytes:
        # a draft made from text that is no longer all there is dropped.
        for bid, text in blocks or ():
            r = store.one("SELECT text FROM blocks WHERE id=?", (bid,))
            if r is None or r["text"] != text:
                return False
        return True
    except Exception:  # noqa: BLE001 - a check that cannot run is not a pass
        return False


def _write_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# ── what a draft must be ─────────────────────────────────────────────────────

def find_span(blocks: list[tuple[int, str]], quote: str) -> int | None:
    """The id of the block whose text holds `quote` exactly (whitespace aside), or None."""
    q = _norm(quote).strip("\"'`")
    if not (QUOTE_MIN <= len(q) <= QUOTE_MAX):
        return None
    for bid, text in blocks:
        if q in _norm(text):
            return bid
    return None


def check_draft(raw: dict, blocks: list[tuple[int, str]]) -> dict:
    """The parts of a model's reply that stand: {"summary": (text, [spans]) | None, "facts": [...]}.
    Anything whose quote is not in the document is dropped."""
    out = {"summary": None, "facts": []}
    if not isinstance(raw, dict):
        return out
    seen = set()
    for f in (raw.get("facts") or [])[:MAX_FACTS * 2]:
        if not isinstance(f, dict):
            continue
        s, p, o = (_norm(f.get(k)) for k in ("subject", "predicate", "object"))
        if not (s and p and o) or max(len(s), len(p), len(o)) > FIELD_MAX:
            continue
        bid = find_span(blocks, f.get("quote"))
        key = (s.lower(), p.lower(), o.lower())
        if bid is None or key in seen:
            continue
        seen.add(key)
        out["facts"].append({"subject": s, "predicate": p, "object": o, "quote": _norm(f.get("quote")).strip("\"'`"),
                             "block_id": bid})
        if len(out["facts"]) >= MAX_FACTS:
            break
    summary = _norm(raw.get("summary"))
    if SUMMARY_MIN <= len(summary) <= SUMMARY_MAX:
        spans = []
        for q in (raw.get("evidence") or [])[:6]:
            bid = find_span(blocks, q)
            if bid is not None:
                spans.append({"quote": _norm(q).strip("\"'`"), "block_id": bid})
        if spans:
            out["summary"] = (summary, spans)
    return out


def _provenance(doc, meta, model: str) -> dict:
    return {"source": "library", "doc_id": doc["id"], "source_path": doc["path"], "title": doc["title"],
            "sha256": doc["sha256"], "scope": doc["scope"], "model": model, "extractor": EXTRACTOR,
            "authority": "model-inferred", "observed_at": doc["mtime"], "created_ts": time.time(),
            "valid_from": None, "valid_until": None, "confidence": None,
            "sensitivity": (meta["sensitivity"] if meta is not None else None)}


def _item_id(doc, kind: str, n: int = 0) -> str:
    return "lib-%d-%s-%s%s" % (doc["id"], (doc["sha256"] or "nohash")[:8], kind, ("-%d" % n) if n else "")


def write_items(doc, meta, model: str, checked: dict) -> list[str]:
    """Write the surviving items of one document to staging. Returns their ids."""
    ids = []
    prov = _provenance(doc, meta, model)
    base = staging_dir()
    with _lock:
        if int(doc["id"]) in _gone_ids():
            return []                                   # forgotten while the model was reading
        if checked["summary"]:
            text, spans = checked["summary"]
            iid = _item_id(doc, "summary")
            if _rejected(base / (iid + ".json")):
                checked = dict(checked, summary=None)
        if checked["summary"]:
            text, spans = checked["summary"]
            iid = _item_id(doc, "summary")
            _write_atomic(base / (iid + ".json"), {
                "schema": 1, "id": iid, "kind": "summary", "status": "proposed", "text": text,
                "citations": [dict(s, doc_id=doc["id"], title=doc["title"]) for s in spans], "provenance": prov})
            ids.append(iid)
        for i, f in enumerate(checked["facts"], 1):
            iid = _item_id(doc, "fact", i)
            if _rejected(base / (iid + ".json")):
                continue                                # the owner said no to this one; it is not proposed again
            _write_atomic(base / (iid + ".json"), {
                "schema": 1, "id": iid, "kind": "fact", "status": "proposed", "subject": f["subject"],
                "predicate": f["predicate"], "object": f["object"],
                "citations": [{"quote": f["quote"], "block_id": f["block_id"], "doc_id": doc["id"],
                               "title": doc["title"]}], "provenance": prov})
            ids.append(iid)
    return ids


# ── review ───────────────────────────────────────────────────────────────────

def _rejected(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("status") == "rejected"
    except (OSError, ValueError):
        return False


def _item_files():
    d = staging_dir()
    return sorted(p for p in d.glob("lib-*.json")) if d.is_dir() else []


def list_items(status: str | None = None, doc_id: int | None = None) -> list[dict]:
    out = []
    for p in _item_files():
        try:
            it = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if status and it.get("status") != status:
            continue
        if doc_id is not None and (it.get("provenance") or {}).get("doc_id") != doc_id:
            continue
        out.append(it)
    return out


def counts() -> dict:
    c = {s: 0 for s in STATUSES}
    for it in list_items():
        c[it.get("status") if it.get("status") in c else "proposed"] += 1
    return c


def review(item_id: str, decision: str) -> dict:
    """Accept, reject or defer one draft. Accepting marks it ready: it does not write a wiki page, and
    nothing else in Friday publishes a draft. Rejecting keeps a tombstone with none of the draft's text, so
    the same draft is not proposed again."""
    decision = str(decision or "").lower()
    status = {"accept": "accepted", "reject": "rejected", "defer": "deferred"}.get(decision)
    if status is None or not re.fullmatch(r"lib-[0-9]+-[0-9a-z]+-(summary|fact)(-[0-9]+)?", str(item_id or "")):
        return {"ok": False, "error": "say accept, reject or defer, for one of the drafts"}
    # The id's shape already keeps it a plain file name; the resolved path is checked against the
    # resolved staging folder as well, before anything is read or written.
    root = os.path.realpath(staging_dir())
    full = os.path.realpath(os.path.join(root, item_id + ".json"))
    if not full.startswith(root + os.sep):
        return {"ok": False, "error": "say accept, reject or defer, for one of the drafts"}
    p = Path(full)
    with _lock:
        try:
            it = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "error": "that draft is not in staging any more"}
        if status == "rejected":
            prov = it.get("provenance") or {}
            _write_atomic(p, {"schema": 1, "id": item_id, "kind": it.get("kind"), "status": "rejected",
                              "reviewed_ts": time.time(),
                              "provenance": {"doc_id": prov.get("doc_id"), "sha256": prov.get("sha256")}})
            return {"ok": True, "status": status}
        it["status"] = status
        it["reviewed_ts"] = time.time()
        _write_atomic(p, it)
    return {"ok": True, "status": status}


def purge_drafts(doc_id: int) -> int:
    """Remove the drafts made from a document's earlier text or shelf; the document may be drafted again."""
    return purge_document(doc_id, gone=False)


def naming(names) -> list:
    """Staged items that name a person (a fact's subject or object, a summary, a quoted sentence)."""
    needles = {str(n).strip().lower() for n in names if len(str(n).strip()) >= 3}
    out = []
    if not needles:
        return out
    for p in _item_files():
        try:
            it = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        parts = [it.get("subject"), it.get("predicate"), it.get("object"), it.get("text")]
        for c in it.get("citations") or []:
            parts += [c.get("quote"), c.get("title")]
        blob = " ".join(str(x) for x in parts if x).lower()
        if any(n in blob for n in needles):
            out.append(p)
    return out


def purge_naming(names) -> int:
    """Delete the staged items that name a person (forget-person). The 'drafted' note stays, so the
    document is not drafted from again."""
    n = 0
    with _lock:
        for p in naming(names):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def purge_document(doc_id: int, gone: bool = True) -> int:
    """Remove every draft made from a document (it was removed or forgotten), and its 'drafted' note.
    `gone` also tombstones the id, so a draft still being made when the document was forgotten is refused."""
    n = 0
    with _lock:
        for p in _item_files():
            if p.name.startswith("lib-%d-" % int(doc_id)):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        idx = _read_index()
        changed = idx.pop(str(int(doc_id)), None) is not None
        if gone and int(doc_id) not in _gone_ids(idx):
            idx["_gone"] = sorted(_gone_ids(idx) | {int(doc_id)})
            changed = True
        if changed:
            try:
                _write_atomic(_index_path(), idx)
            except OSError:
                pass
    return n


# ── the drafting job ─────────────────────────────────────────────────────────

def _learning_off() -> bool:
    try:
        from agent_friday.core import _load_settings
        return str((_load_settings() or {}).get("library_kg_learn") or "") == "off"
    except Exception:  # noqa: BLE001
        return False


def _local_model() -> str | None:
    try:
        from agent_friday.services.knowledge_graph import indexer as kg
        return kg._available_local_model()
    except Exception:  # noqa: BLE001
        return None


def eligible(store, scope: str | None = None) -> list:
    """Documents worth drafting from: open shelf, rated public, not a copy, not drafted from these bytes."""
    seen = _read_index()
    sql = ("SELECT d.* FROM documents d JOIN doc_meta m ON m.doc_id=d.id WHERE d.state='indexed' AND "
           "d.shelf='open' AND m.sensitivity<=1 AND m.dup_of IS NULL")
    args: list = []
    if scope is not None:
        sql += " AND d.scope=?"
        args.append(scope)
    rows = store.q(sql + " ORDER BY d.mtime DESC", args)
    return [d for d in rows if not _is_meeting(d) and seen.get(str(d["id"])) != (d["sha256"] or "")][:MAX_DOCS_PER_PASS]


def _blocks(store, doc) -> list[tuple[int, str]]:
    return [(r["id"], r["text"]) for r in store.q(
        "SELECT id, text FROM blocks WHERE doc_id=? ORDER BY ord LIMIT %d" % MAX_BLOCKS, (doc["id"],))]


def _prompt_text(blocks: list[tuple[int, str]]) -> str:
    """Exactly what the sensitivity classifier read (shelf.tier_of): the raw blocks joined by newlines, cut at
    MAX_CHARS, before any whitespace is collapsed."""
    return "\n".join(text for _bid, text in blocks)[:MAX_CHARS]


def draft_document(store, doc, model: str, complete=None, principal: str = lstore.OWNER) -> list[str]:
    """Ask the local model about one document; stage what survives the span check. `complete(system, user,
    model)` is the model call (local_call.call_json by default)."""
    if _is_meeting(doc):
        return []                          # a meeting's words are never read by a drafting model
    if complete is None:
        from agent_friday.services import local_call
        complete = lambda system, user, m: local_call.call_json(system, user, m, max_tokens=1500)  # noqa: E731
    blocks = _blocks(store, doc)
    if not blocks:
        return []
    raw = complete(SYSTEM, _prompt_text(blocks), model)
    checked = check_draft(raw, blocks) if raw else {"summary": None, "facts": []}
    with _lock:
        # The model may have waited a long time for the owner to be away. Whatever happened meanwhile (a
        # removal, a forget, an edit, a withdrawn consent, a new tier) decides whether anything is written.
        if not _still_ok(store, doc, principal, blocks):
            return []
        return write_items(doc, store.get_doc_meta(doc["id"]), model, checked)


def run_pass(principal: str, scope: str, *, complete=None, model: str | None = None, before=None, progress=None) -> dict:
    """Draft from every eligible document of a folder. Returns {state, done, total, proposed}."""
    if _learning_off():
        return {"state": "off", "done": 0, "total": 0, "proposed": 0}
    model = model or _local_model()
    if not model:
        return {"state": "waiting_model", "done": 0, "total": 0, "proposed": 0}
    store = lstore.store_for(principal)
    docs = eligible(store, scope)
    done = proposed = 0
    for d in docs:
        if before is not None and before() == "hold":
            return {"state": "held", "done": done, "total": len(docs), "proposed": proposed}
        if not _still_ok(store, d, principal):
            continue                                   # no consent now, forgotten, changed: the model never reads it
        ids = draft_document(store, d, model, complete, principal)
        proposed += len(ids)
        done += 1
        with _lock:
            idx = _read_index()
            idx[str(d["id"])] = d["sha256"] or ""
            _write_atomic(_index_path(), idx)
        if progress is not None:
            progress(done, len(docs), proposed)
    return {"state": "done", "done": done, "total": len(docs), "proposed": proposed}


POLL_S = 2.0
_sleep = time.sleep


def holds(principal: str = lstore.OWNER) -> str:
    """Why drafting must wait right now, in words, or "": the owner's Pause, a recording meeting, off the
    record, or battery (the same holds that stop the Library reading a folder)."""
    try:
        from agent_friday.services.library import index_job
        if index_job.progress_for(principal).paused:
            return index_job.OWNER_HOLD
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent_friday.services import turn_queue as _tq
        why = _tq.background_pause_reason()
        if why:
            return "paused: " + why[:1].lower() + why[1:]
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent_friday.services.library import runtime
        ok, why = runtime.machine_is_free()
        if not ok:
            return "paused: " + why
    except Exception:  # noqa: BLE001
        pass
    return ""


def wait_for_holds(principal: str, vj=None) -> None:
    """Block between two documents while a hold stands; a Stop from the card ends the wait."""
    noted = False
    while True:
        why = holds(principal)
        if not why:
            return
        if vj is not None:
            if not noted:
                noted = True
                vj.log("Paused: " + why.replace("paused: ", ""))
            vj.check_stop()
        _sleep(POLL_S)


class Stager:
    """One worker per principal. It waits for the Library to finish reading, then drafts folder by folder as
    a Local AI queue job that is deferrable, so it runs only while the PC is idle and never during a chat."""

    def __init__(self, principal: str):
        self.principal = principal
        self._q: list[str] = []
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self.current: str | None = None

    def enqueue(self, scope: str) -> None:
        with self._cv:
            if scope not in self._q and scope != self.current:
                self._q.append(scope)
            self._cv.notify()
        self._start()

    def _start(self) -> None:
        with self._cv:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="library-staging", daemon=True)
            self._thread.start()

    def pending(self) -> int:
        with self._cv:
            return len(self._q) + (1 if self.current else 0)

    def _reading_done(self) -> bool:
        from agent_friday.services.library import runtime
        return runtime.indexer_for(self.principal).pending() == 0

    def _loop(self) -> None:
        from agent_friday.services.library import index_job
        while True:
            with self._cv:
                if not self._q:
                    self._cv.wait(timeout=30)
                    if not self._q:
                        return
                scope = self._q.pop(0)
                self.current = scope
            try:
                while not self._reading_done():
                    time.sleep(5)                       # passages first: drafting waits for the reading
                self._one(scope, index_job)
            except BaseException:  # noqa: BLE001 - a cancelled or failed pass ends this folder, not the worker
                pass
            finally:
                self.current = None

    def _one(self, scope: str, index_job) -> None:
        """Draft from one folder as ONE visible job (Activity names the local model, shows the steps and the
        trace, and gives its place in the Local AI queue). The job is deferrable: it waits for idle and never
        runs during a chat or a meeting."""
        out = self._draft_once(scope, index_job)
        while out.get("state") == "held":
            # The job ended and gave its queue slot back; nothing waits behind a paused draft. When the
            # hold is lifted a new job takes its place in line and carries on with the rest.
            wait_for_holds(self.principal)
            out = self._draft_once(scope, index_job)

    def _draft_once(self, scope: str, index_job) -> dict:
        pr = index_job.progress_for(self.principal)
        key = str(scope)
        rec = {k: v for k, v in pr.data["folders"].items() if index_job._scope_root(k) == key}
        name = (next(iter(rec.values()), {}) or {}).get("name") or "your folder"
        out = {"state": "waiting_model", "done": 0, "total": 0, "proposed": 0}
        # Nothing to draft (learning off, no local model, no eligible document) is not a job: no card.
        if _learning_off():
            out = {"state": "off", "done": 0, "total": 0, "proposed": 0}
        elif not _local_model():
            out = {"state": "waiting_model", "done": 0, "total": 0, "proposed": 0}
        elif not eligible(lstore.store_for(self.principal), scope):
            out = {"state": "done", "done": 0, "total": 0, "proposed": 0}
        else:
            out = None
        if out is not None:
            for k in rec:
                pr.set(k, stage=out)
            pr.save()
            return out

        with visible_job("Drafting notes from " + name, kind="library_stage", workspace="library",
                         deferrable=True, key="library_stage:" + key,
                         description="A local model proposes a summary and facts, with their sentences, "
                                     "for you to review.") as vj:
            def shown(done, total, proposed):
                for k in rec:
                    pr.set(k, stage={"state": "running", "done": done, "total": total, "proposed": proposed})
                vj.log("%d of %d documents drafted, %d proposed" % (done, total, proposed))
            def between():
                vj.check_stop()
                why = holds(self.principal)
                if why:
                    vj.log("Paused: " + why.replace("paused: ", "") + ". Giving the queue back until then.")
                    return "hold"
            out = run_pass(self.principal, scope, progress=shown, before=between)
            vj.set_result("%d of %d documents drafted, %d proposed" % (out["done"], out["total"], out["proposed"])
                          if out["state"] in ("done", "held") else "Nothing drafted (%s)" % out["state"])
        for k in rec:
            pr.set(k, stage=out)
        pr.save()
        return out


_stagers: dict[str, Stager] = {}


def stager_for(principal: str) -> Stager:
    with _lock:
        s = _stagers.get(principal)
        if s is None:
            s = _stagers[principal] = Stager(principal)
        return s


def enqueue(principal: str, scope: str) -> None:
    stager_for(principal).enqueue(scope)
