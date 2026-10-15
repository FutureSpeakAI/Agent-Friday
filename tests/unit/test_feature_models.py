"""The models features depend on are declared once, installed only by a
visible, consented step, and a feature whose model is missing says so by name.

Before this, Kokoro, spaCy's English model and the whisper models were never
installed on a fresh machine and every failure was silent: media transcripts
counted eight failures in an in-memory dict and logged nothing, misaki was
pinned to 0.7.4 while the only Kokoro that installs beside numpy 2.x (0.9.4)
needs misaki[en]>=0.9.4, and inspect_audio quietly fetched whisper from the hub.

The behavioural tests go through the entry points that already existed
(media_transcripts.ensure_all, media_tools._whisper, podcast_render._whisper,
WhisperASR.load) so they fail on the old code by assertion, not by import.

No network: every model lives under tmp_path, faster_whisper is a stub, and the
downloader is a fake.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import logging
import pathlib
import re
import sys
import types
from collections import deque

import pytest

from agent_friday.services import local_voice as lv
from agent_friday.services import voice_artifacts as va
from agent_friday.services import voice_installer as vi

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _fm():
    from agent_friday.services import feature_models
    return feature_models


@pytest.fixture
def no_models(tmp_path, monkeypatch):
    """Empty model caches: Friday's whisper cache and the Hugging Face cache."""
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    monkeypatch.setattr(lv, "WHISPER_DIR", tmp_path / "whisper")
    monkeypatch.setattr(va, "hf_hub_cache", lambda: tmp_path / "hf", raising=False)
    try:
        fm = _fm()
    except ImportError:
        return tmp_path
    monkeypatch.setattr(fm, "_kokoro_files_present", lambda: False)
    with fm._LOCK:
        fm._REFUSALS.clear()
        fm._LOGGED.clear()
    return tmp_path


@pytest.fixture
def stub_whisper(monkeypatch):
    """faster_whisper whose WhisperModel records every construction and, like
    the real one with no model on disk, fails."""
    built = []

    class WhisperModel:
        def __init__(self, size_or_path, **kw):
            built.append((size_or_path, dict(kw)))
            raise RuntimeError("Unable to open file 'model.bin' in model '%s'" % size_or_path)

    mod = types.ModuleType("faster_whisper")
    mod.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", None)
    mod.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    return built


# ── (i) one set of pins, declared consistently ─────────────────────────────

def test_kokoro_and_misaki_pins_agree_with_each_other_and_with_numpy_2():
    a = va.ARTIFACTS
    assert "kokoro" in a, "the kokoro package itself was never a declared artifact"
    assert a["kokoro"]["kind"] == "pip" and a["kokoro"]["version"] == "0.9.4", (
        "0.9.4 is the only kokoro that does not pin numpy==1.26.4")
    # kokoro 0.9.4 declares misaki[en]>=0.9.4.
    m = a["misaki"]
    assert tuple(int(x) for x in m["version"].split(".")) >= (0, 9, 4), m["version"]
    assert "en" in (m.get("extras") or []), "kokoro needs misaki's English extra"
    assert va.pip_spec("misaki") == "misaki[en]==%s" % m["version"]
    assert "misaki" in a["kokoro"]["requires"]
    assert {"spacy-en-core-web-sm", "espeakng-loader"} <= set(m["requires"])
    sm = a["spacy-en-core-web-sm"]
    assert sm["version"] == "3.8.0" and re.fullmatch(r"[0-9a-f]{64}", sm["sha256"] or "")
    assert va.pinned("kokoro") == (True, "") and va.pinned("misaki") == (True, "")


def test_no_declared_file_pins_a_different_kokoro_misaki_or_an_old_numpy():
    declared = [ROOT / "pyproject.toml", ROOT / "requirements.txt"]
    declared += sorted((ROOT / "packaging" / "windows" / "requirements").glob("*.txt"))
    want = {"kokoro": va.ARTIFACTS["kokoro"]["version"],
            "misaki": va.ARTIFACTS["misaki"]["version"]}
    for f in declared:
        text = f.read_text(encoding="utf-8", errors="replace")
        for pkg, ver in want.items():
            for got in re.findall(r"(?im)^\s*\"?%s(?:\[[^\]]*\])?\s*==\s*([0-9.]+)" % pkg, text):
                assert got == ver, "%s pins %s==%s; voice_artifacts says %s" % (f.name, pkg, got, ver)
        assert not re.search(r"(?im)^\s*\"?numpy\s*==\s*1\.", text), (
            "%s pins numpy 1.x, which kokoro 0.9.4's stack does not need" % f.name)
    # The repair line every Kokoro message gives is the same pinned set.
    from agent_friday.services import kokoro_voice as kv
    assert "kokoro==0.9.4" in kv.KOKORO_PIP and "misaki[en]==%s" % want["misaki"] in kv.KOKORO_PIP
    src = (ROOT / "src" / "agent_friday" / "services" / "kokoro_voice.py").read_text(encoding="utf-8")
    assert "pip install kokoro misaki espeakng-loader" not in src, "an unpinned repair line"


def test_the_lockfile_numpy_is_2x_where_the_app_runs():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    versions = re.findall(r'(?m)^name = "numpy"\nversion = "([0-9.]+)"', lock)
    assert versions and all(int(v.split(".")[0]) >= 2 for v in versions), versions


def test_kokoro_is_not_installed_over_a_missing_torch(monkeypatch):
    """pip would pull a multi-gigabyte torch for a one-megabyte package."""
    monkeypatch.setattr(vi, "_missing_modules", lambda aid, _seen=None: ["torch"])
    ran = []
    monkeypatch.setattr(vi, "_run_pip_stage", lambda args: ran.append(args) or 0)
    out = vi.start("kokoro")
    assert out["state"] == "error" and "PyTorch" in out["error"]
    assert "Nothing was downloaded" in out["error"] and ran == []


# ── (ii) the registry, its status, and a visible install ───────────────────

def test_the_registry_lists_what_each_feature_is_missing_with_size_and_target(no_models):
    fm = _fm()
    st = {f["id"]: f for f in fm.status()["features"]}
    tx = st["transcription"]
    assert tx["ready"] is False
    assert [m["id"] for m in tx["missing"]] == ["whisper-base.en"]
    assert tx["size_mb"] == va.ARTIFACTS["whisper-base.en"]["size_mb"] > 100
    assert tx["install_target"] == "feature:transcription" and tx["install_target"] in vi.TARGETS
    assert "whisper base.en" in tx["message"] and "Settings > Voice" in tx["message"]
    assert [m["id"] for m in st["dictation"]["missing"]] == ["whisper-base"]
    assert "kokoro-82m" in [m["id"] for m in st["kokoro_voice"]["missing"]]


def test_every_hf_model_is_pinned_to_a_commit_and_a_hash_per_file():
    for aid in ("whisper-base.en", "whisper-base", "whisper-small", "kokoro-82m"):
        a = va.ARTIFACTS[aid]
        assert a["kind"] == "hf_files" and re.fullmatch(r"[0-9a-f]{40}", a["revision"]), aid
        assert all(n > 0 and re.fullmatch(r"[0-9a-f]{64}", h) for n, h in a["files"].values()), aid
        assert a["size_bytes"] == sum(n for n, _h in a["files"].values())
        assert va.pinned(aid) == (True, ""), aid
    assert len([p for p in va.ARTIFACTS["kokoro-82m"]["files"] if p.startswith("voices/")]) == 54


def test_a_feature_install_lays_out_a_verified_snapshot_the_loader_finds(no_models, monkeypatch):
    fm = _fm()
    payloads = {"config.json": b"{}", "model.bin": b"weights" * 100,
                "tokenizer.json": b"{\"t\":1}", "vocabulary.txt": b"a\nb\n"}
    files = {p: (len(b), hashlib.sha256(b).hexdigest()) for p, b in payloads.items()}
    monkeypatch.setitem(va.ARTIFACTS, "whisper-base.en",
                        dict(va.ARTIFACTS["whisper-base.en"], files=files,
                             size_bytes=sum(n for n, _h in files.values())))
    monkeypatch.setattr(vi, "_log_path", lambda: no_models / "install.log")
    fetched = []

    def fake_download(url, dest, size_mb, base=0, grand_total=0):
        name = url.rsplit("/", 1)[-1]
        assert "/resolve/%s/" % va.ARTIFACTS["whisper-base.en"]["revision"] in url
        fetched.append(name)
        dest.write_bytes(payloads[name])
    monkeypatch.setattr(vi, "_download", fake_download)
    vi._install_artifact("whisper-base.en")
    snap = fm.whisper_model_dir("base.en")
    assert snap is not None and (snap / "model.bin").read_bytes() == payloads["model.bin"]
    assert sorted(fetched) == sorted(payloads)
    assert vi.artifact_installed("whisper-base.en") and fm.ready("transcription")


def test_a_snapshot_with_a_bad_file_is_never_pointed_at(no_models, monkeypatch):
    files = {"config.json": (2, hashlib.sha256(b"{}").hexdigest()),
             "model.bin": (3, "a" * 64)}
    monkeypatch.setitem(va.ARTIFACTS, "whisper-base.en",
                        dict(va.ARTIFACTS["whisper-base.en"], files=files, size_bytes=5))
    monkeypatch.setattr(vi, "_log_path", lambda: no_models / "install.log")
    monkeypatch.setattr(vi, "_download", lambda url, dest, *a, **k: dest.write_bytes(
        b"{}" if url.endswith("config.json") else b"bad"))
    with pytest.raises(RuntimeError, match="failed its checksum"):
        vi._install_artifact("whisper-base.en")
    snap = va.snapshot_dir("whisper-base.en")
    assert not (snap.parent.parent / "refs" / "main").exists()
    assert not (snap / "model.bin").exists()
    assert not vi.artifact_installed("whisper-base.en")


def test_the_install_route_needs_consent_for_a_feature_target_and_status_is_served():
    src = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    assert "_t.get(\"artifact\") or _t.get(\"feature\")" in src
    assert "/api/voice/feature-models" in src
    ui = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "/api/voice/feature-models" in ui and "function FeatureModelsNeeded" in ui


# ── (iii) a missing model fails loudly, by name, never silently ────────────

def _fresh_queue(monkeypatch, mt):
    monkeypatch.setattr(mt, "transcriber", None)
    monkeypatch.setattr(mt, "_MODEL", None)
    monkeypatch.setattr(mt, "_QUEUE", deque())
    monkeypatch.setattr(mt, "_QUEUED", set())
    monkeypatch.setattr(mt, "_STATE", {"done": 0, "failed": 0, "skipped": 0, "building": None,
                                       "last": None, "unavailable": ""})


def test_media_transcripts_name_the_missing_whisper_model_in_the_log(
        no_models, stub_whisper, monkeypatch, caplog):
    import agent_friday.core as core
    from agent_friday.services import media_transcripts as mt
    monkeypatch.setenv("FRIDAY_TESTING", "1")           # no background worker
    monkeypatch.setattr(core, "FRIDAY_DIR", no_models / ".friday")
    _fresh_queue(monkeypatch, mt)
    clip = no_models / "clip.wav"
    clip.write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(mt, "_path_for", lambda card: clip)
    monkeypatch.setattr(mt, "_json_path", lambda card: no_models / "tx" / (card["id"] + ".json"))
    cards = [{"id": "audio-%d" % i, "kind": "audio", "details": {"duration_s": 4}} for i in range(3)]
    with caplog.at_level(logging.WARNING):
        st = mt.ensure_all(cards, sync=True)
    named = [r for r in caplog.records if r.levelno >= logging.WARNING
             and r.name.startswith("friday") and "whisper base.en" in r.getMessage()]
    assert named, "friday.log must say which model is missing: %r" % [
        r.getMessage() for r in caplog.records]
    assert "not installed" in named[0].getMessage()
    why = st.get("unavailable") or ""
    assert "Transcription unavailable" in why and "whisper base.en" in why
    assert "Settings > Voice" in why
    assert stub_whisper == [], "no model load is attempted when the model is not there"
    assert st["pending"] == 0 and st["failed"] == 1, "one named refusal, not a failure per file"


def test_media_transcripts_are_not_available_without_their_model(no_models, stub_whisper, monkeypatch):
    """The package alone is not a recogniser: the old check said available,
    queued every file, and each one failed."""
    from agent_friday.services import media_transcripts as mt
    _fresh_queue(monkeypatch, mt)
    assert mt.available() is False
    from agent_friday.services import media_convert as mc
    asr = mc.backends()["asr"]
    assert asr["available"] is False and "whisper base.en" in asr["reason"]


def test_inspect_audio_never_fetches_whisper_from_the_hub(no_models, stub_whisper, monkeypatch):
    from agent_friday.services import media_tools
    monkeypatch.setattr(media_tools, "_WHISPER", None)
    with pytest.raises(Exception) as e:
        media_tools._whisper()
    assert stub_whisper == [], "WhisperModel('base.en') without local_files_only downloads it"
    assert "whisper base.en" in str(e.value) and "not installed" in str(e.value)


def test_the_podcast_check_names_the_missing_model_without_loading(no_models, stub_whisper, monkeypatch):
    from agent_friday.services import podcast_render as pr
    monkeypatch.setattr(pr, "_WHISPER", None)
    with pytest.raises(pr.RenderError) as e:
        pr._whisper()
    assert e.value.code == "check_model_missing" and "whisper base.en" in str(e.value)
    assert stub_whisper == []


def test_push_to_transcribe_names_the_missing_model_instead_of_downloading(
        no_models, stub_whisper, monkeypatch, caplog):
    asr = lv.WhisperASR("auto", force_cpu=True)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(Exception) as e:
            asr.load()
    assert "Dictation unavailable" in str(e.value) and "whisper base" in str(e.value)
    assert stub_whisper == [], "no WhisperModel, so no first-run download"
    assert any("whisper base" in r.getMessage() and r.name.startswith("friday")
               for r in caplog.records)
    from agent_friday.services.voice_manifest import plain_language_refusal
    msg, _action = plain_language_refusal(e.value, "ear")
    assert msg == str(e.value), "the Alt+T route shows the named reason, not an error id"


# ── (iv) the size shown is the size downloaded; old installs keep working ──

def test_the_confirmed_size_counts_the_python_dependencies_pip_adds(no_models, monkeypatch):
    """misaki[en] 0.9.4 and kokoro 0.9.4 install with their dependencies
    (spaCy, thinc, blis; transformers). The confirm used to say 4 MB and 1 MB."""
    import importlib.util
    real = importlib.util.find_spec
    absent = {"spacy", "num2words", "phonemizer", "transformers", "loguru"}
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: None if name in absent else real(name, *a, **k))
    monkeypatch.setattr(vi, "artifact_installed", lambda aid: False)
    monkeypatch.setattr(va, "pip_state", lambda aid: None, raising=False)
    fm = _fm()
    kv = {f["id"]: f for f in fm.status()["features"]}["kokoro_voice"]
    rows = {m["id"]: m for m in kv["missing"]}
    assert rows["misaki"].get("deps_mb", 0) >= 24, "spaCy, blis and thinc alone are ~24 MB of wheels"
    assert "spaCy" in (rows["misaki"].get("deps_note") or "")
    assert rows["kokoro"].get("deps_mb", 0) >= 15, "transformers and tokenizers"
    assert kv["size_mb"] == sum(m["size_mb"] + m.get("deps_mb", 0) for m in kv["missing"])
    assert "about" in kv["message"], "an estimate is called one"
    pub = {r["id"]: r for r in va.public_rows()}
    assert pub["misaki"].get("deps_mb", 0) >= 24 and pub["misaki"].get("deps_note")
    ui = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "m.deps_note" in ui and "x.deps_note" in ui, "both confirm boxes name the dependencies"


def test_dependencies_already_here_are_not_counted(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
    assert va.deps_mb("misaki") == 0 and va.deps_note("misaki") == ""


def test_an_earlier_kokoro_install_is_upgrade_available_not_missing(no_models, monkeypatch):
    """An install from before the 0.9.4 pins (kokoro 0.7.x, misaki 0.7.4)
    still speaks. It used to show "Kokoro voice needs ... misaki[en] 0.9.4
    (0.7.4 is installed)" and refuse; now it runs, with an upgrade offered."""
    from importlib import metadata
    have = {"kokoro": "0.7.16", "misaki": "0.7.4", "espeakng-loader": "0.2.4",
            "en-core-web-sm": "3.8.0"}
    real = metadata.version
    monkeypatch.setattr(metadata, "version", lambda name: have[name] if name in have else real(name))
    fm = _fm()
    monkeypatch.setattr(fm, "_kokoro_files_present", lambda: True)
    kv = {f["id"]: f for f in fm.status()["features"]}["kokoro_voice"]
    assert kv["ready"] is True and kv["missing"] == [] and kv["message"] == ""
    ups = kv.get("upgrades") or []
    assert sorted(m["id"] for m in ups) == ["kokoro", "misaki"]
    assert "0.7.4 is installed" in " ".join(m["short"] for m in ups)
    fm.require("kokoro_voice", who="test")          # the runtime keeps working
    assert fm.to_install("kokoro_voice") == ["kokoro", "misaki"], "Upgrade fetches the pins"
    rows = {r["id"]: r for r in vi.artifact_rows()}
    assert rows["misaki"]["installed"] is True
    assert rows["misaki"].get("upgrade") == {"have": "0.7.4", "pin": "0.9.4"}


def test_a_version_older_than_any_working_one_is_still_missing(no_models, monkeypatch):
    from importlib import metadata
    real = metadata.version
    monkeypatch.setattr(metadata, "version",
                        lambda name: "0.6.0" if name == "misaki" else real(name))
    assert va.pip_state("misaki") is None
    assert "misaki" in _fm().missing_artifacts("kokoro_voice")


# ── (v) a fresh install is offered whisper where it sets voice up ──────────

def test_the_first_run_voice_step_offers_both_whisper_models_with_sizes(no_models):
    fm = _fm()
    offer = fm.first_run_offer()
    assert [i["id"] for i in offer["items"]] == ["whisper-base", "whisper-base.en"]
    assert offer["size_mb"] == sum(va.ARTIFACTS[a]["size_mb"] for a in ("whisper-base", "whisper-base.en"))
    text = offer["consent_text"]
    assert "whisper base.en" in text and "whisper base " in text and "%s MB" % offer["size_mb"] in text
    assert "Piper" in text, "the voice the engine fetches after them is named too"
    assert offer["target"] == "tier1-models" and vi.TARGETS["tier1-models"].get("consent") is True


def test_the_wizard_download_installs_both_whisper_models(no_models, monkeypatch):
    fetched = []
    monkeypatch.setattr(vi, "_install_artifact", lambda aid: fetched.append(aid))
    monkeypatch.setattr(vi, "_log_path", lambda: no_models / "install.log")

    class Eng:
        last_error = ""

        def ensure_ready(self, progress=None):
            return True
    monkeypatch.setattr(lv, "get_local_voice_engine", lambda: Eng())
    monkeypatch.setattr(vi, "_JOB", dict(vi._JOB, state="running", log=[]))
    vi._run_job("tier1-models")
    assert fetched == ["whisper-base", "whisper-base.en"], fetched
    assert vi.status()["state"] == "done"


def test_a_stray_file_in_the_whisper_cache_is_not_a_ready_ear(no_models, monkeypatch):
    """Readiness said "models downloaded" when anything sat in the folder,
    so the wizard offered nothing while Alt+T refused."""
    base = no_models / "whisper" / "models--Systran--faster-whisper-base"
    (base / "blobs").mkdir(parents=True)
    (base / "blobs" / "partial.incomplete").write_bytes(b"x")
    piper = no_models / "piper"
    piper.mkdir()
    (piper / "en_US-amy-medium.onnx").write_bytes(b"x")
    (piper / "en_US-amy-medium.onnx.json").write_text("{}")
    monkeypatch.setattr(lv, "PIPER_DIR", piper)
    eng = lv.LocalVoiceEngine()
    monkeypatch.setattr(eng, "_settings", lambda: {})
    assert eng.models_ready() is False
    snap = base / "snapshots" / ("a" * 40)
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"w")
    (snap / "config.json").write_text("{}")
    assert eng.models_ready() is True


# ── push-to-transcribe returns constant text only ────────────────────────────

def _transcribe_client(monkeypatch, exc):
    from flask import Flask
    import agent_friday.routes.voice as rv

    class _ASR:
        def transcribe(self, pcm):
            raise exc

    class _Eng:
        def _get_asr(self):
            return _ASR()
    monkeypatch.setattr(lv, "get_local_voice_engine", lambda: _Eng())
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session on loopback
    app.register_blueprint(rv.voice_bp)
    return app.test_client()


def test_push_to_transcribe_names_a_missing_model_from_the_registry(monkeypatch):
    """A missing model is answered with the registry's fixed sentence for that
    feature, never with the exception's own text."""
    fm = _fm()
    secret_marker = "detail-from-the-exception-xyz"
    exc = fm.FeatureModelMissing("dictation", ["whisper-base"], secret_marker)
    client = _transcribe_client(monkeypatch, exc)
    r = client.post("/api/voice/transcribe", data=b"\x01\x00" * 1600,
                    content_type="application/octet-stream")
    assert r.status_code == 503
    body = r.get_json()
    assert body["error"] == fm.FEATURE_MESSAGES["dictation"]
    assert "Dictation unavailable" in body["error"]
    assert fm.SETTINGS_PATH in body["error"]
    assert secret_marker not in r.get_data(as_text=True)


def test_push_to_transcribe_never_returns_other_exception_text(monkeypatch):
    marker = "C:/some/internal/path-xyz"
    client = _transcribe_client(monkeypatch, RuntimeError(marker))
    r = client.post("/api/voice/transcribe", data=b"\x01\x00" * 1600,
                    content_type="application/octet-stream")
    assert r.status_code == 503
    assert marker not in r.get_data(as_text=True)
    assert "friday.log" in r.get_json()["error"]


def test_public_missing_message_falls_back_for_an_unknown_feature():
    fm = _fm()
    assert fm.public_missing_message("no-such-feature") == fm.MISSING_MODEL_MESSAGE
    assert set(fm.FEATURE_MESSAGES) == set(fm.FEATURES)
