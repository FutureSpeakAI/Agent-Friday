"""Changing media from an owner's phone or tablet (``pc_act``; wider brief step 4, writes).

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet that holds ``pc_act`` may act (the job routes' guard), and the trash list is a
  ``pc_read`` read;
* a hand edit sends only title, tags, favorite and project, each of the right type, and nothing else reaches the
  desktop; a turn-into kind is a lower-case word;
* publishing is the desktop's own route (it raises the card on the PC); nothing here approves it;
* a post card is never deleted from a device (the desktop cannot undo that); a file card goes to the trash and
  can be restored;
* every answer has the host paths left out, and each action writes a ``device_act`` receipt.
"""
import json

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_act_media, pc_read
from tests.ledger_support import rows
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
    """Stands in for the desktop's own media routes: remembers each call, answers with a canned reply."""

    def __init__(self):
        self.sent, self.status, self.answer = [], 200, {"status": "ok", "card": {"id": "m1", "path": "E:/Photos/a.png"}}

    def __call__(self, method, path, body):
        self.sent.append((method, path, body))
        return self.status, self.answer


@pytest.fixture
def desk(monkeypatch):
    stand_in = Desk()
    monkeypatch.setattr(pc_act_media, "_desk_send", stand_in)
    monkeypatch.setattr(pc_read, "_desk", lambda path, params=None: (
        200, json.dumps({"card": {"source_kind": stand_in.kind}}).encode(), "application/json"))
    stand_in.kind = "creation"
    return stand_in


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def device(kind="phone", member="stephen", rights=("pc_act",)):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A device", kind=kind)
    if member:
        household.bind_device(tv.id, member)
    for right in rights:
        device_pairing.set_right(tv.id, right, True)
    return tv


def send(client, tv, method, path, body=None):
    return client.open(path, method=method, json=body, headers=auth(open_session(client, tv)))


def test_a_hand_edit_sends_only_the_four_allowed_fields_each_of_the_right_type(client, desk):
    answer = send(client, device(), "PATCH", "/tv/v1/media/m1",
                  {"title": " New name ", "tags": ["a", "b"], "favorite": True, "project": "p1",
                   "status": "published", "path": "/x", "body": "text"})
    assert answer.status_code == 200
    assert desk.sent == [("PATCH", "/api/media/m1", {"title": "New name", "tags": ["a", "b"], "favorite": True,
                                                    "project": "p1"})]


@pytest.mark.parametrize("body", [{}, {"status": "published"}, {"title": ""}, {"title": 5}, {"tags": "a"},
                                  {"tags": [1]}, {"tags": ["x"] * 21}, {"favorite": "yes"}, {"title": "x" * 201}])
def test_a_hand_edit_with_nothing_valid_to_change_never_reaches_the_desktop(client, desk, body):
    assert send(client, device(), "PATCH", "/tv/v1/media/m1", body).status_code == 400
    assert desk.sent == []


@pytest.mark.parametrize("kind", ["video", "audio_clip", "x1"])
def test_turn_into_sends_only_a_lower_case_word(client, desk, kind):
    assert send(client, device(), "POST", "/tv/v1/media/m1/turn-into", {"kind": kind, "extra": 1}).status_code == 200
    assert desk.sent == [("POST", "/api/media/m1/turn-into", {"kind": kind})]


@pytest.mark.parametrize("kind", ["", "Video", "../x", "a b", 5, None, "x" * 40])
def test_a_bad_turn_into_kind_never_reaches_the_desktop(client, desk, kind):
    assert send(client, device(), "POST", "/tv/v1/media/m1/turn-into", {"kind": kind}).status_code == 400
    assert desk.sent == []


def test_publish_and_unpublish_are_the_desktops_own_routes_with_no_body_from_the_device(client, desk):
    desk.status, desk.answer = 202, {"status": "pending", "approval_id": "a1", "message": "A card is asking you first."}
    tv = device()
    published = send(client, tv, "POST", "/tv/v1/media/m1/publish", {"approve": True})
    assert published.status_code == 202 and published.get_json()["status"] == "pending"
    send(client, tv, "POST", "/tv/v1/media/m1/unpublish")
    assert desk.sent == [("POST", "/api/media/m1/publish", {}), ("POST", "/api/media/m1/unpublish", {})]


def test_a_file_card_is_deleted_to_the_trash_through_the_desktop(client, desk):
    assert send(client, device(), "DELETE", "/tv/v1/media/m1").status_code == 200
    assert desk.sent == [("DELETE", "/api/media/m1", {})]


def test_a_post_card_is_never_deleted_from_a_device(client, desk):
    desk.kind = "post"
    answer = send(client, device(), "DELETE", "/tv/v1/media/m1")
    assert answer.status_code == 409 and answer.get_json()["code"] == "delete_on_pc" and desk.sent == []


@pytest.mark.parametrize("status,body", [
    (200, b'{"status":"ok","entries":[]}'), (200, b'{"card":[]}'), (200, b'{"card":{}}'),
    (200, b'{"card":{"source_kind":""}}'),
    (200, b'<html>'), (200, b'[1]'), (403, b'{"status":"denied"}'), (429, b'{"error":"slow down"}'),
    (500, b'{"error":"boom"}')])
def test_a_card_the_pc_does_not_plainly_describe_is_never_deleted(client, desk, monkeypatch, status, body):
    monkeypatch.setattr(pc_read, "_desk", lambda path, params=None: (status, body, "application/json"))
    answer = send(client, device(), "DELETE", "/tv/v1/media/m1")
    assert answer.status_code == 409 and answer.get_json()["code"] == "delete_on_pc" and desk.sent == []


def test_a_post_id_is_refused_without_asking_the_desktop(client, desk, monkeypatch):
    monkeypatch.setattr(pc_read, "_desk", lambda path, params=None: pytest.fail("the desktop must not be asked"))
    assert send(client, device(), "DELETE", "/tv/v1/media/post:abc").status_code == 409 and desk.sent == []


def test_deleting_a_card_the_pc_does_not_know_is_a_plain_404(client, desk, monkeypatch):
    monkeypatch.setattr(pc_read, "_desk", lambda path, params=None: (404, b"{}", "application/json"))
    assert send(client, device(), "DELETE", "/tv/v1/media/m1").status_code == 404 and desk.sent == []


def test_a_trash_entry_is_restored_through_the_desktop(client, desk):
    assert send(client, device(), "POST", "/tv/v1/media/trash/e1/restore").status_code == 200
    assert desk.sent == [("POST", "/api/media/trash/e1/restore", {})]


def test_the_trash_list_is_a_read_and_has_no_host_path(client, monkeypatch):
    monkeypatch.setattr(pc_read, "_desk", lambda path, params=None: (
        200, json.dumps({"entries": [{"id": "e1"}], "folder": "E:/Trash"}).encode(), "application/json"))
    answer = send(client, device(rights=("pc_read",)), "GET", "/tv/v1/media/trash")
    assert answer.status_code == 200 and answer.get_json() == {"entries": [{"id": "e1"}]}


def test_no_host_path_reaches_the_phone_from_a_write(client, desk):
    answer = send(client, device(), "PATCH", "/tv/v1/media/m1", {"title": "x"})
    assert "path" not in answer.get_json()["card"] and "Photos" not in answer.get_data(as_text=True)


ROUTES = [("PATCH", "/tv/v1/media/m1", {"title": "x"}), ("POST", "/tv/v1/media/m1/turn-into", {"kind": "video"}),
          ("POST", "/tv/v1/media/m1/publish", None), ("POST", "/tv/v1/media/m1/unpublish", None),
          ("DELETE", "/tv/v1/media/m1", None), ("POST", "/tv/v1/media/trash/e1/restore", None)]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_without_the_act_right_nothing_reaches_the_desktop_and_the_pc_asks(client, desk, method, path, body):
    answer = send(client, device(rights=("pc_read",)), method, path, body)
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right" and desk.sent == []


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_change_media(client, desk, method, path, body, kind, member):
    answer = send(client, device(kind, member), method, path, body)
    assert answer.status_code == 403 and answer.get_json()["code"] == "pc_act_not_for_this_device" and desk.sent == []


def test_each_action_writes_one_receipt(client, desk):
    send(client, device(), "POST", "/tv/v1/media/m1/publish")
    [receipt] = rows("device_act")
    fields = (receipt["action"], receipt["media"], receipt["via"], receipt["right"])
    assert fields == ("media_publish", "m1", "lan", "pc_act")


@pytest.mark.parametrize("path", ["/tv/v1/media/.hidden/publish", "/tv/v1/media/trash/.x/restore"])
def test_a_bad_id_never_reaches_the_desktop(client, desk, path):
    assert send(client, device(), "POST", path).status_code in (400, 404) and desk.sent == []


def test_every_desktop_path_the_media_writes_call_is_still_a_desktop_route():
    import pathlib
    import re
    root = pathlib.Path(pc_read.__file__).resolve().parents[1] / "routes"
    src = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    declared = {re.sub(r"<[^>]+>", "<x>", m) for m in re.findall(r"route\(\s*[\"'](/api/[^\"']+)[\"']", src)}
    for path in ("/api/media/<x>", "/api/media/<x>/publish", "/api/media/<x>/unpublish", "/api/media/<x>/turn-into",
                 "/api/media/trash", "/api/media/trash/<x>/restore"):
        assert path in declared, path
