"""One correlation id per request, carried into every record the request writes.

BD-08 / NS-26.17-2: "every request carries one correlation id across its ledgers".
A request to Friday's API gets one id when it arrives (core's first before_request):
the caller's own `X-Request-Id` when it is a plain token, otherwise a new random one.
It is answered back as `X-Friday-Request-Id`, appears on every friday.log line the
request writes (services/log_redaction adds it to each record), and is signed into
the action checkpoint's decision receipts (governance/action_gate._receipt). Other
ledgers read it with `current()`.

A thread or background task the request starts does not inherit it by itself
(ContextVars do not cross threads): code that hands work to a thread may pass
`current()` along and `bind()` it there.

The id is a label, never a secret and never derived from anything private.
"""
from __future__ import annotations

import contextlib
import contextvars
import re
import uuid

#: The request header a caller may supply, and the one Friday answers with.
REQUEST_HEADER = "X-Request-Id"
RESPONSE_HEADER = "X-Friday-Request-Id"

_CURRENT: contextvars.ContextVar = contextvars.ContextVar("friday_correlation_id", default="")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,63}")


def new_id() -> str:
    return "req-" + uuid.uuid4().hex[:16]


def accept(supplied) -> str:
    """The caller's id when it is a plain token (8-64 letters, digits, . _ : -), marked as the
    caller's ("c-" + it, so it can never pass for one Friday made), else a new one. Anything
    else (spaces, newlines, quotes, a very long value) is never echoed into a log."""
    s = str(supplied or "").strip()
    if not _TOKEN.fullmatch(s):
        return new_id()
    return s if s.startswith("c-") else "c-" + s


def current() -> str:
    """This request's correlation id, or "" outside one."""
    return _CURRENT.get()


def begin(supplied=None) -> str:
    """Start a request's id (the caller's, when acceptable). Returns it."""
    cid = accept(supplied)
    _CURRENT.set(cid)
    return cid


@contextlib.contextmanager
def bind(cid):
    """Carry this request's id (from `current()`) into other work, such as a thread the
    request started. An id that is not a plain token gets a new one instead."""
    cid = str(cid or "").strip()
    token = _CURRENT.set(cid if _TOKEN.fullmatch(cid) else new_id())
    try:
        yield _CURRENT.get()
    finally:
        _CURRENT.reset(token)


def clear() -> None:
    _CURRENT.set("")
