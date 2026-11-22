"""The PC's read door for the owner's phone and tablet (Mobile v3: chat list, workspaces, jobs, media).

Stephen, 2026-11-15 ("Phone should be able to fully control the PC", order: chats, workspaces, jobs, media). The first
slice only READS. The promises guarded here:

* only an owner-bound phone or tablet that holds ``pc_read`` is answered: not a watch, not a TV (even one bound to the
  owner by mistake), not a family or unbound device, and not a device without the right, which raises its own
  screen-only card at the PC (once an hour) and is told so;
* the right is never part of the pairing bundle and no pairing raises its card on its own;
* the device never names a path: every call is a fixed desktop GET, ids are checked, parameters come from an allowlist;
* a desktop failure is a plain 503, an oversized answer a 413, a missing preview a refusal, never a hang or a guess;
* each area read writes ONE ``device_read`` receipt per hour (device, kind, area, via), not one per item.
"""
import json

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_read
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
    """Stands in for the desktop's own GET routes (core.app, in process)."""

    def __init__(self):
        self.calls, self.status, self.body, self.ctype, self.boom = [], 200, b'{"status":"ok"}', "application/json", None

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.boom:
            raise self.boom
        return self.status, self.body, self.ctype


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def paired(kind="phone", member="stephen", right=True):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    if right:
        device_pairing.set_right(dev.id, "pc_read", True)
    return dev


def get(client, dev, path):
    return client.get(path, headers=auth(open_session(client, dev)))


ROUTES = ["/tv/v1/chats", "/tv/v1/chats/conv-1", "/tv/v1/chats/conv-1/messages", "/tv/v1/workspaces",
          "/tv/v1/workspaces/calendar", "/tv/v1/jobs", "/tv/v1/jobs/t1", "/tv/v1/jobs/t1/journal",
          "/tv/v1/media", "/tv/v1/media/status", "/tv/v1/media/m1", "/tv/v1/media/m1/preview"]


# ── who ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ROUTES)
def test_every_route_needs_a_device_session(client, desk, path):
    assert client.get(path).status_code == 401 and desk.calls == []


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "family"), ("tv", "stephen"),
                                         ("phone", "family"), ("phone", None), ("tablet", None)])
@pytest.mark.parametrize("path", ROUTES[:1] + ROUTES[3:4] + ROUTES[5:6] + ROUTES[8:9])
def test_only_an_owner_bound_phone_or_tablet_is_answered(client, desk, kind, member, path):
    r = get(client, paired(kind, member, right=False), path)
    assert r.status_code == 403 and r.get_json()["code"] == "pc_read_not_for_this_device" and desk.calls == []


def test_even_with_the_right_set_a_watch_or_tv_is_refused(client, desk):
    for kind, member in (("watch", "stephen"), ("tv", "family")):
        dev = paired(kind, member, right=False)
        device_pairing.set_right(dev.id, "pc_read", True)
        assert get(client, dev, "/tv/v1/chats").status_code == 403
    assert desk.calls == []


@pytest.mark.parametrize("kind", ["phone", "tablet"])
def test_without_the_right_the_pc_asks_on_its_screen_once(client, desk, kind):
    dev = paired(kind, right=False)
    r = get(client, dev, "/tv/v1/chats")
    assert r.status_code == 403 and r.get_json()["code"] == "needs_right" and r.get_json()["needs_right"] == "pc_read"
    cards = [c for c in ap.list_approvals(status="pending") if c.get("kind") == pairing.RIGHT_KIND]
    assert len(cards) == 1 and cards[0]["payload"] == {"device_id": dev.id, "right": "pc_read", "origin": "tv_gateway"}
    get(client, dev, "/tv/v1/jobs")                                              # asking again does not pile up cards
    assert len([c for c in ap.list_approvals(status="pending") if c.get("kind") == pairing.RIGHT_KIND]) == 1
    assert desk.calls == []


def test_withdrawing_the_right_ends_the_reading_at_once(client, desk):
    dev = paired()
    tok = open_session(client, dev)
    assert client.get("/tv/v1/chats", headers=auth(tok)).status_code == 200
    device_pairing.set_right(dev.id, "pc_read", False)
    assert client.get("/tv/v1/chats", headers=auth(tok)).status_code == 403


def test_a_removed_device_is_signed_out(client, desk):
    dev = paired()
    tok = open_session(client, dev)
    device_pairing.remove_device(dev.id)
    assert client.get("/tv/v1/chats", headers=auth(tok)).status_code == 401


# ── the right is its own press ───────────────────────────────────────────────

def test_the_right_is_not_in_the_pairing_bundle():
    assert "pc_read" not in device_pairing.RIGHTS and "pc_read" in device_pairing.ALL_RIGHTS
    assert "pc_read" in pairing.RIGHT_CARDS


def test_pairing_does_not_raise_a_pc_read_card_by_itself():
    pairing._offer_rights("someone")
    assert all(c["payload"]["right"] != "pc_read" for c in ap.list_approvals(status="pending")
               if c.get("kind") == pairing.RIGHT_KIND)


def test_a_nonsense_right_is_still_refused():
    dev = paired(right=False)
    with pytest.raises(ValueError):
        device_pairing.set_right(dev.id, "pc_everything", True)


def test_the_card_says_read_only_in_words():
    title, text = pairing.RIGHT_CARDS["pc_read"]
    assert "read" in title.lower() and "cannot change anything" in text and "approve" in text


# ── what each route asks the desktop for ─────────────────────────────────────

def test_the_chat_list_is_the_desktops_chat_list(client, desk):
    assert get(client, paired(), "/tv/v1/chats").status_code == 200
    assert desk.calls == [("/api/conversations", {})]
    desk.calls.clear()
    get(client, paired("tablet"), "/tv/v1/chats?archived=1")
    assert desk.calls == [("/api/conversations", {"archived": "1"})]


def test_one_chat_and_its_messages(client, desk):
    dev = paired()
    get(client, dev, "/tv/v1/chats/conv-1")
    get(client, dev, "/tv/v1/chats/conv-1/messages?limit=99999")
    assert desk.calls == [("/api/conversations/conv-1", {}), ("/api/conversations/conv-1/messages", {"limit": 500})]


def test_the_desktops_own_answer_and_status_are_passed_on(client, desk):
    desk.status, desk.body = 404, b'{"status":"error","error":"no such conversation: x"}'
    r = get(client, paired(), "/tv/v1/chats/x")
    assert r.status_code == 404 and "no such conversation" in r.get_data(as_text=True)
    assert r.headers["X-Friday-As-Of"].isdigit()                               # the age of the snapshot, for the label


def test_jobs_and_their_journal_cursor(client, desk):
    dev = paired()
    get(client, dev, "/tv/v1/jobs")
    get(client, dev, "/tv/v1/jobs/t1")
    get(client, dev, "/tv/v1/jobs/t1/journal?since=41&limit=50&reasoning=1")
    assert desk.calls == [("/api/tasks", {}), ("/api/tasks/t1", {}),
                          ("/api/tasks/t1/journal", {"since": 41, "limit": 50})]     # reasoning is never asked for


def test_media_takes_only_allowlisted_parameters(client, desk):
    get(client, paired(), "/tv/v1/media?q=cat&view=all&limit=9999&path=C:/files&other=1&evil=1")
    path, params = desk.calls[0]
    assert path == "/api/media" and params == {"q": "cat", "view": "all", "limit": 500}


@pytest.mark.parametrize("path", ["/tv/v1/chats/a%20b", "/tv/v1/chats/.hidden", "/tv/v1/chats/%2e%2e", "/tv/v1/chats/x;y", "/tv/v1/jobs/a%3Bb",
                                  "/tv/v1/media/a%20b", "/tv/v1/media/a%20b/preview", "/tv/v1/workspaces/a%20b",
                                  "/tv/v1/chats/" + "x" * 200])
def test_a_bad_id_never_reaches_the_desktop(client, desk, path):
    r = get(client, paired(), path)
    assert r.status_code in (400, 404) and desk.calls == []


def test_a_desktop_that_fails_is_a_plain_503(client, desk):
    desk.boom = RuntimeError("secret internal text")
    r = get(client, paired(), "/tv/v1/jobs")
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable" and "secret" not in r.get_data(as_text=True)
    desk.boom, desk.status = None, 500
    assert get(client, paired("tablet"), "/tv/v1/jobs").status_code == 503


def test_a_huge_answer_is_a_413_not_a_stalled_phone(client, desk):
    desk.body = b"x" * (pc_read.MAX_JSON + 1)
    r = get(client, paired(), "/tv/v1/media")
    assert r.status_code == 413 and r.get_json()["code"] == "too_big"


def test_a_preview_is_an_image_or_a_plain_refusal(client, desk):
    dev = paired()
    desk.body, desk.ctype = b"\x89PNG....", "image/png"
    r = get(client, dev, "/tv/v1/media/m1/preview")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == b"\x89PNG...."
    desk.body, desk.ctype = b"<html>", "text/html"
    assert get(client, dev, "/tv/v1/media/m1/preview").get_json()["code"] == "no_preview"
    desk.status = 404
    assert get(client, dev, "/tv/v1/media/m1/preview").status_code == 404
    desk.status, desk.ctype, desk.body = 200, "image/jpeg", b"x" * (pc_read.MAX_PREVIEW + 1)
    assert get(client, dev, "/tv/v1/media/m1/preview").status_code == 413


# ── workspaces: the real registry, no stand-in ───────────────────────────────

def test_workspaces_are_the_pcs_registry_without_the_held_ones(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces").get_json()
    ids = {w["id"] for w in got["workspaces"]}
    from agent_friday.services import workspace_registry as reg
    assert ids == set(reg.ids()) and {"calendar", "messages"} <= ids and got["count"] == len(ids)
    # "renderer" (a2ui | legacy) added by contract draft 3 so the device knows which screen draws a workspace
    assert all(set(w) <= set(pc_read._WS_FIELDS) | {"boundary", "renderer"} for w in got["workspaces"])
    assert desk.calls == []                                                    # served from the registry, not the desktop


def test_one_workspace_says_a_device_cannot_act_in_it_yet(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/calendar").get_json()
    assert got["workspace"]["id"] == "calendar" and got["actions_here"] is False


def test_an_unknown_workspace_is_a_404_that_names_the_known_ones(client, desk):
    r = get(client, paired(), "/tv/v1/workspaces/nonesuch")
    assert r.status_code == 404 and r.get_json()["code"] == "unknown_workspace" and "calendar" in r.get_json()["known"]


# ── the audit trail ──────────────────────────────────────────────────────────

def test_one_receipt_per_area_per_hour_not_one_per_item(client, desk):
    dev = paired("tablet")
    tok = open_session(client, dev)
    for path in ("/tv/v1/chats", "/tv/v1/chats/a", "/tv/v1/chats/b/messages", "/tv/v1/jobs", "/tv/v1/jobs/t1", "/tv/v1/media"):
        assert client.get(path, headers=auth(tok)).status_code == 200
    seen = rows("device_read")
    assert sorted(r["area"] for r in seen) == ["chats", "jobs", "media"]
    assert all(r["right"] == "pc_read" and r["via"] == "lan" for r in seen)


def test_a_refused_read_writes_no_read_receipt(client, desk):
    get(client, paired("watch", "stephen", right=False), "/tv/v1/chats")
    get(client, paired("phone", right=False), "/tv/v1/chats")
    assert rows("device_read") == []


def test_the_receipt_says_tailnet_when_it_came_that_way(desk):
    tn = gateway.create_app(tailnet=True).test_client()
    dev = paired()
    peer = {"REMOTE_ADDR": "100.101.102.103"}
    nonce = tn.post("/tv/v1/session/challenge", environ_overrides=peer).get_json()["nonce"]
    tok = tn.post("/tv/v1/session/open", environ_overrides=peer,
                  json={"device_id": dev.id, "nonce": nonce, "sig": dev.sign(nonce)}).get_json()["session"]
    assert tn.get("/tv/v1/chats", environ_overrides=peer, headers=auth(tok)).status_code == 200
    assert rows("device_read")[-1]["via"] == "tailnet"


# ── the desktop routes this door calls still exist (a rename must fail here, not on the phone) ────────

def test_every_desktop_path_the_door_calls_is_still_a_desktop_route():
    import pathlib
    import re
    root = pathlib.Path(pc_read.__file__).resolve().parents[1] / "routes"
    src = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    declared = {re.sub(r"<[^>]+>", "<x>", m) for m in re.findall(r"route\(\s*[\"'](/api/[^\"']+)[\"']", src)}
    for path in ("/api/conversations", "/api/conversations/<x>", "/api/conversations/<x>/messages", "/api/tasks",
                 "/api/tasks/<x>", "/api/tasks/<x>/journal", "/api/media", "/api/media/status", "/api/media/<x>",
                 "/api/media/<x>/preview"):
        assert path in declared, path


def test_bound_voice_hook_is_a_quiet_no_op_without_voices_module():
    from agent_friday.tv_gateway import owner_turn
    assert owner_turn.bound_voice("c1") is None
    assert owner_turn.bound_voice(None) is None
