"""The test-run resource guard: at most two workers, and no broad run without room.

Parallel full suites have exhausted memory and disk on the owner's machine and
left the live Friday unable to answer. These tests pin the floors and prove the
guard is loaded for every pytest invocation from the repository root.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import pytest_resource_guard as g

ROOT = Path(__file__).resolve().parents[2]


def test_the_floors_are_the_agreed_ones():
    assert g.MAX_WORKERS == 2
    assert g.MIN_FREE_RAM_GB == 12.0
    assert g.MIN_FREE_DISK_GB == 20.0


@pytest.mark.parametrize("requested,expected", [(4, 2), (3, 2), (2, 2), (1, 1), (0, 0),
                                                ("auto", "auto"), (None, None)])
def test_worker_requests_are_capped(requested, expected):
    assert g.capped_workers(requested) == expected


@pytest.fixture
def not_ci(monkeypatch):
    """The guard's behaviour on a developer machine, whatever runs this test."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def test_auto_resolves_to_the_cap(not_ci):
    assert g.pytest_xdist_auto_num_workers(None) == 2


@pytest.mark.parametrize("args,broad", [
    ([], True),
    (["tests/unit", "tests/api"], True),
    (["tests"], True),
    (["tests/unit/test_resource_guard.py"], False),
    (["tests/unit/test_resource_guard.py::test_the_floors_are_the_agreed_ones"], False),
    (["-q", "tests/unit/test_resource_guard.py"], False),
])
def test_broad_runs_are_told_from_targeted_ones(args, broad):
    assert g.is_broad_run(args, ROOT) is broad


def test_a_broad_run_is_refused_below_either_floor_and_says_which():
    low_ram = g.refusal(3.1, 40.0)
    assert low_ram and "3.1 GB" in low_ram and "12 GB" in low_ram and "disk" not in low_ram
    low_disk = g.refusal(20.0, 3.7)
    assert low_disk and "3.7 GB" in low_disk and "20 GB" in low_disk
    assert g.refusal(12.0, 20.0) is None
    assert g.refusal(None, None) is None


def _config(args, n):
    return SimpleNamespace(option=SimpleNamespace(numprocesses=n), args=args, rootpath=ROOT)


def test_configure_caps_workers_and_stops_a_broad_run_without_room(monkeypatch, not_ci):
    monkeypatch.setattr(g, "free_ram_gb", lambda: 2.0)
    monkeypatch.setattr(g, "free_disk_gb", lambda p=None: 50.0)
    cfg = _config(["tests/unit", "tests/api"], 4)
    with pytest.raises(pytest.exit.Exception) as ei:
        g.pytest_configure(cfg)
    assert cfg.option.numprocesses == 2
    assert "free memory is 2.0 GB" in str(ei.value)


def test_configure_lets_a_targeted_run_through_whatever_the_room(monkeypatch, not_ci):
    monkeypatch.setattr(g, "free_ram_gb", lambda: 0.4)
    monkeypatch.setattr(g, "free_disk_gb", lambda p=None: 1.0)
    cfg = _config(["tests/unit/test_resource_guard.py"], 4)
    g.pytest_configure(cfg)
    assert cfg.option.numprocesses == 2


def test_only_a_github_runner_is_exempt_and_only_with_both_variables():
    assert g.on_github_actions({"CI": "true", "GITHUB_ACTIONS": "true"}) is True
    assert g.on_github_actions({"CI": "1", "GITHUB_ACTIONS": "True"}) is True
    assert g.on_github_actions({"CI": "true"}) is False, "CI alone is easy to set by hand"
    assert g.on_github_actions({"GITHUB_ACTIONS": "true"}) is False
    assert g.on_github_actions({"CI": "false", "GITHUB_ACTIONS": "true"}) is False
    assert g.on_github_actions({}) is False


def test_on_a_github_runner_the_guard_neither_caps_nor_refuses(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(g, "free_ram_gb", lambda: 2.0)
    monkeypatch.setattr(g, "free_disk_gb", lambda p=None: 1.0)
    assert g.pytest_xdist_auto_num_workers(None) is None
    cfg = _config(["tests/unit", "tests/api"], 4)
    g.pytest_cmdline_main(cfg)
    g.pytest_configure(cfg)  # would raise pytest.exit on a developer machine
    assert cfg.option.numprocesses == 4


def test_the_root_conftest_loads_the_guard_for_every_invocation():
    text = (ROOT / "conftest.py").read_text(encoding="utf-8")
    assert 'pytest_plugins = ["pytest_resource_guard"]' in text


def test_a_real_run_asking_for_four_workers_gets_two(tmp_path):
    """End to end: the cap must bite before xdist builds its workers."""
    import subprocess
    import sys
    probe = ROOT / "tests" / "unit" / "test_resource_guard.py"
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")}
    out = subprocess.run(
        [sys.executable, "-m", "pytest", f"{probe}::test_the_floors_are_the_agreed_ones",
         "-n", "4", "-p", "no:cacheprovider", "-o", "addopts="],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300, env=env)
    text = out.stdout + out.stderr
    assert out.returncode == 0, text[-2000:]
    assert "2 workers" in text and "4 workers" not in text, text[-2000:]
