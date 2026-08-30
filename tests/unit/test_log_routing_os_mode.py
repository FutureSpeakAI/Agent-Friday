"""PR-2 (OS mode switch, OS-mode sequence) — behavior 2: with FRIDAY_OS_MODE
on AND FRIDAY_LOG_TARGET=stdout, logging routes to stderr in a
journald-friendly single-line format instead of the rotating file used on
every other install.

Exercises `agent_friday.core._setup_friday_logging()` directly against a
scratch "friday" logger (save/restore its handlers around each test) rather
than reimporting `agent_friday.core` — reimporting would re-run the whole
~2600-line module's import-time side effects (Flask app bootstrap, the
legacy `~/wiki` migration) for no benefit, since `_setup_friday_logging()`
already reads FRIDAY_DIR / os.environ live and is safely re-callable (it
only skips if the logger already has handlers, which the fixture below
clears before each test).

The whole test suite runs with FRIDAY_HOME/HOME/USERPROFILE redirected to a
throwaway temp dir (tests/conftest.py), so `core.FRIDAY_DIR` already points
under that temp dir — never the real ~/.friday — for every test in this
file.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

import pytest

from agent_friday import core


@pytest.fixture(autouse=True)
def fresh_friday_logger(monkeypatch, tmp_path):
    """Isolate the "friday" root logger for each test: save its real
    handlers/level, clear them so _setup_friday_logging() doesn't see its
    `if root.handlers: return` early-out, point FRIDAY_DIR at a throwaway
    tmp_path (never the real ~/.friday), then restore everything after."""
    logger = logging.getLogger("friday")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    logger.handlers = []
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / "friday-home", raising=False)
    yield logger
    for h in logger.handlers:
        logger.removeHandler(h)
    logger.handlers = saved_handlers
    logger.level = saved_level


def _record(msg: str, level: int = logging.ERROR) -> logging.LogRecord:
    return logging.LogRecord("friday.test", level, __file__, 1, msg, None, None)


def test_os_mode_and_stdout_target_routes_to_stderr_single_line(monkeypatch, fresh_friday_logger):
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    monkeypatch.setenv("FRIDAY_LOG_TARGET", "stdout")

    core._setup_friday_logging()

    handlers = fresh_friday_logger.handlers
    assert len(handlers) == 1, "OS mode + stdout target must ROUTE, not just mirror"
    handler = handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.stream is sys.stderr
    assert handler.level == logging.DEBUG, "the whole point is DEBUG+ reaches journald"

    # Single-line-per-record: a multi-line message (e.g. a traceback) must
    # not fragment into multiple journal lines.
    multiline = _record("first line\nsecond line\nthird line")
    formatted = handler.format(multiline)
    assert "\n" not in formatted
    assert "first line" in formatted and "second line" in formatted

    # No ANSI escape codes.
    assert "\x1b[" not in formatted


def test_os_mode_on_but_target_not_stdout_keeps_default_behavior(monkeypatch, fresh_friday_logger):
    """Only the exact combination (OS mode AND FRIDAY_LOG_TARGET=stdout)
    changes routing — OS mode alone must not."""
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    monkeypatch.delenv("FRIDAY_LOG_TARGET", raising=False)

    core._setup_friday_logging()

    handlers = fresh_friday_logger.handlers
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in handlers)
    stream_handlers = [h for h in handlers
                       if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(stream_handlers) == 1
    assert stream_handlers[0].level == logging.WARNING


def test_default_windows_behavior_unchanged_when_os_mode_unset(monkeypatch, fresh_friday_logger):
    """Regression guard: FRIDAY_OS_MODE unset (the Windows default) must
    produce EXACTLY the pre-PR handler set — a DEBUG rotating file handler
    plus a WARNING-level stderr mirror — even if FRIDAY_LOG_TARGET=stdout is
    set for some unrelated reason."""
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    monkeypatch.setenv("FRIDAY_LOG_TARGET", "stdout")

    core._setup_friday_logging()

    handlers = fresh_friday_logger.handlers
    file_handlers = [h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].level == logging.DEBUG

    stream_handlers = [h for h in handlers
                       if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(stream_handlers) == 1
    assert stream_handlers[0].level == logging.WARNING
    assert stream_handlers[0].stream is sys.stderr

    # The pre-existing stderr formatter has no journald single-line collapsing
    # — a multi-line message passes through with its newlines intact, which
    # pins that this test is really exercising the OLD formatter, not the
    # new one accidentally applying anyway.
    formatted = stream_handlers[0].format(_record("first\nsecond", level=logging.WARNING))
    assert "\n" in formatted


def test_default_behavior_unchanged_when_neither_set(monkeypatch, fresh_friday_logger):
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    monkeypatch.delenv("FRIDAY_LOG_TARGET", raising=False)

    core._setup_friday_logging()

    handlers = fresh_friday_logger.handlers
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in handlers)
    assert len(handlers) == 2
