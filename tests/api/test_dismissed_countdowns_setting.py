"""A start-screen countdown the owner hid stays hidden: it is saved through the app's settings route, read back
after a reload from disk, and a value of the wrong shape is refused. Synthetic ids only."""
import json

from agent_friday import core

HIDDEN = {"todo:t-grant": 1893456000, "wiki:people/dana-reyes.md#0": 1893542400}


def test_a_hidden_countdown_is_saved_through_the_settings_route_and_survives_a_restart(client):
    r = client.post("/api/settings", json={"settings": {"dismissed_countdowns": HIDDEN}})
    assert r.status_code == 200
    on_disk = json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert on_disk["dismissed_countdowns"] == HIDDEN
    assert core._load_settings()["dismissed_countdowns"] == HIDDEN            # a fresh read, as after a restart
    assert client.get("/api/settings").get_json()["settings"]["dismissed_countdowns"] == HIDDEN


def test_nothing_is_hidden_until_the_owner_hides_something(client):
    assert core.DEFAULT_SETTINGS["dismissed_countdowns"] == {}


def test_a_value_of_the_wrong_shape_is_refused_and_changes_nothing(client):
    client.post("/api/settings", json={"settings": {"dismissed_countdowns": HIDDEN}})
    r = client.post("/api/settings", json={"settings": {"dismissed_countdowns": ["todo:t-grant"]}})
    assert r.status_code >= 400
    assert core._load_settings()["dismissed_countdowns"] == HIDDEN
