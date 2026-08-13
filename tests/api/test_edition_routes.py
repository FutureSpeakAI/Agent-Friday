"""API tests for The Friday Edition E0 routes (routes/edition.py).

Composer inputs are blanked (empty archive/front_pages/etc.) via the same
monkeypatch approach as tests/unit/test_edition_engine.py — these tests only
exercise the HTTP layer (status codes, response shape), not composition logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import creations as cr
from agent_friday.services import edition_engine as ee
from agent_friday.services import memory_dreaming as md
from agent_friday.services import news_engine as ne


@pytest.fixture(autouse=True)
def _isolated_edition_dir(tmp_path, monkeypatch):
    base = tmp_path / "edition"
    monkeypatch.setattr(ee, "EDITION_DIR", base)
    monkeypatch.setattr(ee, "EDITIONS_DIR", base / "editions")
    monkeypatch.setattr(ee, "CHARTER_FILE", base / "charter.md")
    monkeypatch.setattr(ee, "CHARTER_VERSIONS_DIR", base / "charter_versions")
    monkeypatch.setattr(ee, "VERBS_FILE", base / "verbs.jsonl")
    monkeypatch.setattr(ee, "CORRECTIONS_SHOWN_FILE", base / "corrections_shown.json")
    monkeypatch.setattr(ne, "_read_archive", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(ne, "_list_front_pages", lambda: [], raising=False)
    monkeypatch.setattr(ne, "_list_editorials", lambda **kw: [], raising=False)
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: [], raising=False)
    monkeypatch.setattr(cr, "_list_daily_creations", lambda: [], raising=False)
    monkeypatch.setattr(md, "recent_dreams", lambda n=7: [], raising=False)
    yield


class TestEditionLatest:
    def test_returns_null_before_any_compose(self, client):
        resp = client.get("/api/edition/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["edition"] is None
        assert data["editions"] == []

    def test_returns_composed_edition(self, client):
        client.post("/api/edition/compose")
        resp = client.get("/api/edition/latest")
        data = resp.get_json()
        assert data["edition"] is not None
        assert data["edition"]["caught_up"] is True
        assert len(data["editions"]) == 1


class TestEditionCompose:
    def test_compose_returns_all_e0_sections(self, client):
        resp = client.post("/api/edition/compose")
        assert resp.status_code == 200
        data = resp.get_json()
        edition = data["edition"]
        section_ids = {s["id"] for s in edition["sections"]}
        assert {"career_signals", "your_people", "fridays_desk"} <= section_ids
        assert "dissent" in edition
        assert "corrections" in edition
        assert "rationale" in edition and len(edition["rationale"]) == 5

    def test_every_card_has_a_receipt(self, client):
        resp = client.post("/api/edition/compose")
        edition = resp.get_json()["edition"]
        all_cards = [c for s in edition["sections"] for c in s["cards"]] + edition["dissent"]
        assert all_cards
        assert all(c.get("receipt") for c in all_cards)


class TestEditionCharter:
    def test_get_returns_default_charter(self, client):
        resp = client.get("/api/edition/charter")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "§1" in data["charter"]["clauses"]

    def test_post_updates_charter(self, client):
        resp = client.post("/api/edition/charter", json={"text": "# X\n\n## §1 Section budgets\n- Friday's Desk: up to 1 cards\n"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "§1" in data["charter"]["clauses"]

    def test_post_rejects_empty_text(self, client):
        resp = client.post("/api/edition/charter", json={"text": "   "})
        assert resp.status_code == 400


class TestEditionVerb:
    def test_logs_keep_verb(self, client):
        resp = client.post("/api/edition/verb", json={"card_id": "c1", "verb": "keep"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entry"]["verb"] == "keep"

    def test_rejects_unknown_verb(self, client):
        resp = client.post("/api/edition/verb", json={"card_id": "c1", "verb": "nope"})
        assert resp.status_code == 400

    def test_rejects_missing_card_id(self, client):
        resp = client.post("/api/edition/verb", json={"verb": "keep"})
        assert resp.status_code == 400

    def test_why_with_reason_appears_in_next_compose(self, client):
        client.post("/api/edition/verb", json={
            "card_id": "c1", "verb": "why", "reason": "This is stale",
            "card_title": "Old Story",
        })
        resp = client.post("/api/edition/compose")
        edition = resp.get_json()["edition"]
        assert len(edition["corrections"]) == 1
        assert "Old Story" in edition["corrections"][0]["title"]
