"""Footnotes: `[lib:<doc>#<block>]`.

The model sees short labels beside each passage (`[1.2]`) and writes those; the
server turns them into `[lib:12#345]` tokens. A token is clickable only when
`search_library` returned that block in the same turn (the rule web citations
already follow); anything else becomes `[unverified-lib:...]` and renders as a
struck chip. Before the reply is shown, saved or spoken, every cited block's
document is checked again: still added, not forgotten, same principal, file
unchanged since it was read. A block that fails becomes plain words.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent_friday.services.library import grants, principal as pr, versions
from agent_friday.services.library.store import store_for

TOKEN_RE = re.compile(r"\[(unverified-)?lib:(\d+)#(\d+)\]")
LABEL_RE = re.compile(r"\[(\d+(?:\.\d+)?)\]")
GONE = "[source no longer in your Library]"


def refs_from_trace(tool_trace) -> dict[str, str]:
    """label -> ref for every passage `search_library` returned this turn.
    The refs ride in the first line of the tool's result, ahead of any clip."""
    out: dict[str, str] = {}
    for entry in tool_trace or []:
        if not isinstance(entry, dict) or entry.get("name") != "search_library":
            continue
        res = entry.get("result")
        if not isinstance(res, str):
            continue
        first = res.split("\n", 1)[0]
        try:
            meta = json.loads(first)
        except ValueError:
            continue
        refs = meta.get("refs") if isinstance(meta, dict) else None
        if isinstance(refs, dict):
            out.update({str(k): str(v) for k, v in refs.items() if str(v).startswith("lib:")})
    return out


def search_info_from_trace(tool_trace) -> dict:
    """{ref: (stamp, receipt)} for every passage returned this turn: the versions
    and the search record that produced each footnote."""
    out: dict = {}
    for entry in tool_trace or []:
        if not isinstance(entry, dict) or entry.get("name") != "search_library":
            continue
        res = entry.get("result")
        if not isinstance(res, str):
            continue
        try:
            meta = json.loads(res.split("\n", 1)[0])
        except ValueError:
            continue
        if isinstance(meta, dict):
            for ref in (meta.get("refs") or {}).values():
                out[str(ref)] = (meta.get("stamp"), meta.get("receipt"))
    return out


def resolve_labels(reply: str, tool_trace) -> str:
    """`[1.2]` -> `[lib:12#345]` for labels returned this turn; `[1]` names the
    first passage of document 1 when no document has a lone label `1`."""
    refs = refs_from_trace(tool_trace)
    if not refs or not reply:
        return reply

    def one(m):
        label = m.group(1)
        ref = refs.get(label) or (refs.get(label + ".1") if "." not in label else None)
        return "[%s]" % ref if ref else m.group(0)

    return LABEL_RE.sub(one, reply)


def _still_valid(principal: str, doc_id: int, block_id: int) -> bool:
    st = store_for(principal)
    doc = st.get_document(doc_id)
    if not doc or doc["state"] != "indexed":
        return False
    blk = st.one("SELECT 1 FROM blocks WHERE id=? AND doc_id=?", (block_id, doc_id))
    if not blk:
        return False
    p = Path(doc["path"])
    if not grants.allowed(principal, p) or st.tombstoned(doc["sha256"], doc["path"]):
        return False
    if doc["shelf"] == "vault" and not st.vault_open():
        return False
    try:
        s = p.stat()
    except OSError:
        return False
    return s.st_size == doc["size"] and abs(s.st_mtime - (doc["mtime"] or 0)) < 1e-3


def verify(reply: str, tool_trace, *, conversation_id: str | None = None) -> tuple[str, list[str]]:
    """Mark unbacked tokens, replace dead ones, record live ones. Returns
    (reply, flagged tokens)."""
    if not reply or "lib:" not in reply:
        return reply, []
    principal = pr.current()
    if principal is None:
        return TOKEN_RE.sub(GONE, reply), []
    live = set(refs_from_trace(tool_trace).values())
    info = search_info_from_trace(tool_trace)
    flagged: list[str] = []
    st = store_for(principal)

    def one(m):
        ref = "lib:%s#%s" % (m.group(2), m.group(3))
        if ref not in live:
            flagged.append(ref)
            return "[unverified-%s]" % ref
        doc_id, block_id = int(m.group(2)), int(m.group(3))
        if not _still_valid(principal, doc_id, block_id):
            return GONE
        stamp, receipt = info.get(ref, (None, None))
        st.add_citation(block_id, doc_id, conversation_id, None, versions.compact(stamp), receipt)
        return "[%s]" % ref

    return TOKEN_RE.sub(one, reply), flagged


def finish(reply: str, tool_trace, *, conversation_id: str | None = None) -> tuple[str, list[str]]:
    """Resolve labels, then verify: the one call a reply passes through."""
    searched = any(isinstance(e, dict) and e.get("name") == "search_library" for e in (tool_trace or []))
    if not reply or (not searched and "lib:" not in reply):
        return reply, []
    return verify(resolve_labels(reply, tool_trace), tool_trace, conversation_id=conversation_id)


def speakable(text: str) -> str:
    """Spoken answers say 'page fourteen of the deposition', never a token."""
    return TOKEN_RE.sub("", text or "")
