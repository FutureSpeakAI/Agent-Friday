"""No test writes to the owner's real data root.

tests/conftest.py points the home at a throwaway directory, but that is a convention: a module that computed its path before the
redirect, a path built from a literal home, a subprocess or a script run outside the fixtures all reach the real `~/.friday`
(its wiki, trust graph, contacts, memory) without any test noticing, and the records they leave stay for good. This plugin makes
it impossible instead of unlikely:

* The real roots are captured BEFORE any redirect (this plugin is loaded from the root conftest, ahead of tests/conftest.py):
  `~/.friday` and a `FRIDAY_HOME` set in the shell. They travel to xdist workers and child pytest runs in the environment
  (`_FRIDAY_REAL_ROOTS`), so a worker that inherits a redirected home still guards the real one.
* A `sys.addaudithook` hook refuses every write-class operation (open for writing, remove, rename, rmdir, mkdir, truncate,
  symlink, link, copy, move, rmtree, utime, chmod, chown, sqlite3.connect) whose path is under a real root, issued by Python
  code in this process, and records it. Reads are allowed. Not seen: writes made by native code that bypasses Python's I/O (a
  C extension opening its own files) and by child processes that are not pytest runs of this suite.
* The refusal is an exception, which code written as "best effort" can swallow. So every refusal is also recorded and
  reported: by the test that was running (it FAILS in teardown), by the next test's setup when it happened between tests
  (import, collection, a background thread), or by the session when it happened after the last test (under xdist the
  worker hands it to the controller, which fails the run). Refused but NOT reported: a write after session finish (atexit,
  unconfigure). Not matched: a path given relative to a directory handle (dir_fd) and a sqlite "file:" URI.
* `snapshot(root)` is a digest of a tree (relative path, size, mtime) for tests and for a before/after check of a root.

Off only when FRIDAY_ALLOW_REAL_ROOT_WRITES=1 is in the environment (a deliberate, visible choice for a live run).
Held by tests/unit/test_real_root_guard.py.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

ENV_ROOTS = "_FRIDAY_REAL_ROOTS"
ENV_ALLOW = "FRIDAY_ALLOW_REAL_ROOT_WRITES"

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
#: audit events whose first path-like argument is a destination that changes; `shutil.*` events name (src, dst) and both count.
_PATH_EVENTS = {
    "os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,), "os.truncate": (0,), "os.rename": (0, 1), "os.symlink": (1,),
    "os.link": (1,), "shutil.copyfile": (1,), "shutil.copymode": (1,), "shutil.copystat": (1,), "shutil.copytree": (1,),
    "shutil.rmtree": (0,), "shutil.move": (0, 1), "sqlite3.connect": (0,),
    "os.utime": (0,), "os.chmod": (0,), "os.chown": (0,),
}


class RealDataRootWrite(PermissionError):
    """A write under the owner's real data root during a test run."""


VIOLATIONS: list[tuple[str, str]] = []
_roots: list[str] = []
_installed = False
_baseline = [0]


def _norm(path) -> str | None:
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fsdecode(path))))
    except (TypeError, ValueError):
        return None


def _capture_roots() -> list[str]:
    """The real roots, captured once and inherited through the environment."""
    inherited = os.environ.get(ENV_ROOTS)
    if inherited is not None:
        return [r for r in inherited.split(os.pathsep) if r]
    candidates = [Path.home() / ".friday"]
    shell_home = os.environ.get("FRIDAY_HOME")
    if shell_home:
        candidates.append(Path(os.path.expanduser(shell_home)))
    scratch = _norm(tempfile.gettempdir()) or ""
    roots = []
    for c in candidates:
        n = _norm(c)
        if n and not (scratch and (n == scratch or n.startswith(scratch + os.sep))) and n not in roots:
            roots.append(n)
    os.environ[ENV_ROOTS] = os.pathsep.join(roots)
    return roots


def set_roots(roots) -> list[str]:
    """Replace the guarded roots (tests of the guard itself); returns the previous ones."""
    previous = list(_roots)
    _roots[:] = [n for n in (_norm(r) for r in roots) if n]
    return previous


def _under_a_root(path) -> str | None:
    n = _norm(path) if not isinstance(path, int) else None
    if not n:
        return None
    for root in _roots:
        if n == root or n.startswith(root + os.sep):
            return n
    return None


def _hook(event, args):
    if not _roots:
        return
    hit = None
    if event == "open":
        if len(args) >= 3 and isinstance(args[2], int) and args[2] & _WRITE_FLAGS:
            hit = _under_a_root(args[0])
    else:
        positions = _PATH_EVENTS.get(event)
        if positions:
            for i in positions:
                if i < len(args) and (hit := _under_a_root(args[i])):
                    break
    if hit:
        VIOLATIONS.append((event, hit))
        raise RealDataRootWrite("%s refused: %s is under the owner's real data root; a test works in its own temporary home"
                                % (event, hit))


def install() -> None:
    global _installed
    if _installed or os.environ.get(ENV_ALLOW) == "1":
        return
    _roots[:] = _capture_roots()
    sys.addaudithook(_hook)
    _installed = True


def snapshot(root) -> str:
    """A digest of a tree: every file's relative path, size and modification time, in order. A root a test run left alone
    has the same digest before and after."""
    base = Path(root)
    h = hashlib.sha256()
    if not base.exists():
        return h.hexdigest()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            h.update(("%s\0%d\0%d\n" % (p.relative_to(base).as_posix(), st.st_size, st.st_mtime_ns)).encode("utf-8", "replace"))
    return h.hexdigest()


install()


def _unreported() -> list:
    """Refusals recorded since the last check. Nothing is ever dropped unreported: setup, teardown and session finish each
    report what is new and move the mark (clamped, since a test of the guard may remove its own records)."""
    start = min(_baseline[0], len(VIOLATIONS))
    new = VIOLATIONS[start:]
    _baseline[0] = len(VIOLATIONS)
    return new


def _listed(new) -> str:
    return "%d refused: %s" % (len(new), "; ".join("%s %s" % pair for pair in new[:5]))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    new = _unreported()
    if new:
        raise pytest.fail.Exception(
            "a write under the owner's real data root was refused BEFORE this test ran (at import, collection, in an "
            "earlier test's leftovers or a background thread): " + _listed(new), pytrace=False)


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item):
    new = _unreported()
    if new:
        raise pytest.fail.Exception("this test wrote under the owner's real data root (" + _listed(new) + ")",
                                    pytrace=False)


#: workeroutput key an xdist worker hands its after-the-last-test refusals over in.
WORKER_KEY = "real_root_refusals"
_from_workers: list[tuple[str, str]] = []


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """xdist controller: take what a finished worker refused after its last test (its own exit status never reaches here)."""
    _from_workers.extend(tuple(pair) for pair in (getattr(node, "workeroutput", None) or {}).get(WORKER_KEY, ()))


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """What was refused after the last test belongs to no test: the run fails and says so. An xdist worker's exit status
    is not forwarded, so a worker also hands the refusals to the controller in workeroutput (xdist sends it after every
    sessionfinish impl has run), and the controller fails the run for them."""
    new = _unreported()
    workeroutput = getattr(getattr(session, "config", None), "workeroutput", None)
    if new and isinstance(workeroutput, dict):
        workeroutput[WORKER_KEY] = [list(pair) for pair in new]
    new = new + _from_workers
    if new:
        sys.stderr.write("\nREAL DATA ROOT: a write was refused after the last test (%s)\n" % _listed(new))
        if not session.exitstatus:
            session.exitstatus = 1
