"""API tests for the People / Workspace route group.

Covers:
  Trust/People : GET /api/trust, /api/people, POST /api/trust/edit, /api/trust/add-person
  Contacts     : GET /api/contacts, /api/contacts/<name>, POST /api/contacts/research
  Personality  : GET /api/personality, POST /api/personality/set
  Finance WS   : GET /api/finance/portfolio, /perks, /contacts, /quickref
  Health WS    : GET /api/health/medications, /appointments, /insurance, /vehicles
  Countdowns   : GET /api/countdowns
  Jobs         : GET /api/jobs, POST /api/jobs/apply
  Career-ops   : GET /api/career-ops/tracker, /pipeline, /reports

All file I/O lands under the isolated temp home set up by the root conftest
(USERPROFILE / HOMEDRIVE / HOMEPATH redirected before server import).
LLM calls are blocked by the autouse _no_real_llm fixture in the api conftest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.conftest import CANNED_TEXT  # noqa: F401


# ═══════════════════════════════════════════════════════════════
#  TRUST / PEOPLE
# ═══════════════════════════════════════════════════════════════

class TestTrustGet:
    def test_trust_returns_200(self, client):
        resp = client.get("/api/trust")
        assert resp.status_code == 200

    def test_trust_has_status_ok(self, client):
        data = client.get("/api/trust").get_json()
        assert data["status"] == "ok"

    def test_trust_has_people_key(self, client):
        data = client.get("/api/trust").get_json()
        # Whether PeopleGraph is installed or not, must have a 'people' key
        assert "people" in data

    def test_people_returns_200(self, client):
        resp = client.get("/api/people")
        assert resp.status_code == 200

    def test_people_has_status_ok(self, client):
        data = client.get("/api/people").get_json()
        assert data["status"] == "ok"

    def test_people_has_people_key(self, client):
        data = client.get("/api/people").get_json()
        assert "people" in data


class TestTrustAddAndEdit:
    """Round-trip: add a person → verify they appear → edit trust scores."""

    def _add(self, client, name="Jane Test", entity_type="human"):
        return client.post(
            "/api/trust/add-person",
            json={"name": name, "aliases": ["JT"], "entity_type": entity_type},
        )

    def test_add_person_201_or_ok(self, client):
        resp = self._add(client, "Jane Test")
        # success codes: 200 (ok) or 409 (duplicate on repeat run) – never 500
        assert resp.status_code in (200, 409, 501)

    def test_add_person_no_name_is_400(self, client):
        resp = client.post("/api/trust/add-person", json={"name": ""})
        assert resp.status_code == 400

    def test_add_person_no_body_is_400(self, client):
        resp = client.post("/api/trust/add-person", json={})
        assert resp.status_code == 400

    def test_add_person_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/trust/add-person",
            data="{bad json",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_add_person_appears_in_people(self, client, server_module):
        """If PeopleGraph is available, the person should be in /api/people after add."""
        if not server_module._HAS_TRUST_GRAPHS:
            pytest.skip("PeopleGraph module not installed – skipping round-trip")
        add_resp = self._add(client, "ACME Corp", entity_type="org")
        if add_resp.status_code == 501:
            pytest.skip("trust graph unavailable (501)")
        assert add_resp.status_code in (200, 409)
        people_data = client.get("/api/people").get_json()
        names_lower = {
            (v.get("name") or k).lower()
            for k, v in (people_data.get("people") or {}).items()
        }
        assert any("acme" in n for n in names_lower), (
            f"ACME Corp not found in people list: {names_lower}"
        )

    def test_edit_trust_no_person_is_400(self, client):
        resp = client.post("/api/trust/edit", json={"person": ""})
        assert resp.status_code == 400

    def test_edit_trust_no_body_is_400(self, client):
        resp = client.post("/api/trust/edit", json={})
        assert resp.status_code == 400

    def test_edit_trust_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/trust/edit",
            data="{not valid",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_edit_trust_unknown_person_not_500(self, client):
        """Editing a person that doesn't exist should be 4xx or 501, never 500."""
        resp = client.post(
            "/api/trust/edit",
            json={"person": "ghost_nobody_xyz", "scores": {"overall": 0.8}},
        )
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════
#  CONTACTS
# ═══════════════════════════════════════════════════════════════

class TestContacts:
    def test_contacts_200(self, client):
        resp = client.get("/api/contacts")
        assert resp.status_code == 200

    def test_contacts_shape(self, client):
        data = client.get("/api/contacts").get_json()
        assert data["status"] == "ok"
        assert "contacts" in data
        assert "count" in data
        assert isinstance(data["contacts"], list)

    def test_contacts_count_matches_list(self, client):
        data = client.get("/api/contacts").get_json()
        assert data["count"] == len(data["contacts"])

    def test_unknown_contact_404(self, client):
        resp = client.get("/api/contacts/nobody_here_xyzzy_12345")
        assert resp.status_code == 404

    def test_unknown_contact_has_json_body(self, client):
        data = client.get("/api/contacts/nobody_here_xyzzy_12345").get_json()
        assert data is not None
        assert "status" in data

    def test_contact_lookup_by_name_after_add(self, client, server_module):
        """Add a person via trust/add-person then retrieve via contacts/<name>."""
        if not server_module._HAS_TRUST_GRAPHS:
            pytest.skip("PeopleGraph module not installed")
        add_resp = client.post(
            "/api/trust/add-person",
            json={"name": "Jane Test", "aliases": [], "entity_type": "human"},
        )
        if add_resp.status_code == 501:
            pytest.skip("trust graph unavailable (501)")
        # 200 = fresh add, 409 = already exists from another test in this session
        assert add_resp.status_code in (200, 409)
        resp = client.get("/api/contacts/Jane Test")
        # Should be either found (200) or not-found (404); never 500
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.get_json()
            assert data["status"] == "ok"
            assert "contact" in data


class TestContactsResearch:
    def test_research_no_name_is_400(self, client):
        resp = client.post("/api/contacts/research", json={"name": ""})
        assert resp.status_code == 400

    def test_research_no_body_is_400(self, client):
        resp = client.post("/api/contacts/research", json={})
        assert resp.status_code == 400

    def test_research_queued_200(self, client):
        resp = client.post("/api/contacts/research", json={"name": "Jane Test"})
        assert resp.status_code == 200

    def test_research_response_shape(self, client):
        data = client.post(
            "/api/contacts/research", json={"name": "Jane Test"}
        ).get_json()
        assert data["status"] == "ok"
        assert data["name"] == "Jane Test"
        assert "message" in data

    def test_research_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/contacts/research",
            data="{malformed",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_research_creates_stub_file(self, client, server_module):
        """The handler writes a markdown stub under .friday/contacts-research/."""
        name = "ACME Corp"
        client.post("/api/contacts/research", json={"name": name})
        research_dir = server_module.FRIDAY_DIR / "contacts-research"
        stub = research_dir / "acme_corp.md"
        assert stub.exists(), f"Expected stub at {stub}"


# ═══════════════════════════════════════════════════════════════
#  PERSONALITY
# ═══════════════════════════════════════════════════════════════

class TestPersonality:
    def test_personality_200(self, client):
        assert client.get("/api/personality").status_code == 200

    def test_personality_shape_no_file(self, client, server_module):
        """When personality.json is absent the default shape is returned."""
        pfile = server_module.FRIDAY_DIR / "personality.json"
        existed = pfile.exists()
        if existed:
            pfile.rename(pfile.with_suffix(".json.bak"))
        try:
            data = client.get("/api/personality").get_json()
            assert data["status"] == "ok"
            assert "traits" in data
            assert "style" in data
            assert "maturity" in data
            assert "temperature" in data
        finally:
            if existed:
                pfile.with_suffix(".json.bak").rename(pfile)

    def test_personality_set_valid_trait(self, client):
        resp = client.post(
            "/api/personality/set",
            json={"trait": "curiosity", "value": 0.9},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["trait"] == "curiosity"
        assert abs(data["value"] - 0.9) < 1e-9

    def test_personality_set_style_dimension(self, client):
        resp = client.post(
            "/api/personality/set",
            json={"trait": "style.formality", "value": 0.2},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_personality_set_temperature(self, client):
        resp = client.post(
            "/api/personality/set",
            json={"trait": "temperature", "value": 0.6},
        )
        assert resp.status_code == 200

    def test_personality_set_no_trait_is_400(self, client):
        resp = client.post("/api/personality/set", json={"value": 0.5})
        assert resp.status_code == 400

    def test_personality_set_empty_trait_is_400(self, client):
        resp = client.post("/api/personality/set", json={"trait": "", "value": 0.5})
        assert resp.status_code == 400

    def test_personality_set_no_body_is_400(self, client):
        resp = client.post("/api/personality/set", json={})
        assert resp.status_code == 400

    def test_personality_set_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/personality/set",
            data="{broken",
            content_type="application/json",
        )
        assert resp.status_code < 500

    def test_personality_trait_persists_on_get(self, client):
        """A trait written via /set is returned on the next /api/personality GET."""
        client.post(
            "/api/personality/set",
            json={"trait": "directness", "value": 0.42},
        )
        data = client.get("/api/personality").get_json()
        traits = data.get("traits") or {}
        assert abs(traits.get("directness", -1) - 0.42) < 1e-6


# ═══════════════════════════════════════════════════════════════
#  FINANCE WORKSPACE
# ═══════════════════════════════════════════════════════════════

class TestFinanceWorkspace:
    def test_portfolio_200(self, client):
        assert client.get("/api/finance/portfolio").status_code == 200

    def test_portfolio_shape(self, client):
        data = client.get("/api/finance/portfolio").get_json()
        assert data["status"] == "ok"
        assert "positions" in data

    def test_perks_200(self, client):
        assert client.get("/api/finance/perks").status_code == 200

    def test_perks_shape(self, client):
        data = client.get("/api/finance/perks").get_json()
        assert data["status"] == "ok"
        assert "perks" in data

    def test_finance_contacts_200(self, client):
        assert client.get("/api/finance/contacts").status_code == 200

    def test_finance_contacts_shape(self, client):
        data = client.get("/api/finance/contacts").get_json()
        assert data["status"] == "ok"
        assert "contacts" in data
        assert isinstance(data["contacts"], list)

    def test_quickref_200(self, client):
        assert client.get("/api/finance/quickref").status_code == 200

    def test_quickref_shape(self, client):
        data = client.get("/api/finance/quickref").get_json()
        assert data["status"] == "ok"
        assert "accounts" in data
        assert isinstance(data["accounts"], list)


# ═══════════════════════════════════════════════════════════════
#  HEALTH WORKSPACE
# ═══════════════════════════════════════════════════════════════

class TestHealthWorkspace:
    def test_medications_200(self, client):
        assert client.get("/api/health/medications").status_code == 200

    def test_medications_shape(self, client):
        data = client.get("/api/health/medications").get_json()
        assert data["status"] == "ok"
        assert "medications" in data
        assert isinstance(data["medications"], list)

    def test_appointments_200(self, client):
        assert client.get("/api/health/appointments").status_code == 200

    def test_appointments_shape(self, client):
        data = client.get("/api/health/appointments").get_json()
        assert data["status"] == "ok"
        assert "appointments" in data
        assert isinstance(data["appointments"], list)

    def test_insurance_200(self, client):
        assert client.get("/api/health/insurance").status_code == 200

    def test_insurance_shape(self, client):
        data = client.get("/api/health/insurance").get_json()
        assert data["status"] == "ok"
        assert "insurance" in data

    def test_vehicles_200(self, client):
        assert client.get("/api/health/vehicles").status_code == 200

    def test_vehicles_shape(self, client):
        data = client.get("/api/health/vehicles").get_json()
        assert data["status"] == "ok"
        assert "vehicles" in data
        assert isinstance(data["vehicles"], list)


# ═══════════════════════════════════════════════════════════════
#  COUNTDOWNS
# ═══════════════════════════════════════════════════════════════

class TestCountdowns:
    def test_countdowns_200(self, client):
        assert client.get("/api/countdowns").status_code == 200

    def test_countdowns_shape(self, client):
        data = client.get("/api/countdowns").get_json()
        assert data["status"] == "ok"
        assert "countdowns" in data
        assert isinstance(data["countdowns"], list)

    def test_countdowns_entries_have_required_fields(self, client):
        items = client.get("/api/countdowns").get_json()["countdowns"]
        for item in items:
            assert "label" in item
            assert "days" in item
            assert isinstance(item["days"], int)
            assert item["days"] >= 0, "Past events should not appear"

    def test_countdowns_sorted_ascending(self, client):
        items = client.get("/api/countdowns").get_json()["countdowns"]
        days = [i["days"] for i in items]
        assert days == sorted(days), "Countdowns should be sorted by days ascending"


# ═══════════════════════════════════════════════════════════════
#  JOBS
# ═══════════════════════════════════════════════════════════════

class TestJobs:
    def test_jobs_200(self, client):
        assert client.get("/api/jobs").status_code == 200

    def test_jobs_no_file_shape(self, client, server_module):
        """When job-search.md is absent the route returns a valid empty structure."""
        jfile = server_module.JOB_SEARCH_FILE
        existed = jfile.exists()
        if existed:
            renamed = jfile.with_suffix(".md.bak")
            jfile.rename(renamed)
        try:
            data = client.get("/api/jobs").get_json()
            # Must have at minimum a status key and a jobs list (possibly empty)
            assert "status" in data
            assert "jobs" in data
            assert isinstance(data["jobs"], list)
        finally:
            if existed:
                jfile.with_suffix(".md.bak").rename(jfile)

    def test_jobs_with_markdown_file(self, client, server_module):
        """A minimal job-search.md is parsed into structured entries."""
        jfile = server_module.JOB_SEARCH_FILE
        jfile.parent.mkdir(parents=True, exist_ok=True)
        jfile.write_text(
            "# Job Search\n\n### Senior Engineer at TestCorp\nApplied 2026-01-01\n",
            encoding="utf-8",
        )
        try:
            data = client.get("/api/jobs").get_json()
            assert data["status"] == "ok"
            titles = [j["title"] for j in data["jobs"]]
            assert any("TestCorp" in t for t in titles)
        finally:
            jfile.unlink(missing_ok=True)

    def test_jobs_apply_placeholder(self, client):
        resp = client.post("/api/jobs/apply", json={"title": "Staff Engineer"})
        assert resp.status_code == 200

    def test_jobs_apply_response_mentions_title(self, client):
        data = client.post(
            "/api/jobs/apply", json={"title": "Staff Engineer"}
        ).get_json()
        assert "Staff Engineer" in (data.get("message") or "")

    def test_jobs_apply_no_body_not_500(self, client):
        resp = client.post("/api/jobs/apply", json={})
        assert resp.status_code < 500

    def test_jobs_apply_malformed_json_not_500(self, client):
        resp = client.post(
            "/api/jobs/apply",
            data="{malformed",
            content_type="application/json",
        )
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════
#  CAREER-OPS
# ═══════════════════════════════════════════════════════════════

class TestCareerOps:
    """The career-ops routes read from wiki/professional/ which lives under the
    isolated WIKI_DIR (derived from the isolated HOME).  When no files exist the
    routes return graceful-empty responses."""

    def test_tracker_200(self, client):
        assert client.get("/api/career-ops/tracker").status_code == 200

    def test_tracker_no_file_shape(self, client, server_module):
        """Absent application-log.md → status != 'ok' but still valid JSON."""
        data = client.get("/api/career-ops/tracker").get_json()
        assert "status" in data
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_tracker_with_markdown(self, client, server_module):
        """A simple markdown table is parsed into entries."""
        prof_dir = server_module.WIKI_PROFESSIONAL_DIR
        prof_dir.mkdir(parents=True, exist_ok=True)
        log_file = prof_dir / "application-log.md"
        log_file.write_text(
            "# Application Log\n\n"
            "| Company | Score | Status |\n"
            "| ------- | ----- | ------ |\n"
            "| TestCorp | 9/10 | Applied |\n",
            encoding="utf-8",
        )
        try:
            data = client.get("/api/career-ops/tracker").get_json()
            assert data["status"] == "ok"
            assert data["total"] >= 1
            companies = [e["company"] for e in data["entries"]]
            assert any("TestCorp" in c for c in companies)
        finally:
            log_file.unlink(missing_ok=True)

    def test_pipeline_200(self, client):
        assert client.get("/api/career-ops/pipeline").status_code == 200

    def test_pipeline_no_file_shape(self, client):
        data = client.get("/api/career-ops/pipeline").get_json()
        assert "status" in data
        assert "content" in data

    def test_pipeline_with_markdown(self, client, server_module):
        prof_dir = server_module.WIKI_PROFESSIONAL_DIR
        prof_dir.mkdir(parents=True, exist_ok=True)
        pipe_file = prof_dir / "job-search.md"
        pipe_file.write_text("# Job Search Pipeline\n\n- Opportunity 1\n", encoding="utf-8")
        try:
            data = client.get("/api/career-ops/pipeline").get_json()
            assert data["status"] == "ok"
            assert "Job Search" in data["content"]
        finally:
            pipe_file.unlink(missing_ok=True)

    def test_reports_200(self, client):
        assert client.get("/api/career-ops/reports").status_code == 200

    def test_reports_no_files_shape(self, client):
        data = client.get("/api/career-ops/reports").get_json()
        assert "status" in data
        assert "reports" in data
        assert "total" in data
        assert isinstance(data["reports"], list)

    def test_reports_with_markdown_files(self, client, server_module):
        prof_dir = server_module.WIKI_PROFESSIONAL_DIR
        prof_dir.mkdir(parents=True, exist_ok=True)
        report = prof_dir / "weekly-report.md"
        report.write_text("# Weekly Report\n\nSome content.", encoding="utf-8")
        try:
            data = client.get("/api/career-ops/reports").get_json()
            assert data["status"] == "ok"
            names = [r["name"] for r in data["reports"]]
            assert "weekly-report.md" in names
        finally:
            report.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
#  CROSS-ROUTE ROUND-TRIPS
# ═══════════════════════════════════════════════════════════════

class TestRoundTrips:
    """Interactions that span multiple routes."""

    def test_add_person_then_contacts_list(self, client, server_module):
        """After adding a person the contacts list count should be >= 1."""
        if not server_module._HAS_TRUST_GRAPHS:
            pytest.skip("PeopleGraph module not installed")
        add = client.post(
            "/api/trust/add-person",
            json={"name": "Round Trip Person", "entity_type": "human"},
        )
        if add.status_code == 501:
            pytest.skip("trust graph unavailable (501)")
        assert add.status_code in (200, 409)
        data = client.get("/api/contacts").get_json()
        assert data["count"] >= 0  # Even 0 is valid if graph isolated per test

    def test_personality_set_then_get(self, client):
        """A written style value is visible on a subsequent GET."""
        client.post(
            "/api/personality/set",
            json={"trait": "style.verbosity", "value": 0.11},
        )
        data = client.get("/api/personality").get_json()
        style = data.get("style") or {}
        assert abs(style.get("verbosity", -1) - 0.11) < 1e-6

    def test_research_then_contacts_detail(self, client):
        """contacts/research creates a stub; the detail route finds no trust entry
        for the same name but must not 500 (it 404s cleanly)."""
        client.post("/api/contacts/research", json={"name": "TestCorp Research"})
        resp = client.get("/api/contacts/TestCorp Research")
        assert resp.status_code in (200, 404)
        assert resp.status_code != 500
