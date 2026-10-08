"""Every local voice artifact is pinned or says why not; a download is verified
against its pinned SHA-256 and resumes from a partial file.

A local HTTP server on 127.0.0.1 stands in for the publisher; nothing leaves
the machine and nothing is written outside tmp_path.
"""
import hashlib
import http.server
import re
import threading

import pytest

from agent_friday.services import voice_artifacts as va
from agent_friday.services import voice_installer as vi

PINNED = ("voice-ear-streaming", "sherpa-onnx", "voice-vad-v6", "voice-front-4b",
          "voice-front-1.7b", "misaki", "espeak-ng-helper", "espeakng-loader")
UNPINNED_WITH_REASON = ("voice-ear-turbo",)


def test_every_artifact_is_pinned_or_reports_why_not():
    rows = {r["id"]: r for r in va.public_rows()}
    assert set(rows) == set(va.ARTIFACTS)
    for aid, r in rows.items():
        if r["installable"]:
            assert r["pinned"] is True and r["why_not"] == "", aid
        else:
            assert r["why_not"].strip(), "%s is unpinned without a reason" % aid
    for aid in PINNED:
        assert rows[aid]["installable"], "%s lost its pin: %s" % (aid, rows[aid]["why_not"])
    for aid in UNPINNED_WITH_REASON:
        assert not rows[aid]["installable"]


def test_pins_are_exact_not_moving():
    for aid, a in va.ARTIFACTS.items():
        if a["kind"] == "pip":
            continue
        if a.get("sha256"):
            assert re.fullmatch(r"[0-9a-f]{64}", a["sha256"]), aid
            assert a.get("size_bytes", 0) > 0, aid
    for aid in ("voice-front-4b", "voice-front-1.7b"):
        assert re.fullmatch(r"[0-9a-f]{40}", va.ARTIFACTS[aid]["revision"]), aid
        assert "resolve/" + va.ARTIFACTS[aid]["revision"] in va.url_for(aid)
    # The size shown to the owner agrees with the pinned byte count.
    for aid in ("voice-ear-streaming", "voice-front-4b", "voice-front-1.7b"):
        a = va.ARTIFACTS[aid]
        assert abs(a["size_mb"] - a["size_bytes"] / 1048576) < 1, aid


def test_an_unpinned_start_still_refuses_and_downloads_nothing(monkeypatch):
    monkeypatch.setitem(va.ARTIFACTS, "voice-front-1.7b",
                        dict(va.ARTIFACTS["voice-front-1.7b"], revision="0123abc", sha256=None))
    fetched = []
    monkeypatch.setattr(vi, "_download", lambda *a, **k: fetched.append(a))
    out = vi.start("voice-front-1.7b")
    assert out["state"] == "error" and "SHA-256" in out["error"]
    assert fetched == []
    out = vi.start("voice-ear-turbo")
    assert out["state"] == "error" and "Nothing was downloaded" in out["error"]


# -- a fake publisher with optional Range support ----------------------------

class _Publisher:
    def __init__(self, payload, ranges=True):
        self.payload, self.ranges, self.seen = payload, ranges, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rng = self.headers.get("Range")
                outer.seen.append(rng)
                body, code = outer.payload, 200
                if rng and outer.ranges:
                    start = int(re.match(r"bytes=(\d+)-", rng).group(1))
                    if start >= len(body):
                        self.send_response(416)
                        self.end_headers()
                        return
                    body, code = body[start:], 206
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/file.bin" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def publisher():
    made = []

    def make(payload, ranges=True):
        p = _Publisher(payload, ranges)
        made.append(p)
        return p
    yield make
    for p in made:
        p.close()


def _pin(monkeypatch, tmp_path, url, sha):
    monkeypatch.setitem(va.ARTIFACTS, "voice-front-1.7b",
                        dict(va.ARTIFACTS["voice-front-1.7b"], url=url, revision="r1",
                             sha256=sha, size_mb=1))
    monkeypatch.setattr("agent_friday.core.runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(vi, "_log_path", lambda: tmp_path / "log.txt")
    return tmp_path / "runtime" / va.ARTIFACTS["voice-front-1.7b"]["dest"]


def _part(dest):
    return dest.with_name(dest.name + ".download.part")


def test_a_pinned_download_is_verified_and_installed(monkeypatch, tmp_path, publisher):
    payload = b"weights" * 5000
    pub = publisher(payload)
    dest = _pin(monkeypatch, tmp_path, pub.url, hashlib.sha256(payload).hexdigest())
    vi._install_artifact("voice-front-1.7b")
    assert dest.read_bytes() == payload
    assert not list(dest.parent.glob("*.download*"))


def test_a_download_that_does_not_match_its_pin_is_refused(monkeypatch, tmp_path, publisher):
    pub = publisher(b"tampered" * 5000)
    dest = _pin(monkeypatch, tmp_path, pub.url, hashlib.sha256(b"genuine").hexdigest())
    with pytest.raises(RuntimeError, match="failed its checksum"):
        vi._install_artifact("voice-front-1.7b")
    assert not dest.exists()
    assert not list(dest.parent.glob("*.download*")), "a bad file must not linger for resume"


def test_resume_continues_from_the_part_file(monkeypatch, tmp_path, publisher):
    payload = bytes(range(256)) * 4000
    pub = publisher(payload)
    dest = _pin(monkeypatch, tmp_path, pub.url, hashlib.sha256(payload).hexdigest())
    dest.parent.mkdir(parents=True)
    _part(dest).write_bytes(payload[:300000])
    vi._install_artifact("voice-front-1.7b")
    assert pub.seen == ["bytes=300000-"], "it must ask only for the rest"
    assert dest.read_bytes() == payload


def test_a_server_that_ignores_range_restarts_cleanly(monkeypatch, tmp_path, publisher):
    payload = b"abc" * 100000
    pub = publisher(payload, ranges=False)
    dest = _pin(monkeypatch, tmp_path, pub.url, hashlib.sha256(payload).hexdigest())
    dest.parent.mkdir(parents=True)
    _part(dest).write_bytes(b"stale-and-wrong")
    vi._install_artifact("voice-front-1.7b")
    assert dest.read_bytes() == payload


def test_a_wrong_partial_is_caught_by_the_checksum(monkeypatch, tmp_path, publisher):
    payload = b"z" * 400000
    pub = publisher(payload)
    dest = _pin(monkeypatch, tmp_path, pub.url, hashlib.sha256(payload).hexdigest())
    dest.parent.mkdir(parents=True)
    _part(dest).write_bytes(b"y" * 1000)
    with pytest.raises(RuntimeError, match="failed its checksum"):
        vi._install_artifact("voice-front-1.7b")
    assert not dest.exists()


def test_progress_is_reported_while_downloading(monkeypatch, tmp_path, publisher):
    payload = b"p" * 3000000
    pub = publisher(payload)
    dest = _pin(monkeypatch, tmp_path, pub.url, hashlib.sha256(payload).hexdigest())
    vi._install_artifact("voice-front-1.7b")
    assert vi.status()["progress"] == {"done": len(payload), "total": len(payload)}
