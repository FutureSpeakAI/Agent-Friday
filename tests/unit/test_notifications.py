"""Unit tests for notifications.py — pure template builders.

Every function under test is stateless; no server, no filesystem, no DB.
Uses synthetic data only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.notifications as notif
from agent_friday.notifications import (
    ICON_DIGEST,
    ICON_INFO,
    ICON_INTERVIEW,
    ICON_OK,
    ICON_PRIORITY,
    ICON_REPORT,
    ICON_SKILL,
    _job_one_liner,
    _location,
    _salary_band,
    daily_digest,
    interview_detected,
    priority_job_alert,
    skill_improvement,
    skill_regression,
    to_os_toast,
    weekly_report,
)

# ── Shared synthetic data ─────────────────────────────────────────────────────

SAMPLE_JOB = {
    "job_id": "job-001",
    "title": "Senior Software Engineer",
    "company": "Acme Corp",
    "location": "Austin, TX",
    "remote": False,
    "hybrid": False,
    "salary_min": 150_000,
    "salary_max": 200_000,
    "relevance_score": 0.92,
    "score_breakdown": {"skills": 0.40, "experience": 0.30, "location": 0.22},
    "keywords_matched": ["Python", "FastAPI", "React"],
    "source_url": "https://jobs.example.com/1",
}

REQUIRED_KEYS = {"channel", "icon", "priority", "title", "body", "summary", "actions", "meta"}


# ── _salary_band ──────────────────────────────────────────────────────────────

class TestSalaryBand:
    def test_both_present(self):
        j = {"salary_min": 150_000, "salary_max": 200_000}
        result = _salary_band(j)
        assert "$150K" in result
        assert "$200K" in result
        assert "–" in result
        # The spec says:  ·  $150K–$200K
        assert result == "  ·  $150K–$200K"

    def test_empty_dict(self):
        assert _salary_band({}) == ""

    def test_missing_both_nones(self):
        assert _salary_band({"salary_min": None, "salary_max": None}) == ""

    def test_only_min(self):
        result = _salary_band({"salary_min": 100_000, "salary_max": None})
        assert "$100K" in result
        assert "?" in result

    def test_only_max(self):
        result = _salary_band({"salary_min": None, "salary_max": 180_000})
        assert "$180K" in result
        assert "?" in result


# ── _location ─────────────────────────────────────────────────────────────────

class TestLocation:
    def test_remote(self):
        assert _location({"remote": True}) == "Remote"

    def test_hybrid_with_location(self):
        result = _location({"hybrid": True, "location": "Chicago, IL"})
        assert "Chicago, IL" in result
        assert "hybrid" in result

    def test_hybrid_no_location(self):
        result = _location({"hybrid": True})
        assert "hybrid" in result

    def test_onsite_with_location(self):
        assert _location({"location": "New York, NY"}) == "New York, NY"

    def test_no_location_returns_dash(self):
        assert _location({}) == "—"


# ── _job_one_liner ────────────────────────────────────────────────────────────

class TestJobOneLiner:
    def test_basic(self):
        result = _job_one_liner(SAMPLE_JOB)
        assert "Senior Software Engineer" in result
        assert "Acme Corp" in result
        assert "Austin, TX" in result
        assert "$150K" in result
        assert "$200K" in result

    def test_missing_title_and_company(self):
        result = _job_one_liner({})
        assert "Untitled role" in result
        assert "Unknown company" in result

    def test_remote_job(self):
        j = {"title": "Dev", "company": "Remote Co", "remote": True}
        result = _job_one_liner(j)
        assert "Remote" in result


# ── priority_job_alert ────────────────────────────────────────────────────────

class TestPriorityJobAlert:
    def test_returns_required_keys(self):
        payload = priority_job_alert(SAMPLE_JOB)
        assert REQUIRED_KEYS.issubset(payload.keys())

    def test_priority_is_high(self):
        assert priority_job_alert(SAMPLE_JOB)["priority"] == "high"

    def test_icon_is_red(self):
        assert priority_job_alert(SAMPLE_JOB)["icon"] == ICON_PRIORITY  # 🔴

    def test_title_contains_percentage(self):
        payload = priority_job_alert(SAMPLE_JOB)
        # 0.92 → 92%
        assert "92%" in payload["title"]

    def test_body_contains_company(self):
        payload = priority_job_alert(SAMPLE_JOB)
        assert "Acme Corp" in payload["body"]

    def test_body_contains_keywords(self):
        payload = priority_job_alert(SAMPLE_JOB)
        assert "Python" in payload["body"]

    def test_meta_kind(self):
        assert priority_job_alert(SAMPLE_JOB)["meta"]["kind"] == "priority_job"

    def test_actions_present(self):
        actions = priority_job_alert(SAMPLE_JOB)["actions"]
        labels = [a["label"] for a in actions if a is not None]
        assert "Apply" in labels
        assert "Snooze" in labels

    def test_minimal_job_no_crash(self):
        payload = priority_job_alert({"title": "Dev", "company": "Co"})
        assert payload["priority"] == "high"

    def test_channel_is_chat(self):
        assert priority_job_alert(SAMPLE_JOB)["channel"] == "chat"


# ── daily_digest ──────────────────────────────────────────────────────────────

class TestDailyDigest:
    def _make(self, jobs=None, apps=None, responses=None):
        return daily_digest(
            jobs_today=jobs or [],
            applications_today=apps or [],
            responses_today=responses or [],
        )

    def test_returns_required_keys(self):
        assert REQUIRED_KEYS.issubset(self._make().keys())

    def test_priority_is_normal(self):
        assert self._make()["priority"] == "normal"

    def test_icon_is_yellow(self):
        assert self._make()["icon"] == ICON_DIGEST  # 🟡

    def test_counts_in_title(self):
        payload = self._make(
            jobs=[SAMPLE_JOB, SAMPLE_JOB],
            apps=[{"company": "X"}],
        )
        assert "2 jobs" in payload["title"]
        assert "1 applied" in payload["title"]

    def test_quiet_day_message(self):
        body = self._make()["body"]
        assert "Quiet day" in body or "quiet day" in body.lower()

    def test_priority_jobs_section(self):
        high_job = dict(SAMPLE_JOB, relevance_score=0.90)
        body = self._make(jobs=[high_job])["body"]
        assert "priority" in body.lower() or "Top priority" in body

    def test_responses_section(self):
        responses = [{"response_kind": "interview", "company": "MegaCorp", "title": "PM"}]
        body = self._make(responses=responses)["body"]
        assert "MegaCorp" in body

    def test_meta_kind(self):
        assert self._make()["meta"]["kind"] == "daily_digest"

    def test_summary_has_counts(self):
        payload = self._make(jobs=[SAMPLE_JOB], apps=[{}], responses=[{}])
        summary = payload["summary"]
        assert "1" in summary  # at least one count appears


# ── weekly_report ─────────────────────────────────────────────────────────────

class TestWeeklyReport:
    _pipeline = {
        "_total_jobs_discovered": 120,
        "_priority_jobs_open": 5,
        "applied": 12,
        "screening": 3,
        "interview": 2,
        "offer": 1,
    }
    _response_rate = {
        "window_days": 30,
        "response_rate": 0.25,
        "interview_rate": 0.10,
        "offer_rate": 0.05,
        "ghost_rate": 0.70,
    }

    def _make(self, **kwargs):
        defaults = dict(
            pipeline_summary=self._pipeline,
            response_rate=self._response_rate,
            top_jobs=[SAMPLE_JOB],
            applications_this_week=12,
            interviews_this_week=2,
        )
        defaults.update(kwargs)
        return weekly_report(**defaults)

    def test_required_keys(self):
        assert REQUIRED_KEYS.issubset(self._make().keys())

    def test_icon_is_report(self):
        assert self._make()["icon"] == ICON_REPORT  # 📊

    def test_priority_is_normal(self):
        assert self._make()["priority"] == "normal"

    def test_title_contains_app_count(self):
        assert "12" in self._make()["title"]

    def test_body_has_pipeline_numbers(self):
        body = self._make()["body"]
        assert "120" in body  # total discovered
        assert "12" in body   # applied

    def test_body_has_response_rates(self):
        body = self._make()["body"]
        assert "25%" in body

    def test_top_jobs_section(self):
        body = self._make()["body"]
        assert "Acme Corp" in body

    def test_meta_kind(self):
        assert self._make()["meta"]["kind"] == "weekly_report"


# ── interview_detected ────────────────────────────────────────────────────────

class TestInterviewDetected:
    def _make(self, **kwargs):
        defaults = dict(company="Skynet Inc", title="ML Engineer", when="Monday 2pm")
        defaults.update(kwargs)
        return interview_detected(**defaults)

    def test_required_keys(self):
        assert REQUIRED_KEYS.issubset(self._make().keys())

    def test_priority_is_high(self):
        assert self._make()["priority"] == "high"

    def test_icon_is_phone(self):
        assert self._make()["icon"] == ICON_INTERVIEW  # 📞

    def test_company_in_title(self):
        assert "Skynet Inc" in self._make()["title"]

    def test_when_in_body(self):
        assert "Monday 2pm" in self._make()["body"]

    def test_no_when_no_crash(self):
        p = interview_detected(company="ACME", title="Dev")
        assert "ACME" in p["title"]

    def test_prep_link_adds_action(self):
        p = self._make(prep_link="https://docs.example.com/prep")
        labels = [a["label"] for a in p["actions"]]
        assert "Open prep doc" in labels

    def test_no_prep_link_no_extra_action(self):
        p = self._make()
        labels = [a["label"] for a in p["actions"]]
        assert "Open prep doc" not in labels

    def test_meta_kind(self):
        assert self._make()["meta"]["kind"] == "interview_detected"

    def test_meta_company_and_title(self):
        m = self._make()["meta"]
        assert m["company"] == "Skynet Inc"
        assert m["title"] == "ML Engineer"


# ── skill_improvement ─────────────────────────────────────────────────────────

class TestSkillImprovement:
    def _make(self, **kwargs):
        defaults = dict(
            skill_name="Python",
            new_version="v3",
            old_version="v2",
            old_score=0.500,
            new_score=0.600,
        )
        defaults.update(kwargs)
        return skill_improvement(**defaults)

    def test_required_keys(self):
        assert REQUIRED_KEYS.issubset(self._make().keys())

    def test_priority_is_normal(self):
        assert self._make()["priority"] == "normal"

    def test_icon_is_brain(self):
        assert self._make()["icon"] == ICON_SKILL  # 🧠

    def test_title_contains_skill_name(self):
        assert "Python" in self._make()["title"]

    def test_title_contains_positive_delta(self):
        # old=0.5 new=0.6 → +20.0%
        title = self._make()["title"]
        assert "+20.0%" in title

    def test_body_has_version_transition(self):
        body = self._make()["body"]
        assert "v2" in body
        assert "v3" in body

    def test_diff_preview_appended(self):
        body = self._make(diff_preview="+ new line\n- old line")["body"]
        assert "```diff" in body
        assert "+ new line" in body

    def test_no_diff_preview_no_block(self):
        body = self._make()["body"]
        assert "```diff" not in body

    def test_meta_kind(self):
        assert self._make()["meta"]["kind"] == "skill_improvement"

    def test_meta_scores(self):
        m = self._make()["meta"]
        assert m["old_score"] == pytest.approx(0.500)
        assert m["new_score"] == pytest.approx(0.600)

    def test_actions_present(self):
        actions = self._make()["actions"]
        assert any("Observatory" in a["label"] for a in actions)
        assert any("diff" in a["label"].lower() for a in actions)

    def test_percent_calculation_exact(self):
        # old=1.0 new=1.2  → +20.0%
        title = skill_improvement(
            skill_name="X", new_version="b", old_version="a",
            old_score=1.0, new_score=1.2,
        )["title"]
        assert "+20.0%" in title


# ── skill_regression ─────────────────────────────────────────────────────────

class TestSkillRegression:
    def _make(self, **kwargs):
        defaults = dict(
            skill_name="Go",
            candidate_version="c5",
            candidate_score=0.700,
            best_score=0.800,
        )
        defaults.update(kwargs)
        return skill_regression(**defaults)

    def test_required_keys(self):
        assert REQUIRED_KEYS.issubset(self._make().keys())

    def test_priority_is_low(self):
        assert self._make()["priority"] == "low"

    def test_icon_is_info(self):
        assert self._make()["icon"] == ICON_INFO  # 💡

    def test_title_contains_skill(self):
        assert "Go" in self._make()["title"]

    def test_negative_delta_in_body(self):
        body = self._make()["body"]
        # candidate 0.7 vs best 0.8 → -12.5%
        assert "-12.5%" in body

    def test_meta_kind(self):
        assert self._make()["meta"]["kind"] == "skill_regression"

    def test_reason_in_body(self):
        body = self._make(reason="performance dropped on eval set")["body"]
        assert "performance dropped on eval set" in body


# ── to_os_toast ───────────────────────────────────────────────────────────────

class TestToOsToast:
    def test_flat_dict_with_four_keys(self):
        payload = priority_job_alert(SAMPLE_JOB)
        toast = to_os_toast(payload)
        assert set(toast.keys()) == {"title", "body", "icon", "priority"}

    def test_title_matches_payload_title(self):
        payload = priority_job_alert(SAMPLE_JOB)
        toast = to_os_toast(payload)
        assert toast["title"] == payload["title"]

    def test_body_is_summary(self):
        payload = priority_job_alert(SAMPLE_JOB)
        toast = to_os_toast(payload)
        assert toast["body"] == payload["summary"]

    def test_icon_matches(self):
        payload = priority_job_alert(SAMPLE_JOB)
        toast = to_os_toast(payload)
        assert toast["icon"] == ICON_PRIORITY

    def test_priority_matches(self):
        payload = priority_job_alert(SAMPLE_JOB)
        toast = to_os_toast(payload)
        assert toast["priority"] == "high"

    def test_works_with_interview_payload(self):
        payload = interview_detected(company="Corp", title="Dev")
        toast = to_os_toast(payload)
        assert set(toast.keys()) == {"title", "body", "icon", "priority"}
        assert toast["priority"] == "high"

    def test_falls_back_to_info_icon_for_unknown(self):
        toast = to_os_toast({})
        assert toast["icon"] == ICON_INFO

    def test_falls_back_to_normal_priority_for_unknown(self):
        toast = to_os_toast({})
        assert toast["priority"] == "normal"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
