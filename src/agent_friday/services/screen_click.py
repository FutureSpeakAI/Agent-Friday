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


def decided_by(request, claimed: str) -> str:
    """The identity a decision may carry: the screen's only for a browser click."""
    claimed = str(claimed or "owner")
    if claimed == SCREEN and not is_browser_click(request):
        return "owner"
    return claimed
