"""The first backup offer on the Doctor page (§34.4-9): its state is read by
the owner's session; an answer is a press on Friday's own page, on this PC."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def test_the_offer_state_and_the_answer_take_the_press(client):
    st = client.get("/api/bundle/first-run").get_json()
    assert st["status"] == "ok" and "due" in st and "why" in st
    assert client.post("/api/bundle/first-run/answer", json={"choice": "never"}).status_code == 403
    r = client.post("/api/bundle/first-run/answer", json={"choice": "never"}, headers=_press(),
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)
    r = client.post("/api/bundle/first-run/answer", json={"choice": "maybe"}, headers=_press())
    assert r.status_code == 400
    r = client.post("/api/bundle/first-run/answer", json={"choice": "not_now"}, headers=_press())
    assert r.status_code == 200 and r.get_json()["answer"] == "not_now"


def test_the_doctor_page_has_the_offer():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="schedule"', 'id="first-backup"', 'id="fb-later"', 'id="fb-never"',
                   "/api/bundle/first-run"):
        assert needle in html, needle
