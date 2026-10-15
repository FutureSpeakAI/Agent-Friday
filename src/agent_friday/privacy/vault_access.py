"""
Vault Access Control — the gatekeeper for the Sovereign Vault.

The Sovereign Vault holds the user's most private data: financial records,
health records, legal archives, contacts, family info,
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
import hashlib
import logging
import threading
import time
from pathlib import Path

from agent_friday.services.local_only_guard import is_cloud_model_tag

# Verdicts go to friday.log (the "friday" logger tree), not print(): under
# pythonw there is no console and a printed refusal is lost.
_log = logging.getLogger("friday.privacy.vault_access")

#: The component a check_action refusal names, unless the public-query floor
#: (privacy/public_query) refused it, which names itself.
COMPONENT = "privacy.vault_access"


class Tier:
    """Sensitivity tiers. Integer-valued so callers can compare/sort."""
    PUBLIC = 1      # TIER_1 — any model (wiki content, news, general docs)
    PRIVATE = 2     # TIER_2 — local only; cloud gets a summary/placeholder
    SENSITIVE = 3   # TIER_3 — local only; cloud gets nothing

    NAMES = {1: "TIER_1", 2: "TIER_2", 3: "TIER_3"}


# Providers that run on-device and are therefore trusted with raw vault data.
# A provider name alone is not enough: an Ollama model tagged `:cloud` or
# `-cloud` is relayed to ollama.com, so can_access() also takes the model.
LOCAL_PROVIDERS = {"ollama", "local"}


# ── Local-sink tools: exempt from the per-action input-tier deny ────────────
# Without this exemption, learn_skill writes are denied as
# "cloud_denied_tier_TIER_2" because check_action classifies the tool's
# ARGUMENTS — for learn_skill, the entire skill YAML — and ordinary skill
# vocabulary ("memory", "todo", "contact") tiers as TIER_2.
#
# The boundary this gate actually protects: a cloud provider must not
# RECEIVE vault data. Tool arguments are authored BY the provider — it has
# already seen them — and a local-sink tool writes them to local disk and
# returns only a status string. Nothing leaves the device, so an input-tier
# deny is a category error there. Input-tier denial still applies to every
# tool that can return vault data to the provider (search_*, read_*,
# query_*) or transmit its input off-device (send_*, navigate, open_url).
#
# Growing this set is a security decision: a member must neither return
# vault data to the provider nor transmit its input off-device. Decisions
# for these tools are still classified and audit-logged.
LOCAL_SINK_TOOLS = frozenset({"learn_skill", "write_file"})


# ── Keyword lists exposed for legacy callers ────────────────────────────────
# The authoritative definitions live in services/sensitivity_classifier.py.
# These are imported from there so both modules agree by construction.
try:
    from agent_friday.services.sensitivity_classifier import (
        TIER_3_KEYWORDS, TIER_2_KEYWORDS,
        classify_legacy as _sc_classify_legacy,
    )
    _HAS_CLASSIFIER = True
except Exception:
    _HAS_CLASSIFIER = False
    TIER_3_KEYWORDS = (
        "financial", "finance", "bank account", "routing number", "account number",
        "investment", "portfolio", "brokerage", "credit card", "tax return",
        "net worth", "salary", "income",
        "health record", "medical", "medication", "prescription", "diagnosis",
        "doctor", "insurance", "appointment",
        "legal", "custody", "court", "divorce", "settlement",
        "ssn", "social security", "passport", "driver's license", "date of birth",
        "encrypted", "sovereign vault",
    )
    TIER_2_KEYWORDS = (
        "contact", "phone number", "home address", "family", "daughter",
        "partner", "personal note", "memory", "trust graph", "relationship",
        "todo",
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

    def can_access(self, provider, model=""):
        """True only when the provider is a local (on-device) model.

        A cloud-tagged Ollama model (`glm-4.6:cloud`, `gpt-oss:120b-cloud`) is
        served through the local daemon but runs in Ollama's cloud, so it gets
        the cloud policy whatever the provider name says.
        """
        if is_cloud_model_tag(model):
            return False
        return str(provider or "").lower() in LOCAL_PROVIDERS

    def classify(self, content, default=Tier.PUBLIC):
        """Best-effort sensitivity tier for a chunk of content.

        Delegates to the unified layered classifier in
        services/sensitivity_classifier.py (which runs regex → Presidio NER →
        embedding similarity → optional local LLM, all on-device). Falls back to
        the legacy keyword scan when the classifier module is unavailable.

        Returns `default` (PUBLIC) when nothing sensitive is detected — this
        legacy default is intentionally PUBLIC for backward-compat with vault
        context assembly. The egress gate uses PRIVATE as its default.
        """
        if not content or not isinstance(content, str):
            return default
        if _HAS_CLASSIFIER:
            try:
                # Use keyword+regex only (no embeddings) for vault prompt assembly:
                # the upstream tier-gating is about what goes INTO the prompt, where
                # keyword precision matters more than semantic recall. The egress gate
                # adds the semantic layer as a last line of defence.
                return _sc_classify_legacy(
                    content, default=default,
                    use_presidio=False, use_embeddings=False,
                )
            except Exception:
                pass
        # Fallback: keyword-only scan
        low = content.lower()
        if any(kw in low for kw in TIER_3_KEYWORDS):
            return Tier.SENSITIVE
        if any(kw in low for kw in TIER_2_KEYWORDS):
            return Tier.PRIVATE
        return default

    def gate_content(self, content, provider, tier=None, fallback="redact",
                     detail="content", model=""):
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

        if self.can_access(provider, model):
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

    def assemble_prompt(self, sections, provider, fallback="redact", model=""):
        """Join tier-tagged prompt sections, gating by provider.

        `sections` is a list of (tier, text) tuples. Returns the gated prompt
        string. Local providers get everything; cloud providers get TIER_1 in
        full, TIER_2 as a placeholder, and TIER_3 dropped entirely.
        """
        local = self.can_access(provider, model)
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
        _log.debug("[VAULT] %s provider=%s tier=%s (%s)",
                   verdict, entry["provider"], entry["tier"], detail)

    # ── Continuous Authorization (zero-trust per-action gate) ─────

    def check_action(self, provider, action, data, access_log_path=None,
                     *, taint_key=None, model="", author_provider=""):
        """Zero-trust per-action authorization check.

        Called before every tool call in the agent loop. Classifies the
        tool input data, gates access by provider, and appends every
        decision to access-log.jsonl for auditability.

        PUBLIC QUERY TEXT (owner decision (a), 2026-10-15; see
        privacy/public_query.py): for a cloud provider, the public query fields
        of search_web / search_news / deep_research are NOT
        topic-classified. They are checked only against the never-send floor
        and the owner's personal identifiers. Any other argument of those
        tools is classified exactly as before. Same with or without consent.
        browse_web.url is not public query text (a URL's query string can
        carry context out), so a URL keeps the ordinary classification.

        When a cloud provider's public query passes, each exact string is
        recorded (public_query.record_cloud_query) under `author_provider`
        (the provider name the egress gate will replay history to; defaults
        to `provider`). Only a recorded string replays to that provider under
        the floor; any other replayed query keeps the ordinary gate.

        Every refusal is logged to friday.log (WARNING, component + reason +
        tool, never the value) as well as to access-log.jsonl.

        Returns (allowed: bool, detail: str, tier: int).
        """
        data_str = data if isinstance(data, str) else json.dumps(data or {}, default=str)
        local = self.can_access(provider, model)
        pq_split = None
        if not local:
            try:
                from agent_friday.privacy import public_query as _pq
                _args = json.loads(data_str) if isinstance(data, str) else data
                pq_split = _pq.split_args(str(action or ""), _args)
            except Exception:
                # Not a mapping, or the module is unreachable: the ordinary
                # policy (topic classification of the whole input) applies.
                pq_split = None
        pq_refusal = None
        if pq_split is not None:
            _texts, _rest = pq_split
            try:
                pq_refusal = _pq.check(_texts, action=str(action or ""),
                                       provider=str(provider or ""),
                                       surface="check_action", log=False)
            except Exception as _e:   # fail closed
                pq_refusal = _pq.Refusal("floor_unavailable",
                                         kinds=(type(_e).__name__,))
            # The tool's OTHER arguments keep the ordinary policy.
            tier = (self.classify(json.dumps(_rest, default=str))
                    if _rest else Tier.PUBLIC)
        else:
            tier = self.classify(data_str)
        allowed = True
        detail = "action_allowed"

        if pq_refusal is not None:
            allowed = False
            detail = f"public_query_refused:{pq_refusal.reason}"
            tier = max(tier, Tier.SENSITIVE)
        elif tier > Tier.PUBLIC and not local:
            if str(action or "") in LOCAL_SINK_TOOLS:
                # Provider-authored content flowing device-ward only — see
                # the LOCAL_SINK_TOOLS note. Allowed, but logged as such.
                detail = f"local_sink_exempt_{Tier.NAMES.get(tier, tier)}"
            else:
                allowed = False
                detail = f"cloud_denied_tier_{Tier.NAMES.get(tier, tier)}"
                # A BUSINESS'S PUBLISHED CONTACT DETAILS ARE NOT THE OWNER'S
                # PRIVATE DATA. See privacy/public_provenance for the whole
                # rule and why it is shaped this way; in one line, it is the
                # LOCAL_SINK_TOOLS argument above ("the provider authored these
                # arguments, it has already seen them") applied to a single
                # value instead of to a whole tool, and only when that value is
                # provably public, is a business-contact type, is not in the
                # owner's own records, and is the only reason for the tier.
                #
                # It runs only when a caller passed a provenance ledger: no
                # ledger, no exemption. Every use is logged below with its own
                # detail string, beside every denial.
                if taint_key:
                    try:
                        from agent_friday.privacy import public_provenance as _pbp
                        _ok, _why = _pbp.exempt(
                            pq_split[1] if pq_split is not None else data,
                            action=str(action or ""), taint_key=taint_key)
                    except Exception as _e:
                        _ok, _why = False, f"provenance check failed ({_e})"
                    if _ok:
                        allowed = True
                        detail = ("public_web_provenance_"
                                  f"{Tier.NAMES.get(tier, tier)}: {_why}")

        if allowed and pq_split is not None and detail == "action_allowed":
            detail = "public_query_ok"
        if allowed and pq_split is not None and pq_refusal is None:
            # Replay provenance: this cloud provider wrote exactly these
            # strings and they passed. Only these may later replay to the
            # same provider under the floor (egress_gate._gate_tool_args).
            try:
                _pq.record_cloud_query(author_provider or provider, pq_split[0])
            except Exception:
                pass                  # unrecorded -> the replay keeps the ordinary gate

        entry = {
            "ts": time.time(),
            "provider": str(provider or "unknown").lower(),
            "action": str(action or "unknown"),
            "tier": Tier.NAMES.get(tier, str(tier)),
            "allowed": allowed,
            "detail": detail,
            "data_hash": hashlib.sha256(data_str.encode("utf-8")).hexdigest(),
        }

        # Persist to access-log.jsonl (separate from the general vault log)
        log_dest = access_log_path or self.log_path
        if log_dest is not None:
            try:
                p = Path(log_dest) if not isinstance(log_dest, Path) else log_dest
                # If caller gave a directory-style path, use access-log.jsonl in it
                if p.suffix != ".jsonl":
                    p = p.parent / "access-log.jsonl"
                else:
                    p = p.parent / "access-log.jsonl"
                p.parent.mkdir(parents=True, exist_ok=True)
                # Rotate when the log exceeds 50 MB to prevent unbounded disk fill.
                _ACCESS_LOG_MAX = 50 * 1024 * 1024
                if p.exists() and p.stat().st_size > _ACCESS_LOG_MAX:
                    try:
                        p.rename(p.with_suffix(".1.jsonl"))
                    except Exception:
                        pass
                with open(p, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass

        self._record(provider, tier, allowed, f"check_action:{action}")
        if allowed:
            _log.debug("[VAULT-ZT] ALLOW provider=%s action=%s tier=%s (%s)",
                       entry["provider"], action, entry["tier"], detail)
        else:
            # Every refusal names its component and reason in friday.log.
            # Kinds and tiers only -- never the tool input.
            _comp, _reason = denial_component(detail)
            _log.warning("[VAULT-ZT] REFUSED component=%s reason=%s tool=%s "
                         "provider=%s tier=%s",
                         _comp, _reason, action, entry["provider"], entry["tier"])
        return allowed, detail, tier

    def regate_on_provider_change(self, old_provider, new_provider, pending_data,
                                   action="provider_switch", access_log_path=None,
                                   new_model=""):
        """Re-gate and redact when the provider changes mid-task.

        If the new provider is cloud and pending data is sensitive, the
        data is redacted. Returns (redacted_data, was_regated: bool).
        """
        if (str(old_provider or "").lower() == str(new_provider or "").lower()
                and not is_cloud_model_tag(new_model)):
            return pending_data, False

        allowed, detail, tier = self.check_action(
            new_provider, action, pending_data, access_log_path,
            model=new_model
        )

        if allowed:
            return pending_data, True

        # New provider can't see this data — redact it
        if isinstance(pending_data, str):
            redacted = self.gate_content(pending_data, new_provider, tier=tier,
                                         model=new_model)
        else:
            redacted = "[VAULT-PROTECTED — provider changed; data redacted]"

        _log.info("[VAULT-ZT] REGATE provider %s->%s tier=%s - data redacted",
                  old_provider, new_provider, Tier.NAMES.get(tier, tier))
        return redacted, True

    def stats(self):
        with self._lock:
            return {
                "allowed": self._counts["allowed"],
                "denied": self._counts["denied"],
                "recent": list(self._log[-50:]),
            }


# ── Refusal transparency ────────────────────────────────────────────────────

_PQ_PREFIX = "public_query_refused:"


def denial_component(detail):
    """(component, reason) for a check_action detail string."""
    d = str(detail or "")
    if d.startswith(_PQ_PREFIX):
        return "privacy.public_query", d[len(_PQ_PREFIX):] or "unknown"
    return COMPONENT, d or "unknown"


def refusal_text(action, detail):
    """What the MODEL is told when check_action refuses a tool call.

    Names the component and the reason, and tells the model to report the
    skipped step to the user rather than leave it out silently. Never carries
    the tool input. Keeps the "[VAULT ACCESS DENIED]" prefix that trace and
    orb correlation read.
    """
    comp, reason = denial_component(detail)
    if comp == "privacy.public_query":
        try:
            from agent_friday.privacy.public_query import _REASON_TEXT
            why = _REASON_TEXT.get(reason, reason)
        except Exception:
            why = reason
        remedy = ("Nothing was sent. Rephrase the query without that part "
                  "(the topic itself is fine), or have a local model handle it.")
    else:
        why = f"the input classifies as {reason.replace('cloud_denied_tier_', '')} private data"
        remedy = "Switch to a local model to work with this content."
    return (f"[VAULT ACCESS DENIED] component={comp} reason={reason} "
            f"tool={action}: {why}. {remedy} This step was skipped: tell the "
            f"user which part you could not do and why (name {comp} and the "
            f"reason) instead of silently leaving it out.")
