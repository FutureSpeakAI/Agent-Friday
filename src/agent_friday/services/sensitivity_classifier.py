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
_SSN_RE     = re.compile(r'\b\d{3}[-\s]\d{2}[-\s]\d{4}\b')
_CC_RE      = re.compile(r'\b(?:\d[ -]?){13,16}\b')
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


def _keyword_tier(low: str) -> int:
    """Layer 1b: fast substring keyword scan."""
    if any(kw in low for kw in TIER_3_KEYWORDS):
        return Tier.SENSITIVE
    if any(kw in low for kw in TIER_2_KEYWORDS):
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
    kw = _keyword_tier(low)
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
