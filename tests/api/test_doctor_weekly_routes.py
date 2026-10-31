"""The weekly Doctor ask from the Doctor page (A3): each step is the owner's
press on this PC; nothing leaves without the approval card.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def test_the_steps_take_the_press_on_this_pc(client, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    st = client.get("/api/doctor/weekly").get_json()
    assert st["status"] == "ok" and "address" in st and "promise" in st
    assert client.post("/api/doctor/weekly/answer", json={"choice": "weekly"}).status_code == 403
    r = client.post("/api/doctor/weekly/answer", json={"choice": "weekly"}, headers=_press(),
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)
    r = client.post("/api/doctor/weekly/answer", json={"choice": "weekly"}, headers=_press())
    assert r.status_code == 200 and r.get_json()["answer"] == "weekly"
    assert client.post("/api/doctor/weekly/send", json={"sha256": "x"}).status_code == 403
    assert client.post("/api/doctor/weekly/nope", json={}, headers=_press()).status_code == 404


def test_the_doctor_page_has_the_weekly_card():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="weekly"', 'id="w-yes"', 'id="w-notnow"', 'id="w-never"', 'id="w-send"',
                   'id="w-skip"', 'id="w-stop"', 'id="weekly-terms"', "/api/doctor/weekly/prepare", "Also while Friday is in local-only mode"):
        assert needle in html, needle


def test_the_page_never_says_the_report_is_never_sent_while_it_offers_the_email():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    assert "never sends it anywhere by itself" in html and "never sends it anywhere." not in html
