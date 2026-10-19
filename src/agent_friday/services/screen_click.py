"""Did a person press this in Friday's own page, on this PC's own desktop?

A card marked screen-only is approved by a press on the screen, never by a word
or by a program that merely names itself the owner. A browser stamps every
request with headers a page's script cannot set or change (`Sec-Fetch-Site`),
and sends the page's own `Origin` on a POST. A request with both, matching this
server, is a click in a page of this server; a bare `curl` from a prompt-injected
command does not carry them unless it forges them on purpose.

That alone proved the page, not the place. Friday's page reached through a
tunnel, a reverse proxy, a `tailscale serve` forward or a LAN bind is the same
page with the same token, so a tap on a phone counted as a press at the desk.
A press now also has to prove it happened on this PC (services/desk_press):

  * it arrived over loopback with no forwarding evidence (core._is_local_request);
  * it named Friday by an address of this PC, not a tunnel or LAN name;
  * it carried this launch's desk key, which only a browser on this PC can fetch.

This raises the bar over a naive call and over every remote path; it does not
claim to stop a program on this PC that forges browser headers and reads memory.
"""
from __future__ import annotations

from urllib.parse import urlparse

SCREEN = "owner:ui"

#: Set on flask.g by `decided_by` when it grants the screen's identity to this
#: request. approvals.decide_with_outcome takes "owner:ui" only while this is
#: set AND the request still passes `screen_session`, so a label typed into
#: code, a tool's surface name or a chat turn can never stand in for a press.
_G_FLAG = "_friday_desk_press"

#: Decision labels only the server itself writes. A request body naming one is the plain owner,
#: so a receipt can never say "a standing grant" or "the system" decided what a page decided.
_SERVER_ONLY_PREFIXES = ("grant:", "system:", "friday:")


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


def page_session(request) -> bool:
    """A click in this server's own page carrying its session token: the page, wherever
    it is open. Not enough for a press (see `screen_session`)."""
    try:
        import agent_friday.core as core
        return is_browser_click(request) and core._api_token_valid(request.headers.get("X-Friday-Token"))
    except Exception:  # noqa: BLE001 - a check that cannot run is not a click
        return False


def screen_session(request) -> bool:
    """The only request the server treats as the owner's press on the screen: a click in
    this server's own page, with its session token, made on this PC's own desktop
    (loopback with no forwarding evidence, a local Host, this launch's desk key)."""
    try:
        from agent_friday.services import desk_press
        return page_session(request) and desk_press.on_this_desk(request)
    except Exception:  # noqa: BLE001 - a check that cannot run is not a press
        return False


def press_check(request) -> dict:
    """Each part of the press test by name (for a refusal and the receipt). Never raises."""
    out = {"page": False, "local": False, "host_local": False, "desk_key": False}
    try:
        out["page"] = page_session(request)
        from agent_friday.services import desk_press
        out.update(desk_press.check(request))
    except Exception:  # noqa: BLE001
        pass
    return out


def refusal_words(request) -> str:
    """Why a press from this request does not count, in words for the page."""
    c = press_check(request)
    if not c["page"]:
        return "That did not come from a press in Friday's own page."
    if not (c["local"] and c["host_local"]):
        return ("That press did not come from this PC's own screen (it came through another device, "
                "a tunnel or the network). Approve it at the computer Friday runs on.")
    if not c["desk_key"]:
        return ("Friday could not confirm that press was made on this PC's screen. Reload Friday's page "
                "on this computer and press again.")
    return ""


def granted_press() -> bool:
    """True while the current request was granted the screen's identity by `decided_by`
    and still passes `screen_session`. False outside a request."""
    try:
        from flask import g, has_request_context, request
        if not has_request_context():
            return False
        return bool(getattr(g, _G_FLAG, False)) and screen_session(request)
    except Exception:  # noqa: BLE001
        return False


def decided_by(request, claimed: str = "owner") -> str:
    """The identity a decision carries. The screen's identity is never taken from the body:
    it is given only to a verified press (same-origin page, the page's session token, on this
    PC's desktop) and a body that claims it without one is the plain owner. Other channel labels
    (voice, text message, caption) are audit labels; no screen-only card accepts them."""
    claimed = str(claimed or "owner")
    pressed = screen_session(request)
    if pressed:
        # This request is a verified press on this PC's screen: the approval it makes
        # may carry the screen's identity, and its receipt says desktop_press.
        try:
            from flask import g
            setattr(g, _G_FLAG, True)
        except Exception:  # noqa: BLE001 - without the mark the press is not taken
            pressed = False
    if claimed == SCREEN:
        return SCREEN if pressed else "owner"
    if claimed.startswith(_SERVER_ONLY_PREFIXES) or "voice" in claimed.lower():
        return "owner"          # a standing grant, the system, Friday herself or voice: never a request body's word
    try:
        from agent_friday.services import approvals
        if claimed in approvals.LAYA_CARD_APPROVERS:
            return "owner"      # the voice label is granted by the server's own voice path, never by a request body
        if any(claimed in allowed for allowed in approvals.SCREEN_ONLY_KINDS.values()):
            return "owner"      # any label a screen-only card accepts is granted by the server, never claimed
    except Exception:       # noqa: BLE001
        return "owner"
    return claimed
