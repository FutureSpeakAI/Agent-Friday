"""The one way a model file arrives: resumable, verified, registered, quiet.

Every rule in services/model_download.py's docstring has a test here. The
downloads run against a local HTTP server that honours byte ranges, so no
test fetches anything from the internet; the identity test captures the
request instead of sending it.
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agent_friday.services import model_download as md
from agent_friday.services import model_shortlist as sl
from agent_friday.services import model_store
from tests.unit.test_update_check import _flatten_request, _identity_leaks


# ── a tiny range-capable file server ─────────────────────────────────────────

class _Server:
    def __init__(self, payload: bytes, *, honour_range=True, serve_sha=None):
        self.payload = payload
        self.honour_range = honour_range
        self.requests = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rng = self.headers.get("Range")
                srv.requests.append({"path": self.path, "range": rng,
                                     "headers": dict(self.headers)})
                body = srv.payload
                if rng and srv.honour_range:
                    start = int(rng.split("=")[1].rstrip("-").split("-")[0])
                    if start >= len(body):
                        self.send_response(416)
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(body) - 1, len(body)))
                    body = body[start:]
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/file.gguf" % self.port

    def stop(self):
        self.httpd.shutdown()


def _gguf_bytes(n=300_000) -> bytes:
    # A GGUF magic plus filler: enough for the store's header reader to
    # recognise the file without parsing a real model.
    return b"GGUF" + (3).to_bytes(4, "little") + (0).to_bytes(8, "little") + (0).to_bytes(8, "little") + b"\x00" * (n - 24)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(model_store, "registry_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(md, "_disk_refusal", lambda profile, total: None)
    monkeypatch.setattr(model_store, "describe", lambda p: {
        "path": str(p), "size_bytes": Path(p).stat().st_size, "architecture": "qwen35",
        "quantization": "PTQ1_0", "can_generate": True, "is_embedding": False})
    md._JOBS.clear()
    md._CTRL.clear()
    return tmp_path


def _ctrl():
    return {"pause": threading.Event(), "cancel": threading.Event()}


# ── fetch_file ───────────────────────────────────────────────────────────────

def test_a_download_is_verified_and_renamed_atomically(store, tmp_path):
    payload = _gguf_bytes()
    srv = _Server(payload)
    try:
        dest = tmp_path / "gguf" / "m.gguf"
        out = md.fetch_file(srv.url, dest, expected_bytes=len(payload),
                            expected_sha256=hashlib.sha256(payload).hexdigest(), ctrl=_ctrl())
        assert out["status"] == "done" and out["checked"] == "sha256"
        assert dest.read_bytes() == payload
        assert not dest.with_name("m.gguf.part").exists()
    finally:
        srv.stop()


def test_an_interrupted_download_resumes_from_its_part_file(store, tmp_path):
    payload = _gguf_bytes()
    srv = _Server(payload)
    try:
        dest = tmp_path / "gguf" / "m.gguf"
        dest.parent.mkdir(parents=True)
        dest.with_name("m.gguf.part").write_bytes(payload[:100_000])
        out = md.fetch_file(srv.url, dest, expected_bytes=len(payload),
                            expected_sha256=hashlib.sha256(payload).hexdigest(), ctrl=_ctrl())
        assert out["status"] == "done"
        assert srv.requests[0]["range"] == "bytes=100000-", "the fetch asked for the rest, not the whole file"
        assert dest.read_bytes() == payload
    finally:
        srv.stop()


def test_a_server_that_ignores_the_range_restarts_the_file(store, tmp_path):
    payload = _gguf_bytes()
    srv = _Server(payload, honour_range=False)
    try:
        dest = tmp_path / "gguf" / "m.gguf"
        dest.parent.mkdir(parents=True)
        dest.with_name("m.gguf.part").write_bytes(b"junk" * 1000)
        out = md.fetch_file(srv.url, dest, expected_bytes=len(payload),
                            expected_sha256=hashlib.sha256(payload).hexdigest(), ctrl=_ctrl())
        assert out["status"] == "done" and dest.read_bytes() == payload
    finally:
        srv.stop()


def test_a_checksum_mismatch_installs_nothing(store, tmp_path):
    payload = _gguf_bytes()
    srv = _Server(payload)
    try:
        dest = tmp_path / "gguf" / "m.gguf"
        with pytest.raises(md.DownloadError) as ei:
            md.fetch_file(srv.url, dest, expected_bytes=len(payload),
                          expected_sha256="0" * 64, ctrl=_ctrl())
        assert "checksum mismatch" in str(ei.value)
        assert not dest.exists() and not dest.with_name("m.gguf.part").exists()
    finally:
        srv.stop()


def test_a_cancel_removes_the_part_file(store, tmp_path):
    payload = _gguf_bytes(5_000_000)
    srv = _Server(payload)
    try:
        dest = tmp_path / "gguf" / "m.gguf"
        ctrl = _ctrl()
        ctrl["cancel"].set()
        out = md.fetch_file(srv.url, dest, expected_bytes=len(payload),
                            expected_sha256=None, ctrl=ctrl)
        assert out["status"] == "cancelled"
        assert not dest.exists() and not dest.with_name("m.gguf.part").exists()
    finally:
        srv.stop()


# ── the job: registration with the engine ────────────────────────────────────

def test_a_shortlist_download_registers_the_model_with_its_engine(store, tmp_path, monkeypatch):
    payload = _gguf_bytes()
    srv = _Server(payload)
    try:
        entry = {"id": "bonsai2:27b", "label": "Bonsai 2 27B", "runtime": "prism-fork",
                 "repo": "x/y", "licence": "Apache-2.0",
                 "serve": {"serve_num_ctx": 131072, "serve_args": ["-b", "4096"]},
                 "files": [{"file": "m.gguf", "packing": "PTQ1_0", "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(), "default": True}]}
        monkeypatch.setattr(sl, "get", lambda mid: dict(entry) if mid == "bonsai2:27b" else None)
        monkeypatch.setattr(sl, "file_url", lambda mid, f: srv.url)
        monkeypatch.setattr(sl, "companions", lambda mid: [])
        engine = tmp_path / "fork" / "llama-server.exe"
        job = md.start_model("bonsai2:27b", engine_override=str(engine), start_thread=False)
        assert job["status"] == "queued"
        out = md.run_job(job["id"])
        assert out["status"] == "installed", out.get("error")
        rec = model_store.get("bonsai2:27b")
        assert rec["engine"] == str(engine), "the Arbiter must spawn the fork, not stock llama.cpp"
        assert rec["serve_args"] == ["-b", "4096"] and rec["serve_num_ctx"] == 131072
        assert rec["sha256"] == hashlib.sha256(payload).hexdigest()
        assert rec["source"] == model_store.SOURCE_DOWNLOAD
        assert Path(rec["path"]).exists()
    finally:
        srv.stop()


def test_the_disk_floor_refuses_before_any_request(store, monkeypatch):
    calls = []
    monkeypatch.setattr(md, "_http_get", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("no request")))
    monkeypatch.setattr(md, "_disk_refusal", lambda profile, total: "only 6 GB free; the floor is 10 GB")
    job = md.start_model("bonsai2:27b")
    assert job["status"] == "refused" and job["rule_id"] == "R8"
    assert "floor" in job["error"] and calls == []


# ── zero telemetry ───────────────────────────────────────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def iter_content(self, chunk_size=1):
        yield self._body

    def close(self):
        pass


def test_outbound_requests_carry_nothing_identifying(store, tmp_path, monkeypatch):
    """The same promise the update check makes, for every weight fetch."""
    calls = []
    payload = _gguf_bytes(2048)

    def _fake_get(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return _Resp(payload)
    monkeypatch.setattr(md, "_http_get", _fake_get)
    from agent_friday.services import hardware_profile as hwp
    monkeypatch.setattr(hwp, "get", lambda *a, **k: {"gpus": [{"name": "Secret GPU 9000", "vendor": "nvidia"}],
                                                     "cpu": {"model": "Secret CPU"}, "ram": {"total_mib": 1}})
    md.fetch_file("https://huggingface.co/prism-ml/x/resolve/main/m.gguf", tmp_path / "gguf" / "m.gguf",
                  expected_bytes=len(payload), expected_sha256=hashlib.sha256(payload).hexdigest(), ctrl=_ctrl())
    assert len(calls) == 1
    call = calls[0]
    kw = call["kwargs"]
    assert "?" not in call["url"] and not kw.get("params")
    headers = {k.lower(): v for k, v in (kw.get("headers") or {}).items()}
    assert set(headers) <= {"range"}, "only a byte range may be added to the request"
    assert not kw.get("cookies") and not kw.get("auth") and not kw.get("data") and not kw.get("json")
    blob = _flatten_request(call)
    assert "secret gpu" not in blob.lower() and "secret cpu" not in blob.lower()
    assert _identity_leaks(blob, our_version="9.9.9") == []


# ── the shortlist and the runtime assets ─────────────────────────────────────

def test_the_shortlist_bonsai_entry_matches_the_tier_manifest():
    tiers = json.load(open(Path(__file__).resolve().parents[2] / "docs" / "design" / "active" /
                           "bonsai2-tiers.json", encoding="utf-8"))
    manifest = {m["file"]: m for m in tiers["model_family"]["bonsai2"]["manifest"] if m.get("sha256")}
    sl.reload_for_tests()
    b = sl.get("bonsai2:27b")
    assert b["friday_standard"] is True
    for f in b["files"] + b["companions"]:
        assert f["bytes"] == manifest[f["file"]]["bytes"], f["file"]
        assert f["sha256"] == manifest[f["file"]]["sha256"], f["file"]
    assert sl.file_entry("bonsai2:27b")["packing"] == "PTQ1_0"
    assert sl.file_url("bonsai2:27b", sl.file_entry("bonsai2:27b")).startswith(
        "https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/main/")


def test_the_runtime_asset_is_picked_per_os_and_backend_with_its_digest():
    release = {"assets": [
        {"name": "llama-prism-b10743-bin-win-cuda-12.4-x64.zip", "size": 10,
         "digest": "sha256:" + "a" * 64, "browser_download_url": "https://github.com/x/a.zip"},
        {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "size": 20,
         "digest": "sha256:" + "b" * 64, "browser_download_url": "https://github.com/x/b.zip"},
        {"name": "llama-prism-b10743-bin-win-cpu-x64.zip", "size": 5,
         "digest": "sha256:" + "c" * 64, "browser_download_url": "https://github.com/x/c.zip"},
    ]}
    picked = sl.pick_release_assets(release, "cuda", "windows")
    assert [p["name"] for p in picked] == ["llama-prism-b10743-bin-win-cuda-12.4-x64.zip",
                                           "cudart-llama-bin-win-cuda-12.4-x64.zip"]
    assert picked[0]["sha256"] == "a" * 64
    assert [p["name"] for p in sl.pick_release_assets(release, "cpu", "windows")] == \
        ["llama-prism-b10743-bin-win-cpu-x64.zip"]
    assert sl.backend_for({"gpus": [{"vendor": "nvidia"}]}) in ("cuda", "metal")
    assert sl.backend_for({"gpus": []}) in ("cpu", "metal")


def test_the_time_estimate_is_unknown_until_a_download_was_measured(store):
    assert md.estimate_seconds(10 ** 9)["basis"] == "unknown"
    md._record_rate(10.0)
    est = md.estimate_seconds(10 * 1048576)
    assert est["basis"] == "measured" and est["seconds"] == 1
