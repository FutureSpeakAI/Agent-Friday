"""
Egress Gate — the security boundary for all outbound cloud API calls.

seal_outbound(payload, provider) runs immediately before EVERY cloud HTTP call,
after payload assembly and before the network request. It is the enforcement
boundary; the model router is an optimization that happens before it.

Architecture:
  Router     — decides WHICH provider a request goes to (routing optimization)
  EgressGate — decides WHAT that provider is allowed to see (security boundary)

These are separate by design. The router can be wrong or bypassed; the gate is
the last line of defense and cannot be bypassed without modifying this module.

Default: REDACT on uncertainty — fail-closed, not fail-open.
Local providers bypass this gate; data stays on-device. "Local" is decided by
the provider REGISTRY's egress classification (classification: "local" + a
local-capable adapter + a loopback/RFC1918 base_url verified at call time —
see is_local_provider), with the legacy {"ollama", "local"} family names kept
only as a fallback for non-registry provider strings.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from agent_friday.services.sensitivity_classifier import classify as _classify_impl, Tier

# ── Provider classification ────────────────────────────────────────────────────
# Legacy family names that historically bypassed the gate. Kept ONLY as the
# belt-and-braces fallback for provider strings that are not registry names
# (the executor's family enums). The primary check is registry-driven:
# is_local_provider() below (GAP-9 fix, spec §9.2).
_LOCAL_PROVIDERS = {"ollama", "local"}


def is_local_provider(name: str) -> bool:
    """Registry-driven local classification (spec §9.2) — the gate bypass test.

    A provider earns the local bypass only when ALL of:
      1. its registry descriptor says `classification: "local"` (effective —
         normalize demotes unearned claims at load),
      2. its adapter is a local-capable transport (ollama / local-voice /
         nemo-local / openai-compatible), and
      3. its base_url resolves to a loopback / RFC1918 / *.local host —
         re-checked HERE at call time, so a settings edit between registry
         load and the call cannot open a hole.

    A descriptor typed "ollama" but pointing at a REMOTE url therefore
    classifies CLOUD and gets sealed — the inverse of GAP-9. Names not in the
    registry fall back to the legacy family set (defense in depth), so the
    executor's "ollama"/"local" enums keep bypassing and everything else —
    including unknown/empty names — is cloud (fail-closed).
    """
    n = (name or "").lower().strip()
    if not n:
        return False  # unknown provenance → cloud → gated
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        from agent_friday.routing.provider_descriptors import (
            classification_of, adapter_of, is_private_host,
            LOCAL_CAPABLE_ADAPTERS,
        )
        p = get_provider_registry().get_provider(n)
    except Exception:
        # Registry unavailable (early boot, tests importing the gate alone):
        # the conservative legacy set is the only authority.
        return n in _LOCAL_PROVIDERS
    if p is None:
        return n in _LOCAL_PROVIDERS  # legacy belt-and-braces
    if classification_of(p) != "local":
        return False
    if adapter_of(p) not in LOCAL_CAPABLE_ADAPTERS:
        return False
    # Call-time re-verification of the actual host (DNS re-resolved, cached
    # briefly inside is_private_host).
    return is_private_host(p.get("base_url"))

_LOG_LOCK = threading.Lock()
_DEFAULT_LOG = Path.home() / ".friday" / "vault" / "egress-log.jsonl"

# Structured logger for the single most security-sensitive line in the codebase —
# every cloud egress allow/block decision. Using `print()` here (the prior state)
# meant that under pythonw.exe / a detached tray launch (no console) these decisions
# were silently discarded, creating an invisible audit gap. Routing through the
# friday.* hierarchy sends them to ~/.friday/friday.log alongside everything else.
_dlog = logging.getLogger("friday.egress")


# ── Classifier rate limiting ──────────────────────────────────────────────────
# The classifier's heavy layers (embeddings, NER) are expensive. Under a load
# spike — many concurrent channel messages, a burst of parallel subagents —
# unbounded concurrent classification can exhaust memory and make the classifier
# raise, which trips the fail-closed path and blocks traffic that a paced
# classifier would have passed. So excess callers QUEUE instead of crashing:
# a token bucket admits at most FRIDAY_EGRESS_CLASSIFY_RATE calls per second
# and briefly sleeps the rest. Pacing is best-effort (a bounded wait, then
# proceed) — the CONTENT gate itself always still runs.
try:
    _RATE_MAX_PER_SEC = max(1, int(os.environ.get("FRIDAY_EGRESS_CLASSIFY_RATE", "40")))
except ValueError:
    _RATE_MAX_PER_SEC = 40
_RATE_MAX_WAIT_S = 10.0
_RATE_LOCK = threading.Lock()
_rate_window_start = 0.0
_rate_count = 0


def _rate_limit() -> None:
    """Pace classifier calls: queue (sleep) instead of crash under load spikes.

    Never raises and never skips classification — after _RATE_MAX_WAIT_S the
    caller proceeds anyway (all work is local; only the pacing is waived), so a
    pathological burst degrades to slower classification, not a blocked gate.
    """
    global _rate_window_start, _rate_count
    deadline = time.monotonic() + _RATE_MAX_WAIT_S
    while True:
        with _RATE_LOCK:
            now = time.monotonic()
            if now - _rate_window_start >= 1.0:
                _rate_window_start = now
                _rate_count = 0
            if _rate_count < _RATE_MAX_PER_SEC:
                _rate_count += 1
                return
            wait = 1.0 - (now - _rate_window_start)
        if time.monotonic() >= deadline:
            return  # never block a request indefinitely — pacing is best-effort
        time.sleep(min(max(wait, 0.01), 0.25))


def _is_cloud(provider: str) -> bool:
    """True when the provider's payloads leave the device (must be gated).

    Registry-first via is_local_provider(): the registry's egress
    classification decides, with the hardcoded family set only as fallback
    for non-registry names. Default on uncertainty: CLOUD (gated).
    """
    return not is_local_provider(provider)


def _classify_cloud(text: str) -> int:
    """Classify content for cloud egress.

    Uses PUBLIC as the base default — content with no signals from any layer is
    treated as public and allowed through. Fail-closed behaviour is provided by
    the embedding layer: text semantically close to sensitive exemplars (sim >=
    0.50) is conservatively classified as PRIVATE before any keyword/regex match
    is required. This catches contextual PII ("my son lives with me on weekends")
    that keyword lists miss, while not blocking genuinely neutral conversations.

    Calls are paced by _rate_limit() so a load spike queues briefly instead of
    OOM-crashing the classifier (which would trip fail-closed and block traffic).
    """
    _rate_limit()
    return _classify_impl(text, default=Tier.PUBLIC)


def _redact_placeholder(tier: int) -> str:
    name = Tier.NAMES.get(tier, f"TIER_{tier}")
    return (
        f"[EGRESS-GATE: {name} content withheld — did not leave your device. "
        f"Use a local model (Ollama) to process this without redaction.]"
    )


def _log(provider: str, field: str, tier: int, action: str, reason: str,
         log_path: Path | None = None):
    entry = {
        "ts": time.time(),
        "provider": provider,
        "field": field,
        "tier": Tier.NAMES.get(tier, str(tier)),
        "action": action,
        "reason": reason,
    }
    verdict = "ALLOW" if action == "allow" else "BLOCK"
    # Structured log (survives pythonw / no-console launches). INFO for allow,
    # WARNING for a block so a withheld leak is visible even at a raised log level.
    _dlog.log(
        logging.INFO if action == "allow" else logging.WARNING,
        "%s provider=%s field=%s tier=%s (%s)",
        verdict, provider, field, Tier.NAMES.get(tier, tier), reason,
    )
    dest = log_path or _DEFAULT_LOG
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with open(dest, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ── Field-level gating ────────────────────────────────────────────────────────

def _gate_text(text: str, provider: str, field: str,
               log_path: Path | None = None) -> str:
    """Gate a single text string for a cloud provider."""
    if not text or not isinstance(text, str):
        return text
    tier = _classify_cloud(text)
    if tier == Tier.PUBLIC:
        _log(provider, field, tier, "allow", "public-content", log_path)
        return text
    if tier == Tier.SENSITIVE:
        _log(provider, field, tier, "drop", f"tier={Tier.NAMES[tier]}", log_path)
        return ""  # cloud gets nothing for SENSITIVE
    # TIER_2 / PRIVATE → redacted placeholder
    _log(provider, field, tier, "redact", f"tier={Tier.NAMES[tier]}", log_path)
    return _redact_placeholder(tier)


def _gate_messages(messages: list, provider: str,
                   log_path: Path | None = None) -> list:
    """Gate a list of message dicts. Returns a new list; never mutates input."""
    gated = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            gated.append(msg)
            continue
        if "content" not in msg:
            gated.append(msg)
            continue
        content = msg["content"]
        if isinstance(content, str):
            gated.append({
                **msg,
                "content": _gate_text(
                    content, provider, f"message[{i}].content", log_path
                ),
            })
        elif isinstance(content, list):
            new_parts = []
            for j, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "text":
                    new_parts.append({
                        **part,
                        "text": _gate_text(
                            part.get("text", ""), provider,
                            f"message[{i}].content[{j}].text", log_path,
                        ),
                    })
                elif isinstance(part, dict) and part.get("type") == "tool_result":
                    new_parts.append(_gate_tool_result(
                        part, provider, f"message[{i}].content[{j}]", log_path))
                else:
                    new_parts.append(part)
            gated.append({**msg, "content": new_parts})
        else:
            gated.append(msg)
    return gated


# What the model sees in place of a SENSITIVE tool result. An empty string
# (what _gate_text returns for SENSITIVE) would read as "the tool returned
# nothing" and send the agent loop into pointless retries; this marker lets it
# report the withholding and move on.
_TOOL_RESULT_WITHHELD = ("[tool result withheld by egress gate — SENSITIVE "
                         "content stays on this device; use a local model to "
                         "work with it]")


def _gate_tool_result(part: dict, provider: str, field: str,
                      log_path: Path | None = None) -> dict:
    """Gate the text inside a tool_result block (file reads, command output —
    whatever a tool pulled mid-loop). Anthropic allows `content` as a plain
    string or a list of typed blocks; non-text blocks (images) pass through
    untouched because a text classifier can't judge them (documented caveat)."""
    def _gate_one(text: str, subfield: str) -> str:
        out = _gate_text(text, provider, subfield, log_path)
        return _TOOL_RESULT_WITHHELD if (text and out == "") else out

    inner = part.get("content")
    if isinstance(inner, str):
        return {**part, "content": _gate_one(inner, f"{field}.tool_result")}
    if isinstance(inner, list):
        new_inner = []
        for k, block in enumerate(inner):
            if isinstance(block, dict) and block.get("type") == "text":
                new_inner.append({
                    **block,
                    "text": _gate_one(block.get("text", ""),
                                      f"{field}.tool_result[{k}]"),
                })
            else:
                new_inner.append(block)
        return {**part, "content": new_inner}
    return part


def _gate_tools(tools: list, provider: str,
                log_path: Path | None = None) -> list:
    """Scan tool descriptions; redact any that carry sensitive context."""
    gated = []
    for tool in tools:
        if not isinstance(tool, dict):
            gated.append(tool)
            continue
        desc = tool.get("description", "")
        if desc and _classify_cloud(desc) > Tier.PUBLIC:
            _log(provider, "tool.description", Tier.PRIVATE,
                 "redact", "sensitive-tool-desc", log_path)
            gated.append({**tool, "description": "[description withheld by egress gate]"})
        else:
            gated.append(tool)
    return gated


# ── Public API ────────────────────────────────────────────────────────────────

def gate_text(text: str, provider: str, field: str = "prompt",
              log_path: Path | None = None) -> str:
    """Gate a single user-supplied text string for a cloud provider.

    Public wrapper around the field-level gate so call sites that do NOT go
    through the Anthropic/OpenAI payload path — most importantly the Gemini
    ``models.generate_content`` sites, which build their own ``contents`` and
    never hit ``seal_outbound`` — can still enforce the boundary on the text
    they send. Local providers pass through unchanged. Same fail-closed
    semantics as ``seal_outbound``: SENSITIVE → "" (dropped), PRIVATE →
    redacted placeholder, PUBLIC → unchanged.
    """
    if not _is_cloud(provider):
        return text
    return _gate_text(text, provider, field, log_path)


def seal_outbound(
    payload: dict[str, Any],
    provider: str,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Gate the assembled call payload before it leaves to a cloud provider.

    Parameters
    ----------
    payload  : the fully assembled call payload (system, messages, tools, …)
    provider : provider name — "anthropic", "openai", "gemini", "ollama", etc.
    log_path : optional path to the egress log file

    Returns a new payload dict with sensitive content redacted or dropped.
    Local providers (Ollama / 'local') are returned unchanged.

    Default on uncertainty: REDACT — fail-closed, not fail-open.
    """
    if not _is_cloud(provider):
        return payload  # stays on-device, no gating needed

    sealed = dict(payload)

    # System prompt
    if "system" in sealed and isinstance(sealed["system"], str):
        sealed["system"] = _gate_text(
            sealed["system"], provider, "system", log_path
        )

    # Message history (Anthropic format: list of dicts)
    if "messages" in sealed and isinstance(sealed["messages"], list):
        sealed["messages"] = _gate_messages(
            sealed["messages"], provider, log_path
        )

    # Tool definitions
    if "tools" in sealed and isinstance(sealed["tools"], list):
        sealed["tools"] = _gate_tools(sealed["tools"], provider, log_path)

    return sealed


# ── Startup self-test (R2, Fable 5 adversarial review) ────────────────────────
_SELF_TEST_RESULT = None  # None = not yet run; dict {"ok": bool, ...} once run


def startup_self_test() -> dict:
    """Seal a known-SENSITIVE probe once and record whether the gate withheld it.

    Called at server boot: if the gate cannot positively withhold a probe that
    is unambiguously Tier-3 (SSN + bank-account keywords) — a layer failed to
    import, the classifier crashed, or gating silently passed the text through —
    cloud routing is refused (model_router._seal_or_block consults
    gate_operational()) so the failure mode is caught at boot, not at first leak.
    """
    global _SELF_TEST_RESULT
    probe = "My SSN is 123-45-6789 and my bank account number is 987654321."  # pragma: allowlist secret
    try:
        sealed = seal_outbound(
            {"messages": [{"role": "user", "content": probe}]},
            "selftest-cloud",
            log_path=Path(os.devnull),
        )
        msgs = sealed.get("messages") or [{}]
        out = str(msgs[0].get("content", "")) if isinstance(msgs[0], dict) else ""
        ok = probe not in out and "123-45-6789" not in out  # pragma: allowlist secret
        _SELF_TEST_RESULT = {"ok": ok}
        if not ok:
            _SELF_TEST_RESULT["error"] = "sensitive probe survived the gate"
    except Exception as e:
        _SELF_TEST_RESULT = {"ok": False, "error": str(e)}
    return _SELF_TEST_RESULT


def gate_operational() -> bool:
    """False only when the startup self-test ran AND failed.

    When the self-test has not run (unit tests, library embedding) the per-call
    fail-closed wrapper in model_router remains the enforcement point, so the
    default is True.
    """
    return _SELF_TEST_RESULT is None or bool(_SELF_TEST_RESULT.get("ok"))
