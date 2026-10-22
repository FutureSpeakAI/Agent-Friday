"""North star §34.4-5: a local model downloaded at first run is checked
against the publisher's checksum, and a file that does not match is never
installed.

These drive the real download job (services/model_download.start_model and
run_job) for a real shortlist model, with only the file's address, size and
checksum pointed at a small file served from this machine, so no test fetches
gigabytes or touches the internet.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_friday.services import model_download as md
from agent_friday.services import model_shortlist as sl
from tests.unit.test_model_download import _Server, _ctrl, _gguf_bytes, store  # noqa: F401


def test_every_shortlist_file_carries_a_checksum_the_job_hands_the_downloader():
    data = json.loads(Path(sl.__file__).resolve().parents[1].joinpath(
        "resources", "model_shortlist.json").read_text(encoding="utf-8"))
    for m in data["models"]:
        for f in m.get("files") or []:
            assert len(str(f.get("sha256") or "")) == 64, (m["id"], f.get("file"))


def _job_for(monkeypatch, url: str, payload: bytes, sha: str) -> str:
    model_id = json.loads(Path(sl.__file__).resolve().parents[1].joinpath(
        "resources", "model_shortlist.json").read_text(encoding="utf-8"))["models"][0]["id"]
    real = sl.file_entry(model_id, None)
    fake = dict(real, bytes=len(payload), sha256=sha)
    monkeypatch.setattr(sl, "file_entry", lambda mid, packing=None: fake)
    monkeypatch.setattr(sl, "file_url", lambda mid, f: url)
    monkeypatch.setattr(sl, "companions", lambda mid: [])
    monkeypatch.setattr(md, "runtime_binary", lambda: Path("llama-server.exe"))
    job = md.start_model(model_id, start_thread=False)
    assert job["status"] == "queued", job
    assert job["items"][0]["sha256"] == sha
    return job["id"]


def test_a_matching_download_is_installed_and_says_it_was_checked(store, monkeypatch):  # noqa: F811
    payload = _gguf_bytes()
    srv = _Server(payload)
    try:
        jid = _job_for(monkeypatch, srv.url, payload, hashlib.sha256(payload).hexdigest())
        out = md.run_job(jid)
        assert out["status"] == "installed", out.get("error")
        assert out["items"][0]["checked"] == "sha256"
    finally:
        srv.stop()


def test_a_download_that_does_not_match_is_never_installed(store, monkeypatch):  # noqa: F811
    payload = _gguf_bytes()
    srv = _Server(payload)
    try:
        jid = _job_for(monkeypatch, srv.url, payload, "ab" * 32)
        out = md.run_job(jid)
        assert out["status"] == "failed" and "checksum mismatch" in (out.get("error") or "")
        dest = Path(out["items"][0]["dest"])
        assert not dest.exists() and not dest.with_name(dest.name + ".part").exists()
        assert not out.get("record"), "a file that failed its checksum was registered"
    finally:
        srv.stop()


def test_a_cut_off_download_keeps_its_part_and_finishes_on_resume(store, tmp_path):  # noqa: F811
    payload = _gguf_bytes()
    short = _Server(payload[:120_000])            # the connection ends early
    full = _Server(payload)
    try:
        dest = tmp_path / "gguf" / "m.gguf"
        sha = hashlib.sha256(payload).hexdigest()
        with pytest.raises(md.DownloadError) as ei:
            md.fetch_file(short.url, dest, expected_bytes=len(payload), expected_sha256=sha, ctrl=_ctrl())
        assert "size mismatch" in str(ei.value)
        part = dest.with_name("m.gguf.part")
        assert part.exists() and part.stat().st_size == 120_000, "the bytes already fetched are kept"
        assert not dest.exists()
        out = md.fetch_file(full.url, dest, expected_bytes=len(payload), expected_sha256=sha, ctrl=_ctrl())
        assert out["status"] == "done" and dest.read_bytes() == payload
        assert full.requests[0]["range"] == "bytes=120000-"
    finally:
        short.stop()
        full.stop()


def test_a_part_file_larger_than_the_model_is_thrown_away(store, tmp_path):  # noqa: F811
    payload = _gguf_bytes()
    srv = _Server(payload)
    try:
        dest = tmp_path / "gguf" / "m.gguf"
        dest.parent.mkdir(parents=True)
        dest.with_name("m.gguf.part").write_bytes(b"x" * (len(payload) + 10))
        out = md.fetch_file(srv.url, dest, expected_bytes=len(payload),
                            expected_sha256=hashlib.sha256(payload).hexdigest(), ctrl=_ctrl())
        assert out["status"] == "done" and dest.read_bytes() == payload
        assert srv.requests[0]["range"] is None, "the oversize part was restarted, not resumed"
    finally:
        srv.stop()
