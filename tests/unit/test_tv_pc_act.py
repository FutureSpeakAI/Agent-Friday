"""Controlling jobs from an owner's phone or tablet (``pc_act``; wider brief step 3). Reading a job's steps needs ``pc_read``.

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet that holds ``pc_act`` may act; without it the device raises its own screen-only
  card (once an hour) and is told so; the right is never in the pairing bundle;
* the device never names a path, a process or a body: each action is one fixed desktop POST with no body, so
  ``confirm_pending`` can never be sent from a device;
* the desktop's refusal (409 and its reason) is passed on and nothing is claimed that did not happen;
* each action writes a ``device_act`` receipt (device, right, action, job, via, outcome).
"""
import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_act, pc_read
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
    def __init__(self):
        self.posts, self.status, self.body, self.boom = [], 200, {"ok": True}, None

    def __call__(self, path):
        self.posts.append(path)
        if self.boom:
            raise self.boom
        return self.status, self.body


@pytest.fixture
def desk(monkeypatch):
    stand_in = Desk()
    monkeypatch.setattr(pc_act, "_desk_post", stand_in)
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


def post(client, tv, path):
    return client.post(path, headers=auth(open_session(client, tv)), json={"confirm_pending": True, "path": "/x"})


ACTIONS = [("stop-after-step", "/api/tasks/j1/stop-after-step"), ("resume", "/api/tasks/j1/resume"),
           ("rerun", "/api/tasks/j1/rerun")]


@pytest.mark.parametrize("action,desk_path", ACTIONS)
def test_an_owner_phone_with_the_right_acts_through_one_fixed_desktop_post(client, desk, action, desk_path):
    answer = post(client, device(), "/tv/v1/jobs/j1/" + action)
    assert answer.status_code == 200 and answer.get_json()["ok"] is True
    assert desk.posts == [desk_path]


def test_the_device_cannot_send_confirm_pending_or_a_path(client, desk):
    post(client, device(), "/tv/v1/jobs/j1/resume")
    assert desk.posts == ["/api/tasks/j1/resume"]


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_act(client, desk, kind, member):
    answer = post(client, device(kind, member), "/tv/v1/jobs/j1/stop-after-step")
    assert answer.status_code == 403 and answer.get_json()["code"] == "pc_act_not_for_this_device"
    assert desk.posts == []


def test_a_device_without_the_right_is_told_and_the_pc_asks_on_its_screen(client, desk):
    answer = post(client, device(rights=()), "/tv/v1/jobs/j1/stop-after-step")
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"
    assert answer.get_json()["needs_right"] == "pc_act" and desk.posts == []
    [card] = [c for c in ap.list_approvals(status="pending") if c.get("kind") == pairing.RIGHT_KIND]
    assert card["payload"]["right"] == "pc_act"


def test_the_read_right_alone_does_not_allow_acting(client, desk):
    assert post(client, device(rights=("pc_read",)), "/tv/v1/jobs/j1/rerun").status_code == 403
    assert desk.posts == []


def test_pairing_never_raises_the_act_card_by_itself():
    pairing._offer_rights(device(rights=()).id)
    assert [c for c in ap.list_approvals(status="pending") if (c.get("payload") or {}).get("right") == "pc_act"] == []


def test_withdrawing_the_right_ends_it_on_a_live_session(client, desk):
    tv = device()
    token = open_session(client, tv)
    ok = client.post("/tv/v1/jobs/j1/stop-after-step", headers=auth(token), json={})
    device_pairing.set_right(tv.id, "pc_act", False)
    again = client.post("/tv/v1/jobs/j1/stop-after-step", headers=auth(token), json={})
    assert ok.status_code == 200 and again.status_code == 403 and len(desk.posts) == 1


@pytest.mark.parametrize("bad", ["..", ".hidden", "a" * 200, "a b"])
def test_a_bad_job_id_never_reaches_the_desktop(client, desk, bad):
    answer = post(client, device(), "/tv/v1/jobs/%s/resume" % bad)
    assert answer.status_code in (400, 404) and desk.posts == []


def test_the_desktops_refusal_and_reason_are_passed_on_and_not_a_receipt_of_success(client, desk):
    desk.status, desk.body = 409, {"error": "cannot resume", "reason": "a tool was mid-flight", "internal": "x"}
    answer = post(client, device(), "/tv/v1/jobs/j1/resume")
    assert answer.status_code == 409 and answer.get_json()["reason"] == "a tool was mid-flight"
    assert "internal" not in answer.get_json()
    assert rows("device_act")[-1]["outcome"] == 409


def test_a_desktop_that_fails_is_a_plain_503(client, desk):
    desk.boom = RuntimeError("down")
    answer = post(client, device(), "/tv/v1/jobs/j1/stop-after-step")
    assert answer.status_code == 503 and answer.get_json()["code"] == "pc_unavailable"


def test_each_action_writes_one_receipt(client, desk):
    tv = device()
    post(client, tv, "/tv/v1/jobs/j1/rerun")
    [receipt] = rows("device_act")
    assert (receipt["right"], receipt["action"], receipt["job"], receipt["via"]) == ("pc_act", "rerun", "j1", "lan")


def test_the_tailnet_listener_acts_the_same_way_and_says_so(desk):
    tv = device()
    tail = gateway.create_app(tailnet=True).test_client()
    env = {"REMOTE_ADDR": "100.70.222.100"}
    nonce = tail.post("/tv/v1/session/challenge", environ_overrides=env).get_json()["nonce"]
    token = tail.post("/tv/v1/session/open", environ_overrides=env,
                      json={"device_id": tv.id, "nonce": nonce, "sig": tv.sign(nonce)}).get_json()["session"]
    assert tail.post("/tv/v1/jobs/j1/stop-after-step", environ_overrides=env, headers=auth(token),
                     json={}).status_code == 200
    assert rows("device_act")[-1]["via"] == "tailnet"


# ── reading steps and resumability: pc_read only ───────────────────────────

def test_steps_and_resumable_are_reads_for_pc_read(client, monkeypatch):
    seen = []

    def read_desk(path, params=None):
        seen.append((path, dict(params or {})))
        return 200, b'{"steps": []}', "application/json"
    monkeypatch.setattr(pc_read, "_desk", read_desk)
    reader = device(rights=("pc_read",))
    assert client.get("/tv/v1/jobs/j1/steps?limit=50", headers=auth(open_session(client, reader))).status_code == 200
    assert client.get("/tv/v1/jobs/j1/resumable", headers=auth(open_session(client, reader))).status_code == 200
    assert seen == [("/api/tasks/j1/steps", {"limit": 50}), ("/api/tasks/j1/resumable", {})]
    stranger = device(rights=())
    assert client.get("/tv/v1/jobs/j1/steps", headers=auth(open_session(client, stranger))).status_code == 403


def test_every_desktop_path_the_job_routes_call_is_still_a_desktop_route():
    import pathlib
    import re
    root = pathlib.Path(pc_act.__file__).resolve().parents[1] / "routes"
    src = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    declared = {re.sub(r"<[^>]+>", "<x>", m) for m in re.findall(r"route\(\s*[\"'](/api/[^\"']+)[\"']", src)}
    for path in ("/api/tasks/<x>/steps", "/api/tasks/<x>/resumable", "/api/tasks/<x>/stop-after-step",
                 "/api/tasks/<x>/resume", "/api/tasks/<x>/rerun"):
        assert path in declared, path
