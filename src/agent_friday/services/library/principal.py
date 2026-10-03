"""Whose Library a request is about.

The principal comes from the request context, never from an argument: a
caller cannot ask for another person's Library by naming it. Friday has one
owner today, so the only Library principal is "owner". The observer principal
(a read-only token) gets no Library at all, only status counts. When household
principals exist, this is the one place that maps a request to a principal.
"""
from __future__ import annotations

from agent_friday.services.library.store import OWNER

OBSERVER = "observer"


def current() -> str | None:
    """The Library principal for this call, or None when the caller may not
    read a Library (an observer). Outside a web request (the agent loop, a
    scheduled task) the caller is the owner's own process."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            who = getattr(g, "friday_principal", "user")
            return None if who == OBSERVER else OWNER
    except Exception:
        pass
    return OWNER
