"""One failure vocabulary (services/failure_state; BD-16, North Star 32).

On a tree without the module each test fails at the fixture with the reason.
"""
from __future__ import annotations

import errno
import importlib
import socket
import sqlite3
import urllib.error

import pytest


@pytest.fixture
def fs():
    try:
        return importlib.import_module("agent_friday.services.failure_state")
    except ImportError:
        pytest.fail("there is no shared failure classifier: services/failure_state does not exist")


def test_the_six_states_have_words(fs):
    assert fs.STATES == ("external_wait", "needs_user_input", "recall_degraded", "index_corruption",
                         "disk_full", "offline")
    for s in fs.STATES:
        d = fs.describe(s)
        assert d["state"] == s and d["words"].endswith(".")
    assert fs.describe("disk_full")["pauses_background"] is True
    assert fs.describe("offline")["pauses_background"] is False
    assert fs.describe("anything else") == {"state": None, "words": "", "pauses_background": False}


def test_a_full_disk_is_named_on_every_platform(fs):
    assert fs.classify(OSError(errno.ENOSPC, "No space left on device")) == "disk_full"

    class WinDiskFull(OSError):
        winerror = 112
    assert fs.classify(WinDiskFull("There is not enough space on the disk")) == "disk_full"


@pytest.mark.parametrize("exc", [
    ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
    socket.gaierror(socket.EAI_NONAME, "Name or service not known"),
    urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "no name")),
])
def test_a_network_that_cannot_be_reached_is_offline(fs, exc):
    assert fs.classify(exc) == "offline"


def test_http_library_errors_are_matched_by_name_without_importing_them(fs):
    ConnectError = type("ConnectError", (Exception,), {})
    assert fs.classify(ConnectError("all connection attempts failed")) == "offline"


def test_a_damaged_index_is_index_corruption(fs):
    assert fs.classify(sqlite3.DatabaseError("database disk image is malformed")) == "index_corruption"
    assert fs.classify(sqlite3.OperationalError("database is locked")) is None


def test_the_cause_is_followed(fs):
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as inner:
            raise RuntimeError("could not save") from inner
    except RuntimeError as outer:
        assert fs.classify(outer) == "disk_full"


def test_an_ordinary_error_stays_an_ordinary_error(fs):
    assert fs.classify(ValueError("bad")) is None
    assert fs.classify(None) is None


def test_blockers_and_connector_health_map_onto_it(fs):
    assert fs.classify(blocker="external_wait") == "external_wait"
    assert fs.classify(blocker="needs_user_input") == "needs_user_input"
    assert fs.classify(blocker="run_failed") is None
    assert fs.classify(health="needs_user") == "needs_user_input"
    assert fs.classify(health="working") is None
