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
_NAMED = re.compile(
    r"(?i)\b(api[_-]?key|x-api-key|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|"
    r"secret[_-]?key|private[_-]?key|password|passwd|passphrase|auth[_-]?token|session[_-]?token|"
    r"token|secret)\b(\"?'?\s{0,3}[:=]\s{0,3}\"?'?)([^\s\"',;&}\]]{6,})")
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
        s = _NAMED.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", s)
        from agent_friday.services import secret_shapes, secret_patterns
        s = secret_shapes.redact(s)
        s = secret_patterns.redact(s)
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
            from agent_friday.services import correlation
            cid = correlation.current()
        except Exception:  # noqa: BLE001
            cid = ""
        record.correlation_id = cid or "-"
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string: check the raw parts
            msg = "%s %s" % (record.msg, record.args)
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
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        record._friday_redacted = True
        return True


_FILTER = RedactingFilter()


def install(*loggers) -> None:
    """Attach the one filter to every handler of the given loggers (default: the "friday"
    logger, whose handlers write friday.log and the stderr mirror). Idempotent."""
    for lg in loggers or (logging.getLogger("friday"),):
        for h in list(lg.handlers):
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(_FILTER)
