"""spaCy's small English model is part of the voice install, never a download
at load.

misaki's English G2P downloads en_core_web_sm silently on first use when it is
missing. Loads now refuse instead (KokoroTTS.load, the podcast CPU Kokoro), so
the consented install path has to provide it: installing misaki from
Settings > Voice installs the model too.
"""
import pytest

from agent_friday.services import kokoro_voice as kv
from agent_friday.services import podcast_render as pr
from agent_friday.services import voice_artifacts as va
from agent_friday.services import voice_installer as vi

URL = ("https://github.com/explosion/spacy-models/releases/download/"
       "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl")


def test_installing_misaki_installs_the_english_model_first(monkeypatch):
    specs = []
    monkeypatch.setattr(vi, "_run_pip_stage", lambda args: specs.append(args[-1]) or 0)
    vi._install_artifact("misaki")
    assert specs == ["en-core-web-sm @ " + URL, "misaki==0.7.4"]


def test_the_model_is_a_declared_pinned_mit_artifact():
    a = va.ARTIFACTS["spacy-en-core-web-sm"]
    assert a["licence"] == "MIT" and a["version"] == "3.8.0" and a["size_mb"] > 0
    assert va.pinned("spacy-en-core-web-sm") == (True, "")
    assert "spacy-en-core-web-sm" in va.ARTIFACTS["misaki"]["requires"]
    rows = {r["id"]: r for r in va.public_rows()}
    assert rows["spacy-en-core-web-sm"]["installable"] and rows["spacy-en-core-web-sm"]["purpose"]


def test_a_hash_pin_goes_into_the_requirement(monkeypatch):
    monkeypatch.setitem(va.ARTIFACTS, "spacy-en-core-web-sm",
                        dict(va.ARTIFACTS["spacy-en-core-web-sm"], sha256="ab" * 32))
    assert va.pip_spec("spacy-en-core-web-sm").endswith("#sha256=" + "ab" * 32)


def test_the_model_line_matches_the_spacy_minor_the_app_installs():
    from importlib import metadata
    try:
        spacy = metadata.version("spacy")
    except metadata.PackageNotFoundError:
        pytest.skip("spaCy not installed here")
    assert spacy.split(".")[:2] == va.ARTIFACTS["spacy-en-core-web-sm"]["version"].split(".")[:2]


def test_the_licence_is_listed():
    import pathlib
    text = (pathlib.Path(va.__file__).resolve().parents[3] / "THIRD_PARTY_LICENSES.md").read_text(
        encoding="utf-8")
    row = [ln for ln in text.splitlines() if ln.startswith("| en_core_web_sm")]
    assert row and "| MIT |" in row[0]


def test_the_podcast_cpu_kokoro_refuses_instead_of_letting_misaki_download(monkeypatch):
    import sys
    import types
    built = []
    fake = types.ModuleType("kokoro")
    fake.KPipeline = lambda **k: built.append(k)
    monkeypatch.setitem(sys.modules, "kokoro", fake)
    monkeypatch.setattr(kv, "english_g2p_model_installed", lambda: False)
    with pytest.raises(pr.RenderError) as e:
        pr.CpuKokoro()._pipe("a")
    assert e.value.code == "voice_model_missing" and "never downloads" in str(e.value)
    assert built == [], "no pipeline was constructed"
