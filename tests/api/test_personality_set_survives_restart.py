"""A trait set through /api/personality/set is still there after a restart, and is read back by /api/personality."""
import json

import pytest

from agent_friday import core
from agent_friday.services import personality_state as ps


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ps, "_BEGUN", False)
    from agent_friday.routes import contacts
    monkeypatch.setattr(contacts, "FRIDAY_DIR", tmp_path)
    (tmp_path / ".setup_complete").write_text("", encoding="utf-8")   # first run is over
    return tmp_path


def test_a_trait_set_by_the_owner_survives_a_restart(client, _home, monkeypatch):
    ps.begin_session()
    r = client.post("/api/personality/set", json={"trait": "humor", "value": 0.95})
    assert r.get_json() == {"status": "ok", "trait": "humor", "value": 0.95}
    monkeypatch.setattr(ps, "_BEGUN", False)                  # the server restarts
    ps.begin_session()
    got = client.get("/api/personality").get_json()
    assert got["traits"]["humor"] == 0.95 and got["session_count"] == 2


def test_a_bad_value_is_an_error_and_changes_nothing(client, _home):
    ps.begin_session()
    before = (_home / "personality.json").read_text(encoding="utf-8")
    r = client.post("/api/personality/set", json={"trait": "humor", "value": "loud"})
    assert r.status_code == 400 and (_home / "personality.json").read_text(encoding="utf-8") == before
