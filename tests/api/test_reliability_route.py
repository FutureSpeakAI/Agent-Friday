"""GET /api/doctor/reliability and the Doctor page section (NS-33.6-1)."""
from __future__ import annotations

from pathlib import Path


def test_the_route_answers_on_this_pc(client):
    resp = client.get("/api/doctor/reliability")
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["status"] == "ok"
    assert "connectors" in body and "sessions" in body
    assert resp.headers.get("Cache-Control") == "no-store"


def test_the_route_refuses_another_machine(client):
    resp = client.get("/api/doctor/reliability", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert resp.status_code in (401, 403)


def test_the_doctor_page_has_the_section():
    page = Path(__file__).resolve().parents[2] / "static" / "doctor.html"
    html = page.read_text(encoding="utf-8")
    assert 'id="reliability"' in html and "/api/doctor/reliability" in html


def test_the_doctor_report_carries_it_and_the_weekly_mail_does_not(monkeypatch):
    from agent_friday.services import doctor, doctor_weekly
    rep = doctor.build()
    assert "reliability" in rep
    text = doctor_weekly._render(rep, False)
    assert "Connector changes" not in text
