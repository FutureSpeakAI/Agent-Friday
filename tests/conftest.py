"""Root conftest — hermetic environment for the WHOLE Friday test suite.

Kept deliberately light: it sets up isolation env vars and sys.path but does NOT
import `server` (which pulls in chromadb + sentence-transformers, ~18s). Unit
tests under tests/unit/ import only the single module they target and stay fast.
The heavyweight `server`/Flask fixtures live in tests/api/conftest.py, scoped to
the API tests that actually need them.

  * `FRIDAY_TESTING=1` — set before anything imports `server`, so the module's
    background daemon loops never start.
  * Windows home redirected to a throwaway temp dir — every `Path.home()/.friday`,
    creations dir, vault, settings.json resolves under isolation. Tests never
    touch the real user's data.

Run everything:   pytest tests/unit tests/api -q
Unit only (fast): pytest tests/unit -q
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# ── Hermetic environment — MUST run before any `import server` ────────────────
# Captured BEFORE the redirect below, into an ENV VAR rather than a module
# constant. pytest imports this file as `conftest`, so a test doing
# `from tests.conftest import ...` executes the module a SECOND time — by then
# the redirect has already happened and a module constant would hold the temp
# home, silently skipping every live test. `setdefault` makes the second
# execution a no-op.
#
# The offline suite must never use this; it exists for the opt-in live tests
# (`--run-live-residency`) that have to reach the real Ollama blob store and
# runtime stack, which by definition do not exist under an isolated home.
os.environ.setdefault("FRIDAY_REAL_HOME", str(Path.home()))


def _sweep_stale_test_homes(base: Path, max_age_seconds: float = 3600) -> None:
    """Best-effort cleanup of temp homes orphaned by a prior run.

    CORRECTION (gauntlet-2026-09-03 F65): this used to say the only case it
    catches is a process that never reached `pytest_sessionfinish` (Ctrl+C,
    OOM kill, a hard crash). That undersold it. A NORMAL exit that touches
    `conversation_memory` (ChromaDB's HNSW index writer, `data_level0.bin`
    under `.friday/memory/conversations/<uuid>/`) reliably leaves that file
    Windows-locked past `pytest_sessionfinish`'s whole retry budget (~0.75s
    total) too -- confirmed directly, reproducibly, on ordinary
    `pytest tests/gauntlet/` runs that never crashed, and a `gc.collect()`
    before the retry loop does NOT fix it (tested), meaning this isn't a
    Python-refcount/GC-timing gap -- something holds the OS-level handle
    open past that whole window. This sweep is therefore this suite's REAL
    backstop for that case too, not just a crash-recovery fallback -- it's
    also the only thing that removes a normal ChromaDB-touching run's own
    temp home if it fails.

    Originally: accumulated 3,858 such directories since June (up to 781MB
    each), which drove the real disk to 0 bytes free and crashed the live
    app with a stack overflow (2026-09-03, see docs/audits/
    gauntlet-2026-09-03). Swept here, at collection time rather than only
    at exit, so a machine that never runs a suite to completion still
    self-heals on the next run -- which, per the correction above, now
    includes "ran to completion normally but ChromaDB kept a handle open."
    """
    try:
        for entry in base.glob("friday_test_home_*"):
            try:
                if time.time() - entry.stat().st_mtime > max_age_seconds:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


_PID_TAG = f"{os.getpid()}:"
_EXISTING_TEST_HOME = os.environ.get("_FRIDAY_TEST_HOME", "")
if _EXISTING_TEST_HOME.startswith(_PID_TAG):
    # This module is executing a SECOND time in the SAME process (see the
    # docstring above pytest_sessionfinish for why that happens routinely
    # -- any test doing `import tests.conftest` re-runs this whole file).
    # Reuse the temp home the FIRST execution already created and
    # registered with pytest, instead of minting a brand new one here.
    # Before this guard, every second execution created its own real
    # tempfile.mkdtemp() directory that pytest's plugin system never knew
    # about -- only the FIRST (pytest-registered) module instance's
    # pytest_sessionfinish ever runs, so this second directory was
    # orphaned on every single run that imported tests.conftest anywhere,
    # a 100%-reproducible leak (unlike the Windows-file-lock race the
    # retry logic below defends against) -- confirmed directly: a
    # controlled before/after directory count showed exactly +1 per run
    # of a test file that imports tests.conftest, with zero relation to
    # how heavy that test otherwise was (docs/audits/
    # gauntlet-2026-09-03/findings.jsonl F47).
    #
    # The PID prefix matters: this env var is inherited by any subprocess
    # a test spawns (e.g. one that shells out to `pytest` itself, per
    # tests/gauntlet/test_conftest_leak_end_to_end.py). Without checking
    # the PID, a CHILD process would see its PARENT's already-set env var
    # and wrongly reuse the parent's still-in-use _TEST_HOME instead of
    # creating its own -- breaking isolation between them and racing the
    # parent's own eventual cleanup. Keying on os.getpid() means only a
    # second import inside the SAME process matches; a genuinely new
    # process always takes the "mint a fresh one" branch below.
    _TEST_HOME = Path(_EXISTING_TEST_HOME[len(_PID_TAG):])
else:
    _sweep_stale_test_homes(Path(tempfile.gettempdir()))
    _TEST_HOME = Path(tempfile.mkdtemp(prefix="friday_test_home_"))
    os.environ["_FRIDAY_TEST_HOME"] = f"{_PID_TAG}{_TEST_HOME}"
os.environ["FRIDAY_TESTING"] = "1"
os.environ["USERPROFILE"] = str(_TEST_HOME)
os.environ["HOMEDRIVE"] = _TEST_HOME.drive or "C:"
os.environ["HOMEPATH"] = str(_TEST_HOME)[len(_TEST_HOME.drive):] or "\\"
# Path.home() reads HOME on POSIX (USERPROFILE is Windows-only) — without this
# a Linux/macOS run writes into the real ~/.friday and accumulates DB state
# across runs (the economy/leaderboard flakes in the Chaos Engineer review).
os.environ["HOME"] = str(_TEST_HOME)
os.environ.setdefault("FRIDAY_PASSWORD", "test-vault-passphrase")
# FRIDAY_VAULT_PASSPHRASE is the canonical vault key env var; FRIDAY_PASSWORD is
# the backward-compat fallback.  Set both so tests exercise the new code path.
os.environ.setdefault("FRIDAY_VAULT_PASSPHRASE", "test-vault-passphrase")
# Quieten noisy optional-dep warnings during test runs.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_ROOT = Path(__file__).resolve().parent.parent
# With the src/ layout, add both the project root (for `from tests.*` imports)
# and src/ (for `import agent_friday.*` imports without an editable install).
_SRC = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

# The canned model reply, shared so api tests can assert against it.
CANNED_TEXT = "[[friday-test-stub-response]]"


def pytest_addoption(parser):
    """`--run-network` opts IN to tests that need live network or spend money.

    Inverted default, decision D9: real provider bodies run for every test
    against offline transport doubles; only genuinely networked tests are
    gated, and they are deselected unless this flag is passed.
    """
    parser.addoption(
        "--run-network", action="store_true", default=False,
        help="run tests marked `network` (live network and/or paid API calls)",
    )
    # Live residency cycles load real multi-GB models and start real processes
    # on whatever machine runs them. Opt-in for the same reason as `network`:
    # the default suite must stay offline and fast.
    parser.addoption(
        "--run-live-residency", action="store_true", default=False,
        help="run tests marked `live_residency` (loads real models, minutes)",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-live-residency"):
        skip_live = pytest.mark.skip(
            reason="loads real multi-GB models; pass --run-live-residency")
        for item in items:
            if item.get_closest_marker("live_residency"):
                item.add_marker(skip_live)
    if config.getoption("--run-network"):
        return
    skip_network = pytest.mark.skip(
        reason="needs live network or spends money; pass --run-network to include")
    for item in items:
        if item.get_closest_marker("network"):
            item.add_marker(skip_network)


def pytest_sessionfinish(session, exitstatus):
    """Best-effort: try to remove this run's temp home on a normal exit.

    CORRECTION (gauntlet-2026-09-03 F65, root-caused; now actually closed
    rather than just documented -- the maintainer's ruling 2026-09-04 that a
    residual which grew from 268MB to 3.3GB since F71 needed a real fix,
    not a bigger bound): `_sweep_stale_test_homes()`'s corrected docstring
    above still applies to a CRASHED run (this function never gets to run
    at all) or any OTHER handle this repo doesn't yet know to close
    explicitly -- but for the one root cause that WAS identified
    (ChromaDB's HNSW index file staying Windows-locked past this retry
    loop's entire budget on a completely normal exit), explicitly closing
    the conversation-memory singleton's ChromaDB client BEFORE attempting
    the rmtree below removes the actual cause instead of hoping the OS
    releases it in time. chromadb's own Client.close() docstring names
    this exact SQLite-file-locking-on-Windows scenario as the reason it
    exists. Best-effort and import-guarded: a session that never touched
    conversation_memory at all must not fail teardown importing it for
    the first time here.
    """
    try:
        from agent_friday.conversation_memory import close_conversation_memory
        close_conversation_memory()
    except Exception:
        pass
    for _delay in (0, 0.05, 0.1, 0.2, 0.4):
        if _delay:
            time.sleep(_delay)
        try:
            shutil.rmtree(_TEST_HOME)
            return
        except FileNotFoundError:
            return  # already gone
        except OSError:
            continue
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


@pytest.fixture
def test_home():
    """Path to the isolated temp home for this run."""
    return _TEST_HOME


@pytest.fixture
def friday_dir():
    """The isolated ~/.friday directory (created on demand)."""
    d = _TEST_HOME / ".friday"
    d.mkdir(parents=True, exist_ok=True)
    return d
