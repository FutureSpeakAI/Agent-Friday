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
import secrets

_log = logging.getLogger("friday.routes.errors")


class UserFacingError(Exception):
    """An error whose message is written for the user and safe to show.

    `status`, when the raiser sets it, is the HTTP status the route answers
    with; left as None, the route keeps the status it uses for the failure.
    """

    status: int | None = None

    def __init__(self, user_message: str, status: int | None = None):
        super().__init__(user_message)
        self.user_message = str(user_message)
        if status is not None:
            self.status = status


class UserFacingValueError(UserFacingError, ValueError):
    """A `ValueError` with a user-worded message.

    Subclasses `ValueError` so existing `except ValueError` handlers and
    callers that test for it keep working.
    """


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
    otherwise `what` plus an error id (and `exc` is logged under that id)."""
    if isinstance(exc, UserFacingError):
        return exc.user_message
    return "%s (error %s)" % (what, log_failure(exc, what))
