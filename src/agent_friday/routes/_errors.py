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

A service result dict that a route returns to the browser goes through
`public_result(result, what)`. The service keeps the real error text for the
model (agent tools read the same dict) and marks it with `exception_text(e)`;
`public_result` logs each marked value under an error id and replaces it with
"<what> (error <id>)". Literal messages a service writes for the user are
plain strings and pass through unchanged.

This module's name starts with "_" so blueprint auto-discovery skips it.
"""
from __future__ import annotations

import html

from flask import jsonify

from agent_friday.user_errors import (  # noqa: F401  (re-exported for routes)
    ExceptionText,
    UserFacingError,
    UserFacingLookupError,
    UserFacingPermissionError,
    UserFacingValueError,
    error_text,
    exception_text,
    log_failure,
    log_text,
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


def public_result(result, what: str):
    """The browser-safe form of a service result.

    Top-level values marked with `exception_text` become "<what> (error <id>)"
    and are logged under that id, and "error_id" is added. A result with no
    marked value, or one that is not a dict, is returned unchanged. The
    service's own dict is never mutated: the model path keeps the real text.
    """
    if not isinstance(result, dict):
        return result
    marked = [k for k, v in result.items() if isinstance(v, ExceptionText)]
    if not marked:
        return result
    error_id = log_text(what, "; ".join("%s=%s" % (k, result[k]) for k in marked))
    safe = {k: v for k, v in result.items() if k not in marked}
    for k in marked:
        safe[k] = "%s (error %s)" % (what, error_id)
    safe["error_id"] = error_id
    return safe
