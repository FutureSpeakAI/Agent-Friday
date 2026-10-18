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
         "old": {"type": "string", "description": "Text of the entry to change (replace, remove)."},
         "when": {"type": "string", "description": "For a friday lesson: when it applies."}},
         "required": ["action"]}},
]
RINGS = {"update_core_memory": 1}


#: How much of an entry's content words must appear in the owner's own words
#: for it to save without asking (see `_rests_on_owner_words`).
OWNER_WORDS_COVERAGE = 0.6
#: How much of the WHOLE line a replace or remove changes must appear in the
#: owner's own words ("from Springfield to Denver" names "Lives in Springfield.").
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
    if target == "friday" and action in ("replace", "remove"):
        # The whole line `old` points at, so its learning record is found.
        _line, _miss = core_memory.resolve_line(target, inp.get("old") or "")
        if _line and not _miss:
            inp = dict(inp, old=_line)
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
    if target == "friday" and not r.get("unchanged"):
        _record_lesson(action, inp, actor)
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


def _record_lesson(action: str, inp: dict, actor: str) -> None:
    """Give a FRIDAY.md change its learning record (services/lesson_records):
    when it applies, what taught it (this call's conversation, turn, task),
    and, for a rewording, the line it replaced. Best-effort; never blocks."""
    try:
        from agent_friday.services import lesson_records as _lr
        when = str(inp.get("when") or "").strip() or "every conversation (always loaded)"
        taught = _lr.current_taught_by(source=actor)
        if action == "add":
            _lr.note_taught("lesson", _lr.lesson_ref(inp.get("text") or ""),
                            applies_when=when, taught_by=taught)
        elif action == "replace":
            old = _lr.record_id("lesson", _lr.lesson_ref(inp.get("old") or ""))
            _lr.note_taught("lesson", _lr.lesson_ref(inp.get("text") or ""),
                            applies_when=when, taught_by=taught, replaces=old)
        elif action == "remove":
            _lr.note_status("lesson", _lr.lesson_ref(inp.get("old") or ""), "retired")
    except Exception:
        pass


#: The path a memory-card hit carries in search_wiki results.
CARD_HIT_PATH = "Memory card (About the user)"


def _card_withheld_from_reader() -> bool:
    """The lines about the owner may not reach this reader: the same rule as the
    prompt head (services/model_router.memory_head_blocks), which gates them for
    a model off this PC only while the owner keeps the vault local."""
    if not _cloud_reader():
        return False
    try:
        from agent_friday.services.model_router import _vault_local_only
        return bool(_vault_local_only())
    except Exception:
        return True                                   # cannot tell: withhold


def search_card(query: str, limit: int = 3) -> list:
    """Memory-card lines about the owner that a search_wiki query asks for.

    The card is a separate store from the wiki, and the facts the owner gave
    ("my husband is 50") live there: a search for the family must find them.
    A line matches when it shares a word of three letters or more with the
    query, or when the query asks about the household and the line is the
    household line. Hits are search_wiki hits with root "memory_card"."""
    from agent_friday.services import household_facts as hf
    words = {w for w in core_memory.content_words(query) if len(w) >= 3}
    if not words:
        return []
    asks_family = bool(words & hf.FAMILY_QUERY_WORDS)
    if not _owner_reader():
        return []
    hits = []
    for line in core_memory.entries("user"):
        if words & core_memory.content_words(line) or (asks_family and hf.is_household_line(line)):
            hits.append(line)
    if not hits:
        return []
    if _card_withheld_from_reader():
        return [{"root": "memory_card", "path": CARD_HIT_PATH,
                 "excerpt": (f"{len(hits)} line(s) about the user match; withheld from this model, "
                             "they stay on this PC (a local model can read them).")}]
    return [{"root": "memory_card", "path": CARD_HIT_PATH, "excerpt": h} for h in hits[:limit]]


def _owner_reader() -> bool:
    """May the lines about the owner go into this call's result? Not when the call answers a remote
    origin (a text message, a channel: agent._CURRENT_ORIGIN), and not when the speaker is not the
    owner (laya_personal.current_profile is None). Cannot tell: no."""
    try:
        from agent_friday.services import agent as _ag
        if str(_ag._CURRENT_ORIGIN.get() or ""):
            return False
        from agent_friday.services import laya_personal
        return laya_personal.current_profile() is not None
    except Exception:
        return False


def _active_profile():
    """The profile this turn speaks for, or None when it cannot be resolved."""
    try:
        from agent_friday.services import laya_personal
        return laya_personal.current_profile()
    except Exception:
        return None


def _minor_profile(profile_id) -> bool:
    """A family / minor profile keeps no words (services/laya_personal); unknown is minor."""
    try:
        from agent_friday.services import laya_personal
        return bool(laya_personal.is_minor(profile_id))
    except Exception:
        return True


def capture_household(message: str, ctx: dict) -> dict:
    """Save the household basics the owner just typed, under the card's rule.

    Called once per typed chat turn (agent.prepare_confirmation_ctx) with the
    owner's own message, before any model sees it. services/household_facts
    reads exactly what the message states; nothing a tool returned is read.

    It saves silently only when all of these hold; otherwise it is queued for
    the owner's OK on the card:
      * the turn is the owner's own line typed in the app's page on this PC
        (ctx["owner_surface"], from reflex_chat.trusted_surface over the live
        request: not a phone, a channel, a relay, a script or voice);
      * the active profile resolves (laya_personal.current_profile);
      * no sentence it was read from appears in earlier tool output in this
        conversation (taint.read_source_of): text pasted from a page or a
        mail is not the owner's own statement;
      * no household line is on the card yet: a different statement is
        queued as a replace, never rewritten silently.
    A phone, channel or background origin, or a minor profile, keeps nothing.

    Returns {"status": "saved"|"queued"|"unchanged"|"none"|"skipped", "text", "note"};
    `note` is one paragraph for the model's turn context, empty when nothing changed.
    """
    from agent_friday.services import household_facts as hf
    ctx = ctx if isinstance(ctx, dict) else {}
    out = {"status": "none", "text": "", "note": ""}
    try:
        facts = hf.extract(message)
    except Exception:
        facts = None
    if not facts:
        return out
    surface = str(ctx.get("surface") or "")
    profile = _active_profile()
    if (ctx.get("origin") or ctx.get("is_background_task") or ctx.get("task_id")
            or surface.startswith("voice") or _minor_profile(profile)):
        return {**out, "status": "skipped"}
    import time as _t
    text = hf.entry_text(facts, _t.strftime("%b %Y"))
    existing = [e for e in core_memory.entries("user") if hf.is_household_line(e)]
    old = existing[-1] if existing else ""
    if old and hf.facts_of(text) <= hf.facts_of(old):
        return {**out, "status": "unchanged", "text": old}
    why, own_words = "", True
    if ctx.get("owner_surface") is not True:
        why, own_words = "It did not come from the owner typing in the app on this PC.", False
    elif profile is None:
        why, own_words = "Friday could not tell whose profile this turn speaks for.", False
    else:
        try:
            from agent_friday.services import taint as _taint
            key = _taint.ledger_key(ctx)
            seen = next((src for src in (_taint.read_source_of(key, sent)
                                         for sent in facts.get("sentences") or []) if src), "")
        except Exception:
            seen = "something Friday could not check"
        if seen:
            why, own_words = "The same words appear in %s earlier in this conversation." % seen[:120], False
    if not why and old:
        why = "It would rewrite the household line already on the card."
    if not why:
        r = core_memory.add("user", text, actor="friday")
        if r.get("ok") and r.get("unchanged"):
            return {**out, "status": "unchanged", "text": text}
        if not r.get("ok"):
            why = "The card could not take it as it is: " + str(r.get("error") or "")[:160]
    if why:
        q = core_memory.queue("user", "replace" if old else "add", text=text, old=old,
                              source="friday",
                              reason=(("The user stated their household in their own words. "
                                       if own_words else "A household statement in a chat turn. ") + why))
        if not q.get("ok"):
            return out
        return {"status": "queued", "text": text,
                "note": ("MEMORY CARD: the household facts the user just stated are waiting for "
                         "their OK on the Memory card (Settings > Privacy & Data > Memory card, "
                         "'Waiting for your review'). " + why + " Tell them in one sentence; do "
                         "not call update_core_memory for these facts again.")}
    return {"status": "saved", "text": text,
            "note": ("MEMORY CARD: the household facts the user just stated were saved to their "
                     "memory card exactly as stated: \"" + text + "\" Use them in this "
                     "conversation; from the next one they are in your core memory. Do not save "
                     "them again; tell the user in a few words that you noted it.")}


HANDLERS = {"update_core_memory": _tool_update_core_memory}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
