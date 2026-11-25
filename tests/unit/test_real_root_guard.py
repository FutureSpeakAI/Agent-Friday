"""A test run cannot write to the owner's real data root (pytest_real_root_guard). A STAND-IN ROOT IN A TEMP DIRECTORY ONLY.

Every case declares a directory under tmp_path as "the real root" (the guard's mechanics are the same for any root) and then does
what a leaking test would do. What each check bites on:
  1. every write-class operation under the root is refused (open for write or append, Path.write_text, remove, rename, rmdir,
     mkdir, copy, move, rmtree, sqlite3.connect) and the root's digest is the same afterwards;
  2. reads under the root are allowed, and writes anywhere else are allowed;
  3. a refusal swallowed by `except Exception: pass` (the codebase's "best effort" shape) is still recorded, and the per-test
     check turns it into a failing test that names the path;
  4. snapshot() changes when a file is added, changed or removed, and not otherwise;
  5. the roots travel in the environment, so a worker that inherits a redirected home still guards the real one;
(The suite's own configuration, a write to the real ~/.friday refused, is held by tests/unit/test_real_root_guard_is_active.py.)
"""
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

import pytest_real_root_guard as guard


@pytest.fixture
def real(tmp_path):
    root = tmp_path / "real_root"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "page.md").write_text("an existing page", encoding="utf-8")
    (root / "trust_graph.json").write_text("{}", encoding="utf-8")
    previous = guard.set_roots([root])
    seen = len(guard.VIOLATIONS)
    yield root
    guard.set_roots(previous)
    del guard.VIOLATIONS[seen:]


def _attempts(root):
    return {
        "open for write": lambda: open(root / "new.txt", "w"),
        "open for append": lambda: open(root / "wiki" / "page.md", "a"),
        "open read-write": lambda: open(root / "trust_graph.json", "r+"),
        "Path.write_text": lambda: (root / "wiki" / "page.md").write_text("changed", encoding="utf-8"),
        "os.remove": lambda: os.remove(root / "trust_graph.json"),
        "os.rename": lambda: os.rename(root / "trust_graph.json", root / "moved.json"),
        "os.mkdir": lambda: os.mkdir(root / "newdir"),
        "os.rmdir": lambda: os.rmdir(root / "wiki"),
        "shutil.copyfile": lambda: shutil.copyfile(__file__, root / "copied.py"),
        "shutil.move": lambda: shutil.move(str(root / "trust_graph.json"), str(root / "moved.json")),
        "shutil.rmtree": lambda: shutil.rmtree(root / "wiki"),
        "sqlite3.connect": lambda: sqlite3.connect(root / "store.db"),
        "os.open O_CREAT": lambda: os.open(root / "made.txt", os.O_CREAT | os.O_WRONLY),
        "os.replace": lambda: os.replace(root / "trust_graph.json", root / "moved.json"),
        "os.makedirs": lambda: os.makedirs(root / "a" / "b"),
        "os.truncate": lambda: os.truncate(root / "trust_graph.json", 0),
        "Path.touch existing": lambda: (root / "trust_graph.json").touch(),
        "os.utime": lambda: os.utime(root / "trust_graph.json", (1, 1)),
        "os.chmod": lambda: os.chmod(root / "trust_graph.json", 0o444),
    }


@pytest.mark.parametrize("name", list(_attempts(Path("x")).keys()))
def test_every_write_under_the_root_is_refused_and_the_root_is_unchanged(real, name):
    before = guard.snapshot(real)
    with pytest.raises(guard.RealDataRootWrite):
        _attempts(real)[name]()
    assert guard.snapshot(real) == before
    assert guard.VIOLATIONS and guard.VIOLATIONS[-1][1].startswith(os.path.normcase(os.path.realpath(real)))


def test_reads_under_the_root_and_writes_elsewhere_are_allowed(real, tmp_path):
    assert (real / "wiki" / "page.md").read_text(encoding="utf-8") == "an existing page"
    assert list(real.glob("*.json"))
    start = len(guard.VIOLATIONS)
    other = tmp_path / "elsewhere.txt"
    other.write_text("fine", encoding="utf-8")
    shutil.copyfile(other, tmp_path / "copy.txt")
    assert len(guard.VIOLATIONS) == start, "nothing outside the root was refused or recorded"


def test_a_refusal_swallowed_by_best_effort_code_still_fails_the_test(real):
    start = len(guard.VIOLATIONS)
    try:
        (real / "wiki" / "leaked.md").write_text("Test intel about a person", encoding="utf-8")
    except Exception:
        pass
    assert not (real / "wiki" / "leaked.md").exists()
    assert len(guard.VIOLATIONS) == start + 1, "the swallowed refusal is still on record"
    guard._baseline[0] = start
    try:
        with pytest.raises(pytest.fail.Exception) as e:
            guard.pytest_runtest_teardown(None)
        assert "leaked.md" in str(e.value)
    finally:
        guard._baseline[0] = len(guard.VIOLATIONS)


def test_a_refusal_recorded_between_tests_fails_the_next_setup(real):
    """A write refused at import, collection, in an earlier test's leftovers or a background thread is not wiped when the
    next test starts: that test's setup fails and names the path."""
    start = len(guard.VIOLATIONS)
    guard._baseline[0] = start
    try:
        (real / "between.md").write_text("x", encoding="utf-8")
    except Exception:
        pass
    try:
        with pytest.raises(pytest.fail.Exception) as e:
            guard.pytest_runtest_setup(None)
        assert "between.md" in str(e.value)
    finally:
        guard._baseline[0] = len(guard.VIOLATIONS)


def test_a_refusal_left_at_the_end_of_the_run_fails_the_session(real):
    """After the last test (session finish, atexit-style writers) nothing is attributed to a test, so the run fails."""
    import types
    start = len(guard.VIOLATIONS)
    guard._baseline[0] = start
    try:
        (real / "late.md").write_text("x", encoding="utf-8")
    except Exception:
        pass
    session = types.SimpleNamespace(exitstatus=0)
    try:
        guard.pytest_sessionfinish(session, 0)
        assert session.exitstatus != 0, "a refusal after the last test left the run green"
    finally:
        guard._baseline[0] = len(guard.VIOLATIONS)


def test_a_refusal_after_a_workers_last_test_fails_the_whole_xdist_run(real):
    """Under xdist a worker's own exit status never reaches the run (the controller only reads its workeroutput, and only a
    keyboard interrupt there stops the run). So the worker hands its unreported refusals over in workeroutput (sent by
    xdist after every sessionfinish impl has run), the controller takes them in pytest_testnodedown and fails its own
    session. The worker and controller halves are driven with the exact shapes xdist uses."""
    import types
    guard._baseline[0] = len(guard.VIOLATIONS)
    try:
        (real / "late.md").write_text("x", encoding="utf-8")
    except Exception:
        pass
    worker = types.SimpleNamespace(exitstatus=0, config=types.SimpleNamespace(workeroutput={"exitstatus": 0}))
    try:
        guard.pytest_sessionfinish(worker, 0)
    finally:
        guard._baseline[0] = len(guard.VIOLATIONS)
    handed = {k: v for k, v in worker.config.workeroutput.items() if k != "exitstatus" and v}
    assert handed, "the worker kept its refusal to itself: xdist would end the run green"

    controller = types.SimpleNamespace(exitstatus=0, config=types.SimpleNamespace())
    node = types.SimpleNamespace(workeroutput=worker.config.workeroutput, gateway=types.SimpleNamespace(id="gw1"))
    try:
        guard.pytest_testnodedown(node, None)
        guard.pytest_sessionfinish(controller, 0)
    finally:
        guard._from_workers.clear()
    assert controller.exitstatus != 0, "a worker's refusal after its last test left the xdist run green"


def test_snapshot_changes_exactly_when_the_tree_does(real):
    a = guard.snapshot(real)
    assert guard.snapshot(real) == a
    guard.set_roots([])
    (real / "wiki" / "new.md").write_text("x", encoding="utf-8")
    b = guard.snapshot(real)
    assert b != a
    (real / "wiki" / "new.md").unlink()
    (real / "trust_graph.json").write_text("{ }", encoding="utf-8")
    assert guard.snapshot(real) not in (a, b)


def test_the_roots_travel_in_the_environment_for_workers_and_child_runs():
    assert os.environ.get(guard.ENV_ROOTS) is not None
    assert guard._capture_roots() == [r for r in os.environ[guard.ENV_ROOTS].split(os.pathsep) if r]

