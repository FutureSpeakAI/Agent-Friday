"""A settings read that meets a save never hands factory defaults to a saver.

On Windows a read of settings.json during a concurrent save raises
PermissionError (a sharing violation). The loader turned that into factory
defaults, and any caller that edited the dict it was given and saved it whole
persisted those defaults over every setting the owner had.

The invariants held here:
  * a read retries a transient failure, and one that still fails serves the
    last good settings, marked degraded, never the defaults when better is known;
  * a save refuses a dict served by a failed read and leaves the file alone;
  * the callers that used to save a whole loaded dict save only their keys;
  * a write is atomic: the file is whole before and after, with no temp left.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from agent_friday import core

OWNER_NAME = "Juniper"


def _on_disk() -> dict:
    # open(), not Path.read_text: the tests patch read_text to fail.
    with open(core.SETTINGS_FILE, encoding="utf-8-sig") as f:
        return json.load(f)


def _sharing_violation():
    return PermissionError(
        13, "The process cannot access the file because it is being used by "
            "another process")


@pytest.fixture
def owned(friday_dir, monkeypatch):
    """settings.json holds an owner's choice; nothing is cached in memory."""
    core._save_settings({"agent_name": OWNER_NAME})
    core._invalidate_settings_cache()
    # Short retry windows keep the failing-read tests fast; absent on a
    # build without retries, which is what the red run measures.
    monkeypatch.setattr(core, "_SETTINGS_READ_RETRY_S", 0.2, raising=False)
    yield
    core._invalidate_settings_cache()


def _fail_reads(monkeypatch, *, times=None, while_flag=None):
    """Make reads of settings.json raise a sharing violation.

    `times`: the first N reads fail. `while_flag`: reads fail while
    while_flag["on"] is true.
    """
    real = core.Path.read_text
    count = {"n": 0}

    def read_text(self, *a, **k):
        if self == core.SETTINGS_FILE:
            if times is not None and count["n"] < times:
                count["n"] += 1
                raise _sharing_violation()
            if while_flag is not None and while_flag["on"]:
                count["n"] += 1
                raise _sharing_violation()
        return real(self, *a, **k)

    monkeypatch.setattr(core.Path, "read_text", read_text)
    return count


def _forget_last_good(monkeypatch):
    """A process that has not yet read settings successfully."""
    monkeypatch.setattr(core, "_SETTINGS_LAST_GOOD",
                        {"path": None, "value": None}, raising=False)


# ── reads ────────────────────────────────────────────────────────────

def test_a_read_that_meets_a_save_returns_the_real_settings(owned, monkeypatch):
    count = _fail_reads(monkeypatch, times=3)
    got = core._load_settings_raw()
    assert got["agent_name"] == OWNER_NAME, (
        "a read that met a concurrent save served factory defaults")
    assert count["n"] == 3, "the sharing violation was not exercised"
    assert not getattr(got, "degraded", False)


def test_a_read_that_keeps_failing_serves_the_last_good_settings(owned, monkeypatch):
    assert core._load_settings_raw()["agent_name"] == OWNER_NAME
    core._invalidate_settings_cache()
    flag = {"on": True}
    _fail_reads(monkeypatch, while_flag=flag)
    got = core._load_settings()
    assert got["agent_name"] == OWNER_NAME, (
        "a failed read served factory defaults while good settings were known")
    assert getattr(got, "degraded", False) is True, "a failed read was not marked degraded"
    flag["on"] = False
    again = core._load_settings_raw()
    assert again["agent_name"] == OWNER_NAME
    assert not getattr(again, "degraded", False)


def test_a_concurrent_writer_never_exposes_defaults_to_a_reader(owned, monkeypatch):
    """A writer saving deltas while reads intermittently meet a sharing violation."""
    real = core.Path.read_text
    n = {"reads": 0}

    def read_text(self, *a, **k):
        if self == core.SETTINGS_FILE:
            n["reads"] += 1
            if n["reads"] % 3 == 0:
                raise _sharing_violation()
        return real(self, *a, **k)

    monkeypatch.setattr(core.Path, "read_text", read_text)
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            try:
                core._save_settings({"show_all_workspaces": bool(i % 2)})
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            i += 1

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        names = []
        for _ in range(60):
            core._invalidate_settings_cache()
            names.append(core._load_settings_raw()["agent_name"])
    finally:
        stop.set()
        t.join(10)
    assert set(names) == {OWNER_NAME}, (
        "a reader saw factory defaults while a writer was saving")
    assert not errors, errors[:3]
    assert _on_disk()["agent_name"] == OWNER_NAME


# ── saves after a failed read ────────────────────────────────────────

def _load_while_unreadable(monkeypatch):
    flag = {"on": True}
    _fail_reads(monkeypatch, while_flag=flag)
    core._invalidate_settings_cache()
    s = core._load_settings()
    flag["on"] = False          # the file is readable again by the time of the save
    return s


def test_a_save_of_a_failed_read_does_not_overwrite_the_file(owned, monkeypatch):
    _forget_last_good(monkeypatch)
    s = _load_while_unreadable(monkeypatch)
    s["show_all_workspaces"] = False            # "load, edit one key, save it all"
    try:
        core._save_settings(s)
    except RuntimeError:
        pass
    assert _on_disk()["agent_name"] == OWNER_NAME, (
        "a save after a failed read wrote factory defaults over settings.json")


def test_a_copied_failed_read_is_refused_too(owned, monkeypatch):
    _forget_last_good(monkeypatch)
    s = dict(_load_while_unreadable(monkeypatch))   # the copy loses any mark
    s["show_all_workspaces"] = False
    try:
        core._save_settings(s)
    except RuntimeError:
        pass
    assert _on_disk()["agent_name"] == OWNER_NAME


def test_a_delta_save_after_a_failed_read_is_kept(owned, monkeypatch):
    _forget_last_good(monkeypatch)
    _load_while_unreadable(monkeypatch)
    core._save_settings({"show_all_workspaces": False})
    disk = _on_disk()
    assert disk["agent_name"] == OWNER_NAME
    assert disk["show_all_workspaces"] is False


def test_the_whole_dict_callers_save_only_their_keys(owned, monkeypatch):
    """With every read returning factory defaults (what a failed read used to
    give), the converted callers still leave the owner's settings alone."""
    defaults = lambda: dict(core.DEFAULT_SETTINGS)  # noqa: E731
    monkeypatch.setattr(core, "_load_settings", defaults)
    monkeypatch.setattr(core, "_load_settings_raw", defaults)

    from agent_friday.services import setup_chat
    setup_chat._set_keep_brain_warm(True)
    assert _on_disk()["agent_name"] == OWNER_NAME
    setup_chat._set_room_approvals_require_name(True)
    assert _on_disk()["agent_name"] == OWNER_NAME

    import agent_friday.routes.federation as fedroutes
    from agent_friday.services import federation as fed
    monkeypatch.setattr(fed, "get_peer", lambda agent_id: None)
    fedroutes._handle_federation_message(
        "SETTINGS_SYNC", {"settings": {"voice_engine": "piper"}}, "peer-pubkey")
    disk = _on_disk()
    assert disk["agent_name"] == OWNER_NAME
    assert disk["voice_engine"] == "piper"


def test_a_binding_delta_carries_only_what_changed():
    before = {"agent_name": OWNER_NAME, "orchestrator_model": "a",
              "model_routing": {"mode": "local_only", "local_model": "x"}}
    after = {"agent_name": OWNER_NAME, "orchestrator_model": "b",
             "model_routing": {"mode": "local_only", "local_model": "y"}}
    assert core._settings_delta(before, after) == {
        "orchestrator_model": "b", "model_routing": {"local_model": "y"}}


# ── atomic writes ────────────────────────────────────────────────────

def _temps():
    return sorted(p.name for p in core.SETTINGS_FILE.parent.glob(".settings-*.tmp"))


def test_a_replace_denied_for_a_moment_still_lands_whole(owned, monkeypatch):
    real = os.replace
    until = time.monotonic() + 0.4
    denied = {"n": 0}

    def replace(src, dst, *a, **k):
        if str(dst) == str(core.SETTINGS_FILE) and time.monotonic() < until:
            denied["n"] += 1
            raise _sharing_violation()
        return real(src, dst, *a, **k)

    monkeypatch.setattr(core.os, "replace", replace)
    before = _temps()
    core._save_settings({"show_all_workspaces": False})
    assert denied["n"] > 0, "the denied replace was not exercised"
    disk = _on_disk()
    assert disk["show_all_workspaces"] is False and disk["agent_name"] == OWNER_NAME
    assert _temps() == before, "a temp file was left beside settings.json"


def test_a_replace_that_never_lands_leaves_the_file_and_no_temp(owned, monkeypatch):
    monkeypatch.setattr(core, "_SETTINGS_REPLACE_RETRY_S", 0.1, raising=False)
    real = os.replace

    def replace(src, dst, *a, **k):
        if str(dst) == str(core.SETTINGS_FILE):
            raise _sharing_violation()
        return real(src, dst, *a, **k)

    monkeypatch.setattr(core.os, "replace", replace)
    before_bytes = core.SETTINGS_FILE.read_bytes()
    before = _temps()
    with pytest.raises(PermissionError):
        core._save_settings({"show_all_workspaces": False})
    assert core.SETTINGS_FILE.read_bytes() == before_bytes
    assert _temps() == before
