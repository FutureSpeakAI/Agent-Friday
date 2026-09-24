"""One unlucky import at boot must not disable local voice until a restart.

Importing Kokoro pulls in torch and transformers. When several subsystems
reach for them at once during startup, the import can lose the race and raise
``cannot import name 'AlbertModel' from 'transformers'`` against a
transformers that is present and healthy.

That answer used to be cached for the life of the process. The consequences
ran all the way to the microphone: ``kokoro_available()`` stayed False, so
``models_ready()`` stayed False, so the engine resolver refused the local
session, so pressing the mic did nothing — and no amount of waiting fixed it,
because nothing ever asked again.

Observed on the live server, which reported every Kokoro dependency installed,
reported the import broken, and then imported Kokoro and spoke a sentence in a
separate process seconds later.

Success is still cached forever — it cannot become untrue. Only failure is
retried, and only after a cooldown, so this never becomes an import storm.
"""
import pytest

kv = pytest.importorskip("agent_friday.services.kokoro_voice")

RACE = {"ok": False,
        "error": "ImportError: cannot import name 'AlbertModel' from 'transformers'",
        "missing": "transformers"}


def _set_attempt_at(value):
    """Set the last-attempt clock if the module has one.

    Tolerated rather than required so these tests RUN against a build without
    the retry and fail on BEHAVIOUR — an AttributeError in a fixture would only
    prove that an attribute is new.
    """
    setattr(kv, "_import_check_at", value)


@pytest.fixture(autouse=True)
def _clean_cache():
    before = kv._import_check
    before_at = getattr(kv, "_import_check_at", None)
    yield
    kv._import_check = before
    if before_at is not None:
        kv._import_check_at = before_at


@pytest.fixture
def importable_kokoro(monkeypatch):
    """A kokoro whose import succeeds, on any host.

    These tests are about WHEN the import is retried, not whether the real
    package (torch and all) is installed; Kokoro is an optional voice
    dependency and CI does not carry it.
    """
    import sys
    import types
    mod = types.ModuleType("kokoro")
    mod.KPipeline = type("KPipeline", (), {})
    monkeypatch.setitem(sys.modules, "kokoro", mod)


def test_a_cached_failure_is_retried_once_the_cooldown_passes(monkeypatch, importable_kokoro):
    kv._import_check = dict(RACE)
    _set_attempt_at(0.0)                            # long past
    monkeypatch.setattr(kv, "kokoro_deps_installed", lambda: True)
    calls = []

    real = kv.kokoro_import_status

    def spy(refresh=False):
        calls.append(refresh)
        return real(refresh)

    assert kv.kokoro_import_status()["ok"] is True, (
        "a failure cached at boot must not outlive its cooldown — this is the "
        "difference between local voice healing itself and needing a restart")


def test_a_fresh_failure_is_not_hammered(monkeypatch):
    """Retrying is not the same as retrying constantly."""
    import time
    kv._import_check = dict(RACE)
    _set_attempt_at(time.monotonic())               # just failed
    attempts = []
    monkeypatch.setattr(kv, "kokoro_deps_installed", lambda: True)

    # If it tried to import inside the cooldown we would see the cached error
    # replaced; instead the same failure is returned untouched.
    out = kv.kokoro_import_status()
    assert out["ok"] is False
    assert out["error"] == RACE["error"]


def test_success_is_cached_and_never_re_imported(monkeypatch):
    kv._import_check = {"ok": True, "error": "", "missing": ""}
    _set_attempt_at(0.0)
    boom = lambda *a, **k: pytest.fail("a cached success must not re-import")
    monkeypatch.setattr(kv, "kokoro_deps_installed", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "kokoro", None)
    assert kv.kokoro_import_status()["ok"] is True


def test_refresh_forces_a_re_check(monkeypatch, importable_kokoro):
    kv._import_check = dict(RACE)
    import time
    _set_attempt_at(time.monotonic())
    monkeypatch.setattr(kv, "kokoro_deps_installed", lambda: True)
    assert kv.kokoro_import_status(refresh=True)["ok"] is True


def test_the_message_does_not_blame_a_dependency_that_is_installed(monkeypatch):
    """Telling someone to reinstall what they already have wastes their evening."""
    monkeypatch.setattr(kv, "kokoro_deps_installed", lambda: True)
    monkeypatch.setattr(kv, "kokoro_deps_status", lambda: {
        "kokoro": True, "torch": True, "misaki": True, "soundfile": True})
    monkeypatch.setattr(kv, "kokoro_import_status", lambda refresh=False: dict(RACE))
    monkeypatch.setattr(kv, "_named_dep_is_installed", lambda name: True)
    detail = kv.kokoro_health()["detail"]
    assert "import race" in detail.lower(), (
        "when the blamed module IS installed the cause is the startup race, "
        "not a --no-deps install: %r" % detail)
    assert "retried automatically" in detail


def test_a_genuinely_missing_dependency_still_says_reinstall(monkeypatch):
    monkeypatch.setattr(kv, "kokoro_deps_installed", lambda: True)
    monkeypatch.setattr(kv, "kokoro_deps_status", lambda: {
        "kokoro": True, "torch": True, "misaki": True, "soundfile": True})
    monkeypatch.setattr(kv, "kokoro_import_status", lambda refresh=False: {
        "ok": False, "error": "ModuleNotFoundError: No module named 'spacy'",
        "missing": "spacy"})
    monkeypatch.setattr(kv, "_named_dep_is_installed", lambda name: False)
    detail = kv.kokoro_health()["detail"]
    assert "--no-deps" in detail and "pip install" in detail
