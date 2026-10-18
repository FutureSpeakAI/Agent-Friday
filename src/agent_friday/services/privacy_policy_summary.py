"""The privacy policy in force, in a few plain lines, for the model's own context.

A model that has not read the rules guesses at them. Refused a call by a privacy gate, it argued the
refusal "may be intended", when the settings in force said the cloud was allowed that data. This block
puts the rules where the model reads them on every turn, text and voice, so it can tell a refusal that
follows the settings from one that contradicts them, and say which.

ONE SOURCE OF TRUTH. Every line is derived from the live settings, read through `core._load_settings`
and resolved by the same resolvers the gates use (`privacy.cloud_consent.resolve`,
`privacy.vault_policy.resolve`), on every call. A change in Settings shows on the next turn; nothing
is cached here and nothing restates a setting from memory.

WHAT IT SAYS. The effective behaviour first (what a cloud seat may receive), then the standing rules:

  * public web and news query text is not private because of its topic; only the never-send floor and
    personal identifiers apply to it;
  * the owner's explicit choice of cloud only (Settings > Models, or the terminal setup's routing
    question) is consent to use cloud models for all but the never-send floor, the same as
    the "Cloud for everything" answer; the factory default is not, and neither is the cautious answer. The block states what the consent
    resolver decides (`cloud_consent.resolve`), the same answer the egress gate acts on, never a
    second reading of the mode;
  * the owner decides once informed: no lectures, no asking again ("Inform once; owner decides");
  * a refusal that contradicts these settings is a bug: say so plainly and offer the local model.

It never lists what is on the never-send list (only how many entries it has) and names no person.
It is written to classify as public text (the vault switch is "local-only records", because
the word for the vault itself tiers as sensitive), so no gate withholds it from a cloud seat. It stays
under MAX_TOKENS.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

HEADER = "== PRIVACY POLICY =="

#: Ceiling on the block, in the four-characters-a-token estimate the prompt budgets use.
MAX_TOKENS = 120

_MODES = {
    "cloud_only": "cloud only",
    "cloud_preferred": "cloud preferred",
    "local_preferred": "local preferred",
    "local_only": "local only",
}

_CHOICES = {
    "cloud_unrestricted": "unrestricted",
    "cloud_guarded": "guarded",
    "local_private": "local private",
}


def _never_send_entries() -> int:
    """How many items the owner's never-send list holds; -1 when it cannot be read."""
    try:
        from agent_friday.services.judgment_gate import never_send_tokens
        return len(never_send_tokens())
    except Exception:
        return -1


def _caps_on(cost_budget) -> bool:
    b = cost_budget if isinstance(cost_budget, dict) else {}

    def on(flag, amount):
        try:
            return bool(b.get(flag)) and float(b.get(amount) or 0) > 0
        except (TypeError, ValueError):
            return False
    return any(on(f, a) for f, a in (("daily_enabled", "daily"), ("monthly_enabled", "monthly"),
                                     ("hard_stop_daily_enabled", "hard_stop_daily"),
                                     ("hard_stop_monthly_enabled", "hard_stop_monthly")))


def summary(settings=None) -> str:
    """The block, from `settings` (the live settings when None). Never raises."""
    try:
        return _summary(settings)
    except Exception as e:                                  # pragma: no cover - defensive
        _log.warning("privacy policy summary unavailable: %s", e)
        return (HEADER + "\nThe settings could not be read. A refusal you cannot explain is reported "
                "plainly, never defended as intended.")


def _summary(settings) -> str:
    if settings is None:
        from agent_friday.core import _load_settings
        settings = _load_settings() or {}
    mr = settings.get("model_routing") if isinstance(settings, dict) else None
    mr = mr if isinstance(mr, dict) else {}
    mode = str(mr.get("mode") or "cloud_only").strip().lower()
    mode = "local_preferred" if mode == "smart" else mode

    from agent_friday.privacy import cloud_consent, vault_policy
    consent = cloud_consent.resolve(mr)
    vault_local = vault_policy.resolve(mr, announce=False).local_only

    if consent.answered and consent.choice:
        said = "Consent: %s" % _CHOICES.get(consent.choice, consent.choice)
        if consent.source == cloud_consent.SOURCE_CLOUD_ONLY_CHOICE:
            said += ", by choosing cloud only"
        if consent.at:
            said += " (%s)" % str(consent.at)[:10]
    else:
        said = "Consent: unanswered"
    n = _never_send_entries()
    floor = ("%d item%s" % (n, "" if n == 1 else "s") if n > 0
             else "empty" if n == 0 else "unreadable")
    facts = "Mode: %s. %s. Local-only records: %s. Never-send list: %s. Spend caps: %s." % (
        _MODES.get(mode, mode.replace("_", " ")), said, "on" if vault_local else "off", floor,
        "on" if _caps_on(settings.get("cost_budget")) else "off")

    vault_clause = " and private records" if vault_local else ""
    if mode == "local_only":
        effect = "No cloud model is used; all work stays on this computer."
    elif consent.unrestricted:
        effect = "Cloud models may use all data except the never-send floor%s." % vault_clause
    elif mode in ("cloud_only", "cloud_preferred"):
        # The starting setting, or the cautious answer: not consent. The gate holds private details.
        effect = ("Private details stay on this computer until the owner says Send on the card shown; "
                  "cloud models get the rest.")
    else:
        effect = "Private data goes to the local model; cloud models get the rest."

    rules = ("Rules: web/news queries are not private by topic (only the floor and personal "
             "identifiers apply). Inform once; owner decides. A refusal against these "
             "settings is a bug: say so plainly, offer the local model, never argue it may be intended.")
    return "\n".join((HEADER, facts, effect, rules))
