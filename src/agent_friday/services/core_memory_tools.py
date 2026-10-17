"""update_core_memory: Friday's tool for her core memory card (services/core_memory).

A shared TEXT tool, registered like services/laya_learning_tools. Ring 1,
INTERNAL in governance/action_gate. Its ``text`` and ``old`` arguments are
``memory_write`` roles in services/taint, so a call made after Friday read
outside content becomes an approval card first, and the approved card re-runs
the exact call.

Behind that gate the handler applies one rule on every surface, typed or
spoken: ONLY THE OWNER'S OWN WORDS SAVE SILENTLY. A change runs without asking
only when the owner said it in this turn (typed on this PC, or spoken on their
own call on this PC, recently) and nothing Friday read or carried forward sits
in the conversation's ledger (6 h). Anything else waits in the memory card's
review queue for the owner's Keep. An approved card for exactly this call is
the owner's OK. The background review never rewrites or removes.

For a model that is not known to be on this PC, `show` and the refusal
listings leave out the lines about the owner, and no file path is returned.

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
         "Your core memory card, always loaded: target=user holds durable facts about the user; "
         "target=friday your lessons about working with them. add saves one short line; replace "
         "rewrites the entry containing old; remove drops it; show lists both with how full they are. "
         "Save what will matter later, never secrets. If an add is refused as full, merge (replace) or "
         "drop (remove) first. Only the user's own words save at once; anything else waits for their OK "
         "on the card. Changes apply from the next conversation."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["add", "replace", "remove", "show"]},
         "target": {"type": "string", "enum": ["user", "friday"]},
         "text": {"type": "string", "description": "One line: the entry (add) or new wording (replace)."},
         "old": {"type": "string", "description": "Text of the entry to change (replace, remove)."}},
         "required": ["action"]}},
]
RINGS = {"update_core_memory": 1}


#: How much of an entry's content words must appear in the owner's own words
#: for it to save without asking (see `_rests_on_owner_words`).
OWNER_WORDS_COVERAGE = 0.6
#: How much of the WHOLE line a replace or remove changes must appear in the
#: owner's own words ("from Austin to Denver" names "Lives in Austin.").
OWNER_LINE_COVERAGE = 0.5
#: Spoken owner words older than this no longer back a save.
VOICE_WORDS_MAX_AGE_S = 300.0


def _actor() -> str:
    """Who is calling: 'friday' in a conversation, or the background pass that
    set core_memory.ACTOR (it may only add; see below)."""
    a = core_memory.ACTOR.get()
    return a if a in core_memory.ACTORS and a != "owner" else "friday"


def _ctx() -> dict:
    try:
        from agent_friday.services import agent
        return dict(agent._CURRENT_TOOL_CONTEXT.get() or {})
    except Exception:
        return {}


def owner_words(ctx: dict) -> str:
    """The owner's own words for the turn this call belongs to, or "".

    Typed: the message the owner sent on this PC (agent._CURRENT_OWNER_TEXT,
    empty for a phone text, a channel or a background task). Spoken: the
    transcript of the owner's own call on this PC, while it is fresh. A paired
    phone, a tunnel or a relay from an unverified caller is not the owner, and
    a cloud voice's relay carries the words only as `voice_owner_words` with
    `voice_owner_local` set by the call itself, never by a model.
    """
    surface = str(ctx.get("surface") or "")
    if surface.startswith("voice") or "voice_owner_words" in ctx:
        if not (ctx.get("owner_local") or ctx.get("voice_owner_local")):
            return ""
        words = str(ctx.get("voice_owner_words") or ctx.get("owner_text") or "")
        at = ctx.get("voice_owner_at") or ctx.get("owner_text_at")
        try:
            import time as _t
            if at and _t.time() - float(at) > VOICE_WORDS_MAX_AGE_S:
                return ""
        except (TypeError, ValueError):
            return ""
        return words
    if ctx.get("origin") in ("phone", "channel") or ctx.get("is_background_task"):
        return ""
    try:
        from agent_friday.services import agent
        return str(agent._CURRENT_OWNER_TEXT.get() or "")
    except Exception:
        return ""


def _rests_on_owner_words(text: str, words: str, coverage: float = OWNER_WORDS_COVERAGE) -> bool:
    from agent_friday.services.core_memory import content_words
    want = content_words(text)
    if not want or not words:
        return False
    have = content_words(words)
    return len(want & have) / len(want) >= coverage


def _read_outside_content(ctx: dict) -> bool:
    """True when this conversation's ledger holds anything Friday read or
    carried forward (a page, a mail, a file, a tool result, a summary), at any
    age the ledger keeps (6 h), not only the last 30 minutes."""
    try:
        from agent_friday.services import taint
        led = taint._ledger(taint.ledger_key(ctx))
        with taint._LOCK:
            led._prune()
            # "content": any tool result written by a third party (a page, a
            # mail, a file, a search, a transcript). "own" (the owner's wiki and
            # contacts) is their own data and does not count.
            return bool(led.carried) or any(e.kind == "content" for e in led.entries)
    except Exception:
        return True                                   # cannot tell: ask


def _owner_decided() -> bool:
    try:
        from agent_friday.governance import action_gate
        return bool(action_gate.owner_decision())
    except Exception:
        return False


def _cloud_reader() -> bool:
    """The result goes to a model that is not KNOWN to be on this PC."""
    try:
        from agent_friday.services.library.tools import _loop_is_local
        return not _loop_is_local()
    except Exception:
        return True


def _ask_first(target: str, action: str, inp: dict, why: str) -> str:
    r = core_memory.queue(target, action, text=inp.get("text") or "", old=inp.get("old") or "",
                          source="friday", reason=why)
    if not r.get("ok"):
        return "NOT SAVED: " + str(r.get("error"))
    return ("NOT SAVED YET: waiting for the user's OK. " + why + " It is on their Memory card "
            "(Settings > Privacy & Data) under 'Waiting for your review'; tell them in one sentence.")


def _tool_update_core_memory(inp=None) -> str:
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "").strip().lower()
    target = str(inp.get("target") or "user").strip().lower()
    cloud = _cloud_reader()
    if action == "show":
        snap = core_memory.snapshot()
        for f in snap.values():
            f.pop("file", None)
        if cloud:
            # The lines about the owner are personal data: not for a model off this PC.
            snap["user"]["entries"] = []
            snap["user"]["note"] = "withheld from this model: the lines about the user stay on this PC"
        return json.dumps(snap, ensure_ascii=False)
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
    if actor == "friday" and not _owner_decided():
        # ONLY THE OWNER'S OWN WORDS SAVE SILENTLY, in text and in voice alike.
        # A change runs without asking when the owner said it in this turn and
        # nothing Friday read sits in this conversation's ledger; anything else
        # (a page, a mail, a tool result, a carried summary, someone who is not
        # the owner, a relay) waits for the owner's OK. An approved card for
        # exactly this call (the gate's owner decision) is the OK.
        # A replace rests on BOTH halves: the new wording and the WHOLE line
        # it overwrites; a remove rests on the whole line. `old` is only a
        # pointer (any substring picks a line), so the line it resolves to is
        # what the owner must have named. The owner's "I moved to Denver"
        # backs a line about Denver, not the overwrite of whichever line a
        # fragment such as "d" or "like" happens to pick.
        ctx = _ctx()
        words = owner_words(ctx)
        if action in ("replace", "remove"):
            line, miss = core_memory.resolve_line(target, inp.get("old") or "")
            if miss:
                return "NOT CHANGED: " + miss
            inp = dict(inp, old=line)               # act on, or queue, exactly this line
        if not words:
            return _ask_first(target, action, inp,
                              "It did not come from the user's own words in this conversation.")
        if action in ("add", "replace") and not _rests_on_owner_words(inp.get("text") or "", words):
            return _ask_first(target, action, inp, "The user did not say this in their own words.")
        if action in ("replace", "remove") and not _rests_on_owner_words(
                inp["old"], words, OWNER_LINE_COVERAGE):
            return _ask_first(target, action, inp,
                              "The user did not name the line this changes in their own words.")
        if _read_outside_content(ctx):
            return _ask_first(target, action, inp,
                              "Friday read outside content in this conversation, so it may come from there.")
    tok = core_memory.REVEAL_USER.set(not cloud)
    try:
        if action == "add":
            r = core_memory.add(target, inp.get("text") or "", actor=actor)
        elif action == "replace":
            r = core_memory.replace(target, inp.get("old") or "", inp.get("text") or "", actor=actor)
        else:
            r = core_memory.remove(target, inp.get("old") or "", actor=actor)
    finally:
        core_memory.REVEAL_USER.reset(tok)
    if not r.get("ok"):
        return str(r.get("error") or "NOT DONE.")
    if r.get("unchanged"):
        if r.get("duplicate_of"):
            return ("Not saved: your core memory already says this as "
                    f"{r['duplicate_of']!r}. Use action=replace if the wording should change.")
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
