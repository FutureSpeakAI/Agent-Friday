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

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
import weakref
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
# The Hugging Face cache stays the machine's shared one. Replacing the home
# below would otherwise move it into the empty test home, and the first test
# needing embeddings would download a whole model repository (every format,
# about a gigabyte) into it, once per pytest process. Read before the home is
# replaced, and exported so xdist workers, which inherit the replaced home,
# inherit the real cache too. Offline: tests use what the cache holds.
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(Path.home() / ".cache" / "huggingface")
if "--run-network" not in sys.argv and os.environ.get("_FRIDAY_KEEP_HOST_PROVIDER_KEYS") != "1":
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
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


# ── No public exposure from a test run ──────────────────────────────────────
# A unit test once approved a publish card, and the code under test launched a
# real static server and a real cloudflared quick tunnel from every worker,
# exposing temporary folders on public addresses with no approval. Two guards
# (tests/exposure_guard.py) make that impossible in every test process:
# process creation refuses tunnel binaries, and a socket refuses to bind
# anything but loopback. tests/unit/test_no_public_exposure_under_tests.py
# proves they are live.
from tests.exposure_guard import PublicExposureRefused, install as _install_exposure_guard  # noqa: E402,F401

_install_exposure_guard()

import pytest  # noqa: E402

# The keystore's file and the vault config the credential store reads are paths
# under FRIDAY_DIR bound when those modules are first imported. They are
# imported here, while FRIDAY_DIR is this run's test home: a test that points
# FRIDAY_DIR at a scratch directory and happened to be their first importer
# bound them there for the rest of the process, and a child process (which
# imports them fresh, against the test home) then held a different key and
# could not read what this process wrote, or the reverse.
import agent_friday.services.keystore  # noqa: E402,F401
import agent_friday.services.credential_store  # noqa: E402,F401

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


@pytest.fixture
def federation_on(monkeypatch):
    """settings.held_features.federation switched on for one test, for tests
    of the federation, marketplace, economy, compute and defederation code
    itself. Off is the default; tests/unit/test_held_federation.py and
    tests/api/test_held_federation_routes.py hold what off means.

    It answers at the reader rather than in the settings, so a test that
    replaces the settings loaders with its own stubs keeps the switch on."""
    from agent_friday.services import held_features
    real = held_features.enabled

    def _enabled(feature, settings=None):
        return True if feature == held_features.FEDERATION else real(feature, settings)
    monkeypatch.setattr(held_features, "enabled", _enabled)
    yield


@pytest.fixture(autouse=True)
def _held_detail_cards_off(request, monkeypatch):
    """The gate raises a card for every private detail it holds back (services/cloud_release,
    owner ruling 2026-10-17). Hundreds of tests seal private text through a shared scratch home;
    a card each would leak approvals from one test into the next. Tests of the cards opt in with
    the `held_cards` marker."""
    try:
        from agent_friday.services import cloud_release
    except Exception:
        yield
        return
    if request.node.get_closest_marker("held_cards") is None:
        monkeypatch.setattr(cloud_release, "HELD_CARDS", False)
    yield


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


class NetworkModelDownload(RuntimeError):
    """A test reached for a Hugging Face host: a network model download."""


_HF_HOSTS = ("huggingface.co", "hf.co", "cdn-lfs.huggingface.co", "cdn-lfs.hf.co")


@pytest.fixture(autouse=True)
def _no_network_model_download(request, monkeypatch):
    """Fail any test that reaches for Hugging Face. Resolving the host is the one
    step every download path shares, whichever library function started it."""
    if request.config.getoption("--run-network", default=False):
        yield
        return
    import socket
    real = socket.getaddrinfo

    def guarded(host, *a, **k):
        h = (host.decode() if isinstance(host, bytes) else str(host or "")).lower().rstrip(".")
        if any(h == d or h.endswith("." + d) for d in _HF_HOSTS):
            raise NetworkModelDownload(
                f"network model download attempted ({h}); tests read the shared "
                f"Hugging Face cache offline")
        return real(host, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    yield


@pytest.fixture(scope="session", autouse=True)
def _core_imported_before_any_turn():
    """A voice turn ends by reading a setting, and the first read in a process imports
    agent_friday.core: seconds on a cold or busy runner. That cost belongs to process
    start, not to a test that times a barge with a short join, so it is paid here once."""
    import agent_friday.core  # noqa: F401
    yield


def _task_and_orb_registries() -> list:
    """The two process-wide registries Activity reads besides the Local AI
    queue (services/activity_lane): the task records (``agent.TASKS``, which
    services/visible_jobs registers jobs into) and the process orbs
    (``core.PROCESSES``). Only modules already imported are touched, and a
    test that puts a stand-in module in their place (one without the registry)
    is left alone."""
    out = []
    for name, lock_attr, reg_attr in (("agent_friday.services.agent", "TASKS_LOCK", "TASKS"),
                                      ("agent_friday.core", "PROCESSES_LOCK", "PROCESSES")):
        mod = sys.modules.get(name)
        lock, reg = getattr(mod, lock_attr, None), getattr(mod, reg_attr, None)
        if lock is not None and isinstance(reg, dict):
            out.append((lock, reg))
    return out


@pytest.fixture(autouse=True)
def _no_task_or_orb_outlives_its_test():
    """Each test leaves the task records and the process orbs as it found them.

    Both live for the whole process, and an xdist worker runs many files in
    one: a record a test leaves "running" (a hand-written TASKS entry, a job
    whose finish never ran, an orb nothing closed) is a running task in every
    later test's Activity, so queue_status says "One running." to a test that
    started nothing. A record the test added is removed and one it removed is
    put back, by identity, so a record that existed before is the same object
    afterwards."""
    before = []
    for lock, reg in _task_and_orb_registries():
        with lock:
            before.append((lock, reg, dict(reg)))
    seen = {id(reg) for _lock, reg, _snap in before}
    yield
    for lock, reg in _task_and_orb_registries():
        if id(reg) not in seen:             # imported during the test: it found none
            before.append((lock, reg, {}))
    for lock, reg, snap in before:
        with lock:
            for key in [k for k in reg if k not in snap]:
                del reg[key]
            for key, rec in snap.items():
                if key not in reg:
                    reg[key] = rec


@pytest.fixture(autouse=True)
def _fresh_off_record_memory():
    """What one test kept off the record is gone before the next one runs."""
    yield
    off = sys.modules.get("agent_friday.services.off_record")
    if off is not None:
        off.end()


@pytest.fixture(autouse=True)
def _fresh_memory_review():
    """The memory card's review counts turns per process, so turns from many
    tests added up to a review that started in the background and called
    whatever model a later test had patched. Each test counts from zero, and
    a review a test did start is finished before the next test runs."""
    mr = sys.modules.get("agent_friday.services.memory_review")
    if mr is not None:
        mr._reset_for_tests()
    yield
    for t in threading.enumerate():
        if t.name == "memory-review" and t is not threading.current_thread():
            t.join(timeout=10)
    mr = sys.modules.get("agent_friday.services.memory_review")
    if mr is not None:
        mr._reset_for_tests()


@pytest.fixture(autouse=True)
def _fresh_warm_cache():
    """A warm value (the model catalog above all) is built from the inputs of
    whichever test built it first; each test gets one built from its own."""
    wc = sys.modules.get("agent_friday.services.warm_cache")
    if wc is not None:
        wc.invalidate()
    yield


@pytest.fixture(autouse=True)
def _fresh_health_cache(monkeypatch):
    """/api/health serves one payload for five seconds; each test computes
    its own from the state it set up. A test that reads the payload's fields
    waits for its computation however slow the machine is; the tests of the
    wait cap set ``_HEALTH_WAIT_S`` themselves."""
    cr = sys.modules.get("agent_friday.routes.core_routes")
    if cr is not None and hasattr(cr, "_reset_health_cache_for_tests"):
        cr._reset_health_cache_for_tests()
    if cr is not None and hasattr(cr, "_HEALTH_WAIT_S"):
        monkeypatch.setattr(cr, "_HEALTH_WAIT_S", 60.0)
    yield


@pytest.fixture(autouse=True)
def _no_real_gpu(request, monkeypatch):
    """The ear's GPU probe (services/gpu_probe) asks CTranslate2 and nvidia-smi,
    not torch, so a test that reaches the device choice unstubbed would ask the
    real card. CUDA is hidden and the probe's caches start empty; a test that
    means to look past this says so with ``@pytest.mark.real_gpu``."""
    if request.node.get_closest_marker("real_gpu") is None:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    gp = sys.modules.get("agent_friday.services.gpu_probe")
    if gp is not None:
        gp.reset()
    yield
    gp = sys.modules.get("agent_friday.services.gpu_probe")
    if gp is not None:
        gp.reset()


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


def _reset_local_queue() -> None:
    """Forget every seat queue, ticket, scheduler source, lease waiter and
    interactive registration this process holds. The Local AI queue
    (turn_queue, local_scheduler, background_gate) is process-wide, and an
    xdist worker runs many files in one process: a ticket, a podcast row or a
    voice registration one test left behind is a foreign job in the next
    test's queue. Only modules already imported are touched."""
    tq = sys.modules.get("agent_friday.services.turn_queue")
    if tq is not None:
        tq.reset_for_tests()
        tq.HELD.set(())
        tq.CURRENT.set(None)
    ls = sys.modules.get("agent_friday.services.local_scheduler")
    if ls is not None:
        ls.reset_for_tests()        # also re-installs the scheduler's gate on turn_queue
    bg = sys.modules.get("agent_friday.services.background_gate")
    if bg is not None:
        with bg._INTERACTIVE_LOCK:
            bg._INTERACTIVE.clear()
        bg._THRESHOLD_CACHE["at"] = 0.0
    # The capability check is a scheduler source read from a state file in the
    # shared test home: a re-assessment one test asked for (a model install
    # calls note_event) is a waiting "local_capability:reassess" job in every
    # later test's queue. The file and the in-memory job go together.
    lvr = sys.modules.get("agent_friday.services.local_voice_readiness")
    if lvr is not None:
        lvr.reset_for_tests()
        try:
            lvr._state_path().unlink(missing_ok=True)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _fresh_local_queue():
    """Each test starts with an empty Local AI queue and leaves one behind."""
    _reset_local_queue()
    yield
    _reset_local_queue()


def _forget_the_current_turn() -> None:
    """No turn is running. turn_plan names the running turn in a ContextVar,
    and every test runs on pytest's main thread in one context: a chat turn a
    test drove in-process (a route through the Flask test client, a reflex
    turn) stays named in the next test, which then tags its rows with that
    turn id and shares its call-verifier repeat cache."""
    tp = sys.modules.get("agent_friday.services.turn_plan")
    if tp is not None:
        tp._current.set(None)
        tp._current_version.set(None)


@pytest.fixture(autouse=True)
def _no_turn_outlives_its_test():
    """Each test starts outside any turn and leaves none named behind."""
    _forget_the_current_turn()
    yield
    _forget_the_current_turn()


def _forget_verified_calls() -> None:
    """Empty the call verifier's per-turn state. A call with no turn id is kept under
    one shared key for UNKEYED_TTL, so a test repeating another test's read would get
    that test's cached result and its own handler would never run."""
    cv = sys.modules.get("agent_friday.services.call_verifier")
    if cv is not None:
        cv.reset()


@pytest.fixture(autouse=True)
def _fresh_call_verifier():
    """Each test starts with no remembered call and leaves none behind."""
    _forget_verified_calls()
    yield
    _forget_verified_calls()


def _forget_live_duplicates() -> None:
    """Empty the Live surface's duplicate-call window. It is keyed by
    conversation, tool and arguments for a few seconds, so a test that repeats
    another test's call (a parametrized pair, the same synthetic conversation)
    would otherwise get that test's result back without running anything."""
    ll = sys.modules.get("agent_friday.services.live_laya")
    if ll is not None:
        ll.reset_idempotency()


@pytest.fixture(autouse=True)
def _laya_writes_finish_inside_their_test():
    """Laya's writer thread appends rows after the turn. A row an earlier test
    queued is written before this test starts, and this test's rows are written
    before it ends, so none lands in another test's home or fake writer."""
    laya = sys.modules.get("agent_friday.services.laya")
    if laya is not None:
        laya.flush()
    yield
    laya = sys.modules.get("agent_friday.services.laya")
    if laya is not None:
        laya.flush()


@pytest.fixture(autouse=True)
def _fresh_live_duplicate_window():
    """Each test starts with no remembered Live call and leaves none behind."""
    _forget_live_duplicates()
    yield
    _forget_live_duplicates()


_EPISODE_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{6}")


# A leaked episode is reported as a RuntimeWarning: a builtin category, so an xdist
# worker can send it to the controller (a class defined in this conftest cannot be
# rebuilt there and takes the worker down), and not a UserWarning, which pytest.ini
# ignores. Its text starts with "podcast episode leak" and names who queued it.


_SHARED_SHOWS: list = [None, frozenset()]    # [the shared home's podcasts dir, ids there at the first test]
_EPISODE_ORIGIN: dict = {}                   # episode id -> who queued it in the shared home
_THREAD_OWNER = weakref.WeakKeyDictionary()  # thread -> the first test it was still alive at the end of


def _episode_ids(shows) -> set:
    try:
        return {p.name for p in shows.iterdir() if p.is_dir() and _EPISODE_ID_RE.fullmatch(p.name)}
    except (OSError, AttributeError):
        return set()


def _call_path(limit: int = 8) -> str:
    """The project frames that led here, outermost first: file:line function."""
    import traceback
    frames = [f for f in traceback.extract_stack()[:-2]
              if ("agent_friday" in f.filename or "tests" in f.filename)
              and not f.filename.endswith("conftest.py")]
    return " <- ".join("%s:%d %s" % (Path(f.filename).name, f.lineno, f.name)
                       for f in reversed(frames[-limit:]))


def _trace_episode_saves(shows: Path) -> None:
    """Note who first writes each episode into the shared test home: the test
    running, the thread and the test that thread was started in, the call path.
    A test that replaces podcast_engine.save gets this back at its teardown."""
    pe = sys.modules.get("agent_friday.services.podcast_engine")
    if pe is None or getattr(pe.save, "_traces_shared_home", False):
        return
    real = pe.save

    def save(ep, *a, **k):
        try:
            eid = str((ep or {}).get("id") or "")
            if eid and eid not in _EPISODE_ORIGIN and Path(pe.root()) == shows:
                th = threading.current_thread()
                _EPISODE_ORIGIN[eid] = {
                    "test": (os.environ.get("PYTEST_CURRENT_TEST") or "").rsplit(" (", 1)[0],
                    "thread": th.name,
                    "main": th is threading.main_thread(),
                    "thread_from": _THREAD_OWNER.get(th, ""),
                    "path": _call_path()}
        except Exception:                           # the diagnosis never changes the write
            pass
        return real(ep, *a, **k)
    save._traces_shared_home = True
    save.__wrapped__ = real
    pe.save = save


def _describe_episode(d: Path) -> str:
    try:
        ep = json.loads((d / "episode.json").read_text(encoding="utf-8"))
    except Exception:
        ep = {}
    att = ep.get("attached") or {}
    made = ep.get("created_at")
    who = _EPISODE_ORIGIN.get(d.name)
    return ("episode %s (status %s, origin %s, routine %s, run %s, refs %s, created %s); %s" % (
        d.name, ep.get("status"), ep.get("origin"), att.get("routine"), att.get("run_id"),
        [r.get("kind") for r in ep.get("refs") or [] if isinstance(r, dict)],
        time.strftime("%H:%M:%S", time.localtime(made)) if isinstance(made, (int, float)) else "?",
        ("queued during %s by thread %s (alive since the end of %s) via %s" % (
            who["test"], who["thread"], who["thread_from"] or "this test", who["path"])) if who
        else "written by no podcast_engine.save this process saw"))


@pytest.fixture(autouse=True)
def _no_podcast_episode_outlives_its_test(request):
    """No podcast episode outlives its test in the shared test home. The
    scheduler lists every pending episode on disk as a "podcast" job, so one
    left behind appears in every later queue test in that process. A test that
    points FRIDAY_DIR at its own tmp_path is untouched.

    An episode a test queues itself, on its own thread, is removed when it
    ends. Anything else is a leak and is said, with who queued it, as a
    RuntimeWarning ("podcast episode leak ...") and on request.config.podcast_episode_leaks:
    one present when a test starts (written after an earlier test ended), and
    one written during a test by another test's thread or code. Leaks
    are removed too, so the queue tests stay about the queue."""
    core = sys.modules.get("agent_friday.core")
    shows = Path(core.FRIDAY_DIR) / "podcasts" if core is not None else None
    if _SHARED_SHOWS[0] is None and shows is not None:
        _SHARED_SHOWS[:] = [shows, frozenset(_episode_ids(shows))]
    if shows is not None:
        _trace_episode_saves(shows)
    leaks = request.config.__dict__.setdefault("podcast_episode_leaks", [])
    me = request.node.nodeid

    def _leak(name: str, when: str) -> None:
        said = "podcast episode leak %s %s: %s" % (when, me, _describe_episode(shows / name))
        leaks.append({"episode": name, "seen": when, "test": me,
                      "origin": dict(_EPISODE_ORIGIN.get(name) or {}), "said": said})
        warnings.warn(said, RuntimeWarning, stacklevel=2)

    for name in sorted(_episode_ids(shows) - _SHARED_SHOWS[1]):
        _leak(name, "at the start of")
        _remove_episode(shows / name)
    before = _episode_ids(shows)
    yield
    for t in threading.enumerate():
        _THREAD_OWNER.setdefault(t, me)
    if shows is not None:
        _trace_episode_saves(shows)
    for name in sorted(_episode_ids(shows) - before):
        who = _EPISODE_ORIGIN.get(name)
        # Untraced means podcast_engine was first imported by this test, so its
        # own code wrote the episode; the trace is in place from here on.
        if who and not (who["test"] == me and (who["main"] or not who["thread_from"])):
            _leak(name, "during")
        _remove_episode(shows / name)


def _remove_episode(d: Path) -> None:
    """Remove one episode directory, or raise. On Windows a file written a
    moment ago can be held open briefly (a virus scanner, the indexer, a reader
    thread) and rmtree then skips it; an episode left on disk here is in every
    later test's `before` set, so it would stay a "podcast" job for the rest of
    the process. Try again for a short while; an episode that cannot be removed
    fails this test's teardown instead of the next queue test."""
    for _delay in (0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6):
        if _delay:
            time.sleep(_delay)
        shutil.rmtree(d, ignore_errors=True)
        if not (d / "episode.json").exists():
            return
    raise RuntimeError(
        "could not remove podcast episode %s after this test; every later test "
        "in this process would list it as a waiting podcast job" % d.name)


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
        "this process would inherit the state it left" % (path, err))


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


#: The stores the top bar's Activity chip counts from (services/activity_lane:
#: needs-you = pending approvals + needs-you notices, updates = today's FYIs).
_TOP_BAR_STORES = ("notifications.json", "notif_trigger_state.json", "approvals.json")


def _top_bar_store_paths() -> list[Path]:
    """The shared-home copies of those stores: the loaded modules' own paths
    (what the app actually writes) plus the home default (for a module the
    test file imports later)."""
    from agent_friday.paths import friday_home
    paths = {friday_home() / name for name in _TOP_BAR_STORES}
    ne = sys.modules.get("agent_friday.notifications_engine")
    if ne is not None:
        paths.update(Path(p) for p in (getattr(ne, "NOTIF_FILE", None),
                                       getattr(ne, "TRIGGER_STATE_FILE", None)) if p)
    ap = sys.modules.get("agent_friday.services.approvals")
    if ap is not None and getattr(ap, "APPROVALS_FILE", None):
        paths.add(Path(ap.APPROVALS_FILE))
    return sorted(paths)


@pytest.fixture(scope="module", autouse=True)
def _notices_and_approvals_do_not_outlive_their_file():
    """Each test file leaves the notice and approval stores as it found them.

    Same leak as settings.json above, other stores: every test in a process
    shares one temp home, so a seat change (a "seat_change" needs-you notice),
    an approval card a test raises and never decides, or an FYI stays in
    notifications.json / approvals.json for every later file on that worker.
    The top bar counts them: after 61 ordinary API files the Activity chip read
    "9 need you" on a CI runner (34 on the release PC), grew to its 300 px
    cap, and the Windows top bar folded it into its hidden overflow pull-out,
    so tests/api/test_workspace_job_card_ui.py timed out clicking a chip that
    was drawn but not visible (sky gate, 2026-10-15). Which files share a
    worker is up to --dist loadfile, so it came and went with the file count
    (tests/unit/test_notices_do_not_leak_between_files.py runs leakers and a
    clean-top-bar probe in one process).

    The bytes, or the files' absence, are put back when the test file ends.
    """
    paths = _top_bar_store_paths()
    before = {}
    for p in paths:
        try:
            before[p] = p.read_bytes()
        except FileNotFoundError:
            before[p] = None
    yield
    for p in set(paths) | set(_top_bar_store_paths()):
        _put_back(p, before.get(p))


def _schedules_file() -> Path:
    """The schedules.json the scheduler reads: its own path once it is loaded
    (a test that points it at tmp has put it back by the time this runs)."""
    sch = sys.modules.get("agent_friday.services.scheduler")
    if sch is not None and getattr(sch, "SCHEDULES_FILE", None):
        return Path(sch.SCHEDULES_FILE)
    from agent_friday.paths import friday_home
    return friday_home() / "schedules.json"


@pytest.fixture(scope="module", autouse=True)
def _schedules_do_not_outlive_their_file():
    """Each test file leaves schedules.json as it found it.

    Same leak as settings.json above, another store. Arming a post (schedule,
    post now, release) switches the Content publisher job on when it is still
    in its seeded-off state (services/publisher.ensure_publisher_running), and
    it can only do that once the job is registered. A file that registers the
    builtins (publisher.start(), e.g. tests/unit/test_content_publisher.py)
    leaves that registration in the worker, so a later file that arms a post
    through the routes (tests/api/test_content_routes.py) wrote an enabled
    sch_content_publisher into the shared home's schedules.json, and every
    later file on that worker saw the job on. Which files share a worker is
    up to --dist loadfile (tests/unit/test_schedules_do_not_leak_between_files.py
    runs a registrar, an arming file and a probe in one process).

    The bytes, or the file's absence, are put back when the test file ends.
    """
    path = _schedules_file()
    try:
        before = path.read_bytes()
    except FileNotFoundError:
        before = None
    yield
    _put_back(path, before)


@pytest.fixture(autouse=True)
def _interruption_state_per_test(tmp_path):
    """The hour's interruption count (services/interruption_policy) starts empty in every test, so
    one test's approvals never use up the budget the next test sees."""
    from agent_friday.services import interruption_policy as _ip
    _ip.set_state_path(tmp_path / "interruptions.json")
    yield
    _ip.set_state_path(None)


@pytest.fixture(autouse=True)
def _remote_alias_set_per_test():
    """The daemon's remote-alias set (local_only_guard) is process-wide: an
    inventory one test feeds in must not make the next test's model cloud.

    By default a test sees an inventory that was read and named no remote alias,
    so no relay transport in a test reads the developer's own daemon to fill it.
    A test of the never-read path resets the set itself (it runs after this)."""
    from agent_friday.services import local_only_guard as _g
    before = _g._reset_aliases_for_tests((frozenset(), True, 0.0))
    yield
    _g._reset_aliases_for_tests(before)
