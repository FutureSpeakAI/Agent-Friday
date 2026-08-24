"""Source Production Mode: does the reply actually carry citations?

`response_provenance` already answers the harder question — is a citation
BACKED by something an executed tool touched — and rewrites the ones that are
not into inert markers. It is good and this module does not touch it.

The gap it leaves is the simpler question nobody was asking: **were there any
citations at all?** Verification that runs over the citations it is given never
notices being given none, so with the mode on, four confident factual
paragraphs and zero citations passed silently. That makes the toggle a promise
the system does not keep.

What this module is
-------------------
A HEURISTIC that decides whether a reply looks like it makes checkable factual
claims, plus a count of the citations it carries. It is deliberately biased
towards saying "I cannot tell" over saying "this is unsourced", because the
failure mode of the noisy direction is worse: a citation warning on a greeting
teaches the user to switch the mode off, and then the honest one never gets
read either (KNOWN_ISSUES.md §1, the wallpaper rule).

What it is NOT
--------------
It is not a claim detector, and it should not be described as one. It cannot:

  * tell a factual claim from a confident opinion ("the 12b is the better
    seat" reads as a claim to it and is a judgement);
  * see that a claim is already sourced by surrounding conversation;
  * understand a claim expressed without any of the surface markers below —
    "the deadline moved" has no number, no proper noun and no attribution
    verb, and this module will not flag a reply made only of sentences like
    that;
  * cope with a language other than English.

The first and third are the ones that matter. The first makes it over-trigger
on argumentative replies; the third makes it under-trigger on plain prose. Both
are why `assess()` returns COUNTS and a confidence, not a verdict, and why the
caller only enforces on `confident=True`.

If this ever needs to be right rather than roughly right, the answer is a model
call, not a longer regex — and that is a design decision with its own latency
cost, not a tweak to this file.

STATUS, 2026-08-24: THE HEURISTIC IS NOT GOOD ENOUGH TO ENFORCE ON, and
`routes/chat.py` therefore uses it to MEASURE ONLY. `RETRY_INSTRUCTION` and
`UNSOURCED_NOTICE` below are written, reviewed and unwired, waiting on a
decision about the claim judgement.

The evidence, measured rather than argued. Asked "what changed in EU AI
regulation during 2024?" with the mode on, the reply came back with five
sentences and zero citations, and this module scored ONE claim-shaped sentence.
The four it missed:

    It shifted the regulatory focus toward a "risk-based" approach...
    It also introduced specific transparency requirements for GPAI models...
    Key shifts during the year included the formalization of rules for
      systemic risks posed by large-scale models...
    Additionally, the legislation introduced strict protections for
      fundamental rights...

Every one is a claim a journalist must source. Not one carries a digit, an
attribution verb, or a proper noun this pattern can see. They are factual
because of what they MEAN, and the surface gives nothing away.

The tempting fix is a fifth marker for verbs of enactment — introduced,
established, required, banned. It catches all four. It also catches "the 12b is
the better seat", which is a judgement, and "the clamp reduced the boxes from
thirteen to three", which is a claim about our own work rather than the world.
There is no surface feature separating a factual assertion from a confident
opinion, which is the actual reason this is hard, and no number of extra
patterns reaches it.
"""
from __future__ import annotations

import re

# Every citation grammar CITATION_INSTRUCTIONS teaches, plus the inert form
# provenance rewrites unbacked web citations into. `[unverified-web:...]` COUNTS
# as a citation attempt here on purpose: the model did its job and cited; the
# provenance layer judged the backing. Counting it as "no citation" would
# punish the model twice for one fault and send a correct reply round again.
CITATION_RE = re.compile(
    r"\[(?:web|unverified-web|wiki|news|memory|conversation):[^\]]+\]")

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# ── Sentences that are NOT factual claims ────────────────────────────────────
# First person about Friday's own state, plans, or limits. "I could not reach
# the calendar" is a report about the run, not a claim about the world.
_SELF_RE = re.compile(
    r"^\s*(?:i|i'll|i'm|i've|i'd|let me|shall i|would you|do you|should i|"
    r"here'?s|that'?s|this is|sure|okay|ok|yes|no|thanks|thank you|got it|"
    r"understood|sorry|hello|hi|hey|good morning|good evening)\b",
    re.I)
# Hedged = offered as judgement, not asserted as fact.
_HEDGE_RE = re.compile(
    r"\b(?:i think|i believe|in my view|my guess|probably|might be|may be|"
    r"seems|appears to|arguably|i suspect|if i had to)\b", re.I)

# ── Surface markers of a checkable claim ─────────────────────────────────────
_NUMBER_RE = re.compile(r"\b\d")
_ATTRIB_RE = re.compile(
    r"\b(?:according to|reported|reports|announced|said|stated|published|"
    r"confirmed|found that|study|survey|data show|filing|court)\b", re.I)
# A capitalised word that is not sentence-initial and not the pronoun I —
# a weak proper-noun signal, which is why it never fires alone.
_PROPER_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z]{2,}\b")
_SUPERLATIVE_RE = re.compile(
    r"\b(?:most|largest|smallest|first|only|fastest|biggest|highest|lowest|"
    r"best|worst|majority|percent|%)\b", re.I)

#: Below this a reply is conversational by length alone. A two-line answer is
#: not the four-paragraph unsourced essay this exists to catch.
MIN_WORDS = 25

#: How many claim-shaped sentences before we are willing to say so out loud.
#: Two, not one, because one marker-bearing sentence in an otherwise
#: conversational reply is exactly where this heuristic is least trustworthy.
MIN_CLAIM_SENTENCES = 2


def count_citations(reply: str) -> int:
    return len(CITATION_RE.findall(reply or ""))


def _sentence_is_claimlike(s: str) -> bool:
    s = s.strip()
    if len(s.split()) < 4:
        return False
    if s.endswith("?"):
        return False
    if _SELF_RE.search(s):
        return False
    if _HEDGE_RE.search(s):
        return False
    markers = 0
    if _NUMBER_RE.search(s):
        markers += 1
    if _ATTRIB_RE.search(s):
        markers += 1
    if _SUPERLATIVE_RE.search(s):
        markers += 1
    if _PROPER_RE.search(s):
        markers += 1
    # Two independent markers. One alone is too easy to hit by accident: a
    # capitalised product name, or "3 things to try".
    return markers >= 2


def assess(reply: str) -> dict:
    """Counts, not a verdict.

    Returns:
      citations        how many citation tokens of any grammar the reply has
      claim_sentences  how many sentences carry >= 2 claim markers
      sentences        total sentences considered
      words            total words
      confident        True only when we are willing to act on this
      reason           why, in words, for the log and for a human reading it
    """
    text = (reply or "").strip()
    words = len(text.split())
    cites = count_citations(text)
    sentences = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    claims = [s for s in sentences if _sentence_is_claimlike(s)]

    out = {
        "citations": cites,
        "claim_sentences": len(claims),
        "sentences": len(sentences),
        "words": words,
        "confident": False,
        "reason": "",
    }
    if cites:
        out["reason"] = "reply carries %d citation(s); nothing to enforce" % cites
        return out
    if words < MIN_WORDS:
        out["reason"] = ("too short to judge (%d words, under the %d-word floor)"
                         % (words, MIN_WORDS))
        return out
    if len(claims) < MIN_CLAIM_SENTENCES:
        out["reason"] = ("%d claim-shaped sentence(s) of %d — below the %d "
                         "needed to call this a sourced-argument reply"
                         % (len(claims), len(sentences), MIN_CLAIM_SENTENCES))
        return out
    out["confident"] = True
    out["reason"] = ("%d of %d sentences carry two or more claim markers and "
                     "the reply cites nothing" % (len(claims), len(sentences)))
    return out


#: Handed to the model on the second attempt. Concrete about what was wrong,
#: because "cite your sources" is what it was already told and did not do.
RETRY_INSTRUCTION = (
    "STOP. Your previous reply made factual claims and contained no citations, "
    "and Source Production Mode is on.\n"
    "Rewrite it. Every factual claim must end with one of: [wiki:page], "
    "[news:outlet/YYYY-MM-DD/headline], [memory:YYYY-MM-DD/\"quote\"], "
    "[web:https://url].\n"
    "Cite ONLY what is actually in your context or tool results. If you cannot "
    "source a claim, either drop the claim or say plainly that you cannot "
    "source it. Do not invent a citation, a URL, a date or an outlet — an "
    "invented citation is a worse failure than an uncited sentence."
)

#: What the user sees when the second attempt is still unsourced. Marked, not
#: hidden and not silently allowed: the reply may well be right, and the point
#: is that nothing here was checked.
UNSOURCED_NOTICE = (
    "\n\n> ⚠ **Unsourced.** Source Production Mode is on, and this answer still "
    "arrived without citations after I asked for them twice. Nothing above has "
    "been checked against a retrieved source — treat it as recollection, not "
    "reporting."
)
