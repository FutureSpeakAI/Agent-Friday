"""
Unified sensitivity classifier — the single source of truth for content tier decisions.

Four layers, all running locally. Content is NEVER sent to a cloud provider to
determine its sensitivity (that would be circular and catastrophic).

  Layer 1 — Regex:     Structured tokens — SSN, CC, routing numbers, API keys.
  Layer 2 — Presidio:  Optional NER via presidio-analyzer. Catches names, dates,
                        medical/financial entities that regex misses.
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

import re
import threading
from typing import Optional

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


# ── Layer 1b: Keyword tiers (authoritative, shared with vault_access) ──────────
TIER_3_KEYWORDS = (
    # Financial
    "financial", "finance", "bank account", "routing number", "account number",
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
    "contact", "phone number", "home address", "family", "daughter",
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

_EMBEDDING_LOCK  = threading.Lock()
_EXEMPLAR_EMBEDS = None  # lazy-loaded numpy array
_EMBEDDER        = None  # lazy-loaded SentenceTransformer


def _load_embedder():
    """Lazy-load the sentence-transformers model (same one as context_pruner)."""
    global _EMBEDDER, _EXEMPLAR_EMBEDS
    with _EMBEDDING_LOCK:
        if _EMBEDDER is not None:
            return _EMBEDDER
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('all-MiniLM-L6-v2')
            _EMBEDDER = model
            _EXEMPLAR_EMBEDS = model.encode(
                _SENSITIVE_EXEMPLARS, normalize_embeddings=True
            )
        except Exception:
            _EMBEDDER = None
            _EXEMPLAR_EMBEDS = None
    return _EMBEDDER


_PRESIDIO_LOCK = threading.Lock()
_ANALYZER      = None  # lazy-loaded AnalyzerEngine


def _load_presidio():
    """Lazy-load the Presidio NER analyzer."""
    global _ANALYZER
    with _PRESIDIO_LOCK:
        if _ANALYZER is not None:
            return _ANALYZER
        try:
            from presidio_analyzer import AnalyzerEngine
            _ANALYZER = AnalyzerEngine()
        except Exception:
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
    if _ROUTING_RE.search(text):
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
_TIER2_STRONG = ("phone number", "home address", "personal note", "trust graph")
_TIER2_COMMON = ("contact", "family", "daughter", "partner", "memory",
                 "relationship", "todo")

# Possessive/personal context for a common word: a personal determiner shortly
# BEFORE it ("my family", "our daughter", "his partner's"), or the word itself
# in possessive form ("daughter's schedule"). Window is deliberately short so
# "my favorite family of typefaces" still matches (over-trigger routes local,
# the safe direction) while a bare stylistic mention does not.
_TIER2_COMMON_CTX_RE = re.compile(
    r"\b(?:my|our|his|her|their|your)\b[\w'\-\s]{0,24}?"
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


def _local_llm_tier(text: str) -> int:
    """Layer 4: local Ollama pass for ambiguous spans. Never calls cloud."""
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
            json={"model": "gemma4:latest", "prompt": prompt, "stream": False},
            timeout=10,
        )
        if r.ok:
            word = r.json().get("response", "").strip().upper().split()[0]
            if "SENSITIVE" in word:
                return Tier.SENSITIVE
            if "PRIVATE" in word:
                return Tier.PRIVATE
            if "PUBLIC" in word:
                return Tier.PUBLIC
    except Exception:
        pass
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

    # Layer 2: Presidio NER
    presidio = _presidio_tier(content) if use_presidio else 0
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
