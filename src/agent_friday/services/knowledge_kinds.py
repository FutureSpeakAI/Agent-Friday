"""What kind of information is this? Six kinds, one vocabulary (north star NS-3.4-1).

Friday is handed information from many places in one prompt: the memory card,
recalled turns, her own summaries and guesses, suggestions waiting for the
owner, web pages, and things she holds but may not use. Untagged, they all read
alike, and a guess gets stated like a fact. Each kind below has a one-line
definition, a label for prompts and the UI, and the rule for which of
Friday's sources it covers.

    known          authoritative now: a live read of a connected service
                   (calendar or mail as read now, a file read now), and the
                   memory card lines the owner wrote or kept
    remembered     recalled from the past, with its date and source: a stored
                   conversation turn, a fact the owner approved
    inferred       Friday's own conclusion: session style inference, dreaming
                   and end-of-day summaries (model summaries), unapproved
                   proposals, relations a model extracted into the graph,
                   learned heuristics
    provisional    waiting for the owner: queued suggestions, pending
                   proposals, goal results nobody has checked yet
    unverifiable   read but not checkable: web pages and search results,
                   documents someone else wrote, tool output the taint ledger
                   (services/taint) records as outside "content"
    not_permitted  present but withheld: vault locked or gated for this
                   model, off the record, held back by the egress gate, a
                   forgotten person. Friday may say that something was
                   withheld; never what it was.

Labels only. Nothing here decides what is included in a prompt or who may see
it; the gates that do that are unchanged.
"""
from __future__ import annotations

from typing import Optional

KNOWN = "known"
REMEMBERED = "remembered"
INFERRED = "inferred"
PROVISIONAL = "provisional"
UNVERIFIABLE = "unverifiable"
NOT_PERMITTED = "not_permitted"

KINDS = (KNOWN, REMEMBERED, INFERRED, PROVISIONAL, UNVERIFIABLE, NOT_PERMITTED)

#: One line each: what the kind means.
DEFINITIONS = {
    KNOWN: "Authoritative now: a live read of a connected service or file, or a memory card "
           "line the owner wrote or kept.",
    REMEMBERED: "Recalled from an earlier conversation or an approved fact, with its date and "
                "source; true when it was said, not necessarily now.",
    INFERRED: "Friday's own conclusion (a guess, a summary, a style read, an extracted "
              "relation); nobody has confirmed it.",
    PROVISIONAL: "Waiting for the owner: a suggestion, proposal or result not yet kept or "
                 "checked.",
    UNVERIFIABLE: "Something Friday read (a web page, a document someone else wrote, outside "
                  "tool output) that she cannot check.",
    NOT_PERMITTED: "Present but withheld from Friday here (locked, off the record, gated, "
                   "forgotten); only the fact that something was withheld may be said.",
}

#: The word a prompt or the UI shows for each kind.
_WORDS = {
    KNOWN: "KNOWN",
    REMEMBERED: "REMEMBERED",
    INFERRED: "INFERRED",
    PROVISIONAL: "PROVISIONAL",
    UNVERIFIABLE: "UNVERIFIABLE",
    NOT_PERMITTED: "NOT PERMITTED",
}

#: The short plain-words gloss that follows the word when no detail is given.
_GLOSS = {
    KNOWN: "authoritative",
    REMEMBERED: "from past conversations, dated",
    INFERRED: "Friday's guesses, not confirmed",
    PROVISIONAL: "waiting for you",
    UNVERIFIABLE: "read, cannot be checked",
    NOT_PERMITTED: "withheld",
}

#: Which kind each of Friday's sources is. Names are the fence sources
#: (services/memory_fence), the prompt blocks and the stores that feed them.
SOURCE_KINDS = {
    # known
    "live_state": KNOWN,
    "live_read": KNOWN,
    "calendar_now": KNOWN,
    "mail_now": KNOWN,
    "file_now": KNOWN,
    "memory_card": KNOWN,
    "core_memory": KNOWN,
    # remembered
    "past_conversations": REMEMBERED,
    "conversation_turn": REMEMBERED,
    "approved_fact": REMEMBERED,
    # inferred
    "last_session_summary": INFERRED,
    "session_summary": INFERRED,
    "model_summary": INFERRED,
    "dream": INFERRED,
    "consolidation": INFERRED,
    "user_model": INFERRED,
    "session_style": INFERRED,
    "learned_heuristics": INFERRED,
    "unapproved_proposal": INFERRED,
    "kg_extracted": INFERRED,
    # provisional
    "queued_suggestion": PROVISIONAL,
    "pending_proposal": PROVISIONAL,
    "goal_unverified": PROVISIONAL,
    # unverifiable
    "web_page": UNVERIFIABLE,
    "web_search": UNVERIFIABLE,
    "untrusted_document": UNVERIFIABLE,
    "untrusted_tool_output": UNVERIFIABLE,
    # not permitted
    "vault_locked": NOT_PERMITTED,
    "vault_gated": NOT_PERMITTED,
    "off_record": NOT_PERMITTED,
    "egress_gated": NOT_PERMITTED,
    "forgotten_person": NOT_PERMITTED,
}

#: The taint ledger's origin kinds (services/taint.Origin.kind) as knowledge
#: kinds: what the owner typed or their own data is known, outside content and
#: a message's sender are unverifiable, a value Friday wrote herself is inferred.
TAINT_KINDS = {
    "user": KNOWN,
    "own": KNOWN,
    "content": UNVERIFIABLE,
    "sender": UNVERIFIABLE,
    "model": INFERRED,
}


def kind_for(source: str, default: str = UNVERIFIABLE) -> str:
    """The kind of a named source. An unknown source is unverifiable: a
    label that overclaims is worse than one that underclaims."""
    return SOURCE_KINDS.get(str(source or "").strip().lower(), default)


def kind_for_taint(origin_kind: str) -> str:
    """The kind of a value by its taint-ledger origin."""
    return TAINT_KINDS.get(str(origin_kind or "").strip().lower(), UNVERIFIABLE)


def _check(kind: str) -> str:
    if kind not in _WORDS:
        raise ValueError(f"unknown knowledge kind: {kind!r}")
    return kind


def word(kind: str) -> str:
    """The bare word: 'KNOWN', 'NOT PERMITTED'."""
    return _WORDS[_check(kind)]


def label(kind: str, detail: Optional[str] = None) -> str:
    """'INFERRED (Friday's guesses, not confirmed)'; `detail` replaces the gloss."""
    return f"{word(kind)} ({detail or _GLOSS[kind]})"


def header(kind: str, detail: Optional[str] = None) -> str:
    """A section header line for a prompt: the label and a colon."""
    return label(kind, detail) + ":"


def tag(kind: str) -> str:
    """An inline tag for one line or a fence opening: '[UNVERIFIABLE]'."""
    return f"[{word(kind)}]"


def withheld_note(why: str = "") -> str:
    """What stands in for withheld content: the fact, never the content."""
    return "(" + word(NOT_PERMITTED) + ": withheld from this model" + (f": {why}" if why else "") + ")"


#: Told once, in the memory section of the system prompt (model_router).
STANDING_INSTRUCTION = (
    "HOW TO SPEAK ABOUT WHAT YOU HOLD: each block says its kind. KNOWN: state it plainly. "
    "REMEMBERED: say when it was said (\"on 3 May you said...\"); it may have changed. "
    "INFERRED: hedge it (\"I think\", \"it seems\"); it is your guess. PROVISIONAL: say it "
    "is not confirmed yet. UNVERIFIABLE: say where it came from and that you cannot check "
    "it. NOT PERMITTED: never reveal or guess its content; you may say there is something "
    "you are not allowed to use.")


def ui_kinds() -> list:
    """The six kinds for the UI: id, word, gloss, definition."""
    return [{"kind": k, "word": _WORDS[k], "label": label(k), "definition": DEFINITIONS[k]}
            for k in KINDS]
