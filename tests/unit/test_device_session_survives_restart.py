"""A paired device's session survives a restart or a deploy, and a refusal says which kind it is.

Stephen, 2026-11-13 19:47-19:50: right after the b50h deploy his phone (paired at 11:38, still in the device
store, still bound to him) was told "Your Friday PC no longer recognises this phone. Pair it again."
The gateway log shows `GET /tv/v1/models -> 401` at 19:47:04 and `POST /tv/v1/messages -> 401` at 19:48:15:
sessions lived only in memory, the deploy restarted the process, every live session vanished, and the phone read
the plain 401 as "unpaired". It paired a second time (a duplicate device). The promises guarded here:

* a session opened before a restart still works after it (the sessions are written, as hashes only, signed
  like the device store, and read back once per process);
* it stops working the moment the device is removed, the gateway is switched off, everything is stopped,
  the session has expired, or the file was altered;
* a request with no live session is a 401 whose body says `code: session_required, action: open_session`
  (open a new session; the device may be paired fine), and only a refusal of the session/open itself means
  not paired.
"""
import base64
import json

import pytest

from agent_friday.services import device_pairing as dp
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    dp._path().unlink(missing_ok=True)
    dp._reset_for_tests()
    yield
    dp._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def restart():
    """What a deploy does to this module's memory."""
    dp._SESSIONS.clear()
    dp._CHALLENGES.clear()
    dp._RESTORED = False


def paired():
    dev = FakeTv()
    dp.add_device(dev.spki, name="Pixel", kind="phone")
    return dev


def test_a_session_opened_before_a_restart_works_after_it(client):
    dev = paired()
    headers = auth(open_session(client, dev))
    assert client.get("/tv/v1/status", headers=headers).status_code != 401
    restart()
    r = client.get("/tv/v1/status", headers=headers)
    assert r.status_code != 401, r.get_json()


def test_the_file_holds_hashes_never_the_token(client):
    dev = paired()
    token = open_session(client, dev)
    text = dp._sessions_path().read_text(encoding="utf-8")
    assert token not in text and dev.id in text


def test_a_removed_device_loses_its_session_even_across_a_restart(client):
    dev = paired()
    headers = auth(open_session(client, dev))
    dp.remove_device(dev.id)
    restart()
    assert client.get("/tv/v1/status", headers=headers).status_code == 401


def test_stopping_everything_ends_every_session_across_a_restart(client):
    dev = paired()
    headers = auth(open_session(client, dev))
    dp.stop_all()
    restart()
    assert client.get("/tv/v1/status", headers=headers).status_code == 401


def test_switching_the_gateway_off_ends_sessions_across_a_restart(client):
    dev = paired()
    headers = auth(open_session(client, dev))
    dp.set_gateway(False)
    restart()
    assert client.get("/tv/v1/status", headers=headers).status_code == 401


def test_an_expired_session_does_not_come_back(client, monkeypatch):
    dev = paired()
    headers = auth(open_session(client, dev))
    real = dp.time.time
    monkeypatch.setattr(dp.time, "time", lambda: real() + dp.SESSION_TTL_S + 5)
    restart()
    assert client.get("/tv/v1/status", headers=headers).status_code == 401


def test_an_altered_session_file_restores_nothing(client):
    dev = paired()
    headers = auth(open_session(client, dev))
    p = dp._sessions_path()
    doc = json.loads(p.read_text(encoding="utf-8"))
    for h in doc["body"]["sessions"]:
        doc["body"]["sessions"][h][1] += 99999
    p.write_text(json.dumps(doc), encoding="utf-8")
    restart()
    assert client.get("/tv/v1/status", headers=headers).status_code == 401


def test_a_request_without_a_session_says_open_one_not_pair_again(client):
    r = client.get("/tv/v1/status", headers={"Authorization": "Bearer nope"})
    b = r.get_json()
    assert r.status_code == 401 and b["code"] == "session_required" and b["action"] == "open_session"


def test_only_the_session_open_refusal_means_not_paired(client):
    stranger = FakeTv()
    nonce = client.post("/tv/v1/session/challenge").get_json()["nonce"]
    r = client.post("/tv/v1/session/open", json={"device_id": stranger.id, "nonce": nonce,
                                                 "sig": base64.b64encode(b"x").decode()})
    assert r.status_code == 401 and r.get_json()["code"] == "not_paired_or_bad_challenge"
