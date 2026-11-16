"""Update the phone app from this PC: GET /tv/v1/app/latest and /tv/v1/app/apk.

Guarded: only an owner-bound phone or tablet is answered (not a watch, TV, family or unbound device, no session);
only the build NAMED in LATEST.json with an acceptance record is served, never a folder listing; a file whose bytes
no longer match the accepted SHA-256 is refused; the SHA-256 and version are sent; Range resumes; every download
writes one receipt with via (lan or tailnet); the tailnet door serves it like the LAN one."""
import hashlib
import os
import json

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import app_update, household, pairing
from tests.ledger_support import rows
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

BYTES = b"PK\x03\x04" + bytes(range(256)) * 40


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pairing, "_OFFERED", {})
    apk_dir = tmp_path / "apks"
    apk_dir.mkdir()
    monkeypatch.setenv("FRIDAY_PHONE_APK_DIR", str(apk_dir))
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def apks(tmp_path):
    return tmp_path / "apks"


def accept(apks, data=BYTES, name="agent-friday-0.12.0-accept-abc1234.apk", **over):
    (apks / name).write_bytes(data)
    rec = {"file": name, "version": "0.12.0-accept", "version_code": 40, "sha256": hashlib.sha256(data).hexdigest(),
           "accepted": {"by": "phone lane critic", "at": "2026-11-15T20:00", "commit": "abc1234"}}
    rec.update(over)
    (apks / "LATEST.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def paired(kind="phone", member="stephen"):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


def get(client, dev, path, **kw):
    return client.get(path, headers={**auth(open_session(client, dev)), **kw.pop("headers", {})}, **kw)


PATHS = ["/tv/v1/app/latest", "/tv/v1/app/apk"]


@pytest.mark.parametrize("path", PATHS)
def test_a_session_is_required(client, apks, path):
    accept(apks)
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("tv", "family"),
                                         ("phone", None), ("phone", "family")])
def test_only_an_owner_phone_or_tablet_is_answered(client, apks, path, kind, member):
    accept(apks)
    response = get(client, paired(kind, member), path)
    assert response.status_code == 403 and response.get_json()["code"] == "app_update_not_for_this_device"
    assert rows("device_app_download") == []


@pytest.mark.parametrize("kind", ["phone", "tablet"])
def test_latest_names_version_hash_size_and_who_accepted(client, apks, kind):
    rec = accept(apks)
    body = get(client, paired(kind), "/tv/v1/app/latest").get_json()
    assert body["ok"] and body["version"] == "0.12.0-accept" and body["version_code"] == 40
    assert body["sha256"] == rec["sha256"] and body["size"] == len(BYTES)
    assert body["accepted"]["by"] == "phone lane critic" and body["apk"] == "/tv/v1/app/apk"
    assert "file" not in body and "path" not in body                      # no PC path or folder detail leaves the PC


def test_have_says_whether_the_phone_is_current(client, apks):
    accept(apks)
    dev = paired()
    assert get(client, dev, "/tv/v1/app/latest?have=39").get_json()["up_to_date"] is False
    assert get(client, dev, "/tv/v1/app/latest?have=40").get_json()["up_to_date"] is True
    assert "up_to_date" not in get(client, dev, "/tv/v1/app/latest?have=x").get_json()


def test_apk_is_sent_byte_for_byte_with_its_hash_and_one_receipt(client, apks):
    rec = accept(apks)
    response = get(client, paired(), "/tv/v1/app/apk")
    assert response.status_code == 200 and response.data == BYTES
    assert response.headers["X-Friday-SHA256"] == rec["sha256"] == hashlib.sha256(response.data).hexdigest()
    assert response.headers["X-Friday-Version"] == "0.12.0-accept"
    assert response.mimetype == "application/vnd.android.package-archive" and response.headers["Cache-Control"] == "no-store"
    got = rows("device_app_download")
    assert len(got) == 1 and got[0]["via"] == "lan" and got[0]["sha256"] == rec["sha256"]


def test_a_range_resumes_and_is_not_a_second_download(client, apks):
    accept(apks)
    dev = paired()
    response = get(client, dev, "/tv/v1/app/apk", headers={"Range": "bytes=100-199"})
    assert response.status_code == 206 and response.data == BYTES[100:200]
    assert rows("device_app_download") == []


def test_no_record_means_no_apk_even_with_files_in_the_folder(client, apks):
    (apks / "agent-friday-0.9.9-untested.apk").write_bytes(BYTES)
    for path in PATHS:
        response = get(client, paired(), path)
        assert response.status_code == 404 and response.get_json()["code"] == "no_accepted_build"


@pytest.mark.parametrize("bad", [{"accepted": None}, {"accepted": {"by": "x"}}, {"accepted": {"at": "now"}},
                                 {"sha256": "nothex"}, {"version": ""}, {"file": "../LATEST.json"},
                                 {"file": "sub/x.apk"}, {"file": "x.exe"}])
def test_a_record_that_is_not_a_clear_acceptance_serves_nothing(client, apks, bad):
    accept(apks, **bad)
    assert get(client, paired(), "/tv/v1/app/apk").status_code == 404


@pytest.mark.parametrize("text", ["[]", '"x"', "7", "null", "{not json"])
def test_a_record_that_is_not_an_object_is_no_build_not_a_crash(client, apks, text):
    (apks / "LATEST.json").write_text(text, encoding="utf-8")
    for path in PATHS:
        response = get(client, paired(), path)
        assert response.status_code == 404 and response.get_json()["code"] == "no_accepted_build"


def test_a_build_changed_after_acceptance_is_refused(client, apks):
    accept(apks)
    (apks / "agent-friday-0.12.0-accept-abc1234.apk").write_bytes(BYTES + b"tampered")
    response = get(client, paired(), "/tv/v1/app/apk")
    assert response.status_code == 503 and response.get_json()["code"] == "apk_changed" and b"tampered" not in response.data
    assert rows("device_app_download") == []


def test_a_same_size_swap_with_the_same_mtime_is_refused(client, apks):
    accept(apks)
    apk = apks / "agent-friday-0.12.0-accept-abc1234.apk"
    stamp = apk.stat().st_mtime_ns
    assert get(client, paired(), "/tv/v1/app/apk").status_code == 200       # the first look must not be cached
    apk.write_bytes(bytes(reversed(BYTES)))
    os.utime(apk, ns=(stamp, stamp))
    response = get(client, paired(), "/tv/v1/app/apk")
    assert response.status_code == 503 and response.get_json()["code"] == "apk_changed"


def test_if_range_with_a_stale_validator_sends_the_whole_file(client, apks):
    accept(apks)
    response = get(client, paired(), "/tv/v1/app/apk",
                   headers={"Range": "bytes=100-199", "If-Range": '"not-the-current-etag"'})
    assert response.status_code == 200 and response.data == BYTES


def test_a_range_from_byte_zero_is_a_download(client, apks):
    accept(apks)
    response = get(client, paired(), "/tv/v1/app/apk", headers={"Range": "bytes=0-"})
    assert response.data == BYTES
    assert len(rows("device_app_download")) == 1


def test_a_stale_if_range_whole_file_is_a_download(client, apks):
    accept(apks)
    get(client, paired(), "/tv/v1/app/apk", headers={"Range": "bytes=100-199", "If-Range": '"stale"'})
    assert len(rows("device_app_download")) == 1


def test_the_named_file_must_exist(client, apks):
    accept(apks)
    (apks / "agent-friday-0.12.0-accept-abc1234.apk").unlink()
    assert get(client, paired(), "/tv/v1/app/latest").status_code == 404


def test_the_tailnet_door_serves_it_and_the_receipt_says_so(apks):
    accept(apks)
    tailnet_client = gateway.create_app(tailnet=True).test_client()
    dev = paired()
    env = {"REMOTE_ADDR": "100.70.222.100"}
    nonce = tailnet_client.post("/tv/v1/session/challenge", environ_overrides=env).get_json()["nonce"]
    tok = tailnet_client.post("/tv/v1/session/open", environ_overrides=env,
                 json={"device_id": dev.id, "nonce": nonce, "sig": dev.sign(nonce)}).get_json()["session"]
    response = tailnet_client.get("/tv/v1/app/apk", environ_overrides=env, headers=auth(tok))
    assert response.status_code == 200 and response.data == BYTES
    assert rows("device_app_download")[-1]["via"] == "tailnet"
    response = tailnet_client.get("/tv/v1/app/apk", environ_overrides={"REMOTE_ADDR": "192.168.1.9"}, headers=auth(tok))
    assert response.status_code == 403


def test_a_removed_device_loses_access(client, apks):
    accept(apks)
    dev = paired()
    headers = auth(open_session(client, dev))
    assert client.get("/tv/v1/app/latest", headers=headers).status_code == 200
    device_pairing.remove_device(dev.id)
    assert client.get("/tv/v1/app/latest", headers=headers).status_code in (401, 403)
