"""/tv/v1/grownup routes and the messages route's window check (services/tv_grownup_window)."""
import pytest

from agent_friday.services import device_pairing
from agent_friday.services import tv_grownup_window as win
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, turn
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

PIN = "4821"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(win, "_path", lambda: tmp_path / "tv_grownup_pin.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "append", lambda kind, rec: None)
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    win._reset_for_tests()
    yield
    win._reset_for_tests()
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


def test_only_an_owner_device_sets_the_pin(client):
    tv = auth(open_session(client, paired("tv", household.FAMILY_ID)))
    assert client.post("/tv/v1/grownup/pin", json={"pin": PIN}, headers=tv).status_code == 403
    assert not win.has_pin()
    phone = auth(open_session(client, paired("phone", household.OWNER_ID)))
    assert client.post("/tv/v1/grownup/pin", json={"pin": "12"}, headers=phone).status_code == 400
    assert client.post("/tv/v1/grownup/pin", json={"pin": PIN}, headers=phone).status_code == 200
    assert win.has_pin()


def test_the_tv_opens_checks_and_closes_a_window(client):
    win.set_pin(PIN)
    h = auth(open_session(client, paired("tv", household.FAMILY_ID)))
    assert client.post("/tv/v1/grownup/open", json={"pin": "0000"}, headers=h).status_code == 403
    r = client.post("/tv/v1/grownup/open", json={"pin": PIN}, headers=h)
    assert r.status_code == 200
    grant = r.get_json()["window"]
    assert client.get("/tv/v1/grownup", headers={**h, "X-Friday-Window": grant}).get_json()["open"] is True
    assert client.post("/tv/v1/grownup/close", headers=h).status_code == 200
    assert client.get("/tv/v1/grownup", headers={**h, "X-Friday-Window": grant}).get_json()["open"] is False


def test_the_window_outlives_a_session_renewal_but_not_a_change_of_tv(client):
    # The TV renews its device session every hour and each screen keeps its own link, so the window is bound
    # to the TV (which every request proves with its key), not to one session.
    win.set_pin(PIN)
    dev = paired("tv", household.FAMILY_ID)
    h1 = auth(open_session(client, dev))
    grant = client.post("/tv/v1/grownup/open", json={"pin": PIN}, headers=h1).get_json()["window"]
    h2 = auth(open_session(client, dev))
    assert client.get("/tv/v1/grownup", headers={**h2, "X-Friday-Window": grant}).get_json()["open"] is True
    other = auth(open_session(client, paired("tv", household.FAMILY_ID)))
    assert client.get("/tv/v1/grownup", headers={**other, "X-Friday-Window": grant}).get_json()["open"] is False


def test_a_grown_up_turn_keeps_the_window_from_going_idle(client, monkeypatch):
    win.set_pin(PIN)
    h = auth(open_session(client, paired("tv", household.FAMILY_ID)))
    grant = client.post("/tv/v1/grownup/open", json={"pin": PIN}, headers=h).get_json()["window"]
    touched = []
    real = win.window_for
    monkeypatch.setattr(win, "window_for", lambda dev, g, touch=False: touched.append(touch) or real(dev, g, touch=touch))
    monkeypatch.setattr(turn, "run", lambda dev, body, p, window_ok=False: iter([turn._event({"type": "message_stop"})]))
    client.post("/tv/v1/messages", json={"messages": [{"role": "user", "content": "hi"}]},
                headers={**h, "X-Friday-Principal": "grownup", "X-Friday-Window": grant})
    assert touched == [True]


@pytest.mark.parametrize("principal,with_grant,want", [("grownup", True, True), ("grownup", False, False),
                                                       ("family", True, False), ("kids", True, False)])
def test_the_messages_route_passes_the_window_only_for_a_grown_up_with_a_live_grant(
        client, monkeypatch, principal, with_grant, want):
    win.set_pin(PIN)
    h = auth(open_session(client, paired("tv", household.FAMILY_ID)))
    grant = client.post("/tv/v1/grownup/open", json={"pin": PIN}, headers=h).get_json()["window"]
    seen = []

    def fake_run(dev, body, p, window_ok=False):
        seen.append(bool(window_ok))
        return iter([turn._event({"type": "message_stop"})])
    monkeypatch.setattr(turn, "run", fake_run)
    hdr = {**h, "X-Friday-Principal": principal}
    if with_grant:
        hdr["X-Friday-Window"] = grant
    client.post("/tv/v1/messages", json={"messages": [{"role": "user", "content": "hi"}]}, headers=hdr)
    assert seen == [want]
