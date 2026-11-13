"""Model switching for paired devices: GET and POST /tv/v1/seats.

Stephen, 2026-11-13: "I want model switching on phone and tablet and PC as well. Fast and easy."
The promises guarded here:

* an owner's personal device (phone, tablet, watch bound to the owner) lists every seat the owner can
  use right now, the current one marked, and switches with one POST; the switch is the top bar pill's own
  save (POST /api/settings, in process, this PC's token), never a write of its own;
* the family member's device, the TV and an unbound device see only the seats the owner allowed that
  member (nothing until set), no "current", and cannot switch (403);
* a device never names a member (400); an unknown seat is refused (404) and nothing is saved;
* a seat the PC refuses (the seat gate, a local-only guard) is a 409 with its reason, and is not reported as set.
"""
import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, seats
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

CATALOGUE = [
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "short": "Sonnet 5.5", "provider": "anthropic",
     "provider_label": "Anthropic", "local": False, "available": True},
    {"id": "bonsai2:27b", "label": "Bonsai 2 27B", "short": "Bonsai2", "provider": "ollama-local",
     "provider_label": "This PC", "local": True, "available": True},
    {"id": "gpt-x", "label": "GPT X", "short": "GPT X", "provider": "openai", "provider_label": "OpenAI",
     "local": False, "available": False},                       # no key: not offered
]


@pytest.fixture
def state():
    return {"model": "claude-sonnet-5-5", "saved": [], "refuse": None}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch, state):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(seats, "_catalogue", lambda: [r for r in CATALOGUE if r["available"] is not False])
    monkeypatch.setattr(seats, "_current", lambda: state["model"])

    def save(model_id):
        if state["refuse"]:
            return False, state["refuse"]
        state["model"] = model_id
        state["saved"].append(model_id)
        return True, ""
    monkeypatch.setattr(seats, "_save", save)
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def paired(kind, member):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


def get(client, dev, path="/tv/v1/seats"):
    return client.get(path, headers=auth(open_session(client, dev)))


def post(client, dev, body):
    return client.post("/tv/v1/seats", json=body, headers=auth(open_session(client, dev)))


@pytest.mark.parametrize("kind", ["phone", "tablet", "watch"])
def test_an_owners_device_lists_the_available_seats_with_the_current_one_marked(client, kind):
    r = get(client, paired(kind, "stephen"))
    b = r.get_json()
    assert r.status_code == 200 and b["ok"] and b["can_switch"] is True and b["current"] == "claude-sonnet-5-5"
    assert [s["id"] for s in b["seats"]] == ["claude-sonnet-5-5", "bonsai2:27b"]      # the keyless one is not offered
    assert [s["current"] for s in b["seats"]] == [True, False]
    assert [s["local"] for s in b["seats"]] == [False, True]


def test_an_owners_device_switches_with_one_post_and_the_list_follows(client, state):
    dev = paired("phone", "stephen")
    r = post(client, dev, {"model": "bonsai2:27b"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "current": "bonsai2:27b"}
    assert state["saved"] == ["bonsai2:27b"]
    assert get(client, dev).get_json()["current"] == "bonsai2:27b"


def test_an_unknown_or_unavailable_seat_is_refused_and_nothing_is_saved(client, state):
    dev = paired("phone", "stephen")
    for wanted in ("gpt-x", "no-such-model", "", "  "):
        assert post(client, dev, {"model": wanted}).status_code in (400, 404)
    assert state["saved"] == []


def test_a_seat_the_pc_refuses_is_a_409_with_its_reason_not_a_silent_success(client, state):
    state["refuse"] = "that model is not allowed in local-only mode"
    r = post(client, paired("phone", "stephen"), {"model": "claude-sonnet-5-5"})
    assert r.status_code == 409 and "local-only" in r.get_json()["error"] and r.get_json()["ok"] is False


@pytest.mark.parametrize("kind,member", [("tv", "family"), ("tablet", "family"), ("phone", None), ("tv", "stephen")])
def test_family_tv_and_unbound_devices_see_nothing_until_allowed_and_cannot_switch(client, state, kind, member):
    dev = paired(kind, member)
    b = get(client, dev).get_json()
    assert b["seats"] == [] and b["can_switch"] is False and b["current"] is None
    r = post(client, dev, {"model": "bonsai2:27b"})
    assert r.status_code == 403 and state["saved"] == []


def test_the_family_member_sees_only_the_seats_the_owner_allowed(client):
    household.set_area("family", "model_defaults", {"allowed_seats": ["bonsai2:27b", "gpt-x", "not-a-seat"]})
    b = get(client, paired("tv", "family")).get_json()
    assert [s["id"] for s in b["seats"]] == ["bonsai2:27b"]          # allowed AND available; never the owner's current
    assert b["can_switch"] is False and b["current"] is None and all(s["current"] is False for s in b["seats"])


def test_a_device_cannot_name_a_member(client, state):
    dev = paired("phone", "stephen")
    assert get(client, dev, "/tv/v1/seats?member=stephen").status_code == 400
    assert post(client, paired("tv", "family"), {"model": "bonsai2:27b", "member": "stephen"}).status_code == 400
    assert post(client, dev, {"model": "bonsai2:27b", "principal": "owner"}).status_code == 400
    assert state["saved"] == []


def test_the_endpoints_need_a_device_session(client):
    assert client.get("/tv/v1/seats").status_code == 401
    assert client.post("/tv/v1/seats", json={"model": "bonsai2:27b"}).status_code == 401


def test_the_save_is_the_pills_own_settings_save_with_this_pcs_token(monkeypatch):
    from agent_friday import core
    seen = {}

    class R:
        status_code = 200

        def get_json(self, silent=True):
            return {"status": "ok"}

    class C:
        def post(self, path, json=None, headers=None):
            seen.update(path=path, body=json, token=(headers or {}).get("X-Friday-Token"))
            return R()
    monkeypatch.undo()                                  # the real _save, not the fixture's stand-in
    monkeypatch.setattr(core.app, "test_client", lambda: C())
    assert seats._save("bonsai2:27b") == (True, "")
    assert seen["path"] == "/api/settings" and seen["body"] == {"settings": {"orchestrator_model": "bonsai2:27b"}}
    assert seen["token"] == core._current_api_token()
