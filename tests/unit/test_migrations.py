"""Versioned migrations of the data folder (north star §28.8-1, §37.3-11):
backed up first, idempotent, resumable after a real stop, integrity-checked,
with a receipt; a failure puts the files back and this start is safe mode."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _mg():
    assert importlib.util.find_spec("agent_friday.services.migrations") is not None, \
        "there is no migration runner"
    return importlib.import_module("agent_friday.services.migrations")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    (h / "settings.json").write_text(json.dumps({"agent_name": "Friday", "version": 3}), encoding="utf-8")
    (h / "notes.txt").write_text("before", encoding="utf-8")
    from agent_friday.services import safe_mode
    safe_mode.reset_for_tests()
    yield h
    # safe mode is decided once per process: a marker a test wrote must not
    # decide it for every later test in this worker
    safe_mode.reset_for_tests()


@pytest.fixture
def asked(monkeypatch):
    """What the runner asked of safe mode for the next start."""
    out = []
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "set_next_start", lambda on, reason=None: out.append((on, reason)) or {})
    return out


def _bump(home: Path, name: str) -> None:
    p = home / ("count-" + name)
    p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1))


def _fakes(mg, *, fail_at=None, bad_check_at=None):
    def apply1(h):
        _bump(h, "1")
        (h / "notes.txt").write_text("after one", encoding="utf-8")

    def apply2(h):
        _bump(h, "2")
        if fail_at == 2:
            raise OSError("disk went away")
        (h / "notes.txt").write_text("after two", encoding="utf-8")
    return [mg.Migration(1, "one", ("notes.txt", "schema.json"), apply1, lambda h: bad_check_at != 1),
            mg.Migration(2, "two", ("notes.txt",), apply2, lambda h: bad_check_at != 2)]


def _siblings(home: Path, part: str) -> list:
    return sorted(p.name for p in home.parent.iterdir() if part in p.name)


def _receipts(home: Path, pattern: str) -> list:
    return [json.loads(p.read_text(encoding="utf-8")) for p in (home / "migrations").glob(pattern)]


def test_the_first_migration_records_every_stores_version_with_a_receipt(home):
    mg = _mg()
    r = mg.run_pending(home)
    assert r and r["ok"] and r["from"] == 0 and r["to"] == mg.latest()
    sch = json.loads((home / "schema.json").read_text(encoding="utf-8"))
    assert sch["schema"] == mg.latest() and sch["stores"]["settings"] == 3
    rec = _receipts(home, "receipt-0-to-*.json")
    assert rec and rec[0]["event"] == "migrated" and rec[0]["ledgered"] is False
    assert not _siblings(home, ".migration-journal")
    assert mg.run_pending(home) is None                            # nothing more to do


def test_steps_run_in_order_once_each_backed_up_first(home, monkeypatch):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    r = mg.run_pending(home)
    assert r["ok"] and r["steps"] == [1, 2] and mg.current(home) == 2
    assert (home / "notes.txt").read_text(encoding="utf-8") == "after two"
    copies = _siblings(home, ".pre-migration-")
    assert len(copies) == 1                                        # copied before the first step
    assert (home.parent / copies[0] / "notes.txt").read_text(encoding="utf-8") == "before"
    assert mg.run_pending(home) is None and (home / "count-1").read_text() == "1"


@pytest.mark.parametrize("how", ["raises", "check"])
def test_a_failed_step_puts_the_files_back_and_this_start_is_safe_mode(home, monkeypatch, how, asked):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg, fail_at=2 if how == "raises" else None,
                                                 bad_check_at=2 if how == "check" else None))
    r = mg.run_pending(home)
    assert r["ok"] is False and r["safe_mode"] is True and r["failed"] == 2
    assert (home / "notes.txt").read_text(encoding="utf-8") == "before"     # step 1's change undone too
    assert mg.current(home) == 0 and not (home / "schema.json").exists()
    assert asked == [(True, "migration_failed")]     # safe mode until the owner leaves it
    assert [x["event"] for x in _receipts(home, "receipt-0-to-2-*.json")] == ["migration_failed"]
    assert not _siblings(home, ".migration-journal")


def test_files_that_cannot_be_put_back_stop_friday_and_name_the_copy(home, monkeypatch):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg, fail_at=2))

    def broken(*a, **k):
        raise PermissionError("held open")
    monkeypatch.setattr(mg, "_put_back", broken)
    r = mg.run_pending(home)
    assert r["ok"] is False and r.get("stop") is True and ".pre-migration-" in r["why"]
    jr = json.loads(mg._journal_path(home).read_text(encoding="utf-8"))
    assert jr["state"] == "restore_failed" and Path(jr["backup"]).is_dir()
    monkeypatch.undo()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    assert mg.run_pending(home).get("stop") is True               # and keeps stopping until settled


def test_a_stop_while_putting_files_back_is_finished_at_the_next_start(home, monkeypatch):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg, fail_at=2))

    class Stop(BaseException):
        pass

    def stop(step):
        if step == "restoring":
            raise Stop()
    monkeypatch.setattr(mg, "_journal_step", stop)
    with pytest.raises(Stop):
        mg.run_pending(home)
    assert (home / "notes.txt").read_text(encoding="utf-8") == "after one"  # half done
    monkeypatch.setattr(mg, "_journal_step", lambda step: None)
    r = mg.run_pending(home)
    assert r["safe_mode"] is True and (home / "notes.txt").read_text(encoding="utf-8") == "before"
    assert mg.current(home) == 0 and not _siblings(home, ".migration-journal")


def test_a_failed_migration_keeps_safe_mode_until_the_owner_presses_on_the_doctor_page(home, monkeypatch):
    mg = _mg()
    from agent_friday import cli
    from agent_friday.services import safe_mode
    monkeypatch.delenv("FRIDAY_SAFE_START", raising=False)
    monkeypatch.setattr(safe_mode, "_failed_starts", lambda: 0)
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg, fail_at=2))
    assert mg.run_pending(home)["safe_mode"] is True
    assert safe_mode.marker_path().exists() and safe_mode.marker_reason() == "migration_failed"
    safe_mode.reset_for_tests()
    try:
        st = safe_mode.state()
        assert st["active"] is True and "Doctor page" in st["why"]
        assert cli.cmd_safe_mode("off") == 2 and safe_mode.marker_path().exists()   # not from the CLI
        safe_mode.set_next_start(True)                       # asking again keeps the reason
        assert safe_mode.marker_reason() == "migration_failed"
        monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
        assert mg.run_pending(home)["ok"]                    # a later success does not leave it either
        assert safe_mode.marker_path().exists()
    finally:
        safe_mode.reset_for_tests()


def test_a_torn_safe_mode_marker_is_read_as_the_stricter_one(home, monkeypatch):
    _mg()
    from agent_friday import cli
    from agent_friday.services import safe_mode
    p = safe_mode.marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"chosen_at": 17', encoding="utf-8")              # cut off mid-write
    assert safe_mode.marker_reason() == "migration_failed"
    assert cli.cmd_safe_mode("off") == 2 and p.exists()
    safe_mode.set_next_start(True)                                   # rewritten whole, reason kept
    assert json.loads(p.read_text(encoding="utf-8"))["reason"] == "migration_failed"
    assert not list(p.parent.glob("*.partial-*"))


def test_safe_mode_is_kept_before_the_files_are_put_back(home, monkeypatch):
    mg = _mg()
    order = []
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "set_next_start", lambda on, reason=None: order.append("marker") or {})
    real = mg._put_back
    monkeypatch.setattr(mg, "_put_back", lambda *a, **k: order.append("put_back") or real(*a, **k))
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg, fail_at=2))
    mg.run_pending(home)
    assert order[:2] == ["marker", "put_back"]


@pytest.mark.parametrize("how", ["link", "cut_off"])
def test_a_migration_that_cannot_run_keeps_safe_mode_too(home, monkeypatch, asked, how):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    if how == "link":
        monkeypatch.setattr(mg, "_links_in", lambda h, t: ["notes.txt"])
    else:
        mg._journal_path(home).write_text(json.dumps({"v": 1, "state": "running", "from": 0, "to": 2,
                                                      "backup": str(home.parent / "gone"),
                                                      "touches": ["notes.txt"], "copied": ["notes.txt"]}),
                                          encoding="utf-8")
    assert mg.run_pending(home)["safe_mode"] is True and asked == [(True, "migration_failed")]


def test_a_newer_data_folder_is_never_touched(home):
    mg = _mg()
    (home / "schema.json").write_text(json.dumps({"schema": 99}), encoding="utf-8")
    r = mg.run_pending(home)
    assert r["safe_mode"] is True and "newer Friday" in r["why"]
    assert json.loads((home / "schema.json").read_text(encoding="utf-8")) == {"schema": 99}
    assert not _siblings(home, ".pre-migration-")


def test_a_schema_that_is_not_a_number_counts_as_none(home):
    mg = _mg()
    for bad in ([1], {"schema": True}, {"schema": "2"}, {"schema": -1}):
        (home / "schema.json").write_text(json.dumps(bad), encoding="utf-8")
        assert mg.current(home) == 0


@pytest.mark.skipif(os.name == "nt", reason="symlinks need a privilege on Windows")
def test_a_touched_path_that_is_a_link_is_never_followed(home, monkeypatch, tmp_path):
    mg = _mg()
    outside = tmp_path / "outside.txt"
    outside.write_text("not hers", encoding="utf-8")
    (home / "notes.txt").unlink()
    (home / "notes.txt").symlink_to(outside)
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    r = mg.run_pending(home)
    assert r["safe_mode"] is True and "link" in r["why"]
    assert outside.read_text(encoding="utf-8") == "not hers" and mg.current(home) == 0
    assert not _siblings(home, ".pre-migration-")


def test_a_running_restore_or_migration_holds_the_runner_off(home, monkeypatch):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    from agent_friday.services import friday_bundle
    with friday_bundle._RestoreLock(home) as rl:
        if not rl.held:
            pytest.skip("the restore lock is not available here")
        r = mg.run_pending(home)
    assert r["stop"] is True and "restore" in r["why"] and mg.current(home) == 0


CHILD = r"""
import os, sys
from pathlib import Path
home = Path(sys.argv[1])
from agent_friday.services import migrations as mg
def bump(h, name):
    p = h / ("count-" + name)
    p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1))
def a1(h):
    bump(h, "1"); (h / "notes.txt").write_text("after one", encoding="utf-8")
def a2(h):
    bump(h, "2"); (h / "notes.txt").write_text("after two", encoding="utf-8")
mg.MIGRATIONS = [mg.Migration(1, "one", ("notes.txt", "schema.json"), a1, lambda h: True),
                 mg.Migration(2, "two", ("notes.txt",), a2, lambda h: True)]
def stop(step):
    if step == "done-1":
        os._exit(9)
mg._journal_step = stop
mg.run_pending(home)
sys.exit(0)
"""


def _stop_at_step_one(home, tmp_path):
    env = dict(os.environ, FRIDAY_HOME=str(home), HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-c", CHILD, str(home)], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 9, (r.returncode, r.stderr[-2000:])


def test_a_migration_stopped_part_way_resumes_at_the_next_start(home, monkeypatch, tmp_path):
    mg = _mg()
    _stop_at_step_one(home, tmp_path)
    assert mg.current(home) == 1 and _siblings(home, ".migration-journal")
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    out = mg.run_pending(home)
    assert out["ok"] and out["resumed"] is True and out["steps"] == [1, 2]
    assert (home / "count-1").read_text() == "1" and (home / "count-2").read_text() == "1"
    assert not _siblings(home, ".migration-journal")


def test_a_cut_off_migration_whose_files_changed_since_is_left_alone(home, monkeypatch, tmp_path):
    mg = _mg()
    _stop_at_step_one(home, tmp_path)
    (home / "notes.txt").write_text("changed by hand", encoding="utf-8")
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    r = mg.run_pending(home)
    assert r["safe_mode"] is True and "changed" in r["why"]
    assert (home / "notes.txt").read_text(encoding="utf-8") == "changed by hand"
    assert not (home / "count-2").exists()


def test_a_cut_off_migration_without_its_copy_is_left_alone(home, monkeypatch):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    mg._journal_path(home).write_text(json.dumps({"v": 1, "state": "running", "from": 0, "to": 2,
                                                  "backup": str(home.parent / "gone"),
                                                  "touches": ["notes.txt"], "copied": ["notes.txt"]}),
                                      encoding="utf-8")
    r = mg.run_pending(home)
    assert r["safe_mode"] is True and "copy is missing" in r["why"]
    assert (home / "notes.txt").read_text(encoding="utf-8") == "before" and not (home / "count-2").exists()


def test_receipts_go_into_the_ledger_after_the_start(home, monkeypatch):
    mg = _mg()
    mg.run_pending(home)
    from agent_friday.services import action_ledger
    got = []
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: got.append((kind, rec)) or {})
    assert mg.flush_receipts(home) == 1 and got[0][0] == "migration" and "ledgered" not in got[0][1]
    assert mg.flush_receipts(home) == 0                          # once each


def test_the_server_settles_migrations_before_it_uses_the_data_folder():
    src = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "_mig_boot.run_pending()" in src, "the server never runs migrations at start"
    i = src.index("_mig_boot.run_pending()")
    assert i < src.index("= _acquire_single_instance_lock()")
    assert src.index("recover_interrupted_restore()") < i            # an interrupted restore first
    assert "SystemExit(3)" in src[i:i + 1200]                         # files not put back: no start
    assert "flush_receipts" in src


# ── §37.3-2: the whole data folder is copied before a migration ──────────────

def _fill(home: Path) -> None:
    (home / "memory").mkdir()
    (home / "memory" / "facts.jsonl").write_text('{"v":1}\n', encoding="utf-8")
    (home / "owners-own-file.txt").write_text("hers", encoding="utf-8")       # a name no store list knows
    for d in ("models", "cache", "logs"):                                     # not durable
        (home / d).mkdir()
        (home / d / "big.bin").write_bytes(b"x" * 1024)
    (home / "friday.log").write_text("log", encoding="utf-8")


def test_the_whole_folder_is_copied_first_except_what_is_not_durable(home, monkeypatch):
    mg = _mg()
    _fill(home)
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    r = mg.run_pending(home)
    assert r["ok"]
    copy = home.parent / _siblings(home, ".pre-migration-")[0]
    names = sorted(p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_file())
    assert names == ["memory/facts.jsonl", "notes.txt", "owners-own-file.txt", "settings.json"], names
    rec = _receipts(home, "receipt-0-to-2-*.json")[0]
    assert rec.get("backup_scope") == "full"
    assert {"models", "cache", "logs", "friday_log"} <= set(rec.get("left_out") or [])
    assert "owners-own-file.txt" not in json.dumps(rec)                       # store names only


def test_a_failed_step_still_puts_back_only_what_it_touched(home, monkeypatch, asked):
    mg = _mg()
    _fill(home)
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg, fail_at=2))
    (home / "owners-own-file.txt").write_text("hers", encoding="utf-8")
    r = mg.run_pending(home)
    assert r["safe_mode"] and (home / "notes.txt").read_text(encoding="utf-8") == "before"
    copy = home.parent / _siblings(home, ".pre-migration-")[0]
    assert (copy / "memory" / "facts.jsonl").is_file()                        # the whole folder was copied
    assert (home / "models" / "big.bin").is_file()                            # and nothing else was replaced


def test_too_little_free_disk_changes_nothing_and_says_how_much_room(home, monkeypatch, asked):
    mg = _mg()
    _fill(home)
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    import shutil as _sh
    monkeypatch.setattr(_sh, "disk_usage", lambda p: _sh._ntuple_diskusage(10**12, 10**12 - 1000, 1000))
    r = mg.run_pending(home)
    assert r["ok"] is False and r["safe_mode"] is True and "free disk" in r["why"]
    assert mg.current(home) == 0 and (home / "notes.txt").read_text(encoding="utf-8") == "before"
    assert not _siblings(home, ".pre-migration-") and not _siblings(home, ".migration-journal")
    assert asked == [(True, "migration_failed")]
    from agent_friday.services import upgrade_review
    items = [it for it in upgrade_review.listing(home) if it["code"] == "migration_no_room"]
    assert items and ("MB" in json.dumps(items) or "GB" in json.dumps(items))


def test_a_copy_that_fails_part_way_is_removed_and_changes_nothing(home, monkeypatch, asked):
    mg = _mg()
    _fill(home)
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    import shutil as _sh
    real, calls = _sh.copy2, []

    def flaky(src, dst, *a, **k):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(_sh, "copy2", flaky)
    r = mg.run_pending(home)
    assert r["ok"] is False and r["safe_mode"] is True and len(calls) >= 2
    assert not _siblings(home, ".pre-migration-") and mg.current(home) == 0
    assert asked == [(True, "migration_failed")]
    from agent_friday.services import upgrade_review
    assert any(it["code"] == "migration_copy_failed" for it in upgrade_review.listing(home))


def test_a_link_inside_the_folder_is_left_out_of_the_copy_and_counted(home, monkeypatch, tmp_path):
    mg = _mg()
    _fill(home)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "not-hers.txt").write_text("x", encoding="utf-8")
    try:
        (home / "memory" / "through").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this machine does not let a test make a link")
    monkeypatch.setattr(mg, "MIGRATIONS", [mg.Migration(1, "one", ("notes.txt",), lambda h: None, lambda h: True)])
    r = mg.run_pending(home)
    assert r["ok"]
    copy = home.parent / _siblings(home, ".pre-migration-")[0]
    assert not (copy / "memory" / "through").exists() and (copy / "memory" / "facts.jsonl").is_file()
    assert _receipts(home, "receipt-0-to-1-*.json")[0].get("links_left_out") == 1


def test_the_newest_two_copies_are_kept(home, monkeypatch):
    mg = _mg()
    for n in range(1, 5):
        monkeypatch.setattr(mg, "MIGRATIONS", [mg.Migration(i, "m%d" % i, ("notes.txt",), lambda h: None,
                                                            lambda h: True) for i in range(1, n + 1)])
        assert mg.run_pending(home)["ok"]
    assert len(_siblings(home, ".pre-migration-")) == 2


def test_this_runs_copy_is_never_pruned_and_a_partial_copy_is_cleared(home, monkeypatch):
    mg = _mg()
    for name in ("9-20991231-235959-aaaaaa", "8-20991231-235958-bbbbbb"):    # made "later" by name
        (home.parent / (home.name + mg.BACKUP_INFIX + name)).mkdir()
    left = home.parent / (home.name + mg.BACKUP_INFIX + "10-20260101-000000-cccccc.partial")
    left.mkdir()
    (left / "settings.json").write_text("half", encoding="utf-8")
    monkeypatch.setattr(mg, "MIGRATIONS", [mg.Migration(10, "ten", ("notes.txt",), lambda h: None,
                                                        lambda h: True)])
    r = mg.run_pending(home)
    assert r["ok"] and r["backup"] and (home.parent / r["backup"]).is_dir()   # the copy this run made
    assert not left.exists()
    assert len(_siblings(home, mg.BACKUP_INFIX)) == mg.KEEP_BACKUPS == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Windows gets an owner-only access list")
def test_the_copy_is_readable_by_this_account_only(home, monkeypatch):
    mg = _mg()
    os.chmod(home / "settings.json", 0o644)
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg))
    assert mg.run_pending(home)["ok"]
    copy = home.parent / _siblings(home, ".pre-migration-")[0]
    assert copy.stat().st_mode & 0o077 == 0


# ── §37.3-8: the history an upgrade carries forward is checked ───────────────

def _prov(home: Path, lines: list) -> None:
    import hashlib
    d = home / "provenance"
    d.mkdir(exist_ok=True)
    prev, out = "0" * 64, []
    for i, bad in enumerate(lines):
        line = json.dumps({"ts": "t", "content_hash": "sha256:%d" % i, "prev": "f" * 64 if bad else prev})
        out.append(line)
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    (d / "ledger.jsonl").write_text("\n".join(out) + "\n", encoding="utf-8")


def _flush(mg, home, monkeypatch, report):
    from agent_friday.services import action_ledger
    got = []
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: got.append((kind, rec)) or {})
    monkeypatch.setattr(action_ledger, "verify", lambda **k: report)
    assert mg.flush_receipts(home) == 1
    return got[0][1]


def test_an_upgrades_receipt_carries_the_checked_history(home, monkeypatch):
    mg = _mg()
    assert hasattr(mg, "history_check"), "an upgrade never checks the history it carries forward"
    _prov(home, [False, False, False])
    mg.run_pending(home)
    rec = _flush(mg, home, monkeypatch, {"ok": True, "records": 12, "problems": []})
    assert rec["history"] == {"receipts": {"records": 12, "open_problems": 0},
                              "provenance": {"entries": 3, "broken_links": 0, "unreadable": 0}}
    from agent_friday.services import upgrade_review
    assert not [i for i in upgrade_review.listing(home) if i["code"].startswith("history_")]


def test_a_broken_history_is_said_on_home_and_nothing_is_changed(home, monkeypatch):
    mg = _mg()
    assert hasattr(mg, "history_check"), "an upgrade never checks the history it carries forward"
    _prov(home, [False, True, False])
    with open(home / "provenance" / "ledger.jsonl", "a", encoding="utf-8") as f:
        f.write("{not json\n")
    before = (home / "provenance" / "ledger.jsonl").read_bytes()
    mg.run_pending(home)
    rec = _flush(mg, home, monkeypatch, {"ok": False, "records": 5,
                                         "problems": [{"acknowledged": True}, {"acknowledged": False}]})
    assert rec["history"]["receipts"] == {"records": 5, "open_problems": 1}
    assert rec["history"]["provenance"] == {"entries": 4, "broken_links": 1, "unreadable": 1}
    assert (home / "provenance" / "ledger.jsonl").read_bytes() == before          # read only
    from agent_friday.services import upgrade_review
    codes = sorted(i["code"] for i in upgrade_review.listing(home) if i["code"].startswith("history_"))
    assert codes == ["history_provenance", "history_receipts"]
    assert "sha256:" not in json.dumps(rec["history"])                             # counts only


def test_a_history_that_cannot_be_checked_is_said_and_checked_once(home, monkeypatch):
    mg = _mg()
    assert hasattr(mg, "history_check"), "an upgrade never checks the history it carries forward"
    mg.run_pending(home)
    from agent_friday.services import action_ledger

    def boom(**k):
        raise RuntimeError("no key")
    monkeypatch.setattr(action_ledger, "verify", boom)
    monkeypatch.setattr(action_ledger, "try_append", lambda *a, **k: None)          # the ledger is not ready
    assert mg.flush_receipts(home) == 0
    ran = []
    monkeypatch.setattr(action_ledger, "verify", lambda **k: ran.append(1) or {"records": 0, "problems": []})
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: {})
    assert mg.flush_receipts(home) == 1 and ran == []                               # not checked twice
    rec = _receipts(home, "receipt-0-to-*.json")[0]
    assert rec["history"]["receipts"] == {"unchecked": "RuntimeError"}


def test_the_open_problem_count_is_the_ledgers_own_and_no_key_is_unchecked(home, monkeypatch):
    mg = _mg()
    assert hasattr(mg, "history_check"), "an upgrade never checks the history it carries forward"
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"records": 900, "problems": [{}] * 50,
                                                              "open_problem_count": 75, "key_available": True})
    assert mg.history_check(home)["receipts"] == {"records": 900, "open_problems": 75}    # not the 50 listed
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"records": 0, "problems": [{"segment": "key"}],
                                                              "key_available": False})
    assert mg.history_check(home)["receipts"] == {"unchecked": "no_key"}


def test_a_provenance_line_still_being_written_is_not_judged(home, monkeypatch):
    mg = _mg()
    assert hasattr(mg, "history_check"), "an upgrade never checks the history it carries forward"
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"records": 0, "problems": []})
    _prov(home, [False, False])
    with open(home / "provenance" / "ledger.jsonl", "a", encoding="utf-8") as f:
        f.write('{"ts": "t", "prev": "')                                         # no newline yet
    assert mg.history_check(home)["provenance"] == {"entries": 2, "broken_links": 0, "unreadable": 0}
