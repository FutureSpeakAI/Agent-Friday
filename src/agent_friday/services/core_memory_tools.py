"""update_core_memory: Friday's tool for her core memory card (services/core_memory).

A shared TEXT tool, registered like services/laya_learning_tools. Ring 1,
INTERNAL in governance/action_gate: a plain save runs with no card (no
pestering for a low-risk write). Its ``text`` and ``old`` arguments are
``memory_write`` roles in services/taint, so a call made after Friday read
outside content (a page, a mail, a file) becomes an approval card first, and
the approved card re-runs the exact call.

Voice reaches it through ask_friday and the deeper mind, and the cloud voice
through more_tools (group wiki_knowledge), like the other memory tools: the
voice front's tool contract has no room for another declaration.
"""
from __future__ import annotations

import json

from agent_friday.services import core_memory

TOOLS = [
    {"name": "update_core_memory",
     "description": (
         "Keep your core memory card: two short files always loaded at the top of your instructions. "
         "target=user holds durable facts about the user (preferences, people, projects, how they like "
         "things); target=friday holds your own lessons about working with them (what worked, what to "
         "avoid). action=add saves one short line; action=replace rewrites the entry that contains old; "
         "action=remove drops it; action=show lists both files with how full they are. Save things that "
         "will matter in later conversations, not passing details; never secrets. Each file has a hard "
         "character cap: when an add is refused as full, merge related entries with replace or drop stale "
         "ones with remove, then add again. A change takes effect from the next conversation; the user "
         "sees every line and can edit or delete it in Settings > Privacy & Data > Memory card."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["add", "replace", "remove", "show"]},
         "target": {"type": "string", "enum": ["user", "friday"]},
         "text": {"type": "string", "description": "The entry to save (add) or the new wording (replace). One line."},
         "old": {"type": "string", "description": "Part of the existing entry's text (replace, remove)."}},
         "required": ["action"]}},
]
RINGS = {"update_core_memory": 1}


def _actor() -> str:
    """Who is calling: 'friday' in a conversation, or the background pass that
    set core_memory.ACTOR (it may only add; see below)."""
    a = core_memory.ACTOR.get()
    return a if a in core_memory.ACTORS and a != "owner" else "friday"


def _tool_update_core_memory(inp=None) -> str:
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "").strip().lower()
    target = str(inp.get("target") or "user").strip().lower()
    if action == "show":
        return json.dumps(core_memory.snapshot(), ensure_ascii=False)
    if action not in ("add", "replace", "remove"):
        return "NOT DONE: action is add, replace, remove or show."
    if target not in core_memory.TARGETS:
        return "NOT DONE: target is user or friday."
    actor = _actor()
    if actor != "friday" and action in ("replace", "remove"):
        # The background review may add; anything it would rewrite or drop
        # waits for the owner.
        r = core_memory.queue(target, action, text=inp.get("text") or "",
                              old=inp.get("old") or "", source=actor,
                              reason=str(inp.get("reason") or ""))
        return ("QUEUED for the user's review (the background review never rewrites or removes "
                "on its own)." if r.get("ok") else "NOT QUEUED: " + str(r.get("error")))
    if action == "add":
        r = core_memory.add(target, inp.get("text") or "", actor=actor)
    elif action == "replace":
        r = core_memory.replace(target, inp.get("old") or "", inp.get("text") or "", actor=actor)
    else:
        r = core_memory.remove(target, inp.get("old") or "", actor=actor)
    if not r.get("ok"):
        return str(r.get("error") or "NOT DONE.")
    if r.get("unchanged"):
        return "Already in your core memory; nothing changed."
    u = r.get("usage") or {}
    verb = {"add": "Saved", "replace": "Updated", "remove": "Removed"}[action]
    return (f"{verb} in core memory ({target}: {u.get('used', 0):,}/{u.get('cap', 0):,} chars). "
            "It takes effect from the next conversation; the user can see and undo it in "
            "Settings > Privacy & Data > Memory card.")


HANDLERS = {"update_core_memory": _tool_update_core_memory}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
