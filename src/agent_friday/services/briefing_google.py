"""What a briefing may say about Google: read from the live account state, never from a fetch's failure text.

The briefing's calendar and email sections used to print whatever the legacy single-account fetch returned when it
found no credentials ("Google not connected. Visit ..."). That text is true only when no account is connected; with
accounts working it appeared anyway (the legacy path holds while no route is chosen, or its first account needs a new
sign-in while another works), and the model repeated it in every briefing. ``state`` asks the live account registry;
``recover`` turns a failed legacy fetch into the data from the connected accounts or into one honest sentence for the
state they are in. Guarded by tests/unit/test_briefing_google_state.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

CONNECTED, NOT_CONNECTED, NEEDS_SIGN_IN, HELD = "connected", "not_connected", "needs_sign_in", "held"

_LABEL = {"calendar": "Google Calendar", "gmail": "Gmail"}


def state(service: str) -> str:
    """The live state of Google for ``service`` (``calendar`` or ``gmail``): connected when an account that has it
    switched on is usable now; needs_sign_in when accounts exist but none is usable; held when no route is chosen yet;
    otherwise not_connected."""
    try:
        from agent_friday.services.cloud_intent import outbound_held
        if outbound_held("connector", "google"):
            return HELD
    except Exception:
        pass
    try:
        from agent_friday.services import capability_state as cap
        google = cap.google_accounts_state()
        if google.state == cap.WORKING or (google.state == cap.PRESENT_FAILING and google.health == "degraded"):
            return CONNECTED                    # at least one account works
        if google.state in (cap.PRESENT_FAILING, cap.PRESENT_UNVERIFIED):
            return NEEDS_SIGN_IN
    except Exception:
        pass
    return NOT_CONNECTED


def live_line() -> str:
    """The line the briefing prompt carries about Google, read now: the capability state the system prompt's own
    CAPABILITY STATE block uses, so a briefing is told what is true when it is generated and an earlier briefing's
    "not connected" is not repeated."""
    calendar, gmail = state("calendar"), state("gmail")
    words = {CONNECTED: "connected and working", NEEDS_SIGN_IN: "needs the owner to sign in again",
             HELD: "not read until a route is chosen", NOT_CONNECTED: "not connected"}
    return ("Connected services, read live now (this outranks anything an earlier briefing or memory says): "
            "Google Calendar %s; Gmail %s." % (words[calendar], words[gmail]))


def sentence(service: str, current: str) -> str:
    """The one honest sentence for a state in which the data could not be read."""
    name = _LABEL.get(service, "Google")
    if current == CONNECTED:
        return "%s is connected but could not be read just now." % name
    if current == NEEDS_SIGN_IN:
        return "%s needs you to sign in again." % name
    if current == HELD:
        return "%s is not read until you choose where Friday's answers may be processed." % name
    return "%s is not connected." % name


def recover(service: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """After the legacy fetch failed: ``(items, "")`` from the connected accounts when they answer (``[]`` when
    they were read without error and hold nothing), else ``(None, sentence)`` for the live state. ``items`` use the
    briefing builders' own keys."""
    current = state(service)
    if current == CONNECTED:
        try:
            from agent_friday.services import google_accounts
            if service == "calendar":
                result = google_accounts.merged_calendar(2)
                items = result.get("events") or []
            else:
                result = google_accounts.merged_gmail(12)
                items = result.get("messages") or []
            if items or (result.get("accounts") and not result.get("errors")):
                return items, ""
        except Exception:
            pass
    return None, sentence(service, current)
