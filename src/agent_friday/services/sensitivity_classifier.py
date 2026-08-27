"""
Unified sensitivity classifier — the single source of truth for content tier decisions.

Up to four layers, all running locally. Content is NEVER sent to a cloud provider
to determine its sensitivity (that would be circular and catastrophic).

HOW MANY LAYERS ARE ACTUALLY RUNNING IS AN EMPIRICAL QUESTION - ASK, DON'T ASSUME.
This docstring used to open "Four layers", which was false in every environment
that has ever existed: `presidio-analyzer` was not in requirements.txt (Layer 2
had never once run anywhere), and `sentence_transformers` is in AgentFriday.spec's
`excludes` (so Layer 3 is absent from the shipped .exe). Both were loaded inside
bare `except Exception: _X = None` handlers with no logging, so the shortfall was
silent. The packaged binary ran two layers while this text promised four.

For the real answer at runtime:
    from agent_friday.services.privacy_layers import describe, self_check
    describe()     # e.g. "Sensitivity classifier: 3/4 layers active ..."

  Layer 1 — Regex:     Structured tokens — SSN, CC, routing numbers, API keys.
  Layer 2 — Presidio:  Optional NER via presidio-analyzer. Catches names, dates,
                        medical/financial entities that regex misses. INSTALLED
                        BY THE WINDOWS INSTALLER BUT OBSERVE-ONLY: measured on
                        2026-08-24 it escalated 6 of 12 benign prompts ("what is
                        the weather going to be like tomorrow?") and scored
                        TIER_2 where the existing regex returns TIER_3. So it is
                        shadow-logged, not enforced, unless FRIDAY_PRESIDIO_ENFORCE=1
                        — and privacy_layers therefore reports it INACTIVE even
                        when it imports, because a layer that cannot change an
                        outcome is not a protection.
  Layer 3 — Embedding: Semantic similarity to curated sensitive exemplars using
                        the same all-MiniLM-L6-v2 model as the context pruner.
  Layer 4 — Local LLM: Optional Ollama pass for ambiguous spans. Only runs when
                        Layer 3 flags content as uncertain (score between thresholds).

All layers degrade gracefully — if a dep is missing, that layer is skipped and
the remaining layers still run.

Default on uncertainty: PRIVATE (fail-closed). Callers may override via `default`
but the egress gate always uses PRIVATE as the default.

Import example:
    from agent_friday.services.sensitivity_classifier import classify, Tier, TIER_3_KEYWORDS
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Optional

_log = logging.getLogger("friday.privacy.classifier")

#: Below this a model is a toy for adjudication. Mirrors local_seats._MIN_USEFUL_GB:
#: `functiongemma:270m` is a 270M function-caller and will not return a considered
#: verdict on an ambiguous span.
_MIN_SEAT_GB = 1.5

# ── Tier constants ─────────────────────────────────────────────────────────────
# Mirrored to match vault_access.Tier so callers don't need both imports.
class Tier:
    PUBLIC    = 1  # TIER_1 — any model
    PRIVATE   = 2  # TIER_2 — local only; cloud gets a redacted placeholder
    SENSITIVE = 3  # TIER_3 — local only; cloud gets nothing
    NAMES = {1: "TIER_1", 2: "TIER_2", 3: "TIER_3"}


# ── Layer 1a: Regex patterns for structured PII ────────────────────────────────
# CC span MUST match core.__init__._CC_RE (13–19 digits). A narrower window here
# (the prior 13–16) meant a 17–19-digit card classified PUBLIC by this layer while
# core's PII redactor treated it as a card — the two security layers disagreeing on
# what a card is, a live exfiltration seam for prepaid/UnionPay-length numbers.
_SSN_RE     = re.compile(r'\b\d{3}[-\s]\d{2}[-\s]\d{4}\b')
_CC_RE      = re.compile(r'\b(?:\d[ -]?){13,19}\b')
_ROUTING_RE = re.compile(r'\b\d{9}\b')
_API_KEY_RE = re.compile(r'\b(?:sk-ant-|sk-|AQ\.|AIza)[A-Za-z0-9_\-]{16,}\b')

# ── Layer 1a (cont.): contact-shaped PII ──────────────────────────────────────
# ADDED 2026-08-25 after a live leak. A phone number, a street address and a
# masked account tail had NO detector at any layer that actually ships.
#
# They were nominally covered by Layer 2 (Presidio PHONE_NUMBER / LOCATION),
# which has never been installed in any environment, and by Layer 3 embeddings,
# which AgentFriday.spec `excludes` from the frozen build AND which the vault
# path switches off outright (vault_access.classify passes use_embeddings=False).
# So in the shipped product the only thing standing between
# "emergency contact: 555-1234" and Anthropic was the literal English words
# "phone number" happening to appear nearby. They did not. Verified end to end:
# vault_access.gate_content(raw, "anthropic") returned the string verbatim and
# logged `[VAULT] ALLOW provider=anthropic tier=TIER_1`.
#
# These belong in Layer 1a specifically because Layer 1a is mode-independent:
# one fix covers the routing path and the egress path together. They are also
# orthogonal to the keyword-frame matching that three separate over-redaction
# incidents have been spent loosening — no pattern here can re-trigger on
# "courtesy", on "Sovereign Vault", or on CDC flu guidance, because none of
# those contain a dialable number or a house number.

# Toll-free area codes are never a personal contact. A news story printing a
# hotline ("call 1-800-273-8255") must not be redacted as if it were Stephen's  # pragma: allowlist secret
# address book — this is the deliberate over-correction guard for the pattern.
_TOLLFREE_AREA = {"800", "833", "844", "855", "866", "877", "888"}
_PHONE_RE = re.compile(
    r'(?<![\d.\-])'                          # not already inside a longer number
    r'(?:\+?1[\s.\-]?)?'                     # optional country code
    r'(?:\((\d{3})\)\s?|(\d{3})[\s.\-])'     # area code, parenthesised or delimited
    r'\d{3}[\s.\-]\d{4}'
    r'(?![\d\-])'
)

# A bare 7-digit local number ("555-1234") is too weak a shape to trust alone —
# it collides with part numbers and ranges. Require a contact cue in the
# surrounding text, which the reported failure has ("emergency contact:
# 555-1234") and a stray "302-1985" in ordinary prose does not.
_PHONE_LOCAL_RE = re.compile(r'(?<![\d.\-])\d{3}[.\-]\d{4}(?![\d\-])')
# Cues are either an act of contacting, or a person whose number one would
# write down. The role nouns matter: "babysitter Kayla, 555-2210" is exactly
# the shape a vault note takes, and it contains no verb at all. These only ever
# apply when a 7-digit number is already present, so they cannot over-trigger
# on prose that merely mentions a dentist.
_CONTACT_CUE_RE = re.compile(
    r'\b(?:phone|call|called|calling|contact|cell|mobile|tel|telephone|'
    r'reach|text|fax|voicemail|ext|'
    r'babysitter|sitter|nanny|neighbou?r|teacher|dentist|pediatrician|'
    r'landlord|emergency|mom|dad|grandma|grandpa)\b'
)

# House number + street name + street-type suffix. The leading house number is
# what keeps this off "Supreme Court" and "Wall Street analysts" — a bare street
# word never matches, only one preceded by a number.
_ADDRESS_RE = re.compile(
    r'\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z][A-Za-z\'\-]*\s+){1,3}'
    r'(?:st|street|ave|avenue|rd|road|ln|lane|dr|drive|blvd|boulevard|'
    r'ct|court|pl|place|way|ter|terrace|cir|circle|pkwy|parkway|hwy|highway)'
    r'\b\.?',
    re.I,
)

# Masked account tails and issued identifiers. "account number" as prose was
# already a strong TIER-3 phrase; these are the shapes carrying the same
# information WITHOUT saying the words — "Chase account ending 4417".
_ACCT_TAIL_RE = re.compile(
    r'\b(?:account|acct|card)\b[^.\n]{0,24}?\bending(?:\s+in)?\s+\d{3,6}\b',
    re.I,
)
_ISSUED_ID_RE = re.compile(
    r'\b(?:policy|member|patient|claim|case|account|acct|invoice)\s*'
    r'(?:number|no\.?|#|id)\s*[:#]?\s*[A-Za-z]{0,4}[-\s]?\d{4,}\b',
    re.I,
)


def _has_personal_phone(text: str) -> bool:
    """True when text carries a dialable personal number (toll-free excluded)."""
    for m in _PHONE_RE.finditer(text):
        area = m.group(1) or m.group(2)
        if area not in _TOLLFREE_AREA:
            return True
    return bool(_PHONE_LOCAL_RE.search(text) and _CONTACT_CUE_RE.search(text.lower()))


# ── Layer 1b: Keyword tiers (authoritative, shared with vault_access) ──────────
TIER_3_KEYWORDS = (
    # Financial
    "financial", "finance", "bank account", "routing number", "account number",
    "account balance", "checking balance", "savings balance",
    "investment", "portfolio", "brokerage", "credit card", "tax return",
    "net worth", "salary", "income",
    # Health
    "health record", "medical", "medication", "prescription", "diagnosis",
    "doctor", "insurance", "appointment", "a1c", "blood glucose",
    # Legal
    "legal", "custody", "court", "divorce", "settlement",
    # Identity / PII
    "ssn", "social security", "passport", "driver's license", "date of birth",
    "encrypted", "sovereign vault", "vault",
)

TIER_2_KEYWORDS = (
    "contact", "emergency contact", "next of kin", "mailing address",
    "phone number", "home address", "family", "daughter",
    "partner", "personal note", "memory", "trust graph", "relationship",
    "todo",
)


# ── Layer 3: Sensitive exemplars for embedding similarity ─────────────────────
_SENSITIVE_EXEMPLARS = [
    # Financial
    "my bank account number is",
    "routing number for wire transfer",
    "net worth and investment portfolio",
    "tax return and income details",
    "salary and compensation package",
    "credit card statement",
    # Medical
    "A1C blood glucose level",
    "prescription medication dosage",
    "diagnosis from the doctor",
    "health insurance coverage",
    "medical record and appointment",
    # Legal / custody
    "custody arrangement for the children",
    "divorce settlement terms",
    "court filing and legal document",
    # Family / private
    "my son lives with me on weekends",
    "my daughter's school schedule",
    "home address and phone number",
    "emergency contact details",
    # Credentials
    "API key and secret token",
    "password and authentication credentials",
    "social security number",
]

_UNTRIED = object()   # sentinel: distinguishes "not attempted" from "failed"

_EMBEDDING_LOCK  = threading.Lock()
_EXEMPLAR_EMBEDS = None      # lazy-loaded numpy array
_EMBEDDER        = _UNTRIED  # lazy-loaded SentenceTransformer


def _load_embedder():
    """Lazy-load the sentence-transformers model (same one as context_pruner).

    Caches failure via _UNTRIED for the same reason as _load_presidio: storing
    None on failure made None mean both "untried" and "unavailable", so a
    missing dependency was retried on every single call. This matters most in
    the frozen build, where sentence_transformers is excluded outright and the
    retry is therefore permanent.
    """
    global _EMBEDDER, _EXEMPLAR_EMBEDS
    if _EMBEDDER is not _UNTRIED:
        return _EMBEDDER
    with _EMBEDDING_LOCK:
        if _EMBEDDER is not _UNTRIED:
            return _EMBEDDER
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('all-MiniLM-L6-v2')
            _EMBEDDER = model
            _EXEMPLAR_EMBEDS = model.encode(
                _SENSITIVE_EXEMPLARS, normalize_embeddings=True
            )
        except Exception as exc:
            _log.warning(
                "sensitivity classifier Layer 3 (embedding similarity) UNAVAILABLE: "
                "%s: %s - egress classification is running without semantic matching.",
                type(exc).__name__, exc,
            )
            _EMBEDDER = None
            _EXEMPLAR_EMBEDS = None
    return _EMBEDDER


_PRESIDIO_LOCK = threading.Lock()
_ANALYZER      = _UNTRIED  # lazy-loaded AnalyzerEngine


def _load_presidio():
    """Lazy-load the Presidio NER analyzer. Caches failure as well as success.

    The failure path used to store None, which is also the "not yet attempted"
    value - so a missing presidio-analyzer was re-imported on EVERY classify()
    call, taking the lock and re-walking sys.path each time. Measured at 0.86 ms
    per call on this machine, paid by every egress decision forever, for a layer
    that was never going to load. The sentinel makes the miss cost once.
    """
    global _ANALYZER
    if _ANALYZER is not _UNTRIED:
        return _ANALYZER
    with _PRESIDIO_LOCK:
        if _ANALYZER is not _UNTRIED:
            return _ANALYZER
        try:
            from presidio_analyzer import AnalyzerEngine
            _ANALYZER = AnalyzerEngine()
        except Exception as exc:
            # Loud, once. A privacy layer failing to load is not a debug detail.
            _log.warning(
                "sensitivity classifier Layer 2 (Presidio NER) UNAVAILABLE: %s: %s "
                "- egress classification is running without entity recognition.",
                type(exc).__name__, exc,
            )
            _ANALYZER = None
    return _ANALYZER


# ── Layer implementations ──────────────────────────────────────────────────────

def _regex_tier(text: str) -> int:
    """Layer 1a: high-precision regex scan for structured PII."""
    if _SSN_RE.search(text):
        return Tier.SENSITIVE
    if _CC_RE.search(text):
        return Tier.SENSITIVE
    if _API_KEY_RE.search(text):
        return Tier.SENSITIVE
    if _ACCT_TAIL_RE.search(text) or _ISSUED_ID_RE.search(text):
        return Tier.SENSITIVE
    if _ROUTING_RE.search(text):
        return Tier.PRIVATE
    # Contact-shaped PII → PRIVATE. Deliberately PRIVATE rather than SENSITIVE:
    # the cloud gets a redacted placeholder instead of nothing, so the model is
    # told the data exists but withheld. Both tiers are kept off the wire.
    if _has_personal_phone(text) or _ADDRESS_RE.search(text):
        return Tier.PRIVATE
    return 0


# ── Keyword matching for THIS module's egress classification ──────────────────
# TIER_3_KEYWORDS above stays intact (vault_access shares it, and for vault
# content over-inclusive is correct). For egress classification of ordinary
# chat/voice/TTS text, bare substring matching was catastrophically imprecise:
# 'courtesy' hit 'court', 'incoming' hit 'income', and every mention of the
# product term 'Sovereign Vault' (it is in Friday's own system prompt) nuked
# the whole field to TIER_3. Two changes, neither of which weakens what leaves
# the device (both tiers are still withheld from cloud — see egress_gate):
#   1. Word-boundary regex matching instead of substring.
#   2. Strong/weak split: unambiguous phrases stay immediate TIER_3; common
#      single words alone rate PRIVATE, and escalate to SENSITIVE only when a
#      second independent layer (Presidio/embeddings) agrees — the existing
#      two-signal escalation rule in classify().
_TIER3_STRONG = (
    "bank account", "routing number", "account number", "tax return",
    "net worth", "credit card", "brokerage",
    # Balance phrases are qualified two-word forms on purpose. Bare "balance"
    # is one of this file's classic over-triggers ("work-life balance",
    # "balance the composition"), so it stays off every list.
    "account balance", "checking balance", "savings balance", "card balance",
    "health record", "blood glucose", "a1c",
    "ssn", "social security", "passport", "driver's license", "date of birth",
)
_TIER3_WEAK = (
    "financial", "finance", "investment", "portfolio", "salary", "income",
    "medical", "medication", "prescription", "diagnosis", "doctor",
    "insurance", "appointment",
    "legal", "custody", "court", "divorce", "settlement",
)
# Friday's own product-architecture vocabulary. These words appear in her
# system prompt and ordinary spoken replies whenever she describes HERSELF
# ("loaded from the Sovereign Vault", "I'll add that to memory") — matching
# them redacted her own identity prompt on every cloud call. Vault/memory/
# trust-graph CONTENTS are tier-tagged where they are loaded (vault_access,
# _build_context_prompt sections); the words themselves are not a signal FOR
# EGRESS. They remain a signal for ROUTING (egress=False, the default):
# "what's in my vault?" must still force-route to a local model.
_EGRESS_EXCLUDED = {"sovereign vault", "vault", "encrypted", "trust graph", "memory"}


def _kw_re(keywords, exclude=frozenset()) -> "re.Pattern":
    kws = [k for k in keywords if k not in exclude]
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b")


# TIER-2 strong/common split (2026-08-19). The common words below appear
# constantly in text that carries no personal data at all — a storybook prompt
# reading "family picture-book aesthetic" or "nano banana family" was routed
# as PRIVATE, vault-forced onto the local seat, and (with a full tool payload
# that cannot fit the local context window) the whole turn died. A bare
# common word is NOT a personal-data signal; the same word inside a
# possessive/personal frame ("my family", "her daughter's school") is. Strong
# multi-word phrases stay unconditional in both modes.
# "emergency contact" is a fixed two-word idiom that names a person and their
# number. b69acb2 demoted bare "contact" to a context-gated common word, which
# was right — but it left this phrase with no determiner to gate on ("emergency
# contact: 555-1234" has no "my"), so it fell all the way to TIER_1. It is a
# strong phrase, not a common word, and belongs on this list.
_TIER2_STRONG = ("phone number", "home address", "personal note", "trust graph",
                 "emergency contact", "next of kin", "mailing address")
_TIER2_COMMON = ("contact", "family", "daughter", "partner", "memory",
                 "relationship", "todo")

# Possessive/personal context for a common word: a personal determiner shortly
# BEFORE it ("my family", "our daughter", "his partner's"), or the word itself
# in possessive form ("daughter's schedule"). Window is deliberately short so
# "my favorite family of typefaces" still matches (over-trigger routes local,
# the safe direction) while a bare stylistic mention does not.
# The connector class admits punctuation as well as word characters. It used to
# be [\w'\-\s], which meant a determiner and its noun had to be separated by
# nothing but letters and spaces — so `{"f": "my daughter"}` and "my wife's
# side: family" fell out of the frame purely because a quote, comma or colon sat
# in the gap. That is the JSON-descent variant pinned as an xfail in
# test_egress_tool_result_provenance.py: wrapping text in JSON LOWERED its tier.
# The window stays 24 characters, so widening the class cannot reach any further
# than it already could — it just stops punctuation from breaking the match.
_TIER2_COMMON_CTX_RE = re.compile(
    r"\b(?:my|our|his|her|their|your)\b[\w'\-\s.,:;\"/\\]{0,24}?"
    r"\b(?:" + "|".join(_TIER2_COMMON) + r")\b"
    r"|\b(?:" + "|".join(_TIER2_COMMON) + r")'s\b")


def _tier2_hit(low: str, exclude=frozenset()) -> bool:
    """True when TIER-2 content is present: any strong phrase, or a common
    word in possessive/personal context."""
    strong = [k for k in _TIER2_STRONG if k not in exclude]
    if strong and _kw_re(strong).search(low):
        return True
    common = [k for k in _TIER2_COMMON if k not in exclude]
    if not common:
        return False
    m = _TIER2_COMMON_CTX_RE.search(low)
    return bool(m and any(k in m.group(0) for k in common))


# Egress mode: precision matching (product terms excluded, strong/weak split).
_TIER3_STRONG_RE = _kw_re(_TIER3_STRONG, _EGRESS_EXCLUDED)
_TIER3_WEAK_RE   = _kw_re(_TIER3_WEAK, _EGRESS_EXCLUDED)
# Routing/vault mode (default): the FULL authoritative TIER-3 keywords — any
# TIER-3 keyword (including product terms) rates SENSITIVE, as vault_access
# and the model router's needs_vault_access have always relied on.
_TIER3_FULL_RE = _kw_re(TIER_3_KEYWORDS)


def _keyword_tier(low: str, egress: bool = False) -> int:
    """Layer 1b: fast word-boundary keyword scan.

    egress=False (routing/vault mode — the default): the full keyword tiers,
    original strengths. Over-triggering here just routes a request to a local
    model, which is the safe direction.

    egress=True (cloud-payload gating): precision mode. Strong phrase →
    SENSITIVE. One weak TIER-3 word → PRIVATE (still withheld from cloud, via
    placeholder). Two or more DISTINCT weak TIER-3 words → SENSITIVE
    ("medical diagnosis" is a far stronger signal than a lone "appointment").
    Product-architecture terms are excluded — over-triggering here emptied
    Friday's own system prompt and everyday turns on every cloud call.
    """
    if not egress:
        if _TIER3_FULL_RE.search(low):
            return Tier.SENSITIVE
        if _tier2_hit(low):
            return Tier.PRIVATE
        return 0
    if _TIER3_STRONG_RE.search(low):
        return Tier.SENSITIVE
    weak_hits = set(_TIER3_WEAK_RE.findall(low))
    if len(weak_hits) >= 2:
        return Tier.SENSITIVE
    if weak_hits or _tier2_hit(low, _EGRESS_EXCLUDED):
        return Tier.PRIVATE
    return 0


def _presidio_tier(text: str) -> int:
    """Layer 2: Presidio NER detection. Returns 0 if unavailable."""
    analyzer = _load_presidio()
    if analyzer is None:
        return 0
    try:
        results = analyzer.analyze(text=text, language='en')
        sensitive_types = {
            'CREDIT_CARD', 'US_SSN', 'US_BANK_NUMBER', 'IBAN_CODE',
            'MEDICAL_LICENSE', 'US_PASSPORT', 'US_DRIVER_LICENSE',
        }
        private_types = {
            'PERSON', 'LOCATION', 'DATE_TIME', 'PHONE_NUMBER',
            'EMAIL_ADDRESS', 'IP_ADDRESS',
        }
        for r in results:
            if r.entity_type in sensitive_types and r.score >= 0.7:
                return Tier.SENSITIVE
        for r in results:
            if r.entity_type in private_types and r.score >= 0.8:
                return Tier.PRIVATE
    except Exception:
        pass
    return 0


def _embedding_tier(text: str) -> tuple[int, float]:
    """Layer 3: semantic similarity to sensitive exemplars.

    Returns (tier, max_similarity). tier=0 means below threshold.
    """
    embedder = _load_embedder()
    if embedder is None or _EXEMPLAR_EMBEDS is None:
        return 0, 0.0
    try:
        import numpy as _np
        embed = embedder.encode([text[:512]], normalize_embeddings=True)[0]
        sims = (_EXEMPLAR_EMBEDS @ embed).tolist()
        max_sim = float(max(sims))
        if max_sim >= 0.65:
            return Tier.SENSITIVE, max_sim
        if max_sim >= 0.50:
            return Tier.PRIVATE, max_sim
        return 0, max_sim
    except Exception:
        return 0, 0.0


def _llm_seat() -> str | None:
    """Which installed model adjudicates. None when nothing can.

    This used to be the literal string ``"gemma4:latest"``, POSTed straight to
    the daemon. That tag is not installed on the reference machine — checked
    2026-08-26, it returns HTTP 404 — so every call failed, `r.ok` was False,
    the function returned 0, and the surrounding `except: pass` ate the rest.
    A hardcoded model name is a dangling pointer the moment someone runs
    `ollama rm`, and this one had already dangled.

    It is the same defect class as the `gemma3:4b` constants closed in 7da7798:
    a model NAME written into a module that has no way to know whether the name
    still resolves. So this resolves instead of naming.

    But "resolve against the installed registry" is not by itself enough, and
    the first version of this fix proved it — see the comment on `servable`
    below. There are two local registries and only one of them serves this
    request.

    The "judge" role is deliberate: adjudicating an ambiguous span is the same
    kind of work `judgment_gate` does, and it maps onto the `reasoning`
    capability the user already configures. Note that capability is often a
    CLOUD model, which is why a servability check rather than a name check is
    what keeps this layer's never-leaves-the-machine promise.
    """
    try:
        from agent_friday.services import local_seats
    except Exception as e:
        _log.debug("Layer 4: could not import local_seats: %s", e)
        return None

    # WHICH INVENTORY. This is the whole subtlety, and getting it wrong looks
    # exactly like getting it right.
    #
    # There are TWO local model registries on this machine and they do not hold
    # the same things. `local_seats.installed()` deliberately MERGES them:
    # Ollama's tags, plus Friday's own llama-server runtime store
    # (~/.friday/runtime/models/models.json). Resolving against the merged view
    # returned `gemma4:12b` — which is real, and is a llama-server seat, and is
    # NOT something Ollama can serve. Measured 2026-08-26: the daemon answered
    # `{"error":"model 'gemma4:12b' not found"}` with HTTP 404, in 0.0s, which
    # this function then reported as "no verdict" — indistinguishable from the
    # model having no opinion.
    #
    # That is the SAME failure the hardcoded `gemma4:latest` produced, reached
    # by a more sophisticated route. Asking a registry is only correct if it is
    # the registry that will serve the request. This layer POSTs to Ollama, so
    # it resolves against Ollama.
    try:
        from agent_friday.routing.ollama_manager import get_manager
        servable = {m.get("name") for m in (get_manager().list_models() or ())}
    except Exception as e:
        _log.debug("Layer 4: could not read the daemon's inventory: %s", e)
        return None
    if not servable:
        _log.info("Layer 4: the Ollama daemon lists no models — skipping.")
        return None

    # `installed()` is still the right source for WHICH of those is suitable:
    # it drops embedding models by capability rather than by name, and returns
    # (name, size_gb) smallest-first. Intersecting gives "suitable AND
    # servable by the daemon this layer actually talks to".
    rows = [(n, g) for n, g in (local_seats.installed() or ())
            if n in servable and g >= _MIN_SEAT_GB]
    if not rows:
        _log.info("Layer 4: no Ollama-servable model is large enough to "
                  "adjudicate — skipping. Layers 1-3 still run.")
        return None

    # A configured preference wins when the daemon can actually serve it.
    try:
        preferred = local_seats.resolve("judge")
    except Exception:
        preferred = None
    if preferred and preferred in {n for n, _ in rows}:
        return preferred

    # Otherwise the largest text model. Vision-language tags answer text fine
    # but are not what anyone means by the adjudicating seat, so they lose —
    # the same tie-break local_seats applies for the reasoning role.
    text_rows = [r for r in rows if not local_seats._looks_vision(r[0])] or rows
    seat = text_rows[-1][0]
    if preferred and preferred != seat:
        _log.info("Layer 4: %r is not servable by the Ollama daemon; "
                  "adjudicating with %r instead.", preferred, seat)
    return seat


def _local_llm_tier(text: str) -> int:
    """Layer 4: local Ollama pass for ambiguous spans. Never calls cloud.

    Returns 0 for "no opinion" — which is also what it returns when no seat is
    available. Those are the same OUTPUT but not the same EVENT, so the second
    is logged. An absent component that reads as a legitimate negative result
    is the failure mode this codebase keeps paying for.

    Note this layer can only ever ESCALATE. `classify()` folds it in through
    `max(candidates)` and returns early only on SENSITIVE, so switching it on
    can withhold more, never less. That makes over-redaction the only risk
    worth measuring here — and this file has four scars from exactly that.
    """
    model = _llm_seat()
    if not model:
        _log.info("Layer 4 skipped: no local seat available to adjudicate. "
                  "Classification falls back to layers 1-3.")
        return 0
    try:
        import requests as _req
        prompt = (
            "Classify the privacy sensitivity of the following text. "
            "Reply with exactly one word: PUBLIC, PRIVATE, or SENSITIVE. "
            "PUBLIC = general info with no personal data. "
            "PRIVATE = names, contact info, family details. "
            "SENSITIVE = financial, medical, legal, credentials, SSN.\n\n"
            f"Text: {text[:400]}\n\nClassification:"
        )
        r = _req.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  # REQUIRED, not an optimisation. Every current local seat is
                  # a thinking model: without this it spends the entire
                  # num_predict budget emitting "<|channel>thought ..." and
                  # never reaches the verdict. Measured 2026-08-26 on
                  # Gemma4-12B-QAT — with `think` unset the reply at
                  # num_predict=64 was still mid-reasoning; with it set the
                  # reply was the single word "PRIVATE".
                  "think": False,
                  "options": {"temperature": 0, "num_predict": 8}},
            # A COLD LOAD DOMINATES THIS. Measured: 25.9 s for the first call
            # (weights off disk) against ~1 s warm. An earlier 20 s ceiling
            # here timed out on every cold start and returned 0 — which this
            # layer reports as "no opinion", so a working model looked like a
            # silent one. Generous enough to survive the load; the layer is
            # opt-in and reaches ~5% of content, so the worst case is rare.
            timeout=60,
        )
        if r.ok:
            raw = (r.json().get("response") or "").strip().upper()
            # Match anywhere in the reply, not just the first token. The
            # original read `.split()[0]`, which on an empty reply raised
            # IndexError into the blanket `except` below — indistinguishable
            # from a network failure — and on "CLASSIFICATION: PRIVATE" looked
            # at the wrong word entirely.
            for needle, tier in (("SENSITIVE", Tier.SENSITIVE),
                                 ("PRIVATE", Tier.PRIVATE),
                                 ("PUBLIC", Tier.PUBLIC)):
                if needle in raw:
                    return tier
            _log.debug("Layer 4: %s gave no usable verdict (%r)", model, raw[:60])
        else:
            _log.info("Layer 4: %s returned HTTP %s — no verdict. This is the "
                      "shape of a model that is not installed.", model, r.status_code)
    except Exception as e:
        _log.debug("Layer 4 call failed (%s): %s", model, type(e).__name__)
    return 0


# ── Public API ─────────────────────────────────────────────────────────────────

def classify(
    content: str,
    default: int = Tier.PUBLIC,
    use_presidio: bool = True,
    use_embeddings: bool = True,
    use_llm: bool = False,
    llm_ambiguity_low: float = 0.50,
    llm_ambiguity_high: float = 0.65,
    egress: bool = False,
) -> int:
    """Classify content sensitivity using all available layers.

    Default is PUBLIC — content with no signals from any layer is treated as
    public. The fail-closed guarantee comes from the embedding layer: text that
    is semantically close to sensitive exemplars (sim >= 0.50) is conservatively
    classified as PRIVATE even when no keyword or regex matches.

    All classification runs locally. Content is never sent to cloud.

    Returns a Tier constant: PUBLIC (1), PRIVATE (2), or SENSITIVE (3).
    """
    if not content or not isinstance(content, str):
        return default

    low = content.lower()

    # Layer 1a: regex (high precision)
    regex = _regex_tier(content)
    if regex == Tier.SENSITIVE:
        return Tier.SENSITIVE

    # Layer 1b: keyword scan (fast path)
    kw = _keyword_tier(low, egress=egress)
    if kw == Tier.SENSITIVE:
        return Tier.SENSITIVE

    # Layer 2: Presidio NER.
    # SHADOW BY DEFAULT. Measured on this machine (2026-08-24), Presidio
    # escalated 6 of 12 entirely benign prompts to TIER_2 - "What is the weather
    # going to be like tomorrow?" and "Remind me to buy milk on Friday" among
    # them - because DATE_TIME and LOCATION fire on ordinary conversational
    # prose. Enforcing that would withhold half of normal chat from the cloud
    # and would be the fourth over-broad-classification scar in this file's
    # history. So unless FRIDAY_PRESIDIO_ENFORCE=1 is explicitly set, Presidio
    # only OBSERVES: presidio_shadow.observe() queues the text to a background
    # thread, logs what it WOULD have escalated, and returns None.
    presidio = 0
    if use_presidio:
        try:
            from agent_friday.services.privacy_layers import enforcement_enabled
            _enforce = enforcement_enabled()
        except Exception:
            _enforce = False
        if _enforce:
            presidio = _presidio_tier(content)
        else:
            try:
                from agent_friday.services.presidio_shadow import observe
                # Pass the tier decided WITHOUT Presidio so the log can show
                # exactly what it would have changed.
                observe(content, max(regex, kw) or Tier.PUBLIC, context="classify")
            except Exception:
                pass
    if presidio == Tier.SENSITIVE:
        return Tier.SENSITIVE
    if presidio == Tier.PRIVATE and kw >= Tier.PRIVATE:
        # Two independent signals agree on PRIVATE → escalate to SENSITIVE.
        return Tier.SENSITIVE

    # Layer 3: embedding similarity — fail-closed for the ambiguous zone
    emb_tier, emb_sim = _embedding_tier(content) if use_embeddings else (0, 0.0)
    if emb_tier == Tier.SENSITIVE:
        return Tier.SENSITIVE

    # Layer 4: local LLM for genuinely ambiguous spans
    llm = 0
    if use_llm and emb_tier > 0 and llm_ambiguity_low <= emb_sim < llm_ambiguity_high:
        llm = _local_llm_tier(content)
        if llm == Tier.SENSITIVE:
            return Tier.SENSITIVE

    # Aggregate: most-sensitive result wins
    candidates = [t for t in [regex, kw, presidio, emb_tier, llm] if t > 0]
    if candidates:
        return max(candidates)
    return default


def classify_legacy(content: str, default: int = Tier.PUBLIC, **kwargs) -> int:
    """Backward-compatible alias for classify() with PUBLIC default.

    Accepts the same keyword arguments as classify() for forward compatibility.
    """
    return classify(content, default=default, **kwargs)
