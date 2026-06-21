"""Career pipeline routes â€” the bundled job_scanner / application_engine
skills exposed over the web API (previously complete but unreachable).

Everything runs offline: scan accepts pushed raw listings (the fetcher seam),
apply defaults to dry_run, and the LLM cover-letter polish is stubbed by the
suite-wide kill-switch.
"""
from __future__ import annotations


RAW_LISTINGS = [
    {
        "title": "Senior AI Engineer",
        "company": "Initech",
        "location": "Austin, TX",
        "url": "https://example.org/jobs/123",
        "id": "ext-123",
        "remote": True,
        "description": "Build agentic AI systems with Python and LLMs.",
        "skills": ["python", "llm"],
    },
    {
        "title": "Junior Data Clerk",
        "company": "Initrode",
        "location": "Remote",
        "url": "https://example.org/jobs/456",
        "id": "ext-456",
        "description": "Spreadsheet entry.",
    },
]


def _seed_scan(client):
    resp = client.post("/api/pipeline/scan", json={"raw_listings": RAW_LISTINGS})
    assert resp.status_code == 200
    return resp.get_json()


def test_scan_with_pushed_listings_tracks_jobs(client):
    data = _seed_scan(client)
    assert data["scanned"] == 2
    assert data["new_listings"] >= 1

    listing = client.get("/api/pipeline/jobs").get_json()
    assert listing["count"] >= 1
    titles = {j["title"] for j in listing["jobs"]}
    assert "Senior AI Engineer" in titles


def test_scan_without_listings_uses_stub_fetcher(client):
    resp = client.post("/api/pipeline/scan", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["scanned"] == 0
    assert "url" in data  # LinkedIn search URL still built from config


def test_apply_dry_run_is_default_and_safe(client):
    _seed_scan(client)
    jobs = client.get("/api/pipeline/jobs").get_json()["jobs"]
    job_id = jobs[0]["job_id"]

    resp = client.post(f"/api/pipeline/jobs/{job_id}/apply", json={})
    assert resp.status_code == 200
    result = resp.get_json()
    assert result["status"] in ("dry_run", "blocked")  # never submits by default
    assert result["application_id"]
    assert result["cover_letter"]
    assert "submit_result" in result and not result["submit_result"].get("submitted")


def test_apply_unknown_job_returns_404(client):
    resp = client.post("/api/pipeline/jobs/job_doesnotexist/apply", json={})
    assert resp.status_code == 404


def test_record_response_advances_stage(client):
    _seed_scan(client)
    jobs = client.get("/api/pipeline/jobs").get_json()["jobs"]
    job_id = jobs[0]["job_id"]
    app_id = client.post(f"/api/pipeline/jobs/{job_id}/apply",
                         json={}).get_json()["application_id"]

    resp = client.post(f"/api/pipeline/applications/{app_id}/response",
                       json={"response_kind": "interview"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["updated"] is True
    assert data["stage"] == "interview"
    assert data["reward"] == 0.7


def test_record_response_unknown_application(client):
    resp = client.post("/api/pipeline/applications/app_nope/response",
                       json={"response_kind": "offer"})
    assert resp.status_code == 404
