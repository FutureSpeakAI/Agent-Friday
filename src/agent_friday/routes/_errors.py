"""How an HTTP route reports a failure: say WHAT failed, never HOW.

A route that catches an exception must not put the exception's text into the
response. That text can carry file paths, library internals, provider error
bodies or traceback fragments, and the response can be rendered anywhere the
UI shows it. Friday still fails visibly: the person learns which operation
failed and gets a short error id, and the full exception with its traceback
goes to the local log under the same id, so the two can be matched.

    try:
        ...
    except Exception as e:
        return api_error(e, "Couldn't save the contact")

answers 500 with {"status": "error", "message": "Couldn't save the contact
(error 1a2b3c4d)", "error_id": "1a2b3c4d"}.

The one exception is `agent_friday.user_errors.UserFacingError`: code raises
it on purpose, with a message written for the user, and that message is shown
as is. Nothing else is: a plain ValueError or RuntimeError goes through the
error-id path whatever it says.

Response shapes follow the route's existing envelope so the UI keeps reading
the same key:
    shape="status" -> {"status": "error", "message": ...}   (the default)
    shape="ok"     -> {"ok": False, "error": ...}
    shape="bare"   -> {"error": ...}
`key=` moves the message to another field ({"status": "error", "error": ...},
{"ok": False, "reason": ...}); any other keyword is added to the body as is
(`results=[]`, `accounts=[]`).

This module's name starts with "_" so blueprint auto-discovery skips it.
"""
from __future__ import annotations

import html

from flask import jsonify

from agent_friday.user_errors import (  # noqa: F401  (re-exported for routes)
    UserFacingError,
    UserFacingLookupError,
    UserFacingPermissionError,
    UserFacingValueError,
    error_text,
    log_failure,
    new_error_id,
)

_ENVELOPES = {
    "status": ({"status": "error"}, "message"),
    "ok": ({"ok": False}, "error"),
    "bare": ({}, "error"),
}


def api_error(exc: BaseException, what: str, status: int = 500, *,
              shape: str = "status", key: str | None = None, **extra):
    """Log `exc` and return `(Response, status)` that says what failed.

    `what` is a short, plain description of the operation ("Couldn't load the
    news feed"). The response never contains the exception's own text unless
    it is a UserFacingError, whose `status`, when set, replaces `status`.
    """
    envelope, default_key = _ENVELOPES[shape]
    body = dict(envelope)
    if isinstance(exc, UserFacingError):
        body[key or default_key] = exc.user_message
        if exc.status is not None:
            status = exc.status
    else:
        error_id = log_failure(exc, what)
        body[key or default_key] = "%s (error %s)" % (what, error_id)
        body["error_id"] = error_id
    body.update(extra)
    return jsonify(body), status


def html_error(exc: BaseException, what: str, status: int = 500):
    """The HTML-page form of `api_error`, for routes a browser lands on
    directly (OAuth callbacks). Returns `(html, status)`."""
    if isinstance(exc, UserFacingError):
        detail = exc.user_message
    else:
        detail = "Error %s. The details are in Friday's log." % log_failure(exc, what)
    return "<h2>%s</h2><p>%s</p>" % (html.escape(what), html.escape(detail)), status
