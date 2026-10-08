"""The guarded suite runner: floors, the lock, the worker cap, and a receipt
written from pytest's real exit code.

The end-to-end cases run the script as a child process against a throwaway
test file, with a private config that zeroes the floors and keeps the lock
and receipts under ``tmp_path``, so they never touch the machine's real lock.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import pytest_resource_guard as guard

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_suite_guarded.py"

spec = importlib.util.spec_from_file_location("run_suite_guarded", SCRIPT)
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


@pytest.fixture(autouse=True)
def isolated_guard_config(monkeypatch):
    """Use this module's synthetic configs, never the outer suite's live one.

    The outer guard retains its own environment, lock and resource floors.
    """
    monkeypatch.delenv("FRIDAY_GUARD_CONFIG", raising=False)


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
    return subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--session", "test",
                           "--", "--rootdir", str(tmp_path), "--confcutdir", str(tmp_path), *args],
                          capture_output=True, text=True, timeout=300, cwd=str(ROOT))


def _receipts(tmp_path):
    return list((tmp_path / "receipts").glob("*/suite.json"))


def test_zero_workers_overrides_pytest_worker_options(tmp_path, monkeypatch):
    cfg = dict(rs.DEFAULTS, suite_lock=str(tmp_path / "SUITE_LOCK"),
               receipts_dir=str(tmp_path / "receipts"),
               max_workers_with_seat=0, max_workers_without_seat=0)
    commands = []
    monkeypatch.setattr(rs, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(rs, "floor_refusal", lambda cfg, tree: None)
    monkeypatch.setattr(rs, "seat_up", lambda port: False)
    monkeypatch.setattr(rs, "git_sha", lambda tree: "a" * 40)
    monkeypatch.setattr(guard, "free_ram_gb", lambda: 64)
    monkeypatch.setattr(guard, "free_disk_gb", lambda tree: 100)
    monkeypatch.setattr(rs, "run_pytest",
                        lambda cmd, tree, log, cfg: commands.append(cmd) or (0, None, ""))
    assert rs.main(["--tree", str(tmp_path), "--", "-n", "auto", "tests/unit"]) == 0
    assert commands[0][-2:] == ["-n", "0"]
    [receipt] = _receipts(tmp_path)
    rec = json.loads(receipt.read_text(encoding="utf-8"))
    assert rec["workers"] == 0 and rec["cmd"][-2:] == ["-n", "0"]


@pytest.mark.parametrize("failure", ["encoding", "closed-pipe"])
def test_console_failure_does_not_stop_log_or_child_output_drain(tmp_path, monkeypatch, failure):
    lines = ["Unicode snowman: \u2603\n", "Second line.\n", "Last line.\n"]
    child = SimpleNamespace(stdout=iter(lines), poll=lambda: 7, wait=lambda: 7)

    class Console:
        encoding = "ascii"

        def write(self, text):
            if failure == "encoding":
                text.encode("ascii")
            return len(text)

        def flush(self):
            if failure == "closed-pipe":
                raise BrokenPipeError("The console output pipe closed")

    monkeypatch.setattr(rs, "sys", SimpleNamespace(stdout=Console()))
    monkeypatch.setattr(rs, "subprocess", SimpleNamespace(Popen=lambda *a, **kw: child,
                                                         PIPE=subprocess.PIPE,
                                                         STDOUT=subprocess.STDOUT))
    log = tmp_path / "suite.log"
    rc, abort, tail = rs.run_pytest(["synthetic-pytest"], tmp_path, log, rs.DEFAULTS)
    assert rc == 7 and abort is None
    assert tail == "".join(lines)
    assert log.read_text(encoding="utf-8") == "".join(lines) + "EXIT=7\n"
    assert list(child.stdout) == [], "every child output line must be consumed"


def test_console_encoding_cannot_drop_child_output_or_deadlock(tmp_path, monkeypatch):
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    log = tmp_path / "suite.log"
    cfg = dict(rs.DEFAULTS, abort_free_ram_gb=0, abort_free_disk_gb=0)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", console)
        code, aborted, tail = rs.run_pytest(
            [sys.executable, "-c", "print('\\u21b5'); print('FINISHED')"],
            tmp_path, log, cfg, poll_s=.05)
    assert code == 0 and aborted is None
    assert "\u21b5\nFINISHED\n" in log.read_text(encoding="utf-8")
    assert "FINISHED" in tail


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
    assert rec["cmd"][-2:] == ["-n", "0"]
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
