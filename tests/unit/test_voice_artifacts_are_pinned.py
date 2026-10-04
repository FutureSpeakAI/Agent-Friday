"""The local voice stack downloads only pinned, verified files, and nothing
in it phones home (local voice spec §6, §11 D1).

An artifact installs only with both pins set (source revision + SHA-256, or
an exact package version); a download whose hash differs is deleted and
nothing is installed; an archive cannot write outside its folder; the voice
worker processes run with the hub offline; the server turns hub telemetry off.
No network: the downloader is a fake.
"""
import hashlib
import inspect
import io
import tarfile

import pytest

from agent_friday.services import voice_artifacts as va
from agent_friday.services import voice_installer as vi


def test_every_d1_artifact_has_a_target_size_licence_and_source():
    for aid in ("voice-ear-streaming", "voice-vad-v6", "voice-front-4b",
                "voice-front-1.7b", "misaki", "voice-ear-turbo"):
        a = va.ARTIFACTS[aid]
        assert a["size_mb"] > 0 and a["licence"] and a.get("source"), aid
        assert aid in vi.TARGETS and vi.TARGETS[aid]["artifact"] == aid


def test_an_unpinned_artifact_is_refused_before_anything_downloads(monkeypatch):
    monkeypatch.setitem(va.ARTIFACTS, "voice-front-1.7b",
                        dict(va.ARTIFACTS["voice-front-1.7b"], revision="0123abc", sha256=None))
    fetched = []
    monkeypatch.setattr(vi, "_download", lambda *a, **k: fetched.append(a))
    out = vi.start("voice-front-1.7b")
    assert out["state"] == "error" and "SHA-256" in out["error"]
    assert fetched == []


def test_a_moving_url_is_never_pinned(monkeypatch):
    monkeypatch.setitem(va.ARTIFACTS, "voice-front-4b",
                        dict(va.ARTIFACTS["voice-front-4b"], revision=None,
                             sha256="0" * 64))
    ok, why = va.pinned("voice-front-4b")
    assert not ok and "revision" in why


def _pin(monkeypatch, tmp_path, payload, pinned_hash):
    monkeypatch.setitem(va.ARTIFACTS, "voice-front-1.7b",
                        dict(va.ARTIFACTS["voice-front-1.7b"], revision="0123abc",
                             sha256=pinned_hash))
    monkeypatch.setattr("agent_friday.core.runtime_dir", lambda: tmp_path / "runtime")

    def fake_download(url, dest, size_mb):
        assert "0123abc" in url
        dest.write_bytes(payload)
    monkeypatch.setattr(vi, "_download", fake_download)
    return tmp_path / "runtime" / va.ARTIFACTS["voice-front-1.7b"]["dest"]


def test_a_checksum_mismatch_deletes_the_download_and_installs_nothing(monkeypatch, tmp_path):
    dest = _pin(monkeypatch, tmp_path, b"tampered", "a" * 64)
    with pytest.raises(RuntimeError, match="failed its checksum"):
        vi._install_artifact("voice-front-1.7b")
    assert not dest.exists()
    assert not list(dest.parent.glob("*.download"))


def test_a_matching_file_is_installed(monkeypatch, tmp_path):
    payload = b"onnx-bytes"
    dest = _pin(monkeypatch, tmp_path, payload, hashlib.sha256(payload).hexdigest())
    vi._install_artifact("voice-front-1.7b")
    assert dest.read_bytes() == payload


def test_an_archive_cannot_write_outside_its_folder(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as t:
        data = b"x"
        info = tarfile.TarInfo("../escaped.txt")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    arc = tmp_path / "a.tar.bz2"
    arc.write_bytes(buf.getvalue())
    with pytest.raises(RuntimeError, match="refused archive member"):
        vi._safe_extract(arc, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_voice_workers_run_with_the_hub_offline():
    from agent_friday.services import voice_workers as vw
    src = inspect.getsource(vw.VoiceWorker._default_spawn)
    for k in ('env["HF_HUB_OFFLINE"] = "1"', 'env["TRANSFORMERS_OFFLINE"] = "1"',
              'env["HF_HUB_DISABLE_TELEMETRY"] = "1"'):
        assert k in src


def test_the_server_turns_hub_telemetry_off_before_core_imports():
    import pathlib
    src = (pathlib.Path(vi.__file__).resolve().parent.parent / "server.py").read_text(
        encoding="utf-8")
    assert src.index('os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")') < \
        src.index("import agent_friday.core as core")


def test_the_vad_is_the_pinned_package_that_ships_its_own_model():
    ok, why = va.pinned("voice-vad-v6")
    assert ok, why
    assert va.pip_spec("voice-vad-v6") == "silero-vad==6.2.1"
