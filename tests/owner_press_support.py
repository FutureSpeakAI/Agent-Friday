"""The headers Friday's own page sends on a press, for tests of routes that take only the owner's press.

Same-origin browser metadata and the page's session token and, where this tree has
the desk-press check (services/desk_press), this launch's desk key.
"""
from __future__ import annotations


def press() -> dict:
    from agent_friday import core
    h = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
         "X-Friday-Token": core._current_api_token()}
    try:
        from agent_friday.services import desk_press
        h["X-Friday-Desk"] = desk_press._KEY
    except ImportError:
        pass
    return h


#: Requests that are not a press: a program on this PC, and browser metadata with no token.
NOT_A_PRESS = [
    {},
    {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
]
