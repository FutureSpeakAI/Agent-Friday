"""The Career workspace reads its job lists from the job store: recent shows what is open and recently seen,
the archive shows what has gone with the date it closed, and a scan records every sighting in the store.
Synthetic listings only."""
from datetime import datetime, timedelta

import pytest

from agent_friday.seed.data import job_tracker_schema as schema
from agent_friday.services import job_store as js
from tests.api.test_jobs_routes import RAW_LISTINGS


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(schema, "DEFAULT_TRACKER_PATH", tmp_path / "job_tracker.json")
    monkeypatch.setattr(js, "_tracker_rows", lambda: [])
    return tmp_path


def seen(title, when, **kw):
    base = {"title": title, "company": "Acme Robotics", "location": "Remote", "source": "feed-a",
            "url": "https://jobs.example.test/" + title.lower().replace(" ", "-")}
    base.update(kw)
    return js.upsert(base, when)


def get(client, view="recent"):
    return client.get("/api/career/jobs?view=%s" % view)


def test_an_open_recently_seen_job_is_in_recent_and_not_in_the_archive(client):
    seen("Staff Engineer", datetime.now() - timedelta(days=1))
    recent, archive = get(client).get_json(), get(client, "archive").get_json()
    assert [j["title"] for j in recent["jobs"]] == ["Staff Engineer"] and recent["count"] == 1
    assert archive["jobs"] == [] and recent["stale_days"] == js.STALE_DAYS


def test_two_duplicates_of_a_closed_job_show_zero_times_in_recent_and_once_in_the_archive(client):
    closed = datetime.now() - timedelta(days=120)
    seen("Closed Back In July", datetime.now() - timedelta(days=150), closes_at=closed.isoformat())
    seen("Closed Back In July", datetime.now() - timedelta(days=149), closes_at=closed.isoformat(),
         source="feed-b", url="https://elsewhere.example.test/dup")
    assert get(client).get_json()["jobs"] == []
    rows = get(client, "archive").get_json()["jobs"]
    assert len(rows) == 1 and rows[0]["status"] == "closed" and rows[0]["closed_at"] == closed.isoformat()


def test_a_job_nobody_has_seen_for_the_stale_window_is_archived_with_the_day_it_went_stale(client):
    last = datetime.now() - timedelta(days=js.STALE_DAYS + 60)
    seen("Quietly Gone", last)
    assert get(client).get_json()["jobs"] == []
    row = get(client, "archive").get_json()["jobs"][0]
    assert row["status"] == "expired" and row["closed_at"] == (last + timedelta(days=js.STALE_DAYS)).isoformat()


def test_the_old_trackers_listings_are_brought_in_once_and_the_dead_ones_are_archived(client, monkeypatch):
    rows = [{"title": "Closed Back In July", "company": "Acme Robotics", "location": "Remote", "source": "feed-a",
             "external_id": "A1", "source_url": "https://jobs.example.test/a1",
             "discovered_at": (datetime.now() - timedelta(days=140)).isoformat(), "relevance_score": 0.95}]
    monkeypatch.setattr(js, "_tracker_rows", lambda: rows)
    assert get(client).get_json()["jobs"] == []
    assert [j["title"] for j in get(client, "archive").get_json()["jobs"]] == ["Closed Back In July"]
    assert len(get(client, "archive").get_json()["jobs"]) == 1                                  # not imported twice


def test_a_scan_records_every_sighting_so_the_panel_sees_it(client):
    assert client.post("/api/pipeline/scan", json={"raw_listings": RAW_LISTINGS}).status_code == 200
    titles = [j["title"] for j in get(client).get_json()["jobs"]]
    assert "Senior AI Engineer" in titles and "Junior Data Clerk" in titles
    assert all(j["job_id"] for j in get(client).get_json()["jobs"])             # so Prepare keeps working
    assert {j["title"]: j["remote"] for j in get(client).get_json()["jobs"]}["Senior AI Engineer"] is True


def test_after_a_rescan_every_recent_job_id_is_one_the_tracker_holds_so_prepare_finds_it(client):
    client.post("/api/pipeline/scan", json={"raw_listings": RAW_LISTINGS})
    first = {j["key"]: j["job_id"] for j in get(client).get_json()["jobs"]}
    client.post("/api/pipeline/scan", json={"raw_listings": RAW_LISTINGS})
    rows = get(client).get_json()["jobs"]
    assert {j["key"]: j["job_id"] for j in rows} == first
    tracker = schema.JobTracker().load()
    assert all(tracker.get_job(j["job_id"]) for j in rows)
    prepared = client.post("/api/pipeline/jobs/%s/apply" % rows[0]["job_id"], json={})
    assert prepared.status_code == 200


def test_a_prepared_application_keeps_the_job_in_recent_as_applied(client):
    client.post("/api/pipeline/scan", json={"raw_listings": RAW_LISTINGS})
    row = next(j for j in get(client).get_json()["jobs"] if j["title"] == "Senior AI Engineer")
    assert row["status"] == "open"
    assert client.post("/api/pipeline/jobs/%s/apply" % row["job_id"], json={}).status_code == 200
    statuses = {j["title"]: j["status"] for j in get(client).get_json()["jobs"]}
    assert statuses["Senior AI Engineer"] == "applied" and statuses["Junior Data Clerk"] == "open"


def test_a_second_scan_of_the_same_listings_adds_no_second_record_and_refreshes_the_sighting(client):
    client.post("/api/pipeline/scan", json={"raw_listings": RAW_LISTINGS})
    first = {j["key"]: j["last_seen"] for j in get(client).get_json()["jobs"]}
    client.post("/api/pipeline/scan", json={"raw_listings": RAW_LISTINGS})
    second = {j["key"]: j["last_seen"] for j in get(client).get_json()["jobs"]}
    assert set(first) == set(second) and all(second[k] >= first[k] for k in first)


def test_an_unknown_view_is_a_400_and_a_damaged_store_is_a_503_never_an_empty_list(client, _home):
    assert get(client, "everything").status_code == 400
    seen("Staff Engineer", datetime.now())
    (_home / "job_store.json").write_text("{not json", encoding="utf-8")
    r = get(client)
    assert r.status_code == 503 and r.get_json()["code"] == "store_unreadable"
    assert (_home / "job_store.json").read_text(encoding="utf-8") == "{not json"
