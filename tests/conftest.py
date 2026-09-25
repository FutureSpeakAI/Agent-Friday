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


# ── No console windows from the test suite ───────────────────────────────────
# Without this, every xdist worker pops a console window on the desktop when
# the suite is launched from the Friday server.
#
# services/no_console.install() patches subprocess.Popen inside the Friday
# server process, so the `python -m pytest` it spawns is created with
# CREATE_NO_WINDOW. That patch lives in the server's interpreter and does not
# survive into the child. pytest-xdist then spawns its workers (`-n auto`, so
# one per core) through plain execnet Popen calls with no creationflags — and
# because their parent has no console to inherit, Windows hands each worker a
# brand-new console window.
#
# So the parent is silenced and the grandchildren shout. Installing the same
# patch here, in the pytest master, is what closes the gap: conftest is
# imported before xdist builds its workers, so the workers inherit
# CREATE_NO_WINDOW like everything else.
#
# Deliberately best-effort. A test suite that refuses to start because a
# cosmetic patch failed to import would be a worse bug than the popups.
def _silence_child_consoles() -> None:
    """Best-effort; called below once sys.path knows where `src/` is.

    A test suite that refused to start because a cosmetic patch failed to
    import would be a worse bug than the popups it fixes.
    """
    try:
        from agent_friday.services.no_console import install
        ok = install()
        if os.environ.get("FRIDAY_CONSOLE_TRACE"):
            import subprocess as _sp
            print("[conftest] no_console install=%s patched=%s" % (
                ok, getattr(_sp.Popen.__init__, "__friday_no_console__", False)),
                flush=True)
    except Exception:  # noqa: BLE001 - deliberately swallowed, see docstring
        pass


def _sweep_stale_test_homes(base: Path, max_age_seconds: float = 3600) -> None:
    """Best-effort cleanup of temp homes orphaned by a prior run.

    Catches a process that never reached `pytest_sessionfinish` (Ctrl+C,
    OOM kill, a hard crash), and also a NORMAL exit whose temp home could
    not be removed (gauntlet findings.jsonl F65): a run that touches
    `conversation_memory` (ChromaDB's HNSW index writer, `data_level0.bin`
    under `.friday/memory/conversations/<uuid>/`) can leave that file
    Windows-locked past `pytest_sessionfinish`'s whole retry budget (~0.75s
    total). A `gc.collect()` before the retry loop does NOT fix it -- the
    OS-level handle is held past that window -- so this sweep is a real
    backstop, not just a crash-recovery fallback.

    Unswept, these directories (up to ~780MB each) accumulate by the
    thousand and can fill the disk, which takes the live app down with them.
    Swept here, at collection time rather than only at exit, so a machine
    that never runs a suite to completion still self-heals on the next run.
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
    # Without this guard, every second execution creates its own real
    # tempfile.mkdtemp() directory that pytest's plugin system never knows
    # about -- only the FIRST (pytest-registered) module instance's
    # pytest_sessionfinish ever runs, so that second directory is orphaned
    # on every run that imports tests.conftest anywhere: exactly +1
    # directory per run, a deterministic leak (unlike the Windows-file-lock
    # race the retry logic below defends against). See
    # docs/history/audits/gauntlet-2026-09-03/findings.jsonl F47.
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
# Provider API keys from the HOST environment do not reach the offline suite.
# A developer machine carries real GEMINI_API_KEY / ANTHROPIC_API_KEY / ...,
# CI carries none, and a test that sees one behaves differently there: the
# health probe counts a keyed provider, a code path decides "cloud is
# available", and an unmocked call becomes a paid one. Tests that need a key
# set it themselves with monkeypatch. `--run-network` keeps them (the choice
# is recorded in the environment so xdist workers, whose argv does not carry
# the flag, make the same one).
if "--run-network" in sys.argv:
    os.environ["_FRIDAY_KEEP_HOST_PROVIDER_KEYS"] = "1"
if os.environ.get("_FRIDAY_KEEP_HOST_PROVIDER_KEYS") != "1":
    for _k in [k for k in os.environ if k.upper().endswith("_API_KEY")]:
        del os.environ[_k]
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

# Now that `src/` is importable — and still before xdist builds its workers,
# which is the only ordering that matters. See the comment block above.
_silence_child_consoles()

import pytest  # noqa: E402

# The canned model reply, shared so api tests can assert against it.
CANNED_TEXT = "[[friday-test-stub-response]]"


def record_own_sleeps(monkeypatch, time_module, into=None):
    """Replace `time_module.sleep` and return the list of durations slept by
    the calling test's own thread (appended to `into` when given).

    A module's `time` is the process-wide time module, so a bare replacement
    also records the sleeps of background threads other tests left running
    on the same worker, and an exact-list assertion then fails at random.
    Sleeps from any other thread still really sleep.
    """
    import threading
    me = threading.get_ident()
    real_sleep = time_module.sleep
    slept = [] if into is None else into

    def fake(seconds):
        if threading.get_ident() == me:
            slept.append(seconds)
        else:
            real_sleep(seconds)

    monkeypatch.setattr(time_module, "sleep", fake)
    return slept


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
    # The honesty corpus asks a real model real questions and judges the
    # answers. It needs a seat, takes minutes, and is not deterministic - the
    # same three reasons the two above are opt-in. Gating commits on it would
    # make the suite slow and flaky; never running it leaves the fixtures
    # unread. Opt-in is the middle: runnable on demand, and its absence from a green default run is
    # not mistaken for a pass.
    parser.addoption(
        "--run-honesty", action="store_true", default=False,
        help="run tests marked `honesty` (asks a live seat; minutes)",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-honesty"):
        skip_honesty = pytest.mark.skip(
            reason="asks a live model and judges the answers; "
                   "pass --run-honesty")
        for item in items:
            if item.get_closest_marker("honesty"):
                item.add_marker(skip_honesty)
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

    Root cause of gauntlet finding F65: ChromaDB's HNSW index file stays
    Windows-locked past this retry loop's entire budget on a completely
    normal exit. Explicitly closing the conversation-memory singleton's
    ChromaDB client BEFORE attempting the rmtree below removes that cause
    instead of hoping the OS releases it in time; a growing residual needs
    a real fix, not a bigger bound. `_sweep_stale_test_homes()` above still
    covers a CRASHED run (this function never runs at all) and any OTHER
    handle this repo does not yet know to close explicitly. chromadb's own Client.close() docstring names
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


@pytest.fixture(autouse=True)
def _fresh_swr_cache():
    """Cached panel data must not carry from one test into the next."""
    try:
        from agent_friday.services import swr_cache
    except Exception:
        yield
        return
    swr_cache.invalidate("")
    yield
    swr_cache.invalidate("")


@pytest.fixture(autouse=True)
def _fresh_warm_cache():
    """A warm value (the model catalog above all) is built from the inputs of
    whichever test built it first; each test gets one built from its own."""
    wc = sys.modules.get("agent_friday.services.warm_cache")
    if wc is not None:
        wc.invalidate()
    yield


@pytest.fixture(autouse=True)
def _no_local_seat_is_serving(monkeypatch):
    """By default, no local seat is serving.

    `scheduler._resolve_local_seat` asks the machine's own model servers what
    is running, and the background task worker uses it to pick the seat that
    grades every task. Left real, any test that runs `_task_worker` would probe
    the developer's live daemon and send it a grading prompt. A test that needs
    a serving seat patches `_resolve_local_seat` itself; that patch is applied
    after this one and wins.
    """
    if ("agent_friday.services.agent" in sys.modules
            or "agent_friday.services.scheduler" in sys.modules):
        from agent_friday.services import scheduler
        monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: None)
    yield


def _settings_file() -> Path:
    """The settings.json the app reads: core's own path once core is loaded."""
    core = sys.modules.get("agent_friday.core")
    if core is not None and getattr(core, "SETTINGS_FILE", None):
        return Path(core.SETTINGS_FILE)
    from agent_friday.paths import friday_home
    return friday_home() / "settings.json"


def _put_back(path: Path, before: bytes | None) -> None:
    """Restore `path` to `before` (None: absent). Atomic, with a short retry
    for a Windows reader holding the file open; raises if it cannot."""
    for _delay in (0, 0.05, 0.1, 0.2):
        if _delay:
            time.sleep(_delay)
        try:
            if before is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != before:
                tmp = path.with_name(path.name + ".restore")
                tmp.write_bytes(before)
                os.replace(tmp, path)
            return
        except OSError as e:
            err = e
    raise RuntimeError(
        "could not restore %s after this test file (%s); every later file in "
        "this process would inherit the settings it left" % (path, err))


@pytest.fixture(scope="module", autouse=True)
def _settings_do_not_outlive_their_file():
    """Each test file leaves settings.json as it found it.

    Every test in one process shares one temp home, so a settings write made
    through the app (POST /api/settings, _save_settings) otherwise stays in
    force for every later file on the same xdist worker. A seat pick is the
    costly case: the router reads capability_routing.reasoning on every
    route() and treats any model other than the factory one as the user's
    binding, so a file that seats claude-opus-5-5 routes the next file's
    ModelRouter tests to it. Which files share a worker is up to --dist
    loadfile, so a leak like that moves whenever a file is added
    (tests/unit/test_settings_do_not_leak_between_files.py runs the pair in
    one process).

    The bytes, or the file's absence, are put back when the test file
    finishes, and the settings cache is dropped so the next read comes off
    disk.
    """
    path = _settings_file()
    try:
        before = path.read_bytes()
    except FileNotFoundError:
        before = None
    yield
    try:
        _put_back(path, before)
    finally:
        core = sys.modules.get("agent_friday.core")
        if core is not None:
            core._invalidate_settings_cache()
