"""An answer written from the evidence, on this PC.

The local brain reads the labelled passages (fenced as data, in the volatile
tail of the user message; the system text never changes, so the seat's cache
holds) and writes a short answer that cites each factual sentence with a label.
The labels then become footnotes through the same check chat uses. Nothing here
calls a cloud model: if the owner wants a cloud answer, they ask in chat and
their routing and the egress gate decide.
"""
from __future__ import annotations

import re

from agent_friday.services.library import cite, envelope

SYSTEM = (
    "You answer questions from passages of the user's own documents. Answer first, in plain words. "
    "Cite every factual sentence with the label of the passage that supports it, in square brackets, "
    "like [1.2]. Quote sparingly and exactly. If the passages only partly cover the question, say which "
    "part they cover and which part none does. If they do not answer it, say plainly that you did not "
    "find it in the Library. Never invent a label, page, name or number.")

MAX_TOKENS = 500


def seat_model() -> str | None:
    try:
        from agent_friday.services import local_brain
        seat = local_brain.brain_seat()
        return (seat or {}).get("model")
    except Exception:
        return None


def write(question: str, evidence: list[dict], *, model: str | None = None, stamp: dict | None = None,
          receipt: str | None = None) -> dict:
    """{"ok": True, "text"} with footnote tokens, or {"ok": False, "reason"}."""
    model = model or seat_model()
    if not model:
        return {"ok": False, "reason": "no local model is loaded to write it"}
    if not evidence:
        return {"ok": False, "reason": "nothing to answer from"}
    from agent_friday.services import local_call
    user = envelope.wrap(evidence) + "\n\nQuestion: " + question.strip()[:600]
    text = local_call.call(SYSTEM, user, model, max_tokens=MAX_TOKENS, timeout=240)
    if not text:
        return {"ok": False, "reason": "the local model did not answer"}
    refs = {e["label"]: e["ref"] for e in evidence if e.get("ref")}
    from agent_friday.services.library import versions
    stamp = stamp or versions.stamp()
    trace = [{"name": "search_library", "input": {},
              "result": '{"refs": %s, "stamp": %s, "receipt": %s}\n' % (_json(refs), _json(stamp), _json(receipt))}]
    text = delink(text)
    text, _flagged = cite.finish(text, trace)
    return {"ok": True, "text": text, "model": model, "stamp": stamp}


_INLINE_LINK = re.compile(r"\[([^\]]{0,200})\]\(\s*(?:<[^>]*>|(?:[^()\s]|\([^()]*\))*)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_REF_USE = re.compile(r"\[([^\]]{1,200})\]\[[^\]]*\]")
_REF_DEF = re.compile(r"^[ \t]{0,3}\[[^\]]+\]:[ \t]*\S.*$", re.M)
_LABEL = re.compile(r"\d+(?:\.\d+)?|lib:\d+#\d+")        # two footnote labels side by side are not a reference link
_ANGLE_AUTOLINK = re.compile(r"<((?:[a-z][a-z0-9+.\-]{1,31}:|www\.)[^\s<>]*)>", re.I)
_HTML_TAG = re.compile(r"</?[a-z][^>]*>", re.I)
_BARE_URL = re.compile(r"(?<![`(])(?:\b(?:https?|ftp|file|data|javascript|vbscript):[^\s)\]>`]+|\bwww\.[^\s)\]>`]+)", re.I)


def delink(text: str) -> str:
    """A written answer carries no clickable link: a poisoned document could ask the model to link its
    reader to an address with their data in the query. Every link form the page's markdown renderer
    turns into an anchor (inline, with an angle-bracket or parenthesised address, reference style,
    autolinks, HTML anchors, bare addresses, and any of them hidden behind HTML entities) is reduced
    to its visible words or, for a bare address, to inert code text."""
    import html
    for _ in range(2):                       # &#104;ttps:// and &amp;#104;ttps:// read as what they spell
        un = html.unescape(text)
        if un == text:
            break
        text = un
    text = _INLINE_LINK.sub(r"\1", text)
    text = _REF_USE.sub(lambda m: m.group(0) if _LABEL.fullmatch(m.group(1)) else m.group(1), text)
    text = _REF_DEF.sub("", text)
    text = _ANGLE_AUTOLINK.sub(lambda m: "`" + m.group(1) + "`", text)
    text = _HTML_TAG.sub("", text)
    return _BARE_URL.sub(lambda m: "`" + m.group(0) + "`", text)


def _json(obj) -> str:
    import json
    return json.dumps(obj)
