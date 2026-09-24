"""Kokoro readiness must reflect what will happen, not what is on disk.

Every test here guards one specific way this subsystem previously lied. If the
enforcement each names is removed, the test fails -- that is the point of it.
Context: docs/history/audits/2026-09-04-five-dead-settings.md.
"""
import sys

import pytest

from agent_friday.services import kokoro_voice as kv


@pytest.fixture(autouse=True)
def _clear_import_cache():
    """The import probe memoises; tests must not inherit each other's answer."""
    kv._import_check = None
    yield
    kv._import_check = None


@pytest.fixture
def numpy_or_stub(monkeypatch):
    """numpy is an optional voice dependency. The synthesis-failure tests fail
    inside the pipeline call, before numpy is used, so a bare stub is enough
    where numpy is not installed; the real module is used where it is."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        import types
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))


@pytest.fixture
def fake_misaki(monkeypatch):
    """misaki is an optional voice dependency, and a real EspeakFallback needs
    the espeak-ng library. attach_espeak_fallback only has to construct one
    and attach it, so a stand-in module proves that on any host."""
    import types

    class EspeakFallback:
        def __init__(self, british=False):
            self.british = british

    espeak = types.ModuleType("misaki.espeak")
    espeak.EspeakFallback = EspeakFallback
    misaki = types.ModuleType("misaki")
    misaki.espeak = espeak
    monkeypatch.setitem(sys.modules, "misaki", misaki)
    monkeypatch.setitem(sys.modules, "misaki.espeak", espeak)
    return EspeakFallback


# ---------------------------------------------------------------- availability

def test_available_is_false_when_package_resolves_but_import_fails(monkeypatch):
    """The exact state a `pip install --no-deps kokoro` leaves behind.

    `find_spec("kokoro")` succeeds, the import raises ModuleNotFoundError for a
    transitive dependency. If availability is ever computed from deps alone
    again, this fails -- which is the whole reason it exists.
    """
    monkeypatch.setattr(kv, "kokoro_deps_status", lambda: {
        "kokoro": True, "torch": True, "misaki": True,
        "espeakng_loader": True, "soundfile": True})
    monkeypatch.setattr(kv, "kokoro_import_status", lambda refresh=False: {
        "ok": False, "error": "ModuleNotFoundError: No module named 'spacy'",
        "missing": "spacy"})

    assert kv.kokoro_deps_installed() is True, "precondition: deps look present"
    assert kv.kokoro_available() is False
    assert kv.kokoro_gpu_ready() is False


def test_available_is_true_only_when_the_import_actually_works(monkeypatch):
    monkeypatch.setattr(kv, "kokoro_deps_status", lambda: {
        "kokoro": True, "torch": True, "misaki": True,
        "espeakng_loader": True, "soundfile": True})
    monkeypatch.setattr(kv, "kokoro_import_status", lambda refresh=False: {
        "ok": True, "error": "", "missing": ""})
    assert kv.kokoro_available() is True


def test_import_status_reports_the_failure_rather_than_raising(monkeypatch):
    """A readiness probe that raises is a readiness probe nobody can call."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def boom(name, *a, **k):
        if name == "kokoro" or name.startswith("kokoro."):
            raise ModuleNotFoundError("No module named 'spacy'", name="spacy")
        return real_import(name, *a, **k)

    monkeypatch.setitem(sys.modules, "kokoro", None)
    monkeypatch.setattr("builtins.__import__", boom)
    kv._import_check = None
    st = kv.kokoro_import_status(refresh=True)
    assert st["ok"] is False
    assert st["missing"] == "spacy"


# --------------------------------------------------------------------- health

def test_health_broken_names_the_missing_module_and_the_remedy(monkeypatch):
    """An unavailable option must say what would make it available."""
    monkeypatch.setattr(kv, "kokoro_deps_status", lambda: {
        "kokoro": True, "torch": True, "misaki": True,
        "espeakng_loader": True, "soundfile": True})
    monkeypatch.setattr(kv, "kokoro_import_status", lambda refresh=False: {
        "ok": False, "error": "ModuleNotFoundError: No module named 'spacy'",
        "missing": "spacy"})

    h = kv.kokoro_health()
    assert h["status"] == "broken"
    assert h["available"] is False
    assert h["gpu_ready"] is False
    assert "spacy" in h["detail"], "the detail must name what is missing"
    assert "pip install" in h["detail"], "the detail must say how to fix it"
    # Piper must be named as unaffected: an unavailable upgrade should not read
    # as a broken subsystem.
    assert "Piper" in h["detail"]


def test_health_never_reports_ok_while_the_import_fails(monkeypatch):
    """The specific regression: status 'ok' / 'Kokoro GPU voice ready' for an
    engine that cannot import. Guards against reordering the checks so the GPU
    branch is consulted before the import branch."""
    monkeypatch.setattr(kv, "kokoro_deps_status", lambda: {
        "kokoro": True, "torch": True, "misaki": True,
        "espeakng_loader": True, "soundfile": True})
    monkeypatch.setattr(kv, "kokoro_import_status", lambda refresh=False: {
        "ok": False, "error": "ModuleNotFoundError", "missing": "spacy"})
    monkeypatch.setattr(kv, "kokoro_gpu_status", lambda: {
        "cuda": True, "sufficient_for_kokoro": True,
        "kokoro_headroom_basis_gb": 8.5})

    h = kv.kokoro_health()
    assert h["status"] != "ok"
    assert h["available"] is False


# ------------------------------------------- the refusal contract holds anyway

def test_resolve_device_refuses_when_the_import_fails(monkeypatch):
    """`_resolve_device` must ask whether Kokoro WORKS, not whether the name
    resolves. Reverting it to the find_spec-only guard lets a `--no-deps`
    install sail past and fail later as an unlabelled crash."""
    monkeypatch.setattr(kv, "kokoro_deps_status", lambda: {
        "kokoro": True, "torch": True, "misaki": True,
        "espeakng_loader": True, "soundfile": True})
    monkeypatch.setattr(kv, "kokoro_import_status", lambda refresh=False: {
        "ok": False, "error": "ModuleNotFoundError: No module named 'spacy'",
        "missing": "spacy"})

    with pytest.raises(kv.KokoroUnavailable) as ei:
        kv.KokoroTTS()._resolve_device()
    assert ei.value.code == "local_voice_kokoro_missing"
    assert "spacy" in ei.value.message
    assert "Piper" in ei.value.offer, "a refusal must carry the alternative"


def test_load_reraises_a_bare_import_error_as_kokoro_unavailable(monkeypatch):
    """Belt and braces. Even if the probe is wrong and the device check passes,
    a failure at the import/construction site must still arrive as a coded
    refusal with an offer -- never as a raw ModuleNotFoundError, which reaches
    the voice session with no reason and no fallback."""
    tts = kv.KokoroTTS()
    monkeypatch.setattr(tts, "_resolve_device", lambda: "cuda")

    real_import = __import__

    def boom(name, *a, **k):
        if name == "kokoro" or name.startswith("kokoro."):
            raise ModuleNotFoundError("No module named 'spacy'", name="spacy")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", boom)

    with pytest.raises(kv.KokoroUnavailable) as ei:
        tts.load()
    assert ei.value.code == "local_voice_kokoro_missing"
    assert "spacy" in ei.value.message
    assert tts._pipeline is None, "a failed load must not leave a half-built pipeline"


def test_load_does_not_swallow_a_well_formed_refusal(monkeypatch):
    """_resolve_device's own KokoroUnavailable must pass through with ITS code,
    not be re-wrapped as a missing-package error."""
    tts = kv.KokoroTTS()

    def refuse():
        raise kv.KokoroUnavailable("local_voice_kokoro_no_gpu", "no cuda here")

    monkeypatch.setattr(tts, "_resolve_device", refuse)
    with pytest.raises(kv.KokoroUnavailable) as ei:
        tts.load()
    assert ei.value.code == "local_voice_kokoro_no_gpu"


# ------------------------------------------ the "starts, then dies" case

def _fake_kokoro_module(fallback):
    """A stand-in `kokoro` module whose KPipeline yields a given g2p fallback."""
    import types
    mod = types.ModuleType("kokoro")

    class _G2P:
        pass

    class _Pipe:
        def __init__(self, lang_code=None, device=None):
            self.g2p = _G2P()
            self.g2p.fallback = fallback

    mod.KPipeline = _Pipe
    return mod


# NOTE: `test_load_refuses_a_pipeline_whose_g2p_has_no_fallback` lived here and
# was removed deliberately, not lost. It asserted that a missing g2p fallback
# always refuses; load() now REPAIRS a repairable pipeline first, so that
# assertion contradicts the design. Both branches are covered precisely by
# `test_load_repairs_a_fallbackless_pipeline_instead_of_refusing` and
# `test_load_refuses_when_the_fallback_cannot_be_repaired`.


def test_load_accepts_a_pipeline_that_has_a_fallback(monkeypatch):
    tts = kv.KokoroTTS()
    monkeypatch.setattr(tts, "_resolve_device", lambda: "cpu")
    monkeypatch.setattr(kv, "ensure_espeak_fallback",
                        lambda: {"wired": True, "library": "x", "data": "y",
                                 "detail": ""})
    monkeypatch.setitem(sys.modules, "kokoro",
                        _fake_kokoro_module(object()))
    tts.load()
    assert tts._pipeline is not None


def test_synthesis_failure_becomes_a_coded_refusal_with_an_offer(numpy_or_stub):
    """The safety net. A raw TypeError from inside misaki reaches the voice
    session as an unhandled crash -- no code, no reason, no offer of Piper.
    Removing the wrapper puts that crash back."""
    tts = kv.KokoroTTS()

    class _Boom:
        g2p = type("g", (), {"fallback": object()})()

        def __call__(self, *a, **k):
            raise TypeError(
                "unsupported operand type(s) for +: 'NoneType' and 'str'")

    tts._pipeline = _Boom()

    with pytest.raises(kv.KokoroUnavailable) as ei:
        tts.synthesize("Kubernetes")
    assert ei.value.code == "local_voice_kokoro_synthesis_failed"
    assert "TypeError" in ei.value.message
    assert "Piper" in ei.value.offer


def test_engine_records_the_refusal_code_where_the_session_reads_it(numpy_or_stub):
    """A code on an exception nobody inspects is a receipt written where nobody
    reads it. The session reads `last_error_code`, so synthesis refusals must
    land there -- while still propagating, never substituting another voice."""
    from agent_friday.services.local_voice import LocalVoiceEngine

    class _Boom:
        g2p = type("g", (), {"fallback": object()})()

        def __call__(self, *a, **k):
            raise TypeError("boom")

    tts = kv.KokoroTTS()
    tts._pipeline = _Boom()
    eng = LocalVoiceEngine()
    eng._tts = tts
    eng._tier = "cpu"

    with pytest.raises(kv.KokoroUnavailable):
        eng.synthesize("Kubernetes")
    assert eng.last_error_code == "local_voice_kokoro_synthesis_failed"
    assert eng.last_error


def test_ensure_espeak_fallback_reports_rather_than_raising(monkeypatch):
    """A wiring helper that raises is one nobody can call from a health path."""
    monkeypatch.setitem(sys.modules, "espeakng_loader", None)
    out = kv.ensure_espeak_fallback()
    assert isinstance(out, dict)
    assert out["wired"] in (True, False)


def test_load_wires_espeak_before_importing_kokoro(monkeypatch):
    """The ordering IS the fix. `misaki/espeak.py` resolves its library at
    import time, so wiring espeakng_loader afterwards is too late -- the g2p is
    already built with `fallback = None`. This asserts load() calls the wiring
    helper, and calls it before `kokoro` is imported. Deleting the call leaves
    the crash exactly where it was."""
    calls = []
    tts = kv.KokoroTTS()
    monkeypatch.setattr(tts, "_resolve_device", lambda: "cpu")

    def spy():
        calls.append("wire")
        return {"wired": True, "library": "x", "data": "y", "detail": ""}

    monkeypatch.setattr(kv, "ensure_espeak_fallback", spy)

    import types
    mod = types.ModuleType("kokoro")

    class _Pipe:
        def __init__(self, lang_code=None, device=None):
            calls.append("import_kokoro")
            self.g2p = type("g", (), {"fallback": object()})()

    mod.KPipeline = _Pipe
    monkeypatch.setitem(sys.modules, "kokoro", mod)

    tts.load()
    assert "wire" in calls, "load() must wire espeak"
    assert calls.index("wire") < calls.index("import_kokoro"), \
        "espeak must be wired BEFORE kokoro/misaki is imported"


@pytest.mark.skipif(
    not kv._module_installed("espeakng_loader"),
    reason="espeakng_loader not installed in this environment")
def test_ensure_espeak_fallback_really_points_at_the_bundled_library():
    """Not a mock: this asserts the helper actually resolves the library that
    pip installed, which is the thing misaki's hardcoded Windows path misses."""
    out = kv.ensure_espeak_fallback()
    assert out["wired"] is True, out.get("detail")
    assert out["library"], "a wired fallback must name the library it found"
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    assert EspeakWrapper._ESPEAK_LIBRARY


# ------------------------------------- out-of-dictionary proper nouns

def test_attach_espeak_fallback_repairs_a_missing_fallback(fake_misaki):
    """Do not depend on kokoro wiring its own fallback. If the g2p we are handed
    has none, build one and attach it to the object we hold -- that is immune to
    import ordering, which is what made this bug hide."""
    class _G2P:
        fallback = None

    class _Pipe:
        g2p = _G2P()

    p = _Pipe()
    out = kv.attach_espeak_fallback(p)
    assert out["fallback"] is True, out.get("detail")
    assert out["repaired"] is True
    assert isinstance(p.g2p.fallback, fake_misaki)


def test_attach_espeak_fallback_leaves_an_existing_fallback_alone():
    sentinel = object()

    class _Pipe:
        g2p = type("g", (), {"fallback": sentinel})()

    p = _Pipe()
    out = kv.attach_espeak_fallback(p)
    assert out["fallback"] is True
    assert out["repaired"] is False
    assert p.g2p.fallback is sentinel


def test_load_repairs_a_fallbackless_pipeline_instead_of_refusing(monkeypatch):
    """A repairable pipeline must be repaired, not rejected -- refusing a voice
    that could work is its own kind of dishonesty."""
    tts = kv.KokoroTTS()
    monkeypatch.setattr(tts, "_resolve_device", lambda: "cpu")
    monkeypatch.setattr(kv, "ensure_espeak_fallback",
                        lambda: {"wired": True, "library": "x", "data": "y",
                                 "detail": ""})
    monkeypatch.setitem(sys.modules, "kokoro", _fake_kokoro_module(None))
    monkeypatch.setattr(kv, "attach_espeak_fallback",
                        lambda p: {"fallback": True, "repaired": True,
                                   "detail": ""})
    tts.load()
    assert tts._pipeline is not None


def test_load_refuses_when_the_fallback_cannot_be_repaired(monkeypatch):
    tts = kv.KokoroTTS()
    monkeypatch.setattr(tts, "_resolve_device", lambda: "cpu")
    monkeypatch.setattr(kv, "ensure_espeak_fallback",
                        lambda: {"wired": False, "library": "", "data": "",
                                 "detail": "no library"})
    monkeypatch.setitem(sys.modules, "kokoro", _fake_kokoro_module(None))
    monkeypatch.setattr(kv, "attach_espeak_fallback",
                        lambda p: {"fallback": False, "repaired": False,
                                   "detail": "espeak not importable"})
    with pytest.raises(kv.KokoroUnavailable) as ei:
        tts.load()
    assert ei.value.code == "local_voice_kokoro_no_g2p_fallback"
    assert "espeak not importable" in ei.value.message


@pytest.mark.skipif(not kv.kokoro_gpu_ready(),
                    reason="Kokoro GPU voice not available here")
def test_real_synthesis_handles_out_of_dictionary_proper_nouns():
    """The regression test for the crash itself, on real inference.

    Names are the class of word least likely to be in a pronunciation
    dictionary, and the class Friday says most. Numbers, times and paths all
    resolved fine while this was broken, which is exactly why it survived
    casual testing -- so the test uses names on purpose.
    """
    tts = kv.KokoroTTS()
    tts.load()
    for text in ("Kokoro", "Jere", "This is Kokoro.",
                 "Libby, Jere and Janet are on the call."):
        audio = tts.synthesize(text)
        assert audio, "no audio for %r" % text
