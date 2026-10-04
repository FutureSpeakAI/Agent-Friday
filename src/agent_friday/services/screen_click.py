"""Did a person click this in Friday's own page?

A card marked screen-only is approved by a click on the screen, never by a word
or by a program that merely names itself the owner. A browser stamps every
request with headers a page's script cannot set or change (`Sec-Fetch-Site`),
and sends the page's own `Origin` on a POST. A request with both, matching this
server, is a click in a page of this server; a bare `curl` from a prompt-injected
command does not carry them unless it forges them on purpose. This raises the bar
over a naive call; it does not claim to stop a program that forges browser headers.
"""
from __future__ import annotations

from urllib.parse import urlparse

SCREEN = "owner:ui"


def is_browser_click(request) -> bool:
    """True for a same-origin request from a browser page of this server."""
    if request.headers.get("Sec-Fetch-Site") != "same-origin":
        return False
    origin = request.headers.get("Origin")
    return bool(origin) and urlparse(origin).netloc == request.host


def is_cross_site(request) -> bool:
    """True when a browser says another site caused this request."""
    site = request.headers.get("Sec-Fetch-Site")
    return bool(site) and site not in ("same-origin", "none")


def screen_session(request) -> bool:
    """A browser click of this server's own page that also carries the page's rotating
    session token: the only request the server treats as the owner's click on the screen."""
    try:
        import agent_friday.core as core
        return is_browser_click(request) and core._api_token_valid(request.headers.get("X-Friday-Token"))
    except Exception:  # noqa: BLE001 - a check that cannot run is not a click
        return False


def decided_by(request, claimed: str = "owner") -> str:
    """The identity a decision carries. The screen's identity is never taken from the body:
    it is given only to a verified click (same-origin page, the page's session token) and a
    body that claims it without one is the plain owner. Other channel labels (voice, text
    message, caption) are audit labels; no screen-only card accepts them."""
    claimed = str(claimed or "owner")
    if claimed == SCREEN:
        return SCREEN if screen_session(request) else "owner"
    return claimed
