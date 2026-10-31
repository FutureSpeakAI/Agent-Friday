"""The local model's repairs from the Doctor page: this PC only, and a press
to start one (§32.2-4)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _press():
    from agent_friday.core import _current_api_token
    from agent_friday.services import desk_press
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token(), "X-Friday-Desk": desk_press._KEY}


def test_the_state_is_given_on_this_pc_only(client):
    r = client.get("/api/doctor/local-model")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    d = r.get_json()
    assert {"brain", "offers", "safe_mode", "job"} <= set(d)
    assert client.get("/api/doctor/local-model", environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code \
        in (401, 403)


def test_a_repair_takes_the_press(client, monkeypatch):
    from agent_friday.services import model_store
    monkeypatch.setattr(model_store, "all_models", lambda: {"m": {}})
    monkeypatch.setattr(model_store, "verify", lambda mid: {"ok": True, "checked": "sha256"})
    assert client.post("/api/doctor/local-model/repair", json={"repair": "verify"}).status_code == 403
    r = client.post("/api/doctor/local-model/repair", json={"repair": "verify"}, headers=_press(),
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)
    r = client.post("/api/doctor/local-model/repair", json={"repair": "nonsense"}, headers=_press())
    assert r.status_code == 400
    r = client.post("/api/doctor/local-model/repair", json={"repair": "verify"}, headers=_press())
    assert r.status_code == 202, r.get_json()
    from agent_friday.services import local_repair
    assert local_repair._LOCK.acquire(timeout=10)      # the repair finished while the fakes are in place
    local_repair._LOCK.release()
    assert local_repair._JOB["state"] == "done"


def test_the_doctor_page_offers_the_repairs():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="localmodel"', 'id="lm-offers"', "/api/doctor/local-model/repair"):
        assert needle in html, needle
