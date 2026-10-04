"""Finding a forgotten document's words wherever Friday kept a copy.

A footnote token lets forget find the chats that cited a document, but a quoted
passage can also sit in saved chats that never cited it, in the conversation
memory's search index, in the older chat history, in the context log, in the
reasoning traces and in the last search held in memory. So forget fingerprints the document before it is
deleted (every run of eight words, hashed, never stored) and sweeps each of those
stores for any run of eight or more of those words, replacing it with
"[forgotten source]". An abridged quote or an unquoted copy goes with it.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

FORGOTTEN = "[forgotten source]"
N = 8
MIN_SHORT = 4            # a document of fewer than N words is matched whole when it has at least this many
# A word is a run of letters or digits in any script. Quotes, dashes, ellipses and other punctuation
# separate words (so “curly” and "straight" quotes, a hyphen and a dash all read alike), and each word is
# compared in its NFKC, case-folded form.
_TOK = re.compile(r"[^\W_]+")


def _tokens(text: str):
    return [(unicodedata.normalize("NFKC", m.group(0)).casefold(), m.start(), m.end()) for m in _TOK.finditer(text)]


def _h(words) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(words).encode("utf-8"), digest_size=8).digest(), "big")


class Fingerprint(set):
    """Hashes of a document's eight-word runs; `doc_id` also names its footnotes. A document too short
    for a full run (a gate code, a one-line note) is matched whole through `short` = (words, hash)."""
    doc_id: int | None = None
    short: tuple[int, int] | None = None


def fingerprint(text: str, doc_id: int | None = None) -> Fingerprint:
    """Hashes of every run of eight words in `text`."""
    w = [t[0] for t in _tokens(text)]
    fp = Fingerprint({_h(w[i:i + N]) for i in range(len(w) - N + 1)})
    fp.doc_id = doc_id
    if not fp and len(w) >= MIN_SHORT:
        fp.short = (len(w), _h(w))
    return fp


def _footnotes_gone(text: str, fp) -> tuple[str, bool]:
    doc_id = getattr(fp, "doc_id", None)
    if doc_id is None or "lib:" not in text:
        return text, False
    new = re.sub(r"\[(?:unverified-)?lib:%d#\d+\]" % int(doc_id), FORGOTTEN, text)
    return new, new != text


def scrub(text: str, fp: set[int]) -> tuple[str, bool]:
    """`text` with the document's footnotes and every run (eight words or more) found in `fp` replaced."""
    if not isinstance(text, str):
        return text, False
    text, tok = _footnotes_gone(text, fp)
    short = getattr(fp, "short", None)
    if (not fp and not short) or len(text) < (short[0] if short else N) * 3:
        return text, tok
    toks = _tokens(text)
    w = [t[0] for t in toks]
    covered = [False] * len(toks)
    for i in range(len(w) - N + 1):
        if _h(w[i:i + N]) in fp:
            for k in range(i, i + N):
                covered[k] = True
    if short:
        n_short, h_short = short
        for i in range(len(w) - n_short + 1):
            if _h(w[i:i + n_short]) == h_short:
                for k in range(i, i + n_short):
                    covered[k] = True
    if not any(covered):
        return text, tok
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


def _memory_index(fp) -> tuple[int, str | None]:
    """The conversation memory's search index: replies are rewritten in place.
    Returns (records changed, why it could not be swept or None)."""
    n = 0
    try:
        from agent_friday.conversation_memory import get_conversation_memory
        cm = get_conversation_memory()
        if not Path(cm.persist_dir).exists():
            return 0, None                     # nothing was ever stored, so nothing needs opening
        if not cm.available() or not cm._ensure():
            return 0, "the conversation memory could not be opened"
        coll = cm._collection
        offset = 0
        while True:
            got = coll.get(include=["documents"], limit=500, offset=offset)
            ids, docs = got.get("ids") or [], got.get("documents") or []
            if not ids:
                return n, None
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
    except Exception as e:  # noqa: BLE001
        return n, "the conversation memory (%s)" % type(e).__name__


def _legacy_history(fp) -> tuple[int, str | None]:
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
    except Exception as e:  # noqa: BLE001
        return n, "the older chat history (%s)" % type(e).__name__
    return n, None


def _context_logs(fp) -> tuple[int, str | None]:
    n, problem = 0, None
    try:
        import agent_friday.core as core
        d = Path(core.CONTEXT_LOG_DIR)
        files = sorted(d.glob("*.jsonl")) if d.exists() else []
    except Exception as e:  # noqa: BLE001
        return 0, "the context log (%s)" % type(e).__name__
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
            problem = "the context log (a file could not be rewritten)"
            continue
    return n, problem


def _traces(fp) -> tuple[int, str | None]:
    """The reasoning traces, live and archived (the archive stays verifiable; see reasoning_trace.redact)."""
    try:
        from agent_friday.services import reasoning_trace
        r = reasoning_trace.redact(lambda t: scrub(t, fp))
    except Exception as e:  # noqa: BLE001
        return 0, "the reasoning traces (%s)" % type(e).__name__
    return r["archived"] + r["live"] + r["pending"], (("the reasoning traces: " + r["incomplete"]) if r["incomplete"] else None)


def sweep_all(fp: set[int], doc_id: int | None = None) -> dict:
    """Run every sweep; returns how many records each changed and, under "incomplete", the
    stores that could not be swept (so the owner is told, not reassured)."""
    incomplete: list[str] = []
    try:
        out = {"chats": _conversations(fp)}
    except Exception as e:  # noqa: BLE001
        out = {"chats": 0}
        incomplete.append("the saved chats (%s)" % type(e).__name__)
    for name, fn in (("memory", _memory_index), ("history", _legacy_history), ("context_log", _context_logs),
                     ("traces", _traces)):
        n, problem = fn(fp)
        out[name] = n
        if problem:
            incomplete.append(problem)
    try:
        from agent_friday.services.library import search
        if doc_id is not None:
            search.forget_last(doc_id)
    except Exception:  # noqa: BLE001
        pass
    out["incomplete"] = incomplete
    return out
