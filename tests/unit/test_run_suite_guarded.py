"""The guarded suite runner: floors, the lock, the worker cap, and a receipt
written from pytest's real exit code.

The end-to-end cases run the script as a child process against a throwaway
test file, with a private config that zeroes the floors and keeps the lock
and receipts under ``tmp_path``, so they never touch the machine's real lock.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import pytest_resource_guard as guard

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_suite_guarded.py"

spec = importlib.util.spec_from_file_location("run_suite_guarded", SCRIPT)
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def test_defaults_come_from_the_pytest_plugin():
    assert rs.DEFAULTS["min_free_ram_gb"] == guard.MIN_FREE_RAM_GB
    assert rs.DEFAULTS["min_free_disk_gb"] == guard.MIN_FREE_DISK_GB
    assert rs.DEFAULTS["max_workers_without_seat"] == guard.MAX_WORKERS
    assert rs.DEFAULTS["max_workers_with_seat"] == 2
    assert rs.DEFAULTS["seat_port"] == 8090


@pytest.mark.parametrize("requested,seat,expected", [
    (None, True, 2), (None, False, 2), (4, True, 2), (4, False, 2), (1, True, 1), (0, True, 0),
])
def test_workers_are_capped_by_the_seat_rule(requested, seat, expected):
    assert rs.worker_count(requested, seat, rs.DEFAULTS) == expected


def test_a_higher_cap_without_the_seat_is_a_config_choice():
    cfg = dict(rs.DEFAULTS, max_workers_without_seat=3)
    assert rs.worker_count(None, False, cfg) == 3
    assert rs.worker_count(None, True, cfg) == 2


def test_seat_probe_is_false_on_a_closed_port():
    assert rs.seat_up(1) is False


def test_lock_states(tmp_path):
    lock = tmp_path / "SUITE_LOCK"
    assert rs.lock_state(lock)[0] == "free"
    lock.write_text(f"holder: me\npid: {os.getpid()}\n", encoding="utf-8")
    assert rs.lock_state(lock)[0] == "held"
    lock.write_text("holder: gone\npid: 999999999\n", encoding="utf-8")
    assert rs.lock_state(lock)[0] == "stale"
    lock.write_text("holder: program-lead-1\nsession: program-lead\n", encoding="utf-8")
    assert rs.lock_state(lock)[0] == "held", "a lock without a pid is not evidence its holder stopped"


def test_release_only_by_ownership(tmp_path):
    lock = tmp_path / "SUITE_LOCK"
    assert rs.claim_lock(lock, "mine", "s", tmp_path, ["pytest"])
    assert not rs.claim_lock(lock, "other", "s", tmp_path, ["pytest"])
    assert rs.release_lock(lock, "other") is False and lock.exists()
    assert rs.release_lock(lock, "mine") is True and not lock.exists()


def _config(tmp_path, **over):
    cfg = {"min_free_ram_gb": 0, "min_free_disk_gb": 0, "abort_free_ram_gb": 0, "abort_free_disk_gb": 0,
           "suite_lock": str(tmp_path / "SUITE_LOCK"), "receipts_dir": str(tmp_path / "receipts"),
           "max_workers_with_seat": 0, "max_workers_without_seat": 0, "seat_port": 1}
    cfg.update(over)
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def _run(tmp_path, cfg_path, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--session", "test", *args],
                          capture_output=True, text=True, timeout=300, cwd=str(ROOT))


def _receipts(tmp_path):
    return list((tmp_path / "receipts").glob("*/suite.json"))


def test_a_failing_run_writes_a_not_ok_receipt_with_the_real_exit_code(tmp_path):
    t = tmp_path / "test_red.py"
    t.write_text("def test_red():\n    assert 1 == 2\n", encoding="utf-8")
    p = _run(tmp_path, _config(tmp_path), str(t))
    assert p.returncode == 1, p.stderr + p.stdout
    [receipt] = _receipts(tmp_path)
    rec = json.loads(receipt.read_text(encoding="utf-8"))
    assert rec["ok"] is False and rec["exit_code"] == 1 and rec["check"] == "suite"
    assert rec["lock_released"] is True and not (tmp_path / "SUITE_LOCK").exists()
    assert "1 failed" in Path(rec["log"]).read_text(encoding="utf-8")


def test_a_passing_run_writes_an_ok_receipt_and_exits_0(tmp_path):
    t = tmp_path / "test_green.py"
    t.write_text("def test_green():\n    assert True\n", encoding="utf-8")
    p = _run(tmp_path, _config(tmp_path), str(t))
    assert p.returncode == 0, p.stderr + p.stdout
    [receipt] = _receipts(tmp_path)
    rec = json.loads(receipt.read_text(encoding="utf-8"))
    assert rec["ok"] is True and rec["exit_code"] == 0 and rec["aborted"] is None
    assert len(rec["tree"]) == 40


def test_below_a_floor_nothing_runs_and_no_receipt_is_written(tmp_path):
    p = _run(tmp_path, _config(tmp_path, min_free_ram_gb=100000), "tests/unit/test_run_suite_guarded.py")
    assert p.returncode == rs.EXIT_REFUSED_FLOOR
    assert "not starting" in p.stderr and "floor 100000 GB" in p.stderr
    assert _receipts(tmp_path) == []


def test_a_held_lock_refuses_and_a_stale_one_is_taken_over(tmp_path):
    lock = tmp_path / "SUITE_LOCK"
    lock.write_text(f"holder: someone\nsession: other\npid: {os.getpid()}\n", encoding="utf-8")
    p = _run(tmp_path, _config(tmp_path), "tests/unit/test_run_suite_guarded.py")
    assert p.returncode == rs.EXIT_REFUSED_LOCK and "held by someone" in p.stderr
    assert lock.read_text(encoding="utf-8").startswith("holder: someone")
    lock.write_text("holder: dead\npid: 999999999\n", encoding="utf-8")
    t = tmp_path / "test_green.py"
    t.write_text("def test_green():\n    assert True\n", encoding="utf-8")
    p = _run(tmp_path, _config(tmp_path), str(t))
    assert p.returncode == 0 and "stale lock" in p.stderr
    assert not lock.exists()
