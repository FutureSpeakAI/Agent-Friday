""""Friday, add a device": the owner's words at this PC open the pairing window.

Opening pairing lets a new phone, tablet or watch ask for every right, so it is only done for the owner's own
typed words on this PC's own page (a loopback request with the page's token and no forwarding evidence); the
same words from a tunnel, a forward or another machine get a plain reply that says where to go instead. The
device still has to show six matching digits and the owner still presses Approve on this screen.
"""
from __future__ import annotations

import re
from typing import Optional

WINDOW_S = 300

_PHRASE = re.compile(
    r"^(?:(?:hey|ok|okay) )?(?:friday[,.!]? )?(?:please )?(?:add|pair|connect|set up)"
    r" (?:a |an |another |a new |my )?(?:new )?(?:device|phone|tablet|watch|tv)(?: to friday)?[.!?]*\Z",
    re.IGNORECASE)

OPENED = ("Pairing is open for %d minutes. On the new phone, tablet or watch, open Friday and start pairing; "
          "then press Approve here only if the six digits on this screen match the ones on the device.")
NOT_HERE = ("I only open pairing for you at this PC, so I did not. Say or type \"Friday, add a device\" on the PC "
            "itself, or open Add a device at /pairing on the PC.")


def is_add_device(text) -> bool:
    return isinstance(text, str) and len(text) < 120 and bool(_PHRASE.match(" ".join(text.split())))


def handle(text, request) -> Optional[str]:
    """The reply to give, or None when the words are not this command."""
    if not is_add_device(text):
        return None
    from agent_friday import core
    from agent_friday.services import device_pairing, screen_click
    if not (screen_click.page_session(request) and core._is_local_request()):
        return NOT_HERE
    device_pairing.open_pairing_window(WINDOW_S, by="owner:typed")
    return OPENED % (WINDOW_S // 60)
