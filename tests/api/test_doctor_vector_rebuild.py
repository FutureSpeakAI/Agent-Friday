"""The Doctor's rebuild of the conversation memory is the owner's press on this PC, offered only
for a damaged store, and asks first when older recall would be lost (BD-16 C2b). On a tree
without the route the post is 404, so this fails by assertion on the status code."""
from __future__ import annotations

import time

from tests.desk_press_support import desk_headers, page_headers

PATH = "/api/doctor/repair/vector-memory"


def _stub(monkeypatch, health):
    from agent_friday.services import memory_store_repair as m
    if hasattr(m, "rebuild_vector"):
        monkeypatch.setattr(m, "rebuild_vector", lambda by, **k: m._VEC_REBUILDING.clear() or {"ok": True})
    monkeypatch.setattr(m, "vector_health", lambda *a, **k: dict(health))
    return m


def _settle(m):
    deadline = time.time() + 5
    while getattr(m, "vector_rebuild_running", lambda: False)() and time.time() < deadline:
        time.sleep(0.02)
    if hasattr(m, "_VEC_REBUILDING"):
        m._VEC_REBUILDING.clear()


def test_the_conversation_memory_rebuild_needs_the_press_on_this_pc(client, monkeypatch):
    m = _stub(monkeypatch, {"state": "damaged", "why": "quick_check"})
    r = client.post(PATH, json={}, headers=page_headers())
    assert r.status_code == 403, r.status_code
    assert r.get_json()["code"] == "desk_proof_required"
    ok = client.post(PATH, json={}, headers=desk_headers())
    assert ok.status_code == 202, ok.status_code
    _settle(m)


def test_a_healthy_store_is_not_rebuilt(client, monkeypatch):
    m = _stub(monkeypatch, {"state": "ok"})
    r = client.post(PATH, json={}, headers=desk_headers())
    assert r.status_code == 409 and r.get_json()["code"] == "not_damaged", r.status_code
    _settle(m)


def test_a_store_that_will_not_open_asks_before_losing_older_recall(client, monkeypatch):
    m = _stub(monkeypatch, {"state": "damaged", "why": "InternalError"})
    r = client.post(PATH, json={}, headers=desk_headers())
    assert r.status_code == 409 and r.get_json()["code"] == "confirm_loss", r.status_code
    ok = client.post(PATH, json={"confirm_loss": True}, headers=desk_headers())
    assert ok.status_code == 202, ok.status_code
    _settle(m)


def test_the_doctor_page_offers_the_conversation_memory_rebuild():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "static" / "doctor.html").read_text(encoding="utf-8")
    assert "/api/doctor/repair/vector-memory" in page and "confirm_loss" in page
