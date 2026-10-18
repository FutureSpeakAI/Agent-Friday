"""The owner can see and undo a change to Friday's core instructions.

GET /api/instructions lists every section with who changed it and any health
row; GET /api/instructions/<id> shows the shipped text, the text in use and
the difference; POST .../undo puts the previous text back, and only the
owner's click on the page counts (services/screen_click).
"""
from __future__ import annotations

import pytest

KEY = b"g" * 32
SID = "50_say_before_acting"


@pytest.fixture
def adopted(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "friday"))
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    from agent_friday.services import instruction_files as inf
    inf._reset_caches_for_tests()
    new = inf.shipped_text(SID).replace("Before your FIRST tool call", "ADOPTED. Before your FIRST tool call")
    inf.write_override(SID, new, actor="card:appr_t1", card="appr_t1", reason="tested")
    yield inf, new
    inf._reset_caches_for_tests()


def test_the_list_shows_who_changed_each_section(client, adopted):
    r = client.get("/api/instructions")
    assert r.status_code == 200
    rows = {x["id"]: x for x in r.get_json()["sections"]}
    assert rows[SID]["changed_by"] == "card:appr_t1" and rows[SID]["can_undo"] is True
    assert rows["20_autonomous_operation"]["locked"] is True


def test_the_detail_shows_both_texts_and_the_difference(client, adopted):
    inf, new = adopted
    d = client.get("/api/instructions/" + SID).get_json()["section"]
    assert d["shipped"] == inf.shipped_text(SID) and d["in_use"] == new
    assert "ADOPTED" in d["diff"]


def test_undo_without_the_owners_click_is_refused(client, adopted):
    inf, new = adopted
    r = client.post("/api/instructions/" + SID + "/undo", json={"decided_by": "owner:ui"})
    assert r.status_code == 403
    assert inf.load(SID) == new


def test_the_owners_click_undoes_the_change(client, adopted, monkeypatch):
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    inf, new = adopted
    r = client.post("/api/instructions/" + SID + "/undo", json={"decided_by": "owner:ui"})
    assert r.status_code == 200, r.get_json()
    assert inf.load(SID) == inf.shipped_text(SID)
    assert inf.history(SID)[-1]["actor"] == "owner:ui"


def test_a_locked_section_cannot_be_undone_here(client, adopted, monkeypatch):
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    r = client.post("/api/instructions/20_autonomous_operation/undo", json={"decided_by": "owner:ui"})
    assert r.status_code == 400
