"""The owner approves the web domains a skill declares, once, in Settings (item 6).

GET /api/governance/send-grants lists each skill that declares `browse_web` domains with that exact list and whether
it is approved; POST with `browse_domains` approves exactly the list the page showed (a changed list approves nothing
and comes back with the new one); a browse approval is not a send approval, so the skill's send needs stay
unapproved. The page row says which sites the skill will read and offers one press. Synthetic domains only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.governance import action_gate

REPO = Path(__file__).resolve().parents[2]
NAME = "weekend-events-synthetic"
SITE = "events-example.test"
TEAM = "team@example.test"


@pytest.fixture
def client(app):
    """Friday's own page on this PC: the page token and a press's desk headers (services/desk_press)."""
    from tests.desk_press_support import desk_headers
    c = app.test_client()
    for k, v in desk_headers().items():
        c.environ_base["HTTP_" + k.upper().replace("-", "_")] = v
    return c


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    yield


def _write_skill(perms):
    from agent_friday import skill_registry
    return skill_registry.save_skill(NAME, description="Weekend events", body="Search %s for this weekend." % SITE,
                                     permissions=perms)


def _row(client):
    listed = client.get("/api/governance/send-grants").get_json()
    return next(s for s in listed["skills"] if s["name"] == NAME)


def test_settings_lists_the_sites_a_skill_will_browse_and_one_press_approves_exactly_them(client):
    _write_skill({"browse_web": [SITE]})
    row = _row(client)
    assert row["browse"] == [SITE] and row["browse_granted"] == []
    r = client.post("/api/governance/send-grants", json={"scope": "skill", "skill": NAME, "browse_domains": [SITE]})
    assert r.status_code == 201, r.get_data(as_text=True)
    g = r.get_json()["grant"]
    assert g["browse_domains"] == [SITE] and g["recipients"] == [] and g["actions"] == []
    assert _row(client)["browse_granted"] == [g["grant_id"]]


def test_a_list_other_than_the_one_declared_approves_nothing_and_shows_the_new_list(client):
    _write_skill({"browse_web": [SITE]})
    r = client.post("/api/governance/send-grants",
                    json={"scope": "skill", "skill": NAME, "browse_domains": [SITE, "extra-example.test"]})
    assert r.status_code == 409, r.get_data(as_text=True)
    assert r.get_json()["needs"] == {"browse_web": [SITE]}
    assert _row(client)["browse_granted"] == []


def test_approving_the_sites_does_not_approve_the_skills_sends(client):
    _write_skill({"browse_web": [SITE], "send_email": [TEAM]})
    r = client.post("/api/governance/send-grants", json={"scope": "skill", "skill": NAME, "browse_domains": [SITE]})
    assert r.status_code == 201
    row = _row(client)
    assert row["browse_granted"] and row["granted"] == [], "a browse approval was shown as approving the skill's sends"


def test_the_settings_row_names_the_sites_and_offers_one_press_in_both_page_files():
    for f in ("index.html", "ui_parts/app.html"):
        text = (REPO / f).read_text(encoding="utf-8")
        assert "will browse: " in text and "Approve these sites" in text, f
        assert "browse_domains: sk.browse" in text, f
