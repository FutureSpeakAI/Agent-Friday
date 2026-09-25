"""career-ops routes: status, the folder setting, the tracker in career-ops' format.

The folder is a fake career-ops checkout in tmp_path with a fictional
candidate. The folder setting is restored to the default after each test so
nothing leaks into other files through the shared test home's settings.json.
"""
from __future__ import annotations

import pytest

TRACKER = """# Applications Tracker

| # | Date | Company | Role | Score | Status | PDF | Report | Notes |
|---|------|---------|------|-------|--------|-----|--------|-------|
| 1 | 2026-01-05 | Example Widgets Co | Staff Widget Engineer | 4.2/5 | Applied | x |  | |
"""


@pytest.fixture
def folder(tmp_path, client):
    r = tmp_path / "career-ops"
    (r / "data").mkdir(parents=True)
    (r / "reports").mkdir()
    (r / "data" / "applications.md").write_text(TRACKER, encoding="utf-8")
    (r / "reports" / "001-example-widgets-co-2026-01-04.md").write_text("# Report\n",
                                                                         encoding="utf-8")
    resp = client.post("/api/career-ops/path", json={"path": str(r)})
    assert resp.status_code == 200, resp.get_json()
    yield r
    client.post("/api/career-ops/path", json={"path": ""})


def test_status_names_what_the_owner_must_add(client, folder):
    data = client.get("/api/career-ops/status").get_json()
    assert data["path"] == str(folder.resolve()) and data["exists"]
    assert not data["ready"]
    assert set(data["missing"]) == {"cv.md", "config/profile.yml", "portals.yml"}


def test_an_empty_path_goes_back_to_the_default(client, folder):
    data = client.post("/api/career-ops/path", json={"path": ""}).get_json()
    assert data["path"] == data["default_path"] and data["configured"] is False


def test_a_path_that_is_not_a_folder_is_refused(client, folder, tmp_path):
    resp = client.post("/api/career-ops/path", json={"path": str(tmp_path / "nope")})
    assert resp.status_code == 400
    assert client.get("/api/career-ops/status").get_json()["path"] == str(folder.resolve())


def test_the_tracker_is_read_from_the_configured_folder_by_column(client, folder, server_module):
    if (server_module.WIKI_PROFESSIONAL_DIR / "application-log.md").exists():
        pytest.skip("a wiki application log takes precedence in this home")
    data = client.get("/api/career-ops/tracker").get_json()
    assert data["status"] == "ok"
    e = data["entries"][0]
    assert (e["company"], e["role"], e["status"], e["score"]) == \
        ("Example Widgets Co", "Staff Widget Engineer", "Applied", "4.2/5")


def test_reports_come_from_the_configured_folder(client, folder):
    names = [r["name"] for r in client.get("/api/career-ops/reports").get_json()["reports"]]
    assert "001-example-widgets-co-2026-01-04.md" in names
    data = client.get("/api/career-ops/report/001-example-widgets-co-2026-01-04.md").get_json()
    assert data["status"] == "ok" and data["content"].startswith("# Report")


def test_a_report_name_cannot_leave_the_folder(client, folder):
    resp = client.get("/api/career-ops/report/..%5C..%5Cdata%5Capplications.md")
    assert resp.status_code in (400, 404)
    assert "Example Widgets Co" not in resp.get_data(as_text=True)
