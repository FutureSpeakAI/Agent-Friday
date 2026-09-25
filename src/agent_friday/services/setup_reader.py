"""Which model, if any, reads what the user tells the setup chat.

The setup chat works with no model at all. When one is available it is used
for exactly two jobs: reading the personality answers into a style
(services/setup_profile.py) and reading public pages during the opt-in
research (services/setup_research.py). Whoever does that reading is named to
the user before they answer anything personal, and the name travels with every
result so the interface can badge it.

The rule for choosing, in order:

1. A model on this computer, if one is installed. Nothing leaves the machine.
2. A cloud model, ONLY if the user chose a cloud routing mode on the consent
   screen AND then confirmed, in the chat, that this model may read their
   onboarding answers. Choosing the cloud for everyday chat is not consent to
   send a personality questionnaire to it.
3. Otherwise no model: deterministic rules.

A reader is a small dict, safe to persist and to show:
    {"kind": "local" | "cloud" | "rules", "model": str, "provider": str}
"""
from __future__ import annotations

import logging

_log = logging.getLogger("friday.setup_reader")

RULES = {"kind": "rules", "model": "", "provider": ""}

#: Routing modes from the consent screen under which a cloud reader may even
#: be offered. `local_only` never offers one.
CLOUD_MODES = ("cloud_only", "local_preferred")


def local_model() -> str | None:
    """The installed local reasoning model, or None when nothing is installed."""
    try:
        from agent_friday.services import local_seats
        if not local_seats.installed():
            return None
        return local_seats.resolve("brain") or None
    except Exception as e:
        _log.debug("no local model: %s", e)
        return None


def cloud_model() -> dict | None:
    """{model, provider} for the cloud reader, or None when no key is usable."""
    try:
        from agent_friday.services import credential_store as cs
        import agent_friday.core as core
        if not (getattr(core, "ANTHROPIC_API_KEY", "")
                or cs.provider_key_status("anthropic") == "connected"):
            return None
    except Exception:
        return None
    try:
        from agent_friday.services.research.harness import _anthropic_model_name
        name = _anthropic_model_name()
    except Exception:
        name = "Claude"
    return {"model": name, "provider": "Anthropic"}


def options(routing_mode: str) -> dict:
    """What the chat can offer: {local, cloud} where either may be None."""
    loc = local_model()
    cloud = cloud_model() if (routing_mode in CLOUD_MODES and not loc) else None
    return {"local": loc, "cloud": cloud}


def choose(routing_mode: str, *, cloud_confirmed: bool) -> dict:
    """The reader to use now, applying the rule in the module docstring."""
    opts = options(routing_mode)
    if opts["local"]:
        return {"kind": "local", "model": opts["local"], "provider": "this computer"}
    if opts["cloud"] and cloud_confirmed:
        return {"kind": "cloud", "model": opts["cloud"]["model"],
                "provider": opts["cloud"]["provider"]}
    return dict(RULES)


def call_json(reader: dict, system: str, user: str, *,
              max_tokens: int = 1536) -> dict | None:
    """One structured call through `reader`. None on any failure or for rules.

    No tool registry is passed to either path: the model can only return text.
    That is what makes it safe to hand it untrusted page text.
    """
    kind = (reader or {}).get("kind")
    model = (reader or {}).get("model") or ""
    if kind == "local" and model:
        try:
            from agent_friday.services import local_call
            return local_call.call_json(system, user, model, max_tokens=max_tokens)
        except Exception as e:
            _log.warning("local reader failed: %s", e)
            return None
    if kind == "cloud":
        try:
            from agent_friday.services import local_call
            from agent_friday.services.model_router import _call_claude
            raw = _call_claude([{"role": "user", "content": user}],
                               system=system, max_tokens=max_tokens)
            return local_call.extract_json(raw or "")
        except Exception as e:
            _log.warning("cloud reader failed: %s", e)
            return None
    return None


def badge(reader: dict) -> str:
    """The words the interface shows next to anything this reader produced."""
    kind = (reader or {}).get("kind")
    if kind == "local":
        return "%s (on this computer)" % reader.get("model")
    if kind == "cloud":
        return "%s (%s, cloud)" % (reader.get("model"), reader.get("provider"))
    return "rules on this computer (no model)"
