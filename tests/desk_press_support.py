"""Request headers for tests that stand in for a press on Friday's page.

`page_headers()` is what Friday's page sends from ANY browser that has the page:
same-origin browser metadata and the rotating session token. `desk_headers()`
adds this launch's desk key, which only a browser on the PC itself can fetch
(services/desk_press). A press counts only with the second.

On a tree without services/desk_press (the old code a red run is aimed at) the
desk key is a placeholder the old server ignores, so a test that needs a real
press still runs and the tests that must refuse fail by assertion, not import.
"""
from __future__ import annotations

from contextlib import contextmanager

ORIGIN = "http://localhost"


def desk_key() -> str:
    try:
        from agent_friday.services import desk_press
    except ImportError:
        return "placeholder-desk-key-old-tree"
    return desk_press._KEY


def page_headers(origin: str = ORIGIN) -> dict:
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": origin,
            "X-Friday-Token": core._current_api_token()}


def desk_headers(origin: str = ORIGIN) -> dict:
    h = page_headers(origin)
    h["X-Friday-Desk"] = desk_key()
    return h


@contextmanager
def pressed():
    """Stand in for an approve route's press, for a test that decides a card in-process
    with decided_by="owner:ui" ("the click"). Not a bypass: it opens a real request from
    this PC's loopback carrying the page's metadata, its token and the desk key, and has
    screen_click.decided_by verify it, which is what grants the label on the real route."""
    import agent_friday.core as core
    from flask import request
    from agent_friday.services import screen_click
    with core.app.test_request_context("/api/approvals/test/decide", method="POST",
                                       headers=desk_headers(), base_url=ORIGIN,
                                       environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert screen_click.decided_by(request, "owner:ui") == "owner:ui", "the stand-in press did not verify"
        yield


@contextmanager
def voice_call_on_this_desk(owner_local: bool = True):
    """Stand in for a voice call's tool call: a spoken decision is made inside one, and it
    approves only when that call is the owner's own page on this PC (the desk key on the
    voice socket; services/approvals.voice_on_this_desk)."""
    from agent_friday.services import agent
    tok = agent._CURRENT_TOOL_CONTEXT.set({"surface": "voice-local", "is_voice": True,
                                           "owner_local": bool(owner_local)})
    try:
        yield
    finally:
        agent._CURRENT_TOOL_CONTEXT.reset(tok)
