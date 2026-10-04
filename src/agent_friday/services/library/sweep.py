"""Finding a forgotten document's words wherever Friday kept a copy.

A footnote token lets forget find the chats that cited a document, but a quoted
passage can also sit in saved chats that never cited it, in the conversation
memory's search index, in the older chat history, in the context log, and in the
last search held in memory. So forget fingerprints the document before it is
deleted (every run of eight words, hashed, never stored) and sweeps each of those
stores for any run of eight or more of those words, replacing it with
"[forgotten source]". An abridged quote or an unquoted copy goes with it.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

FORGOTTEN = "[forgotten source]"
N = 8
_TOK = re.compile(r"[0-9A-Za-zÀ-ɏͰ-￿]+(?:'[0-9A-Za-z]+)?")


def _tokens(text: str):
    return [(m.group(0).lower().replace("'", ""), m.start(), m.end()) for m in _TOK.finditer(text)]


def _h(words) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(words).encode("utf-8"), digest_size=8).digest(), "big")


def fingerprint(text: str) -> set[int]:
    """Hashes of every run of eight words in `text`."""
    w = [t[0] for t in _tokens(text)]
    return {_h(w[i:i + N]) for i in range(len(w) - N + 1)}


def scrub(text: str, fp: set[int]) -> tuple[str, bool]:
    """`text` with every run (eight words or more) found in `fp` replaced."""
    if not fp or not isinstance(text, str) or len(text) < N * 3:
        return text, False
    toks = _tokens(text)
    w = [t[0] for t in toks]
    covered = [False] * len(toks)
    for i in range(len(w) - N + 1):
        if _h(w[i:i + N]) in fp:
            for k in range(i, i + N):
                covered[k] = True
    if not any(covered):
        return text, False
    out, last, i = [], 0, 0
    while i < len(toks):
        if covered[i]:
            j = i
            while j + 1 < len(toks) and covered[j + 1]:
                j += 1
            out.append(text[last:toks[i][1]])
            out.append(FORGOTTEN)
            last = toks[j][2]
            i = j + 1
        else:
            i += 1
    out.append(text[last:])
    return "".join(out), True


def scrub_json(value, fp: set[int]):
    """The same, through every string in a JSON value. Returns (value, changed)."""
    if isinstance(value, str):
        return scrub(value, fp)
    if isinstance(value, list):
        changed, out = False, []
        for v in value:
            nv, c = scrub_json(v, fp)
            out.append(nv)
            changed = changed or c
        return out, changed
    if isinstance(value, dict):
        changed, out = False, {}
        for k, v in value.items():
            nv, c = scrub_json(v, fp)
            out[k] = nv
            changed = changed or c
        return out, changed
    return value, False


def _conversations(fp) -> int:
    from agent_friday.services import conversations
    n = 0
    for conv in conversations.list_all():
        cid = conv.get("id")
        if cid:
            n += conversations.rewrite(cid, lambda m: scrub_json(m, fp)[0])
    return n


def _memory_index(fp) -> int:
    """The conversation memory's search index: replies are rewritten in place."""
    try:
        from agent_friday.conversation_memory import ConversationMemory
        cm = ConversationMemory()
        if not cm.available() or not cm._ensure():
            return 0
        coll = cm._collection
        n, offset = 0, 0
        while True:
            got = coll.get(include=["documents"], limit=500, offset=offset)
            ids, docs = got.get("ids") or [], got.get("documents") or []
            if not ids:
                return n
            fix_ids, fix_docs = [], []
            for i, d in zip(ids, docs):
                nd, changed = scrub(d or "", fp)
                if changed:
                    fix_ids.append(i)
                    fix_docs.append(nd)
            if fix_ids:
                with cm._lock:
                    coll.update(ids=fix_ids, documents=fix_docs)
                n += len(fix_ids)
            offset += 500
    except Exception:  # noqa: BLE001 - a store that is not there has nothing to sweep
        return 0


def _legacy_history(fp) -> int:
    n = 0
    try:
        import agent_friday.core as core
        for m in list(core.CHAT_HISTORY):
            if isinstance(m, dict):
                nm, changed = scrub_json(m, fp)
                if changed:
                    m.clear()
                    m.update(nm)
                    n += 1
        if n:
            core._save_chat_history(core.CHAT_HISTORY)
    except Exception:  # noqa: BLE001
        pass
    return n


def _context_logs(fp) -> int:
    n = 0
    try:
        import agent_friday.core as core
        d = Path(core.CONTEXT_LOG_DIR)
        files = sorted(d.glob("*.jsonl")) if d.exists() else []
    except Exception:  # noqa: BLE001
        return 0
    for f in files:
        try:
            lines, changed = [], 0
            for raw in f.read_text(encoding="utf-8").splitlines():
                try:
                    obj = json.loads(raw)
                except ValueError:
                    lines.append(raw)
                    continue
                nobj, c = scrub_json(obj, fp)
                changed += c
                lines.append(json.dumps(nobj, default=str) if c else raw)
            if changed:
                tmp = f.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
                tmp.replace(f)
                n += changed
        except OSError:
            continue
    return n


def sweep_all(fp: set[int], doc_id: int | None = None) -> dict:
    """Run every sweep; returns how many records each changed."""
    out = {"chats": _conversations(fp), "memory": _memory_index(fp), "history": _legacy_history(fp),
           "context_log": _context_logs(fp)}
    try:
        from agent_friday.services.library import search
        if doc_id is not None:
            search.forget_last(doc_id)
    except Exception:  # noqa: BLE001
        pass
    return out
