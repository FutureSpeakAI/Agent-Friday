"""The safety metrics on the Doctor page (§35.7-1): this PC only."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_metrics_are_given_on_this_pc_only(client):
    r = client.get("/api/doctor/safety-metrics")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    d = r.get_json()
    assert d["window_days"] == 30 and {m["id"] for m in d["metrics"]} >= {"gate_bypasses", "self_tests"}
    assert client.get("/api/doctor/safety-metrics", environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code \
        in (401, 403)


def test_the_doctor_page_shows_them():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="safetymetrics"', 'id="sm-out"', "/api/doctor/safety-metrics"):
        assert needle in html, needle
