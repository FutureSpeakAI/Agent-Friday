"""Did a change at a connected service actually happen? (BD-06; NS-34.11-2, NS-34.11-3)

A change that Google refused, could not be asked for, or did not confirm used to come back from
the handler as an ordinary return value, so the call was recorded as done: an outcome "ok", a
receipt with ok=True, and evidence a goal could count. Two ways in particular:

  * a revoked or expired grant during the call was reported as "Google did not accept it" or
    "Tasks write failed", and the account went on looking connected;
  * an empty reply from Google ({} where the new item should be) was reported as success with no
    id: "Created task" for a task nobody can find.

This module is the one place those cases are named. The Google write functions call `not_done`
(or `failed`, with the exception they caught) and return its sentence as their error; the tool
checkpoint (`agent._execute_tool`) collects what was noted during the call and records the call
as not done: outcome "error", a receipt with ok=False, and NOT_DONE at the front of the result so
nothing downstream counts it as evidence.

The kinds, each with the sentence the owner hears:

  revoked      the account's access was revoked or has expired: reconnect it (the account is
               marked needs_reauth, so Settings says so too)
  scope        the account has not allowed Friday to make this kind of change
  unreachable  Google could not be reached; nothing was sent
  busy         Google asked Friday to slow down (429): nothing was changed
  refused      Google refused this change (400/403/404/409/412)
  unconfirmed  Google did not confirm the change (an empty reply, a timeout or a 5xx after the
               request was sent): it may or may not have happened, so check before retrying
  failed       anything else

No exception text is ever put in a sentence: it can quote a token, an address or event text.
"""
from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import Any, Iterator, List, Optional

_log = logging.getLogger("friday.connector_outcome")

#: What a call's result starts with when a change it asked for did not happen. One of
#: agent._TOOL_ERROR_SENTINELS, so the call is never counted as done.
NOT_DONE = "[NOT DONE] "

KINDS = ("revoked", "scope", "unreachable", "busy", "refused", "unconfirmed", "failed")

_NOTES: ContextVar = ContextVar("friday_connector_not_done", default=None)

_SENTENCES = {
    "revoked": ("{svc} access for this account was revoked or has expired, so nothing was changed. "
                "Reconnect the account in Settings > Connections, then try again."),
    "scope": ("This account has not allowed Friday to make this change in {svc}, so nothing was "
              "changed. Allow it in Settings > Connections, then try again."),
    "unreachable": ("Friday could not reach Google, so nothing was changed. Try again when the "
                    "connection is back."),
    "busy": ("Google asked Friday to slow down and did not make the change. Nothing was changed; "
             "try again in a few minutes."),
    "refused": "Google refused this change in {svc}, so nothing was changed.",
    "unconfirmed": ("Google did not confirm the change in {svc}. It may or may not have happened: "
                    "check {svc} before trying again."),
    "failed": "The change in {svc} did not complete.",
}


def sentence(kind: str, service: str = "Google") -> str:
    return _SENTENCES.get(kind, _SENTENCES["failed"]).format(svc=service or "Google")


def _status(exc: BaseException) -> Optional[int]:
    for v in (getattr(getattr(exc, "resp", None), "status", None), getattr(exc, "status_code", None)):
        try:
            if v is not None:
                return int(v)
        except (TypeError, ValueError):
            continue
    return None


def classify(exc: BaseException) -> str:
    """The kind of a failure raised by a Google call (see the module docstring)."""
    name = type(exc).__name__
    text = str(exc).lower()
    if name == "GmailError":
        # services/gmail_api wraps the HTTP error; the original is its cause
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, BaseException):
            return classify(cause)
        return {"rate_limited": "busy", "not_found": "refused",
                "bad_request": "refused"}.get(getattr(exc, "kind", ""), "failed")
    if name == "RefreshError":
        try:
            from agent_friday.services import google_accounts as _ga
            if _ga._refresh_failure_is_transient(exc):
                return "unreachable"
        except Exception:
            pass
        return "revoked"
    status = _status(exc)
    if status is None and "invalid_grant" in text:
        # only an error with no HTTP status (an auth library's own): an HttpError's text
        # carries the request URL, which holds ids the model chose
        return "revoked"
    if status is not None:
        if status == 401:
            return "revoked"
        if status == 403:
            if ("insufficient" in text and "scope" in text) or "insufficientpermissions" in text:
                return "scope"
            if "ratelimitexceeded" in text or "userratelimitexceeded" in text:
                return "busy"
            return "refused"
        if status == 429:
            return "busy"
        if status >= 500:
            return "unconfirmed"
        if 400 <= status < 500:
            return "refused"
    if isinstance(exc, (TimeoutError, ConnectionResetError, BrokenPipeError,
                        ConnectionAbortedError)) or name in (
            "timeout", "ReadTimeout", "ReadTimeoutError", "RemoteDisconnected", "SSLError",
            "SSLEOFError", "IncompleteRead", "ProtocolError"):
        return "unconfirmed"          # the request may have reached Google
    if isinstance(exc, (ConnectionError, OSError)) or name in (
            "ServerNotFoundError", "TransportError", "ConnectionError", "NewConnectionError",
            "MaxRetryError", "gaierror"):
        return "unreachable"
    return "failed"


def not_done(kind: str, service: str = "Google", *, account_id: Optional[str] = None) -> str:
    """Note that a change asked for in this call did not happen (or was not confirmed), and return
    the sentence to give back. A revoked grant also marks the account needs_reauth."""
    if kind not in KINDS:
        kind = "failed"
    notes = _NOTES.get()
    if notes is not None:
        notes.append({"kind": kind, "service": service})
    if kind == "revoked" and account_id:
        try:
            from agent_friday.services import google_accounts as _ga
            _ga._LAST_PROBLEM[account_id] = "needs_reauth"
            _ga._mark_status(account_id, "needs_reauth")
        except Exception as e:  # noqa: BLE001 - the sentence still says reconnect
            _log.warning("could not mark the account for reconnecting: %s", type(e).__name__)
    return sentence(kind, service)


def failed(exc: BaseException, service: str = "Google", *, account_id: Optional[str] = None) -> str:
    """`not_done` for a caught exception: classified, logged by type only."""
    kind = classify(exc)
    _log.warning("%s change not done (%s): %s%s", service, kind, type(exc).__name__,
                 (" (HTTP %s)" % _status(exc)) if _status(exc) else "")
    return not_done(kind, service, account_id=account_id)


def no_credentials(account_id: Optional[str], service: str = "Google") -> str:
    """The sentence when an account produced no credentials before the call: reconnecting only
    when the grant is really gone; "could not reach Google" when the refresh never got an answer.
    Nothing was sent, so this is noted as not done too."""
    problem = None
    try:
        from agent_friday.services import google_accounts as _ga
        problem = _ga.last_problem(account_id) if account_id else None
    except Exception:
        pass
    kind = "unreachable" if problem == "unreachable" else "revoked"
    # credentials_for already marked the account when the grant is gone
    return not_done(kind, service)


def settle(result: Any) -> Any:
    """For a change tool's handler, on its way out: a result that reports an error (or ok False)
    is a change that did not happen, even when nothing below noted why (a missing account, a
    refusal before Google was asked). Returns `result` unchanged."""
    notes = _NOTES.get()
    if (notes is not None and not notes and isinstance(result, dict)
            and result.get("ok") is not True and (result.get("error") or result.get("ok") is False)):
        notes.append({"kind": "not_attempted", "service": ""})
    return result


def idem_key(tool: str, args: Any) -> Optional[str]:
    """The idempotency key of a create (F3, NS-34.11-2): the same tool with the same arguments in
    the same turn (or, with no turn, the same background task or run) is one create, however many
    times the model sends it: a retry after an unconfirmed reply never makes a second item. A new
    turn is a new request. None outside a turn or a task (an owner's own page)."""
    import hashlib
    import json
    scope = None
    try:
        from agent_friday.services import turn_plan
        scope = turn_plan.current_turn_id()
    except Exception:
        scope = None
    if not scope:
        try:
            from agent_friday.services import agent as _ag
            ctx = _ag._CURRENT_TOOL_CONTEXT.get() or {}
            scope = ctx.get("run_id") or ctx.get("task_id")
        except Exception:
            scope = None
    if not scope:
        return None
    blob = json.dumps({"t": tool, "s": str(scope), "a": args}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def event_id(key: str, *parts: Any) -> str:
    """A Google Calendar event id for one create (base32hex: 0-9 and a-v; 5 to 1024 characters).
    Google refuses a second insert with the same id (409), so the copy is never made."""
    import hashlib
    return "fr" + hashlib.sha256(("%s|%s" % (key, "|".join(str(p) for p in parts))).encode("utf-8")).hexdigest()[:30]


def confirmed(reply: Any, key: str = "id") -> bool:
    """Whether Google's reply to a create or update names the item it changed."""
    return isinstance(reply, dict) and bool(reply.get(key))


@contextlib.contextmanager
def call() -> Iterator[List[dict]]:
    """Collect what is noted during one tool call (agent._execute_tool)."""
    tok = _NOTES.set([])
    try:
        yield _NOTES.get()
    finally:
        _NOTES.reset(tok)


@contextlib.contextmanager
def per_item() -> Iterator[List[dict]]:
    """For a handler that changes several items: what one item's change noted, kept out of the
    call's own notes. The handler decides the call (all failed -> `not_done`)."""
    tok = _NOTES.set([])
    try:
        yield _NOTES.get()
    finally:
        _NOTES.reset(tok)
