"""pytest plugin (-p tests.late_notice_wait) for
tests/unit/test_late_notice_writers_do_not_outlive_their_file.py's child runs.

Before each test's setup (so before its file snapshots the notice stores),
wait for the late writers in tests/unit/late_notice_writers.py to finish:
what they wrote after their own file ended is then on disk when the next file
starts, every time, instead of only when the timing falls that way.
"""
from __future__ import annotations

import threading

import pytest

_LATE = ("laya-personal-notice", "late-notice-racer")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    me = threading.current_thread()
    for t in threading.enumerate():
        if t is not me and t.name in _LATE:
            t.join(20)