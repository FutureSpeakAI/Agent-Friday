"""The Doctor page's scheduled-bundle controls and strength meter.

Red on the old code by assertion: the routes did not exist (404), and the
one-off bundle accepted a weak passphrase of 12 characters or more.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASS = "orbit lantern pepper tundra violet 42"   # pragma: allowlist secret


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token()}


def test_the_meter_answers_on_this_pc_only(client):
    r = client.post("/api/bundle/strength", json={"passphrase": "password1234"})
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert client.post("/api/bundle/strength", json={"passphrase": PASS}).get_json()["ok"] is True
    r = client.post("/api/bundle/strength", json={"passphrase": PASS},
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)


def test_a_one_off_bundle_refuses_a_weak_passphrase(client):
    r = client.post("/api/bundle/create", json={"passphrase": "password1234"}, headers=_press())
    assert r.status_code == 400 and r.get_json()["code"] == "weak_passphrase"


def test_the_schedule_takes_the_press_and_never_shows_the_passphrase(client, tmp_path):
    from agent_friday.services import backup_schedule as bs
    bs._TEST_SECRETS.clear()
    body = {"enabled": True, "every": "weekly", "folder": str(tmp_path / "out"), "keep": 3,
            "passphrase": PASS}
    assert client.post("/api/bundle/schedule", json=body).status_code == 403
    assert client.post("/api/bundle/schedule", json=body, headers=_press(),
                       environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code in (401, 403)
    r = client.post("/api/bundle/schedule", json=dict(body, passphrase="password1234"), headers=_press())
    assert r.status_code == 400
    r = client.post("/api/bundle/schedule", json=body, headers=_press())
    assert r.status_code == 200, r.get_data(as_text=True)
    g = client.get("/api/bundle/schedule")
    st = g.get_json()["schedule"]
    assert st["enabled"] and st["every"] == "weekly" and st["keep"] == 3 and st["passphrase_kept"]
    assert PASS not in g.get_data(as_text=True)
    assert client.post("/api/bundle/schedule/run", json={}).status_code == 403
    assert client.post("/api/bundle/schedule/nope", json={}, headers=_press()).status_code == 404
    r = client.post("/api/bundle/schedule", json={"enabled": False}, headers=_press())
    assert r.get_json()["schedule"]["passphrase_kept"] is False


def test_the_doctor_page_has_the_schedule_and_the_meter():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="sched-on"', 'id="sched-rehearse"', "/api/bundle/schedule", "/api/bundle/strength",
                   'id="bp1-meter"', 'id="sp1-meter"'):
        assert needle in html, needle
