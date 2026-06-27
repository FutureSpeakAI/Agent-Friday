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
Local providers (Ollama / 'local') bypass this gate; data stays on-device.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from agent_friday.services.sensitivity_classifier import classify as _classify_impl, Tier

# ── Provider classification ────────────────────────────────────────────────────
_LOCAL_PROVIDERS = {"ollama", "local"}

_LOG_LOCK = threading.Lock()
_DEFAULT_LOG = Path.home() / ".friday" / "vault" / "egress-log.jsonl"


def _is_cloud(provider: str) -> bool:
    return (provider or "").lower().strip() not in _LOCAL_PROVIDERS


def _classify_cloud(text: str) -> int:
    """Classify content for cloud egress.

    Uses PUBLIC as the base default — content with no signals from any layer is
    treated as public and allowed through. Fail-closed behaviour is provided by
    the embedding layer: text semantically close to sensitive exemplars (sim >=
    0.50) is conservatively classified as PRIVATE before any keyword/regex match
    is required. This catches contextual PII ("my son lives with me on weekends")
    that keyword lists miss, while not blocking genuinely neutral conversations.
    """
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
    print(
        f"  [EGRESS] {verdict} provider={provider} "
        f"field={field} tier={Tier.NAMES.get(tier, tier)} ({reason})"
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
                else:
                    new_parts.append(part)
            gated.append({**msg, "content": new_parts})
        else:
            gated.append(msg)
    return gated


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
