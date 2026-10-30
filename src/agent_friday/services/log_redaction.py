"""The one logging filter every Friday log line passes: secrets out, correlation id in.

BD-08 / NS-26.5-5, NS-26.17-1, NS-23.3-3: logs must not contain credentials. Friday
already had text redactors (services/secret_shapes for credential shapes,
services/secret_patterns for key blocks and vendor tokens, including encoded copies)
but nothing applied them to logging, so an exception that quoted an API key, a
refresh token in a URL or an `Authorization: Bearer ...` header went to friday.log
as it was.

`RedactingFilter` is attached to every handler of the "friday" logger tree
(core._setup_friday_logging: friday.log and the stderr mirror, or the journald
stream on the kiosk), so it sees every record those handlers write, including
records that propagate up from child loggers (a logger-level filter would not).
For each record it:

  * renders the message with its arguments and replaces every key-shaped value
    (`redact`), keeping the field name: `api_key=[redacted]`;
  * does the same to the formatted traceback, if any;
  * prefixes the request's correlation id ("[req-...]", services/correlation)
    and sets `record.correlation_id`.

It fails closed: a line that cannot be checked is replaced by a fixed notice, never
written as it was. Lines are checked up to 20,000 characters; the rest of a longer
line is cut, because a log line that long is itself the problem.
"""
from __future__ import annotations

import logging
import re

#: Fixed words for a line that could not be checked.
WITHHELD = "[log line withheld: it could not be checked for secrets]"
_MAX = 20000

# A secret named by its field, in logs (where `password=hunter2` is never the owner's own
# document): the name is kept and the value replaced.
_NAMES = (r"api[_-]?key|x-api-key|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|"
          r"secret[_-]?key|private[_-]?key|password|passwd|passphrase|auth[_-]?token|"
          r"session[_-]?(?:id|token|key)|auth(?:orization)?[_-]?code|token|secret")
_NAMED = re.compile(
    r"(?i)\b(" + _NAMES + r")\b(\"?'?\s{0,3}[:=]\s{0,3}\"?'?)([^\s\"',;&}\]\[]{6,})")
# An OAuth code in a query string (?code=... / &code=...): long, no spaces.
_QUERY_CODE = re.compile(r"(?i)([?&]code=)([^&\s#\"']{16,})")
# Cookie headers: every value after "=" goes, the cookie names stay.
_COOKIE = re.compile(r"(?i)\b(set-cookie|cookie)(\s{0,3}:\s{0,3})([^\r\n]{1,4000})")
# A quoted value may hold spaces ("password": "correct horse battery staple"): the whole quote goes.
_NAMED_QUOTED = re.compile(
    r"(?i)([\"'](?:" + _NAMES + r")[\"']\s{0,3}:\s{0,3})([\"'])([^\"'\r\n]{1,2000})\2")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/-]{12,}=*)")
_URL_CREDS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,15}://)([^/\s:@]{1,64}):([^/\s@]{1,256})@")


def redact(text) -> str:
    """`text` with every key-shaped value replaced. Never raises: on any failure the whole
    text is withheld."""
    try:
        s = str(text)
        if len(s) > _MAX:
            s = s[:_MAX] + " ...[cut: the rest of a very long line]"
        s = _URL_CREDS.sub(lambda m: m.group(1) + m.group(2) + ":[redacted]@", s)
        s = _BEARER.sub(lambda m: m.group(1) + " [redacted]", s)
        s = _COOKIE.sub(lambda m: m.group(1) + m.group(2)
                        + re.sub(r"=([^;\s]{1,4000})", "=[redacted]", m.group(3)), s)
        s = _QUERY_CODE.sub(lambda m: m.group(1) + "[redacted]", s)
        s = _NAMED_QUOTED.sub(lambda m: m.group(1) + m.group(2) + "[redacted]" + m.group(2), s)
        s = _NAMED.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", s)
        from agent_friday.services import secret_shapes, secret_patterns
        s = secret_shapes.redact(s)
        s = secret_patterns.redact(s)
        # The values Friday actually holds, whatever their shape (services/secret_values).
        from agent_friday.services import secret_values
        s = secret_values.redact_known(s)
        return s
    except Exception:  # noqa: BLE001 - a line that cannot be checked is not written
        return WITHHELD


class RedactingFilter(logging.Filter):
    """Redact each record in place and give it this request's correlation id (as the
    attribute `correlation_id`, and as a "[req-...]" prefix on the message, so no format
    string has to know about it). Always lets the record through."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_friday_redacted", False):
            return True                       # a second handler: already done
        try:
            self._redact(record)
        except Exception:  # noqa: BLE001 - never raise into the code that logged; never leak
            record.msg, record.args = WITHHELD, None
            record.exc_info, record.exc_text, record.stack_info = None, None, None
        record._friday_redacted = True
        return True

    @staticmethod
    def _redact(record: logging.LogRecord) -> None:
        try:
            from agent_friday.services import correlation
            cid = correlation.current()
        except Exception:  # noqa: BLE001
            cid = ""
        record.correlation_id = cid or "-"
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string or object: check the raw parts
            try:
                msg = "%s %s" % (record.msg, record.args)
            except Exception:  # noqa: BLE001
                msg = WITHHELD
        msg = redact(msg)
        record.msg = ("[%s] %s" % (cid, msg)) if cid else msg
        record.args = None
        if record.exc_info:
            try:
                text = logging.Formatter().formatException(record.exc_info)
            except Exception:  # noqa: BLE001
                text = ""
            record.exc_text = redact(text) if text else None
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)


_FILTER = RedactingFilter()


class RedactingStream:
    """A text stream (the server's stdout/stderr) that redacts what is written to it. The tray
    keeps the server's console in server_stderr.log, so a print() or traceback that quoted a key
    would be on disk as it was; every write passes `redact` first."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, s):
        try:
            text = redact(s) if s and not s.isspace() else s
        except Exception:  # noqa: BLE001
            text = WITHHELD + "\n"
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_streams() -> None:
    """Redact the server's stdout and stderr (idempotent). Called once by the server at start;
    tests keep their own captured streams."""
    import sys
    for name in ("stdout", "stderr"):
        cur = getattr(sys, name, None)
        if cur is not None and not isinstance(cur, RedactingStream):
            setattr(sys, name, RedactingStream(cur))


def install(*loggers) -> None:
    """Attach the one filter to every handler of the given loggers (default: the "friday"
    logger, whose handlers write friday.log and the stderr mirror). Idempotent."""
    for lg in loggers or (logging.getLogger("friday"),):
        for h in list(lg.handlers):
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(_FILTER)
