"""A log that cannot rotate must keep logging.

On Windows a rename fails with PermissionError [WinError 32] while any other
process holds the file open, and this app runs two nested server.py processes
that both open friday.log. The stock RotatingFileHandler raises that failure
through handleError, which prints "--- Logging error ---" plus a full
traceback to stderr for every record it could not write.

Measured on the reference machine, 2026-09-22: friday.log froze at
10,485,736 bytes at 15:10 and never advanced again. Every subsequent log line
became a stderr traceback instead, and server_stderr.log grew at 5.6 GB/hour.
Three hours with no application log at all -- which is also exactly the signal
a reader uses to decide the process has silently hung, so the app looked dead
while it was working.

An oversized log is a far smaller problem than no log. These tests pin that
trade: the records still land, the failure is reported once, and nothing
raises into the caller.
"""

import logging
import logging.handlers

import pytest

from agent_friday.core import _ResilientRotatingFileHandler


@pytest.fixture
def handler(tmp_path, monkeypatch):
    """A handler that rolls on every record and can never complete the roll."""
    path = tmp_path / "friday.log"
    h = _ResilientRotatingFileHandler(path, maxBytes=1, backupCount=3,
                                      encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))

    def _locked(src, dst):
        raise PermissionError(
            "[WinError 32] The process cannot access the file because it is "
            "being used by another process")

    monkeypatch.setattr(h, "rotate", _locked)
    _ResilientRotatingFileHandler._warned = False
    h._retry_after = 0.0
    return h


def _records(h, n, start=0):
    for i in range(start, start + n):
        h.emit(logging.LogRecord("t", logging.WARNING, __file__, 1,
                                 "line-%d" % i, None, None))


def test_records_still_reach_the_file_when_rotation_is_impossible(handler):
    """The whole point. A locked log kept 10 MB of history and threw away
    everything after it."""
    _records(handler, 40)
    handler.flush()
    text = handler.baseFilename and open(handler.baseFilename,
                                         encoding="utf-8").read()
    for i in (0, 17, 39):
        assert ("line-%d" % i) in text, (
            "a record was lost because the log could not be rotated")


def test_a_failed_rotation_never_raises_into_the_caller(handler, monkeypatch):
    """handleError is what prints '--- Logging error ---' and the traceback.
    It must not be reached: that output is the 5.6 GB/hour."""
    hit = []
    monkeypatch.setattr(handler, "handleError", lambda r: hit.append(r))
    _records(handler, 40)
    assert hit == [], (
        "a failed rotation still reached handleError, so every suppressed "
        "line becomes a traceback on stderr")


def test_the_failure_is_reported_once_not_per_record(handler, capsys):
    """One line saying the log cannot rotate is useful. One per record is the
    bug wearing a different hat."""
    _records(handler, 40)
    err = capsys.readouterr().err
    assert err.count("cannot rotate") == 1, (
        "the rotation failure must be announced exactly once")
    assert "friday.log" in err


def test_rotation_is_retried_rather_than_abandoned_forever(handler,
                                                           monkeypatch):
    """Backing off is not giving up: once the other holder lets go, the log
    must start rotating again on its own."""
    _records(handler, 5)
    assert handler._retry_after > 0, "a failed roll must back off"

    rolled = []
    monkeypatch.setattr(handler, "rotate", lambda s, d: rolled.append((s, d)))
    handler._retry_after = 0.0            # the backoff window has passed
    _records(handler, 5, start=100)
    assert rolled, "rotation was never attempted again after the lock cleared"
    assert handler._retry_after == 0.0, "a successful roll must clear the backoff"
