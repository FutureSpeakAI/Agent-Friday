"""The Doctor's rebuilds of conversation memory and the knowledge graph are the owner's press
on this PC (BD-16; 32.5). On a tree without the routes the posts are 404, so these fail by
assertion on the status code."""
from __future__ import annotations

import pytest

from tests.desk_press_support import desk_headers, page_headers


@pytest.mark.parametrize("path,fn", [("/api/doctor/repair/vector-memory", "repair_vector"),
                                     ("/api/doctor/repair/knowledge-graph", "repair_kg")])
def test_each_rebuild_needs_the_press_on_this_pc(client, monkeypatch, path, fn):
    try:
        from agent_friday.services import memory_store_repair as m
        monkeypatch.setattr(m, fn, lambda by: {"ok": True})
    except ImportError:
        pass
    r = client.post(path, json={}, headers=page_headers())
    assert r.status_code == 403, r.status_code
    assert r.get_json()["code"] == "desk_proof_required"
    ok = client.post(path, json={}, headers=desk_headers())
    assert ok.status_code == 202, ok.status_code


def test_the_doctor_page_offers_both_rebuilds():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "static" / "doctor.html").read_text(encoding="utf-8")
    assert "/api/doctor/repair/vector-memory" in page
    assert "/api/doctor/repair/knowledge-graph" in page
