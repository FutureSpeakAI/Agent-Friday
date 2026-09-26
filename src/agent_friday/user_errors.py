"""Exceptions whose message is written for the person using Friday.

An HTTP route never puts an arbitrary exception's text into a response: that
text can carry file paths, library internals or fragments of a traceback.
`routes/_errors.api_error` logs such an exception locally and answers with a
plain description of what failed plus an error id.

`UserFacingError` is the one narrow exception to that rule. Code raises it
deliberately, with a message worded for the user ("That address is not a
Gmail message id", "The budget must be a positive number"), and the route may
show that message verbatim. The message lives on `user_message`, set by the
raiser; nothing copies it out of another exception.

`log_failure` and `error_text` are the non-HTTP half of the same rule, for
code that builds a result dict rather than a response (a service returning
{"ok": False, "error": ...} to a route): the dict gets `what` plus an error
id, the log gets the exception.

This module does not import Flask or the route layer, so services can use it.
"""
from __future__ import annotations

import logging
import re
import secrets

_log = logging.getLogger("friday.routes.errors")


class UserFacingError(Exception):
    """An error whose message is written for the user and safe to show.

    `status`, when the raiser sets it, is the HTTP status the route answers
    with; left as None, the route keeps the status it uses for the failure.

    `detail`, when given, is what `str(exc)` returns: the full account for
    the model and the log (a provider's own error text, say), while routes
    show only `user_message`.
    """

    status: int | None = None

    def __init__(self, user_message: str, status: int | None = None,
                 detail: str | None = None):
        super().__init__(user_message if detail is None else detail)
        self.user_message = str(user_message)
        if status is not None:
            self.status = status


class UserFacingValueError(UserFacingError, ValueError):
    """A `ValueError` with a user-worded message.

    Subclasses `ValueError` so existing `except ValueError` handlers and
    callers that test for it keep working.
    """


class UserFacingRuntimeError(UserFacingError, RuntimeError):
    """A `RuntimeError` with a user-worded message."""


class UserFacingPermissionError(UserFacingError, PermissionError):
    """A `PermissionError` with a user-worded message (a refusal, not a crash)."""


class UserFacingLookupError(UserFacingError, KeyError):
    """A missing-thing error with a user-worded message."""

    def __str__(self) -> str:  # KeyError would repr() the message
        return self.user_message


def new_error_id() -> str:
    """A short id that ties a response to its log entry (8 hex characters)."""
    return secrets.token_hex(4)


def _where() -> str:
    """"METHOD /path" of the current request, or "-" outside one."""
    try:
        from flask import has_request_context, request
        if has_request_context():
            return "%s %s" % (request.method, request.path)
    except Exception:
        pass
    return "-"


def log_failure(exc: BaseException, what: str) -> str:
    """Log `exc` with its traceback at ERROR, tagged with a new id; return the id."""
    error_id = new_error_id()
    _log.error("[error %s] %s: %s", error_id, _where(), what,
               exc_info=(type(exc), exc, exc.__traceback__))
    return error_id


def error_text(exc: BaseException, what: str) -> str:
    """The text a user sees for `exc`: its message if it is a UserFacingError,
    otherwise `what` plus an error id (and `exc` is logged under that id).

    For HTTP responses only. A result the model reads through an agent tool
    keeps the real text; use `exception_text` there."""
    if isinstance(exc, UserFacingError):
        return exc.user_message
    return "%s (error %s)" % (what, log_failure(exc, what))


class ExceptionText(str):
    """An exception's text, marked as such.

    A service result dict has two audiences: the model (through agent tools),
    which needs the real error to explain a failure, and the browser (through
    a route), which must not see internals. The service writes the real text
    as `exception_text(e)`; it compares, prints and serialises exactly like the
    plain string, so the model path is unchanged. At the HTTP boundary
    `routes._errors.public_result` recognises the mark and swaps the text for
    "<what> (error <id>)". Literal messages a service writes for the user are
    plain strings and pass through.
    """

    __slots__ = ()


def exception_text(exc: BaseException, template: str = "%s") -> ExceptionText:
    """`template % str(exc)`, marked as exception text for the HTTP boundary.

    `exception_text(e, "Gmail refused the message: %s")` gives the model the
    same sentence it always had; the browser gets "<what> (error <id>)".
    Formatting a marked value into another string drops the mark, so a
    service builds the whole sentence here rather than around the result."""
    return ExceptionText(template % (str(exc),))


def log_text(what: str, text: str) -> str:
    """Log an error that arrives as text (no traceback left), under a new id."""
    error_id = new_error_id()
    _log.error("[error %s] %s: %s: %s", error_id, _where(), what, text)
    return error_id


_PATHISH = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|/(?:home|Users|usr|var|tmp|opt|etc|mnt|srv|root)/)"
    r"[^\s'\"()<>,;]*")


def message_only(value, limit: int = 200) -> str:
    """The message of an exception (or text) that is deliberately shown to the
    user or the model: one line, no traceback, no file paths, bounded length.

    For the few places that pass an error's words on on purpose (a governance
    explanation, a data source the model must say is unavailable) rather than
    hiding them behind an error id.
    """
    text = str(value if not isinstance(value, BaseException) else
               (value.args[0] if value.args and isinstance(value.args[0], str)
                else value))
    line = next((ln.strip() for ln in text.splitlines()
                 if ln.strip() and not ln.strip().startswith(("Traceback", "File \""))), "")
    line = _PATHISH.sub("<path>", line)
    if len(line) > limit:
        line = line[:limit - 1].rstrip() + "…"
    return line or "unknown error"
