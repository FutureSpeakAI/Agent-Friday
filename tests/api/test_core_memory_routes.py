"""The memory card panel's routes (routes/core_memory): the owner sees, edits and
deletes every line, keeps or discards suggestions, and undoes Friday's changes.

Red on 7a898476e: /api/core-memory did not exist.
"""
from __future__ import annotations

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


def _post(client, path, body):
    r = client.post("/api/core-memory/" + path, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()


def test_the_owner_sees_edits_and_deletes_every_line(client):
    cm.add("user", "Prefers tea.", actor="friday")
    d = client.get("/api/core-memory").get_json()
    assert d["ok"] and d["card"]["user"]["entries"] == ["Prefers tea."]
    assert d["changes"][0]["op"] == "add"
    assert _post(client, "add", {"target": "friday", "text": "Lead with the answer."})["ok"]
    assert _post(client, "edit", {"target": "user", "index": 0, "text": "Prefers green tea."})["ok"]
    assert cm.entries("user") == ["Prefers green tea."]
    assert _post(client, "delete", {"target": "user", "index": 0})["ok"]
    assert cm.entries("user") == []
    assert _post(client, "delete", {"target": "user", "index": 5})["ok"] is False


def test_an_owner_edit_drops_the_frozen_prompt(client):
    prompt_freeze.frozen("conv-x", "core_memory:ungated", lambda: "old")
    _post(client, "add", {"target": "user", "text": "New line."})
    assert prompt_freeze.frozen("conv-x", "core_memory:ungated", lambda: "fresh") == "fresh"


def test_review_and_undo(client):
    q = cm.queue("user", "add", text="Runs on Sundays.", source="dream:2026-10-13")
    assert client.get("/api/core-memory").get_json()["pending"][0]["id"] == q["id"]
    assert _post(client, "review", {"id": q["id"], "keep": True})["ok"]
    assert cm.entries("user") == ["Runs on Sundays."]
    r = cm.add("user", "Plays piano.", actor="friday")
    assert _post(client, "undo", {"change_id": r["change_id"]})["ok"]
    assert cm.entries("user") == ["Runs on Sundays."]


def test_the_panel_is_in_both_ui_files():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for f in ("index.html", "ui_parts/app.html"):
        text = (root / f).read_text(encoding="utf-8")
        assert "function MemoryCardPanel()" in text, f
        assert "MemoryCardPanel" in text.split("function MemoryCardPanel()")[0], f + ": panel not mounted"
        assert "/api/core-memory" in text
