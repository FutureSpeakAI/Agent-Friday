"""The readiness panel says what to do, not what the traceback said.

The panel's promise is that it tells someone what is wrong and how to fix it.
Answering with ``ImportError: cannot import name 'AlbertModel' from
'transformers'`` keeps that promise only for people who could have read the
log anyway — and in that particular case it is also actively misleading,
because transformers is installed and healthy and the real cause is a race at
startup that resolves itself.

These are the failures actually seen on this machine.
"""
import pytest

vm = pytest.importorskip("agent_friday.services.voice_manifest")

plain = vm.plain_language_refusal


def _msg(exc, noun="voice"):
    return plain(exc, noun)[0]


def test_the_startup_import_race_is_not_reported_as_a_broken_package():
    m = _msg(ImportError(
        "cannot import name 'AlbertModel' from 'transformers'"))
    assert "installed" in m, (
        "transformers IS present; telling someone it is missing sends them "
        "to reinstall a package they already have: %r" % m)
    assert "race" in m.lower()
    assert "30 seconds" in m, "it heals itself; say so, so nobody restarts"


def test_a_full_card_offers_the_cpu_rather_than_just_failing():
    m = _msg(RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"))
    assert "CPU" in m, (
        "the CPU path is the designed floor, not a failure state: %r" % m)
    assert "never" in m, "name the setting that gets them running now"


def test_a_missing_dependency_names_itself():
    m = _msg(ModuleNotFoundError("No module named 'kokoro'"))
    assert "kokoro" in m
    assert "Gemini" in m, "offer the way out that works this second"


def test_models_not_downloaded_is_not_described_as_broken():
    m = _msg(FileNotFoundError(
        "No such file or directory: 'kokoro-v1_0.onnx'"))
    assert "downloaded" in m.lower()
    assert "broken" not in m.lower()


def test_an_unrecognised_error_is_shown_verbatim_rather_than_guessed_at():
    """A comforting sentence that names the wrong cause is worse than the
    exception."""
    m = _msg(ValueError("something nobody has seen before"), "ear")
    assert "something nobody has seen before" in m
    assert "ear" in m


def test_every_refusal_offers_a_next_step():
    for exc in (ImportError("cannot import name 'X' from 'transformers'"),
                RuntimeError("CUDA out of memory"),
                ModuleNotFoundError("No module named 'kokoro'"),
                ValueError("unknown")):
        _m, action = plain(exc)
        assert action and action.get("label"), (
            "a dead end is not a readiness check: %r" % (exc,))


def test_the_prover_uses_it(monkeypatch):
    """The translation is worth nothing if the proof path still formats the
    exception itself."""
    import inspect
    src = inspect.getsource(vm.VoiceManifest.prove)
    assert "plain_language_refusal" in src, (
        "the generic failure path must translate, not re-format")
    assert "couldn't prove her {self._noun(stage)}" not in src, (
        "the raw-exception sentence must live in one place — the translator — "
        "or the next failure mode gets formatted twice, differently")
