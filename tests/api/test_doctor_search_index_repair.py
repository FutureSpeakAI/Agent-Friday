"""The Doctor's 'Rebuild the search index' is the owner's press on this PC (NS-32.6-1).

On a tree without the route the posts are 404, so these fail by assertion on the status code.
"""
from __future__ import annotations

from tests.desk_press_support import desk_headers, page_headers


def test_the_rebuild_needs_the_press_on_this_pc(client, monkeypatch):
    from agent_friday.services import conversation_fts as fts
    calls = []
    monkeypatch.setattr(fts, "repair", lambda reason="owner": calls.append(reason) or {"ok": True}, raising=False)
    r = client.post("/api/doctor/repair/search-index", json={}, headers=page_headers())
    assert r.status_code == 403, r.status_code
    assert r.get_json()["code"] == "desk_proof_required"
    ok = client.post("/api/doctor/repair/search-index", json={}, headers=desk_headers())
    assert ok.status_code == 202, ok.status_code
    assert ok.get_json()["started"] is True


def test_the_doctor_page_offers_the_rebuild_only_for_a_damaged_index():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "static" / "doctor.html").read_text(encoding="utf-8")
    assert "c.id === 'search_index' && c.state === 'degraded'" in page
    assert "api('/api/doctor/repair/search-index'" in page
