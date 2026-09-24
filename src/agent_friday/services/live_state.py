"""live_state — questions about the world RIGHT NOW, which recall cannot answer.

THE RULE THIS MODULE EXISTS TO ENFORCE:

    LIVE STATE IS NEVER ANSWERABLE FROM MEMORY.

There are two kinds of question a user can ask, and they need different
machinery:

  RECALLED-FACT questions ask what was established, decided, said or preferred.
      "What did we decide about the launch date?"  "What's my sister's name?"
      "What did I say I wanted the tone to be?"
      Memory is the right and only source. The answer does not decay, and if it
      does, the user is the one who changes it.

  LIVE-STATE questions ask what is true of the world at this instant.
      "Are my Google accounts connected?"  "Is the model loaded?"
      "How much disk is free?"  "Is the server reachable?"
      Recall is STRUCTURALLY INCAPABLE of answering these correctly. Not
      unreliable -- incapable. A memory can only report what was true when it
      was written, and the entire content of a live-state question is what is
      true now. A recalled answer is not a stale fact; it is a category error
      that happens to be phrased as a fact, and it will be delivered with the
      full confidence of a real memory, complete with a citation.

WHY THIS IS WRITTEN DOWN RATHER THAN JUST HANDLED:

A settings page that shows expired Google accounts as "connected" is read by
the user, who tells Friday what they saw. Friday stores that sentence as a
user-authored fact -- the highest-trust source it has -- and from then on
answers "are my Google accounts connected?" by retrieving the user's own
sentence and citing them, never consulting anything live. Every calendar
answer after that is confidently wrong.

Note what does NOT catch it. The claim-verifier cannot: nothing is
fabricated. The tool layer cannot: no tool is called. Fixing the settings
page cannot: the memory was written before the fix. The only
defence is structural -- a question of this class must be routed to a live
source before recall is ever consulted.

ADDING A NEW STATUS QUESTION:

Append a Probe to PROBES below. That is the whole job: classification, the
recall caveat, and the authoritative answer all follow from the registration,
so the default path for a new status readout is the correct one. A probe's
`answer` must call something live -- a store read, a socket, a syscall. If you
find yourself writing a probe that reads a cached summary written by an earlier
turn, you have reintroduced the bug this module exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Probe:
    """One class of live-state question and the live source that answers it."""

    key: str
    # What the question is about, in words a maintainer will recognise.
    subject: str
    # Lowercase regex fragments; any match classifies the question.
    patterns: tuple
    # Called with no arguments. MUST consult something live. Returns the text
    # handed to the model as authoritative, or "" if it cannot determine the
    # answer (in which case the model is told it does not know -- never that
    # recall may fill the gap).
    answer: Callable[[], str] = field(repr=False)


# ── probes ──────────────────────────────────────────────────────────────────

def _google_accounts_answer() -> str:
    """Read the account store now. Never a cached or remembered verdict."""
    try:
        from agent_friday.services import google_accounts as ga
        s = ga.accounts_summary()
    except Exception as e:
        return (f"Friday could not read the Google account store just now "
                f"({type(e).__name__}), so it does not know whether the "
                f"accounts are connected. Say that you cannot tell, and do not "
                f"guess from anything said earlier.")
    if s["total"] == 0:
        return ("LIVE: no Google account has ever been connected to Friday on "
                "this machine.")
    if not s["connected"]:
        names = "; ".join(
            f"{a.get('email') or a.get('label')} ({a.get('summary')})"
            for a in s["needs_attention"])
        return (f"LIVE: 0 of {s['total']} Google account(s) are working. NOT "
                f"connected: {names}. Answer NO, name the accounts, and point "
                f"the user at Settings -> Accounts & Keys -> Google -> Reconnect.")
    if s["degraded"]:
        names = "; ".join(
            f"{a.get('email') or a.get('label')} ({a.get('summary')})"
            for a in s["needs_attention"])
        return (f"LIVE: {s['healthy']} of {s['total']} Google account(s) are "
                f"working. Answer PARTIALLY -- do not say yes. Broken: {names}.")
    return (f"LIVE: all {s['total']} Google account(s) are connected and "
            f"working right now.")


PROBES: tuple = (
    Probe(
        key="google_accounts",
        subject="whether the user's Google accounts are connected/working",
        patterns=(
            r"\bgoogle\b.{0,40}\b(account|accounts|connected|connection|linked|"
            r"working|authoriz|authoris|sync)",
            r"\b(account|accounts|gmail|calendar|drive)\b.{0,30}\b(connected|"
            r"linked|working|authoriz|authoris)\b",
            r"\bam i (still )?(connected|linked|signed in)\b.{0,30}\bgoogle\b",
            r"\bis (my|the) (gmail|calendar|drive)\b.{0,30}\b(connected|working)\b",
        ),
        answer=_google_accounts_answer,
    ),
)


# ── classification ──────────────────────────────────────────────────────────

def classify(message: str) -> Probe | None:
    """Return the Probe for a LIVE-STATE question, or None for everything else.

    None means "this is a recalled-fact question (or not a state question at
    all)" -- recall proceeds normally. Only an affirmative match diverts.
    """
    text = " ".join((message or "").lower().split())
    if not text:
        return None
    for probe in PROBES:
        for pat in probe.patterns:
            if re.search(pat, text):
                return probe
    return None


def annotate_user_turn(message: str) -> str:
    """The user's message with the live reading attached, or it unchanged.

    The system-prompt block (live_state_block) is necessary but not sufficient.
    The transcript replayed into `messages` is memory too, and it carries the
    assistant's OWN earlier answers -- for example, several turns of "yep,
    they're connected" sitting closer to the question than any system text.
    A small local model continues its own recent voice.

    So the live reading is also attached to the turn being answered, where
    nothing sits between it and the question.
    """
    block = live_state_block(message)
    if not block:
        return message
    return f"{message}\n{block}"


def live_state_block(message: str) -> str:
    """The prompt block for a live-state question, or "" if it is not one.

    Two jobs, and the second matters as much as the first: it supplies the
    authoritative answer, AND it explicitly withdraws recall's licence to
    answer this question. Without the second half the model has both a live
    reading and a remembered one in front of it, and the remembered one comes
    with a citation and the user's own voice behind it.
    """
    probe = classify(message)
    if probe is None:
        return ""
    try:
        answer = probe.answer() or ""
    except Exception:
        answer = ""
    if not answer:
        answer = (f"Friday could not determine {probe.subject} just now. Say "
                  f"you cannot tell right now.")
    return (
        "\n== LIVE STATE (authoritative, read just now) ==\n"
        f"The user is asking about {probe.subject}. This is a question about "
        "the world RIGHT NOW.\n"
        f"{answer}\n"
        "Answer from the line above and nothing else. Recalled conversations "
        "CANNOT answer this: they record what was true when they were written, "
        "which is a different question. If an excerpt below contradicts this "
        "line -- including one where the user themselves said it was working -- "
        "the line above is correct and the excerpt is out of date. Do not cite "
        "a conversation as evidence about current status.\n"
    )
