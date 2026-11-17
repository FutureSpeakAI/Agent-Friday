"""``GET /tv/v1/approvals/slot``: which pending cards wait for one subject (CONTRACT_approval_slot_android_tv_2026-11-16).

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet holding ``pc_read`` may ask;
* the subject and kind are checked before the desktop is asked, and a refusal is a closed code that never repeats what
  the caller sent;
* the desktop is asked for exactly the subject and kind, and the answer is ids and nothing else (at most 20, each a
  plain id); a missing desktop route, a failure, or an answer of any other shape is 503 ``pc_unavailable``, so a device
  never reads a failed ask as "nothing is waiting" and never falls back to the whole list;
* an empty answer is 200 with no ids.
"""
import json

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_read
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


class Desk:
    """Stands in for the desktop's ``/api/liquid/approval-slot``: remembers what it was asked, answers a canned reply."""

    def __init__(self):
        self.asked, self.status, self.body = [], 200, json.dumps({"ok": True, "ids": ["a1", "a2"]}).encode()

    def __call__(self, path, params=None):
        self.asked.append((path, dict(params or {})))
        return self.status, self.body, "application/json"


@pytest.fixture
def desk(monkeypatch):
    stand_in = Desk()
    monkeypatch.setattr(pc_read, "_desk", stand_in)
    return stand_in


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def device(kind="phone", member="stephen", rights=("pc_read",)):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A device", kind=kind)
    if member:
        household.bind_device(tv.id, member)
    for right in rights:
        device_pairing.set_right(tv.id, right, True)
    return tv


def ask(client, tv, query):
    return client.get("/tv/v1/approvals/slot" + query, headers=auth(open_session(client, tv)))


def test_the_answer_is_the_ids_the_desktop_names_and_nothing_else(client, desk):
    desk.body = json.dumps({"ok": True, "ids": ["a1", "a2"], "title": "SECRET", "subject": "crew:c1"}).encode()
    answer = ask(client, device(), "?subject=crew:crew-3fa9")
    assert answer.status_code == 200 and answer.get_json() == {"ok": True, "ids": ["a1", "a2"]}


def test_the_desktop_is_asked_for_exactly_the_subject_and_kind(client, desk):
    ask(client, device(), "?subject=crew:crew-3fa9&kind=send_email")
    assert desk.asked == [("/api/liquid/approval-slot", {"subject": "crew:crew-3fa9", "kind": "send_email"})]


def test_a_missing_kind_is_not_sent(client, desk):
    ask(client, device(), "?subject=crew:crew-3fa9")
    assert desk.asked == [("/api/liquid/approval-slot", {"subject": "crew:crew-3fa9"})]


def test_nothing_waiting_is_a_200_with_no_ids(client, desk):
    desk.body = json.dumps({"ok": True, "ids": []}).encode()
    answer = ask(client, device(), "?subject=crew:none")
    assert answer.status_code == 200 and answer.get_json()["ids"] == []


@pytest.mark.parametrize("subject", ["", "crew", "Crew:x", "crew:", ":x", "crew:" + "x" * 81, "a" * 33 + ":x", "crew:a b",
                                     "crew:a;b", "crew:../x", "crew:x\n"])
def test_a_bad_subject_is_refused_before_the_desktop_is_asked_and_never_echoed(client, desk, subject):
    answer = ask(client, device(), "?subject=" + subject.replace("\n", "%0A").replace(" ", "%20"))
    assert answer.status_code == 400 and answer.get_json()["code"] == "bad_subject"
    assert desk.asked == [] and subject.strip() == "" or subject.strip() not in answer.get_data(as_text=True)


@pytest.mark.parametrize("kind", ["", "Send", "a b", "x" * 49, "a;b", "../x"])
def test_a_bad_kind_is_refused_before_the_desktop_is_asked_and_never_echoed(client, desk, kind):
    answer = ask(client, device(), "?subject=crew:c1&kind=" + kind.replace(" ", "%20"))
    assert answer.status_code == 400 and answer.get_json()["code"] == "bad_kind" and desk.asked == []
    assert kind == "" or kind not in answer.get_data(as_text=True)


@pytest.mark.parametrize("status,body", [
    (404, b'{"error": "not found"}'),                       # a desktop without the route
    (500, b'{"ok": false}'), (400, b'{"ok": false, "error": "bad_subject"}'),
    (200, b"<html>"), (200, b"[1]"), (200, b'{"ok": true}'), (200, b'{"ok": false, "ids": []}'),
    (200, b'{"ok": true, "ids": "a1"}'), (200, b'{"ok": true, "ids": [1]}'), (200, b'{"ok": true, "ids": ["../x"]}'),
    (200, json.dumps({"ok": True, "ids": ["a%d" % n for n in range(21)]}).encode())])
def test_an_answer_that_is_not_plainly_ids_is_a_503_never_an_empty_list(client, desk, status, body):
    desk.status, desk.body = status, body
    answer = ask(client, device(), "?subject=crew:c1")
    assert answer.status_code == 503 and answer.get_json()["code"] == "pc_unavailable" and "ids" not in answer.get_json()


def test_a_desktop_that_raises_is_a_503(client, monkeypatch):
    def boom(path, params=None):
        raise RuntimeError("down")
    monkeypatch.setattr(pc_read, "_desk", boom)
    assert ask(client, device(), "?subject=crew:c1").status_code == 503


def test_without_the_read_right_nothing_reaches_the_desktop_and_the_pc_asks(client, desk):
    answer = ask(client, device(rights=()), "?subject=crew:c1")
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right" and desk.asked == []


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_ask(client, desk, kind, member):
    answer = ask(client, device(kind, member), "?subject=crew:c1")
    assert answer.status_code == 403 and desk.asked == []
