"""An answer written from the evidence, on this PC.

The local brain reads the labelled passages (fenced as data, in the volatile
tail of the user message; the system text never changes, so the seat's cache
holds) and writes a short answer that cites each factual sentence with a label.
The labels then become footnotes through the same check chat uses. Nothing here
calls a cloud model: if the owner wants a cloud answer, they ask in chat and
their routing and the egress gate decide.
"""
from __future__ import annotations

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


def write(question: str, evidence: list[dict], *, model: str | None = None) -> dict:
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
    trace = [{"name": "search_library", "input": {}, "result": '{"refs": %s}\n' % _json(refs)}]
    text, _flagged = cite.finish(text, trace)
    return {"ok": True, "text": text, "model": model}


def _json(obj) -> str:
    import json
    return json.dumps(obj)
