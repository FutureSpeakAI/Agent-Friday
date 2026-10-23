"""The memory card's correction controls (north star Â§34.9-4): the owner can
delete a line as wrong, sees how many of their earlier messages stopped being
used as evidence, and can put them back. Routes plus both UI files."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import core_memory as cm
from agent_friday.services import prompt_freeze


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


def test_a_delete_marked_wrong_reports_the_retired_messages_and_they_can_come_back(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(cm, "_supersede_turns", lambda line, why: ["m-1", "m-2"])
    monkeypatch.setattr(cm, "restore_superseded", lambda ids: seen.setdefault("ids", list(ids)) and len(ids))
    cm.add("user", "Allergic to peanuts and cashews.", actor="owner")
    cm.add("user", "Dentist is Dr Okafor at Riverside.", actor="owner")
    plain = client.post("/api/core-memory/delete", json={"target": "user", "index": 1}).get_json()
    assert plain["ok"] and "superseded_turns" not in plain
    wrong = client.post("/api/core-memory/delete", json={"target": "user", "index": 0, "wrong": True}).get_json()
    assert wrong["ok"] and wrong["superseded_turns"] == 2 and wrong["superseded_ids"] == ["m-1", "m-2"]
    back = client.post("/api/core-memory/restore-messages", json={"ids": ["m-1", "m-2"]}).get_json()
    assert back == {"ok": True, "restored": 2} and seen["ids"] == ["m-1", "m-2"]


@pytest.mark.parametrize("body", [{}, {"ids": []}, {"ids": "m-1"}, {"ids": [1, 2]}, {"ids": ["x" * 81]},
                                  {"ids": ["m"] * 201}])
def test_restore_takes_only_a_short_list_of_ids(client, body):
    r = client.post("/api/core-memory/restore-messages", json=body)
    assert r.status_code == 400 and r.get_json()["ok"] is False


def test_both_ui_files_have_the_controls():
    root = Path(__file__).resolve().parents[2]
    for f in ("index.html", "ui_parts/app.html"):
        panel = (root / f).read_text(encoding="utf-8").split("function MemoryCardPanel(", 1)[1][:20000]
        assert "Wrong: delete" in panel and "wrong: true" in panel, f
        assert "restore-messages" in panel and "data-core-memory-retired" in panel, f
        assert "no longer used as evidence" in panel, f
