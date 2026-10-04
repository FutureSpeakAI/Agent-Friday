"""Showing the Library on the owner's own screen (the `library_show` tool and
the Shift-click on a footnote). It moves the owner's desktop and nothing else:
no approval is needed, and nothing leaves the PC."""
from __future__ import annotations

import re

from agent_friday.services.library import cards, principal as pr, search
from agent_friday.services.library.store import store_for

VIEWS = {"list": "list", "3d": "shelves", "shelves": "shelves", "tree": "tree"}


def _resolve(principal: str, target: str, page) -> tuple[dict | None, str]:
    """(what to open, plain words for it) or (None, why not)."""
    last = search.last_result(principal)
    t = (target or "").strip().lower()
    if t in ("next", "next passage", "previous", "previous passage", "back"):
        if not last or not last["evidence"]:
            return None, "there is no search to step through"
        step = 1 if t.startswith("next") else -1
        last["cursor"] = (last["cursor"] + step) % len(last["evidence"])
        e = last["evidence"][last["cursor"]]
        return _of(e), "passage %s of %s" % (e["label"], e["doc"])
    m = re.fullmatch(r"\[?(\d+(?:\.\d+)?)\]?", t)
    if m and last:
        for i, e in enumerate(last["evidence"]):
            if e["label"] == m.group(1):
                last["cursor"] = i
                return _of(e), "passage %s of %s" % (e["label"], e["doc"])
        return None, "no passage is labelled %s in the last search" % m.group(1)
    if t:
        hits = cards.find_documents(principal, t)
        if len(hits) == 1:
            out = {"doc": hits[0]["doc_id"]}
            if page:
                out["page"] = int(page)
            return out, hits[0]["title"]
        if hits:
            return None, "several documents match: " + "; ".join(h["title"] for h in hits[:5])
        return None, "nothing in the Library matches %r" % target
    if page and last and last["evidence"]:
        e = last["evidence"][last["cursor"]]
        return {"doc": e["doc_id"], "page": int(page)}, "page %d of %s" % (int(page), e["doc"])
    return {}, "the Library"


def _of(e: dict) -> dict:
    return {"doc": e["doc_id"], "block": e.get("block_id"), "page": e.get("page"), "section": e.get("section_id")}


def show(inp: dict) -> str:
    from agent_friday.services import desktop_bus
    principal = pr.current()
    if principal is None:
        return "The Library is not available to this account."
    spot, words = _resolve(principal, str(inp.get("target") or ""), inp.get("page"))
    if spot is None:
        return "LIB_FAIL: " + words + "."
    view = VIEWS.get(str(inp.get("view") or "").lower())
    action = {"type": "navigate", "workspace": "library"}
    if spot:
        action["lib"] = {k: v for k, v in spot.items() if v is not None}
    if view:
        action["view"] = view
    sent = desktop_bus.send([action])
    if not sent.get("delivered"):
        return "LIB_FAIL: %s, so %s was not shown." % (sent.get("reason"), words)
    if not sent.get("acked"):
        return "LIB_SENT: sent %s to the desktop, but the page did not confirm it." % words
    ack = sent.get("ack") or {}
    if ack.get("opened") is False:
        return "LIB_FAIL: the desktop did not open %s." % words
    return "LIB_OK: showing %s." % words
