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
        return s
    except Exception:  # noqa: BLE001 - a line that cannot be checked is not written
        return WITHHELD


class RedactingFilter(logging.Filter):
    """Redact each record in place and give it this request's correlation id (as the
    attribute `correlation_id`, and as a "[req-...]" prefix on the message, so no format
    string has to know about it). Always lets the record through."""

    def filter(self, record: logging.LogRecord) -> bool:
        if _already_redacted(record):
            return True                       # a second handler, or made by the factory: done
        _redact_once(record)
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


def _already_redacted(record) -> bool:
    """True when this record's message is still the one the redactor wrote. The mark is
    that message itself, so a record whose message or arguments were set after it was
    made (logging.makeLogRecord, a filter that rewrites it) is redacted again."""
    done = getattr(record, "_friday_redacted", None)
    return (isinstance(done, str) and record.msg is done and not record.args
            and not record.exc_info)


def _redact_once(record) -> None:
    try:
        RedactingFilter._redact(record)
    except Exception:  # noqa: BLE001 - never raise into the code that logged; never leak
        record.msg, record.args = WITHHELD, None
        record.exc_info, record.exc_text, record.stack_info = None, None, None
    record._friday_redacted = record.msg


_FILTER = RedactingFilter()


def install(*loggers) -> None:
    """Attach the one filter to every handler of the given loggers (default: the "friday"
    logger, whose handlers write friday.log and the stderr mirror). Idempotent."""
    for lg in loggers or (logging.getLogger("friday"),):
        for h in list(lg.handlers):
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(_FILTER)


# -- every logger, not only the "friday" tree --------------------------------
#
# A handler filter only sees records that reach that handler. Records from
# loggers outside the "friday" tree (werkzeug's request lines, urllib3, a
# library logging at the root, Python's last-resort handler when nothing is
# configured) went to stderr unchecked. The record factory runs for every
# record any logger creates, after the level check and before any handler, so
# redacting there covers them all; the handler filter then sees the record is
# done and leaves it.

def _redacting_factory(base):
    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        if not _already_redacted(record):
            _redact_once(record)
        return record
    factory._friday_redacting = True
    return factory


def install_everywhere() -> None:
    """Redact every log record from every logger in this process (record factory), and
    put the filter on the root, werkzeug and last-resort handlers as well, for records
    made before the factory was in place or by a factory installed later. Idempotent."""
    base = logging.getLogRecordFactory()
    if not getattr(base, "_friday_redacting", False):
        logging.setLogRecordFactory(_redacting_factory(base))
    install(logging.getLogger("friday"), logging.getLogger(), logging.getLogger("werkzeug"))
    last = logging.lastResort
    if last is not None and not any(isinstance(f, RedactingFilter) for f in last.filters):
        last.addFilter(_FILTER)


# -- the process's own stdout and stderr ----------------------------------------
#
# print() and tracebacks written straight to sys.stderr (a thread's uncaught
# exception, a library that prints) bypass logging entirely. The server wraps
# its two streams once at start: each complete line is redacted before it is
# written.
#
#   * Each thread keeps its own open line, so another thread's flush never
#     sends half of this thread's line (print() writes its arguments one call
#     at a time), and two threads' text never merges into one line.
#   * A line still open when its thread flushes is redacted as it stands; the
#     rest are redacted at exit.
#   * A line longer than _MAX is checked in pieces cut at whitespace (a key has
#     none), so a value is not split between two checks.
#   * No lock is held while redacting, and a write made from inside the
#     redactor (a warning while it imports) goes out redacted at once instead
#     of waiting on itself.
#
# Not covered: bytes written to `.buffer` or straight to the file descriptors
# (faulthandler, child processes that inherit them), and anything printed
# before the server wraps its streams.

def _pieces(text: str):
    """`text` in pieces of at most _MAX characters, cut at whitespace where it can be."""
    while len(text) > _MAX:
        cut = max(text.rfind(" ", 0, _MAX), text.rfind("\t", 0, _MAX))
        cut = cut + 1 if cut > 0 else _MAX
        yield text[:cut]
        text = text[cut:]
    if text:
        yield text


def _redact_long(text: str) -> str:
    return "".join(redact(p) for p in _pieces(text))


class RedactingStream:
    """A text stream that redacts every line before passing it to `inner`. Everything
    else (encoding, fileno, isatty, buffer, reconfigure) is the inner stream's."""

    _friday_redacting = True

    def __init__(self, inner):
        import threading
        self._inner = inner
        self._open = {}                       # thread id -> that thread's open line
        self._local = threading.local()
        self._get_ident = threading.get_ident

    def write(self, s) -> int:
        text = s if isinstance(s, str) else str(s)
        if getattr(self._local, "busy", False):
            self._inner.write(_redact_long(text))      # from inside the redactor itself
            return len(text)
        self._local.busy = True
        try:
            tid = self._get_ident()
            pending = self._open.pop(tid, "") + text
            out = []
            cut = pending.rfind("\n")
            if cut >= 0:
                for line in pending[:cut].split("\n"):
                    out.append(_redact_long(line) + "\n")
                pending = pending[cut + 1:]
            if len(pending) > _MAX:
                pieces = list(_pieces(pending))
                out.extend(redact(p) for p in pieces[:-1])
                pending = pieces[-1]
            if pending:
                self._open[tid] = pending
            if out:
                self._inner.write("".join(out))
        finally:
            self._local.busy = False
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def _send(self, pending: str) -> None:
        if pending:
            self._local.busy = True
            try:
                self._inner.write(_redact_long(pending))
            finally:
                self._local.busy = False

    def flush(self) -> None:
        """Send this thread's open line (redacted as it stands), then flush."""
        if not getattr(self._local, "busy", False):
            self._send(self._open.pop(self._get_ident(), ""))
        self._inner.flush()

    def flush_all(self) -> None:
        """Every thread's open line, redacted (at exit)."""
        try:
            for tid in list(self._open):
                self._send(self._open.pop(tid, ""))
            self._inner.flush()
        except Exception:  # noqa: BLE001 - at exit the stream may already be closed
            pass

    def __getattr__(self, name):
        return getattr(self._inner, name)


def wrap_stream(stream):
    """`stream` behind the redactor (the same stream when it already is)."""
    if stream is None or getattr(stream, "_friday_redacting", False):
        return stream
    return RedactingStream(stream)


def install_streams() -> None:
    """Wrap sys.stdout and sys.stderr (the server calls this once, at start)."""
    import atexit
    import sys
    for name in ("stdout", "stderr"):
        current = getattr(sys, name)
        wrapped = wrap_stream(current)
        if wrapped is not current:
            setattr(sys, name, wrapped)
            atexit.register(wrapped.flush_all)


# -- what an error answer says ---------------------------------------------------

def redact_json(obj, _depth: int = 0):
    """`obj` (decoded JSON) with every string value redacted; keys and structure kept.
    Returns (new_obj, changed)."""
    if _depth > 40:
        return obj, False
    if isinstance(obj, str):
        r = redact(obj)
        return r, r != obj
    if isinstance(obj, list):
        changed, out = False, []
        for v in obj:
            nv, c = redact_json(v, _depth + 1)
            out.append(nv)
            changed = changed or c
        return out, changed
    if isinstance(obj, dict):
        changed, out = False, {}
        for k, v in obj.items():
            nv, c = redact_json(v, _depth + 1)
            out[k] = nv
            changed = changed or c
        return out, changed
    return obj, False
