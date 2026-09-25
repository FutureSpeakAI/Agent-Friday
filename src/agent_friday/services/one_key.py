"""One key is enough: Anthropic, or OpenRouter instead.

A cloud install needs exactly one AI key. Anthropic is the default; OpenRouter
is the alternative, and it serves the same Claude models under its own ids
(``claude-sonnet-5`` there is ``anthropic/claude-sonnet-5``,
``claude-haiku-4-5-20251001`` is ``anthropic/claude-haiku-4.5``).

Everything that thinks in Friday reaches a model through one of three doors:
the model router's decision (chat, ``_generate_text``, ``_generate_agent``),
the bare ``_call_claude`` / ``_call_claude_agent`` primitives, or a probe that
asks "is there a cloud key?". Each door consults this module, so an install
holding only one of the two keys is served by that key, and neither key is
ever required when the other is present.

THE RULE, in one place:

* Anthropic is used whenever an Anthropic key is present.
* Otherwise an OpenRouter key serves the same Claude model through OpenRouter.
* The reverse holds too: an ``anthropic/claude-*`` id bound to OpenRouter is
  served natively when only the Anthropic key exists.
* With neither key, nothing is substituted and the caller's own "no key"
  path runs unchanged.

No function here returns, logs or stores a key value. ``verify`` sends one
token through the existing verdict logic (services/key_verdict.py) and
reports only the verdict.
"""
from __future__ import annotations

import logging
import re

_log = logging.getLogger("friday.one_key")

ANTHROPIC = "anthropic"
OPENROUTER = "openrouter"

LABELS = {ANTHROPIC: "Anthropic", OPENROUTER: "OpenRouter"}

KEY_URLS = {ANTHROPIC: "https://console.anthropic.com/settings/keys",
            OPENROUTER: "https://openrouter.ai/keys"}

#: The cheapest current Claude model on each door, for a one-token check.
PROBE_MODEL = {ANTHROPIC: "claude-haiku-4-5-20251001",
               OPENROUTER: "anthropic/claude-haiku-4.5"}

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_VERSION_DASH = re.compile(r"(\d)-(\d)")
_VERSION_DOT = re.compile(r"(\d)\.(\d)")


# ── Model ids across the two doors ───────────────────────────────────────────

def openrouter_id_for(model) -> str:
    """The OpenRouter id serving the same Claude model.

    ``claude-opus-5-5`` -> ``anthropic/claude-opus-5.5``;
    ``claude-haiku-4-5-20251001`` -> ``anthropic/claude-haiku-4.5``. An id that
    already carries a vendor prefix is returned unchanged. A missing or
    non-Claude id falls back to the default cloud model's equivalent.
    """
    mid = str(model or "").strip()
    if "/" in mid:
        return mid
    if not mid.startswith("claude"):
        mid = _default_claude_model()
    mid = _DATE_SUFFIX.sub("", mid)
    return "anthropic/" + _VERSION_DASH.sub(r"\1.\2", mid)


def anthropic_id_for(model) -> str | None:
    """The native Anthropic id for an ``anthropic/claude-*`` OpenRouter id.

    ``anthropic/claude-opus-5.5`` -> ``claude-opus-5-5``. A ``:variant``
    suffix (``:batch``, ``:free``) has no native twin, so it returns None, as
    does any id that is not an Anthropic model.
    """
    mid = str(model or "").strip()
    if not mid.startswith("anthropic/claude") or ":" in mid:
        return None
    tail = mid.split("/", 1)[1]
    return _VERSION_DOT.sub(r"\1-\2", tail)


def _default_claude_model() -> str:
    try:
        from agent_friday.core import ANTHROPIC_MODEL_DEFAULT
        if str(ANTHROPIC_MODEL_DEFAULT or "").startswith("claude"):
            return ANTHROPIC_MODEL_DEFAULT
    except Exception:
        pass
    return "claude-sonnet-5"


# ── Which key exists ─────────────────────────────────────────────────────────

def anthropic_ready() -> bool:
    """True when the native Anthropic client can be built (a key exists)."""
    try:
        from agent_friday import core
        return core.get_anthropic_client() is not None
    except Exception:
        return False


def _descriptor(name: str) -> dict | None:
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        return get_provider_registry().get_provider(name)
    except Exception:
        return None


def openrouter_ready() -> bool:
    """True when an OpenRouter key is saved or in the environment."""
    prov = _descriptor(OPENROUTER)
    if not prov or not prov.get("enabled", True):
        return False
    try:
        from agent_friday.routing.provider_descriptors import provider_api_key
        return bool(provider_api_key(prov))
    except Exception:
        return False


def key_in_use() -> str | None:
    """"anthropic" | "openrouter" | None: the key Friday thinks with."""
    if anthropic_ready():
        return ANTHROPIC
    if openrouter_ready():
        return OPENROUTER
    return None


def cloud_key_present() -> bool:
    """True when either key is present: the cloud can think."""
    return key_in_use() is not None


# ── Substitution at the three doors ──────────────────────────────────────────

def substitute_route(result: dict) -> dict:
    """Serve a routing decision through whichever of the two keys exists.

    A decision for Anthropic with no Anthropic key moves to OpenRouter when an
    OpenRouter key exists; an ``anthropic/claude-*`` decision on OpenRouter
    with no OpenRouter key moves to Anthropic when that key exists. Anything
    else, including every local decision, is returned untouched. Never raises.
    """
    try:
        if not isinstance(result, dict):
            return result
        provider = result.get("provider")
        model = str(result.get("model") or "")
        if provider == "cloud" and (not model or model.startswith("claude")):
            if anthropic_ready() or not openrouter_ready():
                return result
            result["provider"] = "openai"
            result["provider_name"] = OPENROUTER
            result["model"] = openrouter_id_for(model)
            result["one_key"] = OPENROUTER
            result["reason"] = ((result.get("reason") or "")
                                + " (no Anthropic key: the same model through OpenRouter)")
            return result
        if (provider == "openai" and result.get("provider_name") == OPENROUTER
                and anthropic_id_for(model)):
            if openrouter_ready() or not anthropic_ready():
                return result
            result["provider"] = "cloud"
            result["provider_name"] = ANTHROPIC
            result["model"] = anthropic_id_for(model)
            result["one_key"] = ANTHROPIC
            result["reason"] = ((result.get("reason") or "")
                                + " (no OpenRouter key: the same model from Anthropic)")
        return result
    except Exception as e:  # a routing helper must never break a turn
        _log.debug("one-key substitution skipped: %s", e)
        return result


def openrouter_instead(model=None) -> str | None:
    """For a bare Anthropic primitive about to fail for want of a key: the
    OpenRouter model id to use instead, or None when OpenRouter cannot serve
    either (so the caller raises its usual error)."""
    if anthropic_ready() or not openrouter_ready():
        return None
    return openrouter_id_for(model)


def served_name(model=None) -> str:
    """How to name the model a bare Claude call will actually reach."""
    alt = openrouter_instead(model)
    if alt:
        return "%s (via OpenRouter)" % alt
    return str(model or _default_claude_model())


# ── What the setup chat and Settings say ─────────────────────────────────────

def status() -> dict:
    """The one-key summary the checklist, Settings and the setup chat show.

    Never contains a key value. ``line`` is a plain sentence; ``links`` are the
    two places a key comes from, Anthropic first.
    """
    from agent_friday.services import setup_chat_copy as copy
    in_use = key_in_use()
    links = [{"provider": ANTHROPIC, "label": copy.ONE_KEY_LINK_LABELS[ANTHROPIC],
              "url": KEY_URLS[ANTHROPIC]},
             {"provider": OPENROUTER, "label": copy.ONE_KEY_LINK_LABELS[OPENROUTER],
              "url": KEY_URLS[OPENROUTER]}]
    if in_use:
        line = copy.ONE_KEY_IN_USE.format(label=LABELS[in_use])
    else:
        line = copy.ONE_KEY_NONE
    return {"in_use": in_use, "label": LABELS.get(in_use or "", ""),
            "sufficient": bool(in_use), "line": line, "links": links}


# ── Checking a key works ─────────────────────────────────────────────────────

def verify(name: str) -> dict:
    """Spend one token with the stored key for `name` and say what happened.

    Reuses the settings-side verdict logic (services/key_verdict.py) that
    ``POST /api/providers/<name>/test`` uses. Returns
    {provider, verdict, can_think, text}; fails open to ``unknown``.
    """
    from agent_friday.services import key_verdict as kv
    prov = _descriptor(name)
    label = (LABELS.get(name) or (prov or {}).get("label") or name)
    if not prov:
        return {"provider": name, "verdict": kv.UNKNOWN, "can_think": False,
                "text": kv.explain(kv.UNKNOWN, label)}
    model = PROBE_MODEL.get(name) or (prov.get("models") or [None])[0]
    verdict = kv.ping(prov, model)
    return {"provider": name, "verdict": verdict,
            "can_think": verdict == kv.OK or (verdict == kv.UNKNOWN
                                              and cloud_key_present()),
            "text": kv.explain(verdict, label)}
