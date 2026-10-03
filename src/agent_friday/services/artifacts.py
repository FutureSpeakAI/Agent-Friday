"""Artifacts: what a model makes that is better seen than read.

The artifact panel beside every chat (docs/design/active/vibe-coding-salon.md
§4.2) shows things of a `kind`: a markdown draft, a table, a chart, a small
`html` app, a diff, an image or an svg. A model makes one with the
`artifact_put` tool; a model without reliable tool calls emits a fenced
```friday-artifact block instead, and `absorb_fenced` turns that into the
same call.

Three rules are load-bearing:

* **Every update is a new version; nothing is overwritten.** The panel's
  timeline scrubs through versions and "restore" is itself a new version.
* **A hand edit is a version authored by "you"**, and the model is shown it
  as a diff on its next turn (`context_block`), once, so it never edits over
  something the user changed without knowing.
* **Off the record, nothing is written.** `off_record.active()` is asked
  before every write, as every store asks it; the artifact then lives only in
  this process's memory and is gone when off-record ends.

Layout, one directory per artifact:

    ~/.friday/artifacts/<conversation_id>/<artifact_id>/
        artifact.json     index: kind, title, current version, what the model has seen
        v<N>.json         one version each, append-only

Written like conversations are (tmp + fsync + replace, plain JSON under the
Friday home): the conversation store is the precedent and it is not encrypted
at rest, so this is not either.

Every record carries the north star's artifact contract (§24.1, §30.14): id,
principal scope (the conversation), optional task and goal, version, sha256,
sensitivity, source refs, provenance manifest id, qa_status and created_at.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from agent_friday.paths import safe_name

#: The kinds the panel renders. `image` and `svg` are the spec's one row.
KINDS = ("markdown", "table", "chart", "html", "diff", "image", "svg")

#: The header of the block the model is shown each turn.
CONTEXT_HEADER = "== ARTIFACTS (this conversation's panel) =="

#: How many diff lines of a hand edit the model is shown.
_DIFF_LINES_SHOWN = 60

_LOCK = threading.RLock()

#: conversation id -> artifact id -> {"index": {...}, "versions": [records]}.
#: Only used off the record; cleared when off-record ends.
_OFF_MEMORY: dict = {}


def _root() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "artifacts"


def _settings(settings):
    if settings is not None:
        return settings
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _off(settings) -> bool:
    from agent_friday.services import off_record
    return off_record.active(_settings(settings))


def new_id() -> str:
    return "art-" + secrets.token_hex(4)


def _atomic_write(path: Path, text: str) -> None:
    """tmp + fsync + replace, the house pattern (services/conversations)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
    tmp.replace(path)


def _dir(cid: str, aid: str) -> Path:
    return _root() / safe_name(cid, what="conversation id") / safe_name(aid, what="artifact id")


# ── content shapes ──────────────────────────────────────────────────────────

def _normalise(kind: str, content):
    """One shape per kind, so every renderer and every diff sees the same thing."""
    if kind not in KINDS:
        raise ValueError("unknown artifact kind %r; one of %s" % (kind, ", ".join(KINDS)))
    if kind in ("markdown", "html", "diff", "svg"):
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ValueError("%s content must be text" % kind)
        return content
    if kind == "image":
        if isinstance(content, str):
            return {"src": content, "alt": ""}
        if isinstance(content, dict) and isinstance(content.get("src"), str):
            return {"src": content["src"], "alt": str(content.get("alt") or "")}
        raise ValueError("image content must be a data: URL or {src, alt}")
    if kind == "table":
        if not isinstance(content, dict):
            raise ValueError("table content must be {columns, rows}")
        rows = content.get("rows") or []
        cols = content.get("columns")
        if rows and isinstance(rows[0], dict):
            if not cols:
                cols = []
                for r in rows:
                    for k in r.keys():
                        if k not in cols:
                            cols.append(k)
            rows = [[r.get(c) for c in cols] for r in rows]
        cols = [str(c.get("name") if isinstance(c, dict) else c) for c in (cols or [])]
        if not cols and rows:
            cols = ["col%d" % (i + 1) for i in range(len(rows[0]))]
        out = {"columns": cols, "rows": [list(r) for r in rows]}
        for extra in ("title", "note", "types"):
            if content.get(extra) is not None:
                out[extra] = content[extra]
        return out
    if kind == "chart":
        if not isinstance(content, dict):
            raise ValueError("chart content must be a spec: {type, rows|series, x, y}")
        out = dict(content)
        out["type"] = str(out.get("type") or "bar").lower()
        return out
    raise ValueError("unknown artifact kind %r" % kind)  # pragma: no cover


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, indent=1, sort_keys=True, ensure_ascii=False, default=str)


def _sha(content) -> str:
    return hashlib.sha256(_text_of(content).encode("utf-8")).hexdigest()


def _meta_only(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "content"}


# ── the store ───────────────────────────────────────────────────────────────

def _read_index(cid: str, aid: str, settings) -> dict | None:
    if _off(settings):
        with _LOCK:
            ent = _OFF_MEMORY.get(cid, {}).get(aid)
            return dict(ent["index"]) if ent else None
    p = _dir(cid, aid) / "artifact.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_index(cid: str, aid: str, index: dict, settings) -> None:
    if _off(settings):
        with _LOCK:
            _OFF_MEMORY.setdefault(cid, {}).setdefault(aid, {"index": {}, "versions": []})["index"] = dict(index)
        return
    _atomic_write(_dir(cid, aid) / "artifact.json", json.dumps(index, indent=1, ensure_ascii=False))


def _read_version(cid: str, aid: str, n: int, settings) -> dict | None:
    if _off(settings):
        with _LOCK:
            ent = _OFF_MEMORY.get(cid, {}).get(aid)
            if not ent:
                return None
            for v in ent["versions"]:
                if v["version"] == n:
                    return dict(v)
            return None
    p = _dir(cid, aid) / ("v%d.json" % n)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def put(cid: str, kind: str, title: str, content, meta: dict | None = None,
        artifact_id: str | None = None, author: str = "friday",
        settings: dict | None = None, note: str | None = None,
        restored_from: int | None = None) -> dict:
    """Create an artifact, or add a version to one. Returns the new version."""
    cid = safe_name(cid, what="conversation id")
    meta = dict(meta or {})
    content = _normalise(kind, content)
    settings = _settings(settings)
    off = _off(settings)
    if off:
        from agent_friday.services import off_record
        off_record.skip("artifacts", settings)
    with _LOCK:
        if artifact_id:
            aid = safe_name(artifact_id, what="artifact id")
            index = _read_index(cid, aid, settings)
        else:
            aid, index = new_id(), None
        if index is None:
            index = {"id": aid, "conversation_id": cid, "kind": kind, "title": title,
                     "current": 0, "created_at": datetime.now().isoformat(timespec="seconds"),
                     "model_seen_version": 0}
        n = int(index.get("current") or 0) + 1
        now = time.time()
        rec = {
            "id": aid,
            "conversation_id": cid,
            "kind": kind,
            "title": str(title or index.get("title") or kind),
            "version": n,
            "content": content,
            "sha256": _sha(content),
            "author": author,
            "note": note or "",
            "restored_from": restored_from,
            "created_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "ts": now,
            # The north star's artifact contract (§24.1, §30.14).
            "task_id": meta.pop("task_id", None),
            "goal_id": meta.pop("goal_id", None),
            "sensitivity": meta.pop("sensitivity", None) or "unclassified",
            "source_refs": meta.pop("source_refs", None) or [],
            "provenance_id": meta.pop("provenance_id", None),
            "qa_status": meta.pop("qa_status", None) or "unreviewed",
            "meta": meta,
            "off_record": bool(off),
        }
        index.update({"kind": kind, "title": rec["title"], "current": n,
                      "updated_at": rec["created_at"], "last_author": author})
        if author != "you" and int(index.get("model_seen_version") or 0) == n - 1:
            # The model wrote this version itself, so it has "seen" it.
            index["model_seen_version"] = n
        if off:
            ent = _OFF_MEMORY.setdefault(cid, {}).setdefault(aid, {"index": {}, "versions": []})
            ent["versions"].append(dict(rec))
            ent["index"] = dict(index)
        else:
            _atomic_write(_dir(cid, aid) / ("v%d.json" % n), json.dumps(rec, indent=1, ensure_ascii=False, default=str))
            _write_index(cid, aid, index, settings)
    _announce(rec)
    return rec


def _announce(rec: dict) -> None:
    """Tell every open chat page which artifact moved. Content stays home; the
    page reads the store."""
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.broadcast({"type": "artifact_put", "conversation_id": rec["conversation_id"],
                               "artifact_id": rec["id"], "version": rec["version"],
                               "kind": rec["kind"], "title": rec["title"],
                               "author": rec["author"]}, kind="chat")
    except Exception:
        pass


def edit(cid: str, aid: str, content, title: str | None = None,
         note: str | None = None, settings: dict | None = None) -> dict:
    """A hand edit in the panel: a new version authored by the user."""
    cur = get(cid, aid, settings=settings)
    if cur is None:
        raise KeyError(aid)
    return put(cid, cur["kind"], title or cur["title"], content, meta=_carried_meta(cur),
               artifact_id=aid, author="you", settings=settings,
               note=note or "edited by hand")


def restore(cid: str, aid: str, version: int, settings: dict | None = None) -> dict:
    """"Restore" makes a new version with an old version's content."""
    old = get(cid, aid, version=version, settings=settings)
    if old is None:
        raise KeyError("v%s" % version)
    return put(cid, old["kind"], old["title"], old["content"], meta=_carried_meta(old),
               artifact_id=aid, author="you", settings=settings,
               note="restored from v%d" % int(version), restored_from=int(version))


def _carried_meta(rec: dict) -> dict:
    """A new version made from an old one keeps its metadata and contract
    fields: a hand edit must not drop a plan's milestones or an artifact's
    provenance. `put` lifts the contract keys back out of the meta dict."""
    meta = dict(rec.get("meta") or {})
    for k in ("task_id", "goal_id", "sensitivity", "source_refs", "provenance_id", "qa_status"):
        if rec.get(k) is not None and k not in meta:
            meta[k] = rec[k]
    return meta


def get(cid: str, aid: str, version: int | None = None, settings: dict | None = None) -> dict | None:
    cid = safe_name(cid, what="conversation id")
    aid = safe_name(aid, what="artifact id")
    index = _read_index(cid, aid, settings)
    if not index:
        return None
    n = int(version) if version is not None else int(index.get("current") or 0)
    if n < 1:
        return None
    return _read_version(cid, aid, n, settings)


def versions(cid: str, aid: str, settings: dict | None = None) -> list[dict]:
    """Every version's metadata, oldest first, without content."""
    cid = safe_name(cid, what="conversation id")
    aid = safe_name(aid, what="artifact id")
    index = _read_index(cid, aid, settings)
    if not index:
        return []
    out = []
    for n in range(1, int(index.get("current") or 0) + 1):
        v = _read_version(cid, aid, n, settings)
        if v:
            out.append(_meta_only(v))
    return out


def list_for(cid: str, settings: dict | None = None) -> list[dict]:
    """The current version of each artifact in a conversation, metadata only,
    most recently updated last."""
    cid = safe_name(cid, what="conversation id")
    out = []
    if _off(settings):
        with _LOCK:
            aids = list(_OFF_MEMORY.get(cid, {}).keys())
    else:
        d = _root() / cid
        aids = sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []
    for aid in aids:
        cur = get(cid, aid, settings=settings)
        if cur:
            out.append(_meta_only(cur))
    out.sort(key=lambda r: r.get("ts") or 0)
    return out


def _end_off_record() -> None:
    with _LOCK:
        _OFF_MEMORY.clear()


try:
    from agent_friday.services import off_record as _off_record
    _off_record.on_end(_end_off_record)
except Exception:  # pragma: no cover - the hook is optional at import
    pass


# ── the fenced-block fallback ───────────────────────────────────────────────

_FENCE_RE = re.compile(r"```friday-artifact[ \t]*([^\n]*)\n(.*?)\n?```", re.S)


def _parse_block(header: str, body: str) -> dict | None:
    """Two forms. Whole-block JSON:

        ```friday-artifact
        {"kind": "table", "title": "Rent", "content": {...}}
        ```

    or a JSON header line and raw content after it, which a local model
    writes far more reliably than JSON-escaped HTML:

        ```friday-artifact {"kind": "html", "title": "Counter"}
        <!doctype html>...
        ```
    """
    header = (header or "").strip()
    try:
        if header:
            spec = json.loads(header)
            if not isinstance(spec, dict):
                return None
            spec = dict(spec)
            spec["content"] = body
        else:
            spec = json.loads(body)
            if not isinstance(spec, dict) or "content" not in spec:
                return None
    except Exception:
        return None
    if not spec.get("kind") or spec.get("kind") not in KINDS:
        return None
    return spec


def absorb_fenced(cid: str, text: str, settings: dict | None = None) -> tuple[str, list[dict]]:
    """Turn every well-formed ```friday-artifact block in `text` into a stored
    artifact and replace it with a one-line pointer. Malformed blocks are left
    exactly as they were, so a broken attempt is visible rather than lost.

    Returns (clean_text, [version records])."""
    text = text or ""
    if "```friday-artifact" not in text:
        return text, []
    records: list[dict] = []

    def _sub(m):
        spec = _parse_block(m.group(1), m.group(2))
        if spec is None:
            return m.group(0)
        try:
            rec = put(cid, spec["kind"], str(spec.get("title") or spec["kind"]),
                      spec["content"], meta=spec.get("meta") if isinstance(spec.get("meta"), dict) else None,
                      artifact_id=spec.get("artifact_id") or None, author="friday",
                      settings=settings)
        except Exception:
            return m.group(0)
        records.append(rec)
        return "\U0001F4CE **%s** — in the panel (v%d)" % (rec["title"], rec["version"])

    return _FENCE_RE.sub(_sub, text), records


# ── what the model is told ──────────────────────────────────────────────────

def diff_between(cid: str, aid: str, v_from: int, v_to: int, settings: dict | None = None,
                 redact: bool = False) -> str:
    """The unified diff of two versions, whole. With `redact` it is the copy the
    model is shown: each version has a key block or token withheld from its WHOLE
    text (services/credential_paths) before the two are compared, so a changed
    line in the middle of a key, which a diff shows with no armor around it and a
    "+" or "-" in front of it, never appears."""
    a = get(cid, aid, version=v_from, settings=settings)
    b = get(cid, aid, version=v_to, settings=settings)
    if a is None or b is None:
        return ""
    ta, tb = _text_of(a["content"]), _text_of(b["content"])
    if redact:
        from agent_friday.services import credential_paths as _cred
        ta, tb = _cred.redact_secrets(ta), _cred.redact_secrets(tb)
    return "".join(difflib.unified_diff(
        ta.splitlines(True), tb.splitlines(True),
        fromfile="v%d" % int(v_from), tofile="v%d" % int(v_to)))


def context_block(cid: str, settings: dict | None = None) -> str:
    """The block appended to the system prompt each turn: which artifacts this
    conversation has, and any hand edit the model has not yet seen, shown as
    a diff once. '' when the conversation has none."""
    try:
        items = list_for(cid, settings=settings)
    except Exception:
        return ""
    if not items:
        return ""
    lines = ["", "", CONTEXT_HEADER,
             "Update one with artifact_put(artifact_id=...) rather than making a new one."]
    for it in items:
        aid, cur = it["id"], int(it["version"])
        lines.append("- %s · %s · \"%s\" · v%d" % (aid, it["kind"], it["title"], cur))
        index = _read_index(cid, aid, settings) or {}
        seen = int(index.get("model_seen_version") or 0)
        if seen < cur:
            by_user = [v for v in versions(cid, aid, settings=settings)
                       if v["version"] > seen and v.get("author") == "you"]
            if by_user:
                # Nothing of a key rides in the prompt (read_file's rule): the model
                # is shown the redacted copy of the diff.
                d = diff_between(cid, aid, max(seen, 1), cur, settings=settings, redact=True)
                dl = d.splitlines()
                if len(dl) > _DIFF_LINES_SHOWN:
                    dl = dl[:_DIFF_LINES_SHOWN] + ["... (%d more lines)" % (len(dl) - _DIFF_LINES_SHOWN)]
                lines.append("  edited by the user since your last turn (v%d→v%d); keep their changes:" % (max(seen, 1), cur))
                lines.extend("  " + x for x in dl)
            index["model_seen_version"] = cur
            try:
                _write_index(cid, aid, index, settings)
            except Exception:
                pass
    return "\n".join(lines) + "\n"
