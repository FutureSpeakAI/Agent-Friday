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


@pytest.fixture(autouse=True)
def _no_settings_memory_across_tests(monkeypatch):
    """The last-good settings and the degraded hint are process state: each
    test here starts without them, and none leaves them to a later file."""
    monkeypatch.setattr(core, "_SETTINGS_LAST_GOOD",
                        {"path": None, "value": None}, raising=False)
    monkeypatch.setattr(core, "_SETTINGS_DEGRADED", {"path": None}, raising=False)
    yield


@pytest.fixture
def owned(friday_dir, monkeypatch):
    """A fresh settings.json holding an owner's choice; nothing cached.

    Fresh per test: a test whose fix is missing (the red run) writes into the
    file, and the next test must not inherit what it wrote."""
    core._invalidate_settings_cache()
    core.SETTINGS_FILE.unlink(missing_ok=True)
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


# ── provenance travels with the data, not with a process-wide flag ───

def test_a_good_read_elsewhere_does_not_launder_a_failed_one(owned, monkeypatch):
    """Thread A reads while the file is unreadable and copies the result;
    thread B then reads successfully; A's whole-dict save must still fail."""
    _forget_last_good(monkeypatch)       # the failed read serves defaults
    a = dict(_load_while_unreadable(monkeypatch))     # A: failed read, copied
    assert core._load_settings_raw()["agent_name"] == OWNER_NAME   # B: good read
    a["show_all_workspaces"] = False
    try:
        core._save_settings(a)
    except RuntimeError:
        pass
    assert _on_disk()["agent_name"] == OWNER_NAME, (
        "a save derived from a failed read landed after another read succeeded")


def test_a_failed_read_elsewhere_does_not_block_a_valid_save(owned, monkeypatch):
    """Thread B holds a whole dict from a good read; thread A's read then
    fails; B's save is still B's to make."""
    b = dict(core._load_settings_raw())                # B: good read
    _load_while_unreadable(monkeypatch)                # A: failed read
    b["show_all_workspaces"] = False
    core._save_settings(b)
    disk = _on_disk()
    assert disk["show_all_workspaces"] is False and disk["agent_name"] == OWNER_NAME


def test_a_delta_of_a_failed_read_is_refused(owned, monkeypatch):
    _forget_last_good(monkeypatch)       # the failed read serves defaults
    before = _load_while_unreadable(monkeypatch)
    after = dict(before)
    after["orchestrator_model"] = "bound-from-defaults"
    with pytest.raises(RuntimeError):
        core._save_settings(core._settings_delta(before, after))
    assert "orchestrator_model" not in _on_disk() or (
        _on_disk()["orchestrator_model"] != "bound-from-defaults")


def test_boot_seat_binding_over_a_failed_read_writes_nothing(owned, monkeypatch):
    _forget_last_good(monkeypatch)       # the failed read serves defaults
    from agent_friday import server
    from agent_friday.services import hardware_profile as hwp
    from agent_friday.services import residency_catalog as rc
    from agent_friday.services import residency_arbiter as ra
    from agent_friday.services import seat_binding as sb

    flag = {"on": True}
    _fail_reads(monkeypatch, while_flag=flag)
    core._invalidate_settings_cache()

    class Arb:
        def __init__(self, **k):
            pass

        def compute_plan(self, overrides):
            return {}

    def apply(plan, settings):
        settings["orchestrator_model"] = "bound-from-defaults"
        flag["on"] = False              # the file is readable again for the save
        return {"changes": {"reasoning": {"provider": "x",
                                          "model": "bound-from-defaults"}}}

    monkeypatch.setattr(hwp, "get", lambda: {})
    monkeypatch.setattr(hwp, "summary", lambda p: "test")
    monkeypatch.setattr(rc, "installed_entries", lambda p: [])
    monkeypatch.setattr(ra, "Arbiter", Arb)
    # Boot publishes the instance it built as the process-wide handle: put the
    # real one back afterwards, or every later test that asks for the arbiter
    # gets this stand-in.
    monkeypatch.setattr(ra, "ARBITER", ra.ARBITER)
    monkeypatch.setattr(sb, "overrides_from_settings", lambda s: {})
    monkeypatch.setattr(sb, "apply", apply)
    monkeypatch.setattr(sb, "describe", lambda p: "")
    before = core.SETTINGS_FILE.read_bytes()
    server._residency_boot()
    assert not flag["on"], "the binding was not exercised"
    assert core.SETTINGS_FILE.read_bytes() == before, (
        "boot seat binding saved seats bound over a failed settings read")


# ── the converted routes ─────────────────────────────────────────────

@pytest.mark.parametrize("route", ["context_pause", "context_resume"])
def test_context_pause_and_resume_save_only_their_key(owned, monkeypatch, route):
    from agent_friday import server
    from agent_friday.routes import context as ctx
    monkeypatch.setattr(ctx, "_load_settings",
                        lambda: dict(core.DEFAULT_SETTINGS), raising=False)
    with server.app.test_request_context():
        resp = getattr(ctx, route)()
    assert resp.get_json()["status"] == "ok"
    assert _on_disk()["agent_name"] == OWNER_NAME, (
        "%s wrote the settings it read back over settings.json" % route)
    assert _on_disk()["context_logging_enabled"] is (route == "context_resume")


# ── first-run seed and the install check ─────────────────────────────

def test_the_seed_never_overwrites_a_file_that_appeared(owned, monkeypatch):
    """settings.json appears between the existence check and the seed."""
    real_exists = core.Path.exists
    first = {"done": False}

    def exists(self, *a, **k):
        if self == core.SETTINGS_FILE and not first["done"]:
            first["done"] = True
            return False
        return real_exists(self, *a, **k)

    monkeypatch.setattr(core.Path, "exists", exists)
    got = core._load_settings_raw()
    assert first["done"], "the seed path was not exercised"
    assert _on_disk()["agent_name"] == OWNER_NAME, "the seed overwrote settings.json"
    assert got["agent_name"] == OWNER_NAME


def test_an_install_with_a_bom_settings_file_is_recognised(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_bytes(b"\xef\xbb\xbf" + json.dumps({"setup_complete": True}).encode())
    monkeypatch.setattr(core, "SETTINGS_FILE", settings)
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    assert core._is_existing_install() is True, (
        "a byte-order mark made a finished install look like a first run")
