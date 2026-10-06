"""Career reads the same files its native tools update, with a legacy fallback."""
from agent_friday.routes import insights


def test_career_records_win_over_stale_wiki(client, tmp_path, monkeypatch):
    from agent_friday.services import career_ops

    career = tmp_path / "career"
    wiki = tmp_path / "wiki"
    (career / "data").mkdir(parents=True)
    (career / "reports").mkdir()
    wiki.mkdir()
    monkeypatch.setattr(career_ops, "configured_path", lambda: str(career))
    monkeypatch.setattr(insights, "WIKI_PROFESSIONAL_DIR", wiki)
    (career / "data" / "applications.md").write_text(
        "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| 1 | 2026-01-01 | Current Widgets | Engineer | 4.1/5 | Interview | | | |\n",
        encoding="utf-8",
    )
    (wiki / "application-log.md").write_text(
        "| Legacy Widgets | 3.0 | Applied |\n", encoding="utf-8"
    )
    (career / "data" / "pipeline.md").write_text("Current shortlist", encoding="utf-8")
    (wiki / "job-search.md").write_text("Old shortlist", encoding="utf-8")
    (career / "reports" / "001-example.md").write_text("Current report", encoding="utf-8")
    (wiki / "001-example.md").write_text("Old report", encoding="utf-8")

    tracker = client.get("/api/career-ops/tracker").get_json()
    assert tracker["entries"][0]["company"] == "Current Widgets"
    assert client.get("/api/career-ops/pipeline").get_json()["content"] == "Current shortlist"
    reports = client.get("/api/career-ops/reports").get_json()["reports"]
    same_name = [report for report in reports if report["name"] == "001-example.md"]
    assert len(same_name) == 1 and same_name[0]["source"] == "career-ops"
    assert client.get("/api/career-ops/report/001-example.md").get_json()["content"] == "Current report"


def test_legacy_career_files_remain_available_without_native_files(client, tmp_path, monkeypatch):
    from agent_friday.services import career_ops

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setattr(career_ops, "configured_path", lambda: str(tmp_path / "absent"))
    monkeypatch.setattr(insights, "WIKI_PROFESSIONAL_DIR", wiki)
    (wiki / "application-log.md").write_text("| Legacy Widgets | 3.0 | Applied |\n", encoding="utf-8")
    (wiki / "job-search.md").write_text("Legacy shortlist", encoding="utf-8")
    (wiki / "legacy-report.md").write_text("Legacy report", encoding="utf-8")
    assert client.get("/api/career-ops/tracker").get_json()["entries"][0]["company"] == "Legacy Widgets"
    assert client.get("/api/career-ops/pipeline").get_json()["content"] == "Legacy shortlist"
    assert client.get("/api/career-ops/report/legacy-report.md").get_json()["content"] == "Legacy report"
