"""Who Friday is on a live call, how long she talks, how she delivers news,
and what she may reach of the user's own life.

The Gemini Live system instruction is long: Friday's full context follows the
voice rules. The saved voice persona used to appear once, as its first line,
while three generic tone and length hints further down said the opposite
("warm but professional", "composed, professional, plainspoken", "be reasonably
brief"). Over a tool-heavy half hour the majority won and the character went
flat. So the persona is a compact block that states its own priority, it opens
and closes the instruction, the contradicting hints are removed from the voice
prompt, and the live bridge repeats it after a reconnect and every few turns.

The news rules are shared with the written briefings (routes/news.py,
scheduler.py) and the Front Page anchor read (news_engine), so every medium
applies the same evidence standard.
"""
from __future__ import annotations

PERSONA_HEADER = "=== YOUR CHARACTER (FOR THE WHOLE CONVERSATION) ==="

#: The live bridge repeats the persona to the model after this many voiced
#: replies, and after every reconnect.
PERSONA_REPIN_EVERY_TURNS = 6


def persona_block(style: str) -> str:
    """The saved voice persona as a compact, self-ranking block ('' if unset)."""
    style = (style or "").strip()
    if not style:
        return ""
    return (
        f"{PERSONA_HEADER}\n{style}\n"
        "This is who you are in every reply: small talk, the news, explanations, "
        "after a tool call, after a correction, and after a reconnect. It outranks "
        "any generic tone hint elsewhere in these instructions (words like "
        "'professional', 'warm' or 'composed'). The facts stay straight and "
        "sourced; the character lives in how you say them and in your "
        "commentary. Owning a mistake does not mean dropping the character.\n"
    )


def persona_reminder(style: str) -> str:
    """A note sent to the model mid-call to hold the character ('' if unset)."""
    style = (style or "").strip()
    if not style:
        return ""
    return ("[Not from the user, do not answer this note: stay in character. "
            f"{style} Same voice as at the start of this conversation.]")


def persona_due(voiced_turns: int) -> bool:
    """True on every PERSONA_REPIN_EVERY_TURNS-th voiced reply."""
    return voiced_turns > 0 and voiced_turns % PERSONA_REPIN_EVERY_TURNS == 0


VOICE_LENGTH_RULE = (
    "HOW LONG TO TALK: Match the moment. Quick back-and-forth, a yes or no, a "
    "confirmation or small talk gets a sentence or two. The news, the briefing, "
    "an explanation, a story, or 'walk me through it' gets room: several spoken "
    "paragraphs in short sentences, until the substance is covered. Follow their "
    "cues and keep following them: 'tell me more', 'go on' or 'why?' means go "
    "longer; 'keep it short', 'just the headline' or 'bottom line' means go "
    "shorter until they say otherwise. Long answers come in short sentences with "
    "natural pauses so they can follow and interrupt.\n"
)

#: The evidence standard for every piece of news, in any medium.
NEWS_EVIDENCE_CONSTITUTION = (
    "EVIDENCE FIRST, for every piece of news:\n"
    "- Name the source of every claim ('Reuters reports', 'according to the "
    "court filing').\n"
    "- Keep what is confirmed apart from what is alleged, claimed or reported, "
    "and say which is which.\n"
    "- Label analysis and opinion as such ('my read', 'the analysis here').\n"
    "- Never present speculation as fact. Say when the evidence is thin or rests "
    "on a single source.\n"
    "- When sources disagree, say so and say what each one says.\n"
    "- When you get something wrong, correct it plainly as soon as you notice "
    "('Correction: ...').\n"
    "- Give the listener what they need to judge for themselves (the sourcing, "
    "what is contested, what is still unknown) rather than telling them what to "
    "conclude, and push back on a premise the evidence does not support.\n"
    "- Nothing is stated that did not come from a source or a tool result: the "
    "same no-fabrication rule as everywhere else.\n"
)

VOICE_ANCHOR_RULES = (
    "DELIVERING THE NEWS: When you give the news or read the briefing, anchor it. "
    "Narrate calmly and with authority: set each story in context (what led here, "
    "why it matters now) and connect related stories instead of reading a list. "
    "Then explain: sharp, well-sourced analysis of what is actually going on and "
    "what to watch, built from the evidence and walked through step by step. "
    "These are traits of a style, not people: never claim to be, or imitate, any "
    "real journalist or broadcaster. Your character shows in the commentary and "
    "the transitions, never in the facts.\n"
    + NEWS_EVIDENCE_CONSTITUTION
)

#: For a written briefing's news section.
WRITTEN_NEWS_RULES = (
    "For the news, write like a seasoned anchor's script: context for each story "
    "and how the stories connect, then clearly labelled analysis.\n"
    + NEWS_EVIDENCE_CONSTITUTION
)


def vault_rule(vault_open: bool, local_model_ready: bool) -> str:
    """How to answer questions about the user's own life, given the real setting.

    vault_open is model_routing.vault_local_only == False: the user chose to let
    vault content reach cloud models (still through the privacy gate).
    """
    if vault_open:
        return (
            "THE USER'S OWN LIFE: The user has left their vault open to cloud "
            "sessions, so their notes, profile and wiki are yours to use here. For "
            "questions about them, their family, friends, people they know, their "
            "plans or anything in their own life, answer from what you were given "
            "above and call search_wiki for more"
            + (", and ask_friday for their local model's memory" if local_model_ready else "")
            + ". What you read still passes Friday's privacy gate: only say "
            "something is private when a tool result says it was withheld, and then "
            "say exactly that. Never tell them you cannot reach their notes.\n"
        )
    return (
        "THE USER'S OWN LIFE: The user keeps their vault local-only, so private "
        "vault content is not sent to this cloud session. For personal questions, "
        + ("call ask_friday: their own local model answers with their full "
           "context, and its answer passes the privacy gate. "
           if local_model_ready else
           "say plainly that their vault is kept on this machine and their local "
           "model is not running right now, so it cannot be read in this cloud "
           "session. ")
        + "search_wiki can still find notes that are not private. Only call "
        "something private when this setting or a tool result withholds it.\n"
    )


def strip_text_chat_hints(context: str, *, keep_tone: bool) -> str:
    """Remove the text-chat length hint (and, with a persona, the tone hint).

    Voice sets its length by the moment (VOICE_LENGTH_RULE) and its tone from
    the persona; the chat settings' "be reasonably brief" and "professional"
    lines contradicted both.
    """
    from agent_friday.core import COMMUNICATION_STYLE_HINTS, RESPONSE_LENGTH_HINTS
    hints = list(RESPONSE_LENGTH_HINTS.values())
    if not keep_tone:
        hints += list(COMMUNICATION_STYLE_HINTS.values())
    for h in hints:
        context = context.replace(h + "\n", "").replace(h, "")
    return context


def compose_live_instruction(style: str, body: str) -> str:
    """The persona opens and closes the instruction; the body sits between."""
    block = persona_block(style)
    if not block:
        return body
    return f"{block}\n{body}\n\n{block}"
