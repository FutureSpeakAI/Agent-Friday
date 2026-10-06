"""Native career compatibility with synthetic boards and a fake evaluator."""
from datetime import date
from pathlib import Path

import pytest

from agent_friday.services import career_ops


@pytest.fixture
def career_folder(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "cv.md").write_text(
        "# Alex Example\n\nWidget Engineer at Example Co. Built Python tooling.\n",
        encoding="utf-8")
    (tmp_path / "config" / "profile.yml").write_text(
        'target_roles:\n  primary: ["Widget Engineer"]\n', encoding="utf-8")
    monkeypatch.setattr(career_ops, "configured_path", lambda: str(tmp_path))
    monkeypatch.setattr(career_ops, "fetch_json", lambda *a, **k: pytest.fail("unexpected network"))
    monkeypatch.setattr(career_ops, "_llm", lambda *a, **k: pytest.fail("unexpected model call"))
    monkeypatch.setattr(career_ops, "_gmail_search", lambda *a: {"ok": True, "messages": []})
    return tmp_path


@pytest.mark.parametrize("api,kind", [
    ("https://api.ashbyhq.com/posting-api/job-board/example", "ashby"),
    ("https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=true", "ashby"),
    ("https://api.lever.co/v0/postings/example?mode=json", "lever"),
    ("https://api.eu.lever.co/v0/postings/example", "lever"),
])
def test_explicit_public_api_wins_over_corporate_careers_page(api, kind):
    assert career_ops.board_api({"api": api, "careers_url": "https://careers.example.org"}) == (kind, api)


@pytest.mark.parametrize("api", [
    "http://api.ashbyhq.com/posting-api/job-board/example",
    "https://api.ashbyhq.com.evil.example.org/posting-api/job-board/example",
    "https://api.lever.co@evil.example.org/v0/postings/example",
    "https://api.lever.co/other-path/example",
    "https://api.lever.co/v0/postings/example/job-id",
])
def test_explicit_api_only_accepts_known_https_board_endpoints(api):
    assert career_ops.board_api({"api": api}) is None


def test_european_lever_careers_url():
    assert career_ops.board_api({"careers_url": "https://jobs.eu.lever.co/example"}) == (
        "lever", "https://api.eu.lever.co/v0/postings/example?mode=json")


@pytest.mark.parametrize("location,expected", [
    ("", True),
    ("Porto Alegre, Brazil", False),
    ("Porto or London", True),
    ("London", False),
    ("Remote, Portugal", True),
    ("Berlin", False),
    ("Portobello, Scotland", False),
])
def test_location_tiers_respect_hard_blocks_then_home_region_rescue(location, expected):
    filters = {"block_hard": ["Brazil"], "always_allow": ["Porto"],
               "block": ["London"], "allow": ["Portugal"]}
    assert career_ops.location_matches(location, filters) is expected


def test_location_without_configuration_passes():
    assert career_ops.location_matches("Anywhere", {})


def test_scan_filters_location_and_deduplicates_same_role_in_one_scan(career_folder, monkeypatch):
    monkeypatch.setattr(career_ops, "load_portals", lambda base=None: {
        "title_filter": {"positive": ["Engineer"]},
        "location_filter": {"allow": ["Portugal"], "block": ["London"],
                            "always_allow": ["Porto"], "block_hard": ["Brazil"]},
        "tracked_companies": [{"name": "Example Co", "api":
                               "https://api.lever.co/v0/postings/example?mode=json"}]})
    jobs = [
        ("Widget Engineer", "Porto or London", "1"),
        ("Widget Engineer", "Portugal", "2"),
        ("Senior Engineer", "London", "3"),
        ("Platform Engineer", "Porto Alegre, Brazil", "4"),
        ("API Engineer", "", "5"),
    ]
    before = {p: p.read_bytes() for p in career_folder.rglob("*") if p.is_file()}
    result = career_ops.scan(fetch=lambda url: [
        {"text": title, "hostedUrl": "https://jobs.example.org/" + job_id,
         "categories": {"location": location}} for title, location, job_id in jobs])
    assert [job["title"] for job in result["new"]] == ["Widget Engineer", "API Engineer"]
    assert result["skipped_location"] == 2
    assert result["skipped_duplicate"] == 1
    assert {p: p.read_bytes() for p in career_folder.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("value", ["Hired", "hired", "accepted", "contratado"])
def test_hired_is_a_canonical_fallback_state(career_folder, value):
    assert career_ops.canonical_status(value) == "Hired"


@pytest.mark.parametrize("closed_status", ["Hired", "Offer"])
def test_closed_rows_do_not_receive_inbox_suggestions(career_folder, monkeypatch, closed_status):
    (career_folder / "data" / "applications.md").write_text(
        "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        f"| 1 | 2026-01-01 | Example Co | Engineer | 4.5/5 | {closed_status} | | | |\n",
        encoding="utf-8")
    monkeypatch.setattr(career_ops, "_gmail_search", lambda query: {"ok": True, "messages": [
        {"from": "Recruiter at Example Co", "subject": "Interview availability",
         "snippet": "Please schedule a call", "when": "2026-01-02"}]})
    result = career_ops.inbox(today=date(2026, 2, 1))
    assert result["candidates"] == []
    assert result["nudges"] == []
    assert all("proposed_change" not in row for row in result["unmatched"])


@pytest.mark.parametrize("model_score,expected", [
    ("4.1/5", "4.1/5"), ("4,5/5", "4.5/5"), ("1/5", "1.0/5"),
    ("5.0/5", "5.0/5"), ("9.0/5", ""), ("0/5", ""), ("-1/5", ""),
    ("4.9/10", ""), ("4.2", ""), ("5.01/5", ""), ("4.1e10/5", ""), ("NaN", ""), ("unknown", ""),
])
def test_only_valid_fit_scores_are_promoted_to_tracker(career_folder, monkeypatch,
                                                      model_score, expected):
    monkeypatch.setattr(career_ops, "_llm", lambda *a: (
        "# Evaluation\n\n**Score:** " + model_score + "\n\n## Match\nPython tooling.\n"))
    result = career_ops.evaluate(job_description="Build Python tooling", company="Example Co",
                                 role="Engineer", url="https://jobs.example.org/42")
    assert result["score"] == expected
    assert result["tracker_suggestion"]["score"] == expected
    report = Path(result["report"]).read_text(encoding="utf-8")
    assert "**Score:** " + (expected or "Not available") in report
    if not expected:
        assert "**Score:** " + model_score + "\n" not in report


@pytest.mark.parametrize("model_report", [
    "# Evaluation\n\n**Score:** 4.1/5\n**URL:** https://invented.example.org/job\n",
    "# Evaluation\n\n**Score:** 4.1/5",
    "# Evaluation\n\n**URL:** https://invented.example.org/job\nNo score available.",
])
def test_report_source_is_attached_from_input_even_when_model_invents_a_url(
        career_folder, monkeypatch, model_report):
    monkeypatch.setattr(career_ops, "_llm", lambda *a: model_report)
    result = career_ops.evaluate(job_description="Build Python tooling", company="Example Co",
                                 role="Engineer", url="https://jobs.example.org/42")
    report = Path(result["report"]).read_text(encoding="utf-8")
    assert report.startswith("# Evaluation")
    assert report.count("**URL:**") == 1
    assert "**URL:** https://jobs.example.org/42" in report
    assert "https://invented.example.org" not in report


def test_pasted_description_does_not_gain_a_model_invented_source(career_folder, monkeypatch):
    monkeypatch.setattr(career_ops, "_llm", lambda *a: (
        "# Evaluation\n\n**Score:** 4.1/5\n**URL:** https://invented.example.org/job\n"))
    result = career_ops.evaluate(job_description="Build Python tooling", company="Example Co", role="Engineer")
    report = Path(result["report"]).read_text(encoding="utf-8")
    assert "**URL:** Not provided (pasted job description)" in report
    assert "https://invented.example.org" not in report


def test_native_evaluation_guidance_covers_evidence_and_unknown_research(career_folder, monkeypatch):
    calls = []

    def fake_llm(prompt, system):
        calls.append((prompt, system))
        return "# Evaluation\n\n**Score:** 3.6/5\n"

    monkeypatch.setattr(career_ops, "_llm", fake_llm)
    career_ops.evaluate(job_description="Build Python tooling", company="Example Co", role="Engineer")
    prompt, system = calls[0]
    for requirement in ("holistic Global Score", "Do not average", "Score Evidence",
                        "supported, partial or", "Evidence confidence is", "STAR+Reflection",
                        "Never invent metrics", "Unknown research stays", "CV-grounded"):
        assert requirement in prompt
    assert "no web access" in system
    assert "Source URLs are attached by code" in system
    assert "Alex Example" in prompt


def test_location_filters_see_secondary_regions_from_both_public_boards():
    ashby = career_ops.parse_board("ashby", {"jobs": [{
        "title": "Engineer", "jobUrl": "https://jobs.example.org/1", "location": "Canada",
        "secondaryLocations": [{"location": "Europe", "address": {"postalAddress": {
            "addressLocality": "Porto", "addressCountry": "Portugal"}}}]}]})[0]
    lever = career_ops.parse_board("lever", [{
        "text": "Engineer", "hostedUrl": "https://jobs.example.org/2",
        "categories": {"location": "Canada", "allLocations": ["Canada", "Portugal"]}}])[0]
    for job in (ashby, lever):
        assert career_ops.location_matches(job["location"], {
            "always_allow": ["Portugal"], "block": ["Canada"]})


@pytest.mark.parametrize("fields,remote", [
    ({"isRemote": True}, True),
    ({"workplaceType": "Remote", "isRemote": False}, True),
    ({"workplaceType": "Hybrid", "isRemote": True}, False),
])
def test_ashby_explicit_workplace_type_controls_remote_location(fields, remote):
    job = career_ops.parse_board("ashby", {"jobs": [{
        "title": "Engineer", "jobUrl": "https://jobs.example.org/1",
        "location": "Example City", **fields}]})[0]
    assert career_ops.location_matches(job["location"], {"allow": ["Remote"]}) is remote


@pytest.mark.parametrize("filters,expected", [
    ({"allow": "Portugal"}, True),
    ({"allow": 42}, True),
    ({"allow": [None, 42, ""]}, True),
    ({"always_allow": [None, ""], "block": "Portugal"}, False),
    ({"block_hard": 42, "allow": "Portugal"}, True),
])
def test_location_keyword_normalization_ignores_empty_and_non_string_values(filters, expected):
    assert career_ops.location_matches("Portugal", filters) is expected


def test_invalid_location_filter_configuration_reports_a_clear_error():
    with pytest.raises(career_ops.CareerError, match="location_filter must be a mapping"):
        career_ops.location_matches("Portugal", ["Portugal"])
