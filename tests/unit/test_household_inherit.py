"""The household store (D4) and the "pair a fresh device -> zero re-entry" inherit endpoint (R3/G4).

Plan: Projects/friday-tablet/docs/SPLIT-PLAN.md (D4, R3). The promises guarded here:

* the store is keyed by member id; a member's data is that member's alone, and a shared space holds
  what everyone sees;
* a device is bound to one member; its bundle is that member's data plus the shared space and nothing
  of any other member's, and a device never names which member it wants;
* one authenticated GET gives a freshly paired device everything it needs (zero re-entry);
* no key value is ever in the store, the bundle or the response, and key copy is not offered here;
* the file is signed: an edited or restored store grants nothing.
"""
import json

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

from agent_friday.tv_gateway import household, inherit

# built at run time so no key-shaped literal sits in the file
SECRET = "-".join(["sk", "ant", "THIS", "MUST", "NEVER", "LEAVE", "THE", "PC", "0123456789"])


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def home():
    """Stephen and Janet, each with data of their own, plus a shared space."""
    household.add_member("stephen", "Stephen", role="owner")
    household.add_member("janet", "Janet", role="adult")
    household.set_area("stephen", "profile", {"name": "Stephen", "voice": "owner-private-voice"})
    household.set_area("stephen", "chats", [{"id": "s1", "title": "STEPHEN-ONLY-CHAT"}])
    household.set_area("stephen", "creations", [{"id": "sc1", "title": "STEPHEN-PRIVATE-CREATION"}])
    household.set_area("janet", "profile", {"name": "Janet", "voice": "janet-voice"})
    household.set_area("janet", "settings", {"text_size": "large"})
    household.set_area("janet", "model_defaults", {"chat": "local-small"})
    household.set_area("janet", "chats", [{"id": "j1", "title": "Janet's garden plan"}])
    household.set_area("janet", "arcade", {"favourites": ["pacman"], "saves": {"pacman": "slot1"}})
    household.set_area("janet", "creations", [{"id": "jc1", "title": "Janet's card"}])
    household.set_shared("arcade", {"library": ["pacman", "tetris"]})
    household.set_shared("creations", [{"id": "sh1", "title": "Family photo reel"}])


@pytest.fixture
def client():
    app = gateway.create_app()
    inherit.register(app)
    return app.test_client()


@pytest.fixture
def tablet():
    t = FakeTv()
    device_pairing.add_device(t.spki, name="Janet's tablet", kind="tablet")
    return t


def get(client, tablet, path="/tv/v1/inherit"):
    return client.get(path, headers=auth(open_session(client, tablet)))


# ── the acceptance: pair a fresh device -> usable with zero re-entry ───────────────────────

def test_a_freshly_paired_device_gets_its_members_whole_setup_in_one_call(home, client, tablet):
    household.bind_device(tablet.id, "janet")
    r = get(client, tablet)
    assert r.status_code == 200, r.get_json()
    b = r.get_json()
    assert b["ok"] and b["member"] == {"id": "janet", "name": "Janet", "role": "adult"}
    assert b["profile"]["name"] == "Janet"
    assert b["settings"] == {"text_size": "large"}
    assert b["model_defaults"] == {"chat": "local-small"}
    assert [c["id"] for c in b["chats"]] == ["j1"]
    assert b["arcade"]["favourites"] == ["pacman"] and b["arcade"]["saves"] == {"pacman": "slot1"}
    assert [c["id"] for c in b["creations"]] == ["jc1"]
    # the shared household space rides along, kept apart from her own
    assert b["shared"]["arcade"]["library"] == ["pacman", "tetris"]
    assert [c["id"] for c in b["shared"]["creations"]] == ["sh1"]


def test_a_members_bundle_never_carries_another_members_data(home, client, tablet):
    household.bind_device(tablet.id, "janet")
    text = get(client, tablet).get_data(as_text=True)
    for private in ("STEPHEN-ONLY-CHAT", "STEPHEN-PRIVATE-CREATION", "owner-private-voice"):
        assert private not in text


def test_the_owners_device_gets_the_owners_data_and_not_janets(home, client):
    t = FakeTv()
    device_pairing.add_device(t.spki, name="Stephen's phone", kind="phone")
    household.bind_device(t.id, "stephen")
    b = get(client, t).get_json()
    assert b["member"]["id"] == "stephen" and [c["id"] for c in b["chats"]] == ["s1"]
    assert "Janet's garden plan" not in json.dumps(b)


def test_a_device_cannot_name_a_different_member(home, client, tablet):
    household.bind_device(tablet.id, "janet")
    for path in ("/tv/v1/inherit?member=stephen", "/tv/v1/inherit?member_id=stephen"):
        r = get(client, tablet, path)
        assert r.status_code == 400
        assert "STEPHEN" not in r.get_data(as_text=True)


def test_a_device_with_no_member_chosen_gets_no_data_and_is_told_why(home, client, tablet):
    r = get(client, tablet)
    assert r.status_code == 409
    b = r.get_json()
    assert b["ok"] is False and b["needs_member"] is True
    text = r.get_data(as_text=True)
    assert "Janet's garden plan" not in text and "STEPHEN" not in text


def test_the_endpoint_needs_a_device_session(home, client, tablet):
    household.bind_device(tablet.id, "janet")
    assert client.get("/tv/v1/inherit").status_code == 401


# ── keys never travel, never rest here ─────────────────────────────────────────────────────

def test_no_key_value_is_stored_or_sent_and_key_copy_is_not_offered(home, client, tablet, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    household.bind_device(tablet.id, "janet")
    with pytest.raises(ValueError):
        household.set_area("janet", "keys", {"anthropic": SECRET})
    r = get(client, tablet)
    assert SECRET not in r.get_data(as_text=True)
    assert r.get_json()["keys"] == {"mode": "proxy", "copied": []}
    assert SECRET not in household._path().read_text(encoding="utf-8")


def test_secret_shaped_values_in_a_members_data_are_refused(home):
    with pytest.raises(ValueError):
        household.set_area("janet", "settings", {"anthropic_api_key": SECRET})


# ── the store itself ───────────────────────────────────────────────────────────────────────

def test_member_ids_are_plain_and_cannot_be_paths(home):
    for bad in ("", "../x", "a/b", "A B", "x" * 65, "Janet"):
        with pytest.raises(ValueError):
            household.add_member(bad, "Nobody")


def test_a_device_binds_to_a_known_member_only_and_to_one_member(home, tablet):
    with pytest.raises(KeyError):
        household.bind_device(tablet.id, "nobody")
    household.bind_device(tablet.id, "janet")
    household.bind_device(tablet.id, "stephen")        # re-binding moves it, it never holds two
    assert household.member_for_device(tablet.id) == "stephen"


def test_removing_a_member_unbinds_their_devices_and_deletes_their_data(home, tablet):
    household.bind_device(tablet.id, "janet")
    household.remove_member("janet")
    assert household.member_for_device(tablet.id) is None
    assert household.bundle_for("janet") is None


def test_an_edited_store_reads_as_empty(home):
    p = household._path()
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["body"]["members"]["janet"]["areas"]["profile"]["name"] = "Mallory"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert household.list_members() == []


def test_the_store_survives_a_reload(home):
    again = household.bundle_for("janet")
    assert again["profile"]["name"] == "Janet" and again["shared"]["arcade"]["library"] == ["pacman", "tetris"]
