"""Weekly self-improvement endpoints — view the latest report and trigger a
run on demand. The scheduled Sunday job shares the same generation path."""
from __future__ import annotations


def test_latest_when_none(client):
    resp = client.get("/api/self-improvement/latest")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "report" in data          # may be null on a fresh install
    assert isinstance(data["weeks"], list)


def test_run_async_returns_started(client):
    # Default is fire-and-forget: the work surfaces as a process orb.
    resp = client.post("/api/self-improvement/run", json={"limit": 10})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"


def test_run_wait_generates_report(client):
    # wait=true blocks and returns the report inline (sync mode).
    resp = client.post("/api/self-improvement/run", json={"limit": 10, "wait": True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    rep = data["report"]
    for k in ("week_id", "generated_at", "responses_analyzed",
              "epistemic", "sycophancy", "personality", "focus_areas", "markdown"):
        assert k in rep


def test_run_then_latest_roundtrips(client):
    run = client.post("/api/self-improvement/run", json={"wait": True}).get_json()
    week_id = run["report"]["week_id"]
    latest = client.get("/api/self-improvement/latest").get_json()
    assert latest["report"] is not None
    assert latest["report"]["week_id"] == week_id
    assert week_id in latest["weeks"]
