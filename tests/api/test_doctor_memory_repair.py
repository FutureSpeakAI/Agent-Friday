"""The Doctor's rebuild of the knowledge graph is the owner's press on this PC (BD-16; 32.5).
On a tree without the route the post is 404, so this fails by assertion on the status code."""
from __future__ import annotations

from tests.desk_press_support import desk_headers, page_headers


def test_the_graph_rebuild_needs_the_press_on_this_pc(client, monkeypatch):
    try:
        from agent_friday.services import memory_store_repair as m
        monkeypatch.setattr(m, "repair_kg", lambda by, **k: m._KG_REPAIRING.clear() or {"ok": True})
    except ImportError:
        m = None
    path = "/api/doctor/repair/knowledge-graph"
    r = client.post(path, json={}, headers=page_headers())
    assert r.status_code == 403, r.status_code
    assert r.get_json()["code"] == "desk_proof_required"
    ok = client.post(path, json={}, headers=desk_headers())
    assert ok.status_code == 202, ok.status_code
    if m is not None:
        import time
        deadline = time.time() + 5
        while m.kg_repair_running() and time.time() < deadline:
            time.sleep(0.02)
        m._KG_REPAIRING.clear()


def test_the_doctor_page_offers_the_graph_rebuild():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "static" / "doctor.html").read_text(encoding="utf-8")
    assert "/api/doctor/repair/knowledge-graph" in page
