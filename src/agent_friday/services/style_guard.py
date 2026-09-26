"""What a personality style may not say, and what a profile may never become.

The first-run style block (services/setup_profile.py) reaches the system prompt
in the personality position. Personality is tunable: warmth, humour, how blunt,
how long. Two things are not:

* HONESTY. Friday tells the user when they are wrong and does not flatter them
  into a mistake. A style can make that gentler; it cannot switch it off. The
  HONEST LIMITS directive (model_router.REFUSAL_HONESTY_DIRECTIVE) and the
  anti-sycophancy text in the base prompt sit AFTER the style block, and this
  module removes any style sentence that argues with them.
* THE ACTION POLICY. Asking before a real-world action is the one rule between
  a model and the user's accounts (services/action_policy.py). A style sentence
  like "don't ask permission" is removed, and the policy is still appended last.

Removal is by sentence, not by span: a sentence with the override cut out of
it can read as a different instruction. The count of removed sentences is
returned so a caller can say something was dropped.

The second half is the clinical filter. The personality questions produce a
communication-style profile, never a psychological assessment. Model output is
checked for diagnostic language (disorders, conditions, attachment styles,
"issues"), and a sentence carrying it is dropped rather than shown or saved.
"""
from __future__ import annotations

import re

#: Sentences that would tune honesty or the approval policy away. Each pattern
#: names a behaviour, not a word: "agree" alone is ordinary vocabulary.
_ANTI_CONSTITUTION = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\balways agree\b",
    r"\bagree with (?:me|the user|everything|whatever|anything)\b",
    r"\bnever (?:push ?back|disagree|contradict|challenge|correct|argue)\b",
    r"\b(?:don't|do not|never|no need to) (?:push ?back|disagree|contradict|challenge|correct)\b",
    r"\b(?:don't|do not|never) (?:tell|say|point out|mention)\b[^.!?\n]{0,30}\bwrong\b",
    r"\b(?:tell|say)\b[^.!?\n]{0,20}\bwhat (?:i|they|he|she|the user) (?:want|wants|like|likes) to hear\b",
    r"\b(?:always )?(?:flatter|praise) (?:me|the user|them)\b",
    r"\byes[- ]man\b",
    r"\bbe (?:more )?sycophantic\b",
    r"\b(?:never|don't|do not) (?:admit|say) (?:that )?(?:you (?:don't|do not) know|you'?re (?:unsure|uncertain|wrong))\b",
    r"\bpretend (?:to|that)\b",
    r"\bmake (?:things|facts|it) up\b",
    r"\bignore (?:the |your |all )?(?:honesty|policy|policies|rules|guardrails|instructions|limits|safety)\b",
    r"\b(?:don't|do not|never|no need to|stop) (?:ask|asking|check|checking|confirm|confirming)\b[^.!?\n]{0,30}\b(?:permission|approval|approve|first|before|confirmation)\b",
    r"\bskip (?:the |any |all )?(?:approval|approvals|confirmation|confirmations|permission|permissions|approval cards?)\b",
    r"\bwithout (?:asking|approval|confirmation|confirming|checking|my (?:ok|okay|approval|permission))\b",
    r"\b(?:act|send|post|buy|pay|delete|publish)\b[^.!?\n]{0,30}\bwithout\b[^.!?\n]{0,20}\b(?:asking|approval|permission|checking)\b",
    r"\bfull (?:authority|autonomy|control)\b",
    r"\boverride\b[^.!?\n]{0,30}\b(?:policy|rules|approval|safety|honesty)\b",
))

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _authority_override(text: str) -> bool:
    try:
        from agent_friday.services.action_policy import contains_authority_override
        return contains_authority_override(text)
    except Exception:
        return False


def violates_constitution(text) -> bool:
    """True when `text` tries to tune away honesty or the approval policy."""
    if not text:
        return False
    s = str(text)
    return _authority_override(s) or any(rx.search(s) for rx in _ANTI_CONSTITUTION)


def sanitize(text) -> tuple[str, int]:
    """(`text` with every offending sentence removed, how many were removed).

    Lines are kept as lines, so a bulleted block stays a bulleted block. Never
    raises: this runs inside prompt assembly.
    """
    if not text:
        return ("" if text is None else str(text)), 0
    try:
        removed = 0
        out_lines = []
        for line in str(text).splitlines():
            if not line.strip():
                out_lines.append(line)
                continue
            # Keep a bullet marker and indentation, check the sentences after it.
            m = re.match(r"^(\s*(?:(?:[-*+]|\d+\.)\s*)?)(.*)$", line)
            lead, body = (m.group(1), m.group(2)) if m else ("", line)
            parts = [p for p in _SENTENCE_SPLIT.split(body) if p is not None]
            kept = []
            for p in parts:
                if p.strip() and violates_constitution(p):
                    removed += 1
                    continue
                kept.append(p)
            body2 = " ".join(k.strip() for k in kept if k.strip())
            if body2:
                out_lines.append(lead + body2)
            elif not body.strip():
                out_lines.append(line)
        return "\n".join(out_lines), removed
    except Exception:
        return str(text), 0


# ── The clinical filter ──────────────────────────────────────────────────────

#: Diagnostic and clinical language. A communication-style summary never needs
#: any of it, so a sentence containing it is dropped whole.
_CLINICAL = re.compile(r"\b(?:" + "|".join((
    r"diagnos\w*", r"disorders?", r"syndromes?", r"conditions?",
    r"adhd", r"autis\w*", r"asperger\w*", r"on the spectrum",
    r"neurodivergen\w*", r"neurotypical",
    r"depress\w*", r"anxiety", r"anxious attachment", r"bipolar", r"manic",
    r"ocd", r"obsessive[- ]compulsive", r"ptsd", r"trauma\w*", r"traumati\w*",
    r"narcissis\w*", r"borderline", r"schizo\w*", r"psychopath\w*",
    r"sociopath\w*", r"psychosis", r"psychotic", r"paranoi\w*",
    r"codependen\w*", r"attachment (?:style|issues?|wound)",
    r"(?:insecure|avoidant|anxious|disorgani[sz]ed) attachment",
    r"(?:mommy|mummy|mother|daddy|father) issues", r"oedipal", r"oedipus",
    r"repress\w*", r"neuros\w*", r"neurotic", r"patholog\w*",
    r"mental (?:health|illness)", r"mentally ill", r"therap\w*",
    r"counsel+ing", r"psychiatr\w*", r"medicat\w*", r"symptoms?",
    r"eating disorder", r"self[- ]harm", r"suicid\w*", r"addict\w*",
    r"personality type", r"introvert\w*", r"extrovert\w*",
)) + r")\b", re.IGNORECASE)


def has_clinical_language(text) -> bool:
    return bool(text) and bool(_CLINICAL.search(str(text)))


def strip_clinical(text) -> tuple[str, int]:
    """(`text` without any sentence carrying clinical language, how many)."""
    if not text:
        return ("" if text is None else str(text)), 0
    removed = 0
    kept = []
    for p in _SENTENCE_SPLIT.split(str(text)):
        if not p or not p.strip():
            continue
        if has_clinical_language(p):
            removed += 1
            continue
        kept.append(p.strip())
    return " ".join(kept), removed


def clean_model_prose(text) -> tuple[str, int]:
    """Both filters, for anything a model wrote about the user."""
    t, a = strip_clinical(text)
    t, b = sanitize(t)
    return t.strip(), a + b
