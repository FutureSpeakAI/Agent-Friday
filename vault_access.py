"""
Vault Access Control — the gatekeeper for the Sovereign Vault.

The Sovereign Vault holds Stephen's most private data: financial records,
health records, legal / co-parenting (OFW) archives, contacts, family info,
and encrypted PII. The governing rule is simple and non-negotiable:

    Vault content is readable by LOCAL models only.

Cloud providers (Anthropic) never receive TIER_2 (private) or TIER_3
(sensitive) vault content. TIER_1 (public) content — wiki articles, news,
general docs — flows to any model.

This module is pure policy + logging. It performs no I/O beyond appending to
the access log, so it is safe to import from both the server's chat pipeline
and the model router. The complementary mechanism — *forcing* a vault request
onto a local model so it CAN see the data — lives in model_router.py. This
module decides what a given provider is *allowed to see*; the router decides
*which provider* a vault-touching request is sent to.
"""

import json
import threading
import time


class Tier:
    """Sensitivity tiers. Integer-valued so callers can compare/sort."""
    PUBLIC = 1      # TIER_1 — any model (wiki content, news, general docs)
    PRIVATE = 2     # TIER_2 — local only; cloud gets a summary/placeholder
    SENSITIVE = 3   # TIER_3 — local only; cloud gets nothing

    NAMES = {1: "TIER_1", 2: "TIER_2", 3: "TIER_3"}


# Providers that run on-device and are therefore trusted with raw vault data.
LOCAL_PROVIDERS = {"ollama", "local"}


# Keyword → tier classification. TIER_3 wins over TIER_2 wins over TIER_1.
# Kept lowercase; matching is substring-based on lowercased content.
TIER_3_KEYWORDS = (
    # Financial
    "financial", "finance", "bank account", "routing number", "account number",
    "investment", "portfolio", "brokerage", "amex", "credit card", "tax return",
    "net worth", "salary", "income",
    # Health
    "health record", "medical", "medication", "prescription", "diagnosis",
    "doctor", "insurance", "cigna", "henry meds", "glp", "appointment",
    # Legal / co-parenting
    "legal", "custody", "court", "ofw", "our family wizard", "coparent",
    "co-parent", "divorce", "settlement",
    # Identity / PII
    "ssn", "social security", "passport", "driver's license", "date of birth",
    "encrypted", "sovereign vault",
)

TIER_2_KEYWORDS = (
    "contact", "phone number", "home address", "family", "daughter",
    "partner", "personal note", "memory", "trust graph", "relationship",
    "todo", "co-parenting schedule",
)


class VaultAccessDenied(Exception):
    """Raised when cloud access to gated vault content is hard-denied."""


class VaultAccessControl:
    """Decides what each provider may read from the vault, and logs every check.

    Construct once and reuse (it is cheap and thread-safe). Pass an optional
    `log_path` to persist access decisions as JSONL; otherwise decisions are
    kept in a capped in-memory ring and echoed to stdout.
    """

    def __init__(self, log_path=None, enabled=True, max_log=2000):
        self.log_path = log_path
        self.enabled = enabled
        self._lock = threading.Lock()
        self._log = []          # in-memory ring of recent decisions
        self._max_log = max_log
        self._counts = {"allowed": 0, "denied": 0}

    # ── Core policy ─────────────────────────────────────────────────────

    def can_access(self, provider):
        """True only when the provider is a local (on-device) model."""
        return str(provider or "").lower() in LOCAL_PROVIDERS

    def classify(self, content, default=Tier.PUBLIC):
        """Best-effort sensitivity tier for a chunk of content.

        Scans for TIER_3 markers first (most sensitive wins), then TIER_2.
        Returns `default` (PUBLIC) when nothing sensitive is detected.
        """
        if not content or not isinstance(content, str):
            return default
        low = content.lower()
        if any(kw in low for kw in TIER_3_KEYWORDS):
            return Tier.SENSITIVE
        if any(kw in low for kw in TIER_2_KEYWORDS):
            return Tier.PRIVATE
        return default

    def gate_content(self, content, provider, tier=None, fallback="redact",
                     detail="content"):
        """Return content the given provider is allowed to see.

        - Local provider: always returns the raw content.
        - Cloud provider, TIER_1: returns raw content (public).
        - Cloud provider, TIER_2: returns a redacted placeholder (or "" / raises
          depending on `fallback`).
        - Cloud provider, TIER_3: returns "" (cloud gets nothing), unless
          `fallback == "deny"`, which raises VaultAccessDenied.

        `fallback` mirrors the `vault_cloud_fallback` setting:
          "redact" → placeholder for TIER_2, nothing for TIER_3
          "deny"   → raise VaultAccessDenied for any gated content
          "warn"   → same redaction as "redact" (the caller surfaces the warning)
        """
        if not isinstance(content, str):
            return content
        if tier is None:
            tier = self.classify(content)

        if self.can_access(provider):
            self._record(provider, tier, True, detail)
            return content

        # Cloud path.
        if tier == Tier.PUBLIC:
            self._record(provider, tier, True, detail)
            return content

        self._record(provider, tier, False, detail)

        if fallback == "deny":
            raise VaultAccessDenied(
                f"Cloud access denied to {Tier.NAMES.get(tier, tier)} vault content"
            )

        if tier == Tier.SENSITIVE:
            return ""  # cloud gets nothing for sensitive data

        # TIER_2 → redacted placeholder so the model knows data exists but is withheld.
        return self._redact(content)

    def assemble_prompt(self, sections, provider, fallback="redact"):
        """Join tier-tagged prompt sections, gating by provider.

        `sections` is a list of (tier, text) tuples. Returns the gated prompt
        string. Local providers get everything; cloud providers get TIER_1 in
        full, TIER_2 as a placeholder, and TIER_3 dropped entirely.
        """
        local = self.can_access(provider)
        out = []
        for tier, text in sections:
            if not text:
                continue
            if local:
                self._record(provider, tier, True, "prompt-section")
                out.append(text)
                continue
            if tier == Tier.PUBLIC:
                self._record(provider, tier, True, "prompt-section")
                out.append(text)
            elif tier == Tier.PRIVATE:
                self._record(provider, tier, False, "prompt-section")
                if fallback == "deny":
                    raise VaultAccessDenied("Cloud access denied to TIER_2 prompt section")
                out.append(self._redact(text))
            else:  # SENSITIVE — cloud gets nothing
                self._record(provider, tier, False, "prompt-section")
                if fallback == "deny":
                    raise VaultAccessDenied("Cloud access denied to TIER_3 prompt section")
                # drop entirely
        return "\n".join(out)

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _redact(text):
        """Replace a section body with a placeholder, preserving its header line."""
        header = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                if stripped.startswith("==") or stripped.startswith("#"):
                    header = stripped
                break
        note = (
            "[VAULT-PROTECTED — private content withheld from cloud models. "
            "This data is available only to a local model. Switch to local "
            "routing to access it.]"
        )
        return f"{header}\n{note}" if header else note

    def _record(self, provider, tier, allowed, detail):
        if not self.enabled:
            return
        entry = {
            "ts": time.time(),
            "provider": str(provider or "unknown").lower(),
            "tier": Tier.NAMES.get(tier, str(tier)),
            "allowed": bool(allowed),
            "detail": detail,
        }
        with self._lock:
            self._counts["allowed" if allowed else "denied"] += 1
            self._log.append(entry)
            if len(self._log) > self._max_log:
                self._log = self._log[-(self._max_log // 2):]
        # Persist (best-effort) and echo.
        if self.log_path is not None:
            try:
                from pathlib import Path
                p = Path(self.log_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass
        verdict = "ALLOW" if allowed else "DENY"
        print(f"  [VAULT] {verdict} provider={entry['provider']} "
              f"tier={entry['tier']} ({detail})")

    def stats(self):
        with self._lock:
            return {
                "allowed": self._counts["allowed"],
                "denied": self._counts["denied"],
                "recent": list(self._log[-50:]),
            }
