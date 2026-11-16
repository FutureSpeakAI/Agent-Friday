"""Media on the PC, for the owner's phone and tablet (wider brief step 4, reads): host paths out, strips, capped files,
collections. Everything needs ``pc_read``.

Rules guarded here, each with a case that fails if the check is removed:
* no answer about media carries where a file lives on the PC (a key that names a path, or text that is an absolute
  Windows, UNC or Unix path), while ids, titles, file names and the desktop's own relative URLs stay;
* a file is sent in ranges of at most 8 MiB: a request with no range gets the first chunk, a longer range is cut, and
  the answer says what it sent (Content-Range) and the cap (X-Friday-Chunk-Cap);
* the device never names a path: ids are checked and every call is a fixed desktop GET.
"""
import json

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_common, pc_read
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pc_read, "_AUDITED", {})
    monkeypatch.setattr(pairing, "_OFFERED", {})
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def reader(rights=("pc_read",)):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Phone", kind="phone")
    household.bind_device(tv.id, "stephen")
    for right in rights:
        device_pairing.set_right(tv.id, right, True)
    return tv


def get(client, tv, path, **kw):
    return client.get(path, headers={**auth(open_session(client, tv)), **kw.pop("headers", {})}, **kw)


CARD = {"status": "ok", "card": {
    "id": "m1", "kind": "image", "title": "Holiday", "filename": "holiday.png", "preview": "/api/media/m1/preview",
    "path": "E:\\Photos\\holiday.png", "source_ref": "/home/someone/pics/holiday.png",
    "paths": ["D:/Backups/holiday.png", "relative/holiday.png"], "unc": "\\\\nas\\share\\holiday.png",
    "nested": {"folder": "E:\\Photos", "label": "kept"}}, "relations": []}


class Desk:
    def __init__(self):
        self.calls, self.body, self.status, self.ctype = [], b"{}", 200, "application/json"

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self.status, self.body, self.ctype


@pytest.fixture
def desk(monkeypatch):
    stand_in = Desk()
    monkeypatch.setattr(pc_read, "_desk", stand_in)
    return stand_in


def test_no_host_path_reaches_the_phone(client, desk):
    desk.body = json.dumps(CARD).encode()
    answer = get(client, reader(), "/tv/v1/media/m1")
    text = answer.get_data(as_text=True)
    card = answer.get_json()["card"]
    assert answer.status_code == 200 and "path" not in card and "folder" not in card["nested"]
    for leak in ("E:\\\\Photos", "D:/Backups", "/home/someone", "\\\\\\\\nas"):
        assert leak not in text
    assert card["filename"] == "holiday.png" and card["preview"] == "/api/media/m1/preview"
    assert card["paths"] == ["relative/holiday.png"] and card["nested"]["label"] == "kept"


def test_the_list_and_the_collections_are_scrubbed_too(client, desk):
    desk.body = json.dumps({"cards": [CARD["card"]], "collections": [{"id": "c1", "root": "C:\\Media"}]}).encode()
    tv = reader()
    for path in ("/tv/v1/media", "/tv/v1/media/collections", "/tv/v1/media/collections/c1"):
        text = get(client, tv, path).get_data(as_text=True)
        assert "C:\\\\" not in text and "/home/" not in text, path


@pytest.mark.parametrize("value", ["C:\\x", "c:/x", "\\\\nas\\x", "/Users/a/b", "/home/a", "/mnt/c/x"])
def test_the_scrubber_drops_every_kind_of_absolute_path(value):
    assert pc_common.hide_host_paths({"label": value, "kept": "x"}) == {"kept": "x"}


@pytest.mark.parametrize("value", ["/api/media/m1/preview", "relative/x.png", "holiday.png", "https://example.com/x"])
def test_the_scrubber_keeps_urls_and_names(value):
    assert pc_common.hide_host_paths({"label": value}) == {"label": value}


def test_a_strip_is_an_image_or_a_plain_refusal(client, desk):
    tv = reader()
    desk.body, desk.ctype = b"WEBP", "image/webp"
    answer = get(client, tv, "/tv/v1/media/m1/strip")
    assert answer.status_code == 200 and answer.data == b"WEBP" and answer.headers["Cache-Control"] == "no-store"
    desk.body, desk.ctype = b'{"status":"not_found"}', "application/json"
    assert get(client, tv, "/tv/v1/media/m1/strip").status_code == 503
    desk.status = 404
    assert get(client, tv, "/tv/v1/media/m1/strip").status_code == 404


class RangeDesk:
    def __init__(self):
        self.asked, self.status, self.body, self.ctype, self.content_range = [], 206, b"x" * 10, "video/mp4", "bytes 0-9/100"

    def __call__(self, path, byte_range):
        self.asked.append((path, byte_range))
        return self.status, self.body, self.ctype, self.content_range


@pytest.fixture
def files(monkeypatch):
    stand_in = RangeDesk()
    monkeypatch.setattr(pc_read, "_desk_range", stand_in)
    return stand_in


CAP = pc_read.MAX_FILE_CHUNK


@pytest.mark.parametrize("sent,asked", [
    (None, "bytes=0-%d" % (CAP - 1)),
    ("bytes=100-199", "bytes=100-199"),
    ("bytes=100-", "bytes=100-%d" % (100 + CAP - 1)),
    ("bytes=0-999999999", "bytes=0-%d" % (CAP - 1)),
    ("bytes=0-1,5-9", "bytes=0-%d" % (CAP - 1)),
    ("items=0-9", "bytes=0-%d" % (CAP - 1)),
])
def test_a_file_is_sent_in_capped_ranges(client, files, sent, asked):
    headers = {"Range": sent} if sent else {}
    answer = get(client, reader(), "/tv/v1/media/m1/file", headers=headers)
    assert answer.status_code == 206 and files.asked == [("/api/media/m1/file", asked)]
    assert answer.headers["Content-Range"] == "bytes 0-9/100" and answer.headers["X-Friday-Chunk-Cap"] == str(CAP)
    assert answer.headers["Cache-Control"] == "no-store" and answer.headers["X-Content-Type-Options"] == "nosniff"


def test_a_missing_file_is_a_plain_404_and_a_failing_desktop_a_503(client, files):
    tv = reader()
    files.status = 404
    assert get(client, tv, "/tv/v1/media/m1/file").status_code == 404
    files.status = 500
    assert get(client, tv, "/tv/v1/media/m1/file").status_code == 503


@pytest.mark.parametrize("path", ["/tv/v1/media/m1/file", "/tv/v1/media/m1/strip", "/tv/v1/media/collections",
                                  "/tv/v1/media/collections/c1"])
def test_every_new_media_read_needs_the_read_right(client, desk, files, path):
    answer = get(client, reader(rights=()), path)
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


@pytest.mark.parametrize("path", ["/tv/v1/media/..%2Fx/file", "/tv/v1/media/.hidden/strip", "/tv/v1/media/collections/.x"])
def test_a_bad_id_never_reaches_the_desktop(client, desk, files, path):
    assert get(client, reader(), path).status_code in (400, 404)
    assert desk.calls == [] and files.asked == []


def test_every_desktop_path_the_media_reads_call_is_still_a_desktop_route():
    import pathlib
    import re
    root = pathlib.Path(pc_read.__file__).resolve().parents[1] / "routes"
    src = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    declared = {re.sub(r"<[^>]+>", "<x>", m) for m in re.findall(r"route\(\s*[\"'](/api/[^\"']+)[\"']", src)}
    for path in ("/api/media/<x>/strip", "/api/media/<x>/file", "/api/media/collections", "/api/media/collections/<x>"):
        assert path in declared, path
