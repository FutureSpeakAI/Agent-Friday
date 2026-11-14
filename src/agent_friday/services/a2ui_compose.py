"""Deterministic composer for the Friday A2UI profile (Liquid UI L1; roadmap section 41, slice A1).

Turns a small, plain "block list" into an A2UI message stream with no model involved: the same
blocks always give the same document, byte for byte (ids are c1, c2, ... in document order). The
result is validated by `a2ui_profile.check` before it is returned, so a composer bug can never
hand a renderer a document the profile would refuse.

Blocks (plain dicts; every value is a literal, a composer document carries no data model):
  {"kind": "heading", "text": ...}                     text_block, style heading
  {"kind": "text", "text": ..., "style"?: "body"|...}  text_block
  {"kind": "divider"}
  {"kind": "status", "title": ..., "children": [blocks]}
  {"kind": "button", "label": ..., "verb": ..., "context"?: {...}}   verb_button
  {"kind": "link", "label": ..., "destination": ...}   nav_link (an internal registry id)
  {"kind": "approval_slot", "approval_kind"?: ...}     the shell's own card goes here, nothing else

It never writes an approve, pay or allow control: a button's verb is judged by its manifest in the
validator, and the approval card is only ever the shell-owned slot.
"""
from __future__ import annotations

from agent_friday.services import a2ui_profile as prof
from agent_friday.services.a2ui_profile import Context, ProfileError

SURFACE_ID = "view"


class _Builder:
    def __init__(self, subject: str):
        self.subject = subject
        self.comps: list[dict] = []
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"c{self._n}"

    def add(self, block: dict) -> str:
        if not isinstance(block, dict) or "kind" not in block:
            raise ProfileError("V1", "a block is a dict with a kind")
        kind = block["kind"]
        cid = self._id()
        slot_index = len(self.comps)
        self.comps.append({})            # reserve the position so ids follow document order
        if kind in ("heading", "text"):
            comp = {"id": cid, "component": "text_block", "text": block.get("text", ""),
                    "style": "heading" if kind == "heading" else block.get("style", "body")}
        elif kind == "divider":
            comp = {"id": cid, "component": "divider"}
        elif kind == "status":
            comp = {"id": cid, "component": "status_block", "title": block.get("title", ""),
                    "children": [self.add(b) for b in block.get("children", [])]}
        elif kind == "button":
            verb = block["verb"]
            event = {"name": verb}
            if block.get("context"):
                event["context"] = dict(block["context"])
            comp = {"id": cid, "component": "verb_button", "label": block.get("label", ""),
                    "verb": verb, "action": {"event": event}}
        elif kind == "link":
            comp = {"id": cid, "component": "nav_link", "label": block.get("label", ""),
                    "destination": block["destination"]}
        elif kind == "approval_slot":
            flt = {"subject": self.subject}
            if block.get("approval_kind"):
                flt["kind"] = block["approval_kind"]
            comp = {"id": cid, "component": "approval_slot", "filter": flt}
        else:
            raise ProfileError("V4", f"no composer block {kind!r}")
        self.comps[slot_index] = comp
        return cid


def compose(subject: str, blocks: list[dict], ctx: Context) -> list[dict]:
    """The A2UI message stream for `blocks`, validated against the profile with `ctx`
    (its verb lookup decides which buttons are allowed). Raises ProfileError, never returns a
    document the profile would reject."""
    b = _Builder(subject)
    top = [b.add(blk) for blk in blocks]
    root = {"id": "root", "component": "column", "children": top}
    messages = [{
        "version": prof.VERSION,
        "createSurface": {
            "surfaceId": SURFACE_ID,
            "catalogId": prof.CATALOG_ID,
            "sendDataModel": False,
            "components": [root, *b.comps],
        },
    }]
    prof.check(messages, ctx)
    return messages
