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
    asked = []
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "set_next_start", lambda on: asked.append(on) or {})
    h.asked = asked
    return h


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
def test_a_failed_step_puts_the_files_back_and_this_start_is_safe_mode(home, monkeypatch, how):
    mg = _mg()
    monkeypatch.setattr(mg, "MIGRATIONS", _fakes(mg, fail_at=2 if how == "raises" else None,
                                                 bad_check_at=2 if how == "check" else None))
    r = mg.run_pending(home)
    assert r["ok"] is False and r["safe_mode"] is True and r["failed"] == 2
    assert (home / "notes.txt").read_text(encoding="utf-8") == "before"     # step 1's change undone too
    assert mg.current(home) == 0 and not (home / "schema.json").exists()
    assert home.asked == []                          # this start only: the next start tries again
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
