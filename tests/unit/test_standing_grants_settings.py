"""Settings lists standing grants and can revoke them; "don't offer again" holds (K5 PR C). Synthetic cards only."""
from __future__ import annotations

import itertools
import os

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import action_ledger
from agent_friday.services import approval_novelty as an
from agent_friday.services import approvals, standing_grants as sg, standing_offers as so

PAGE = {"X-Friday-Token": "the-page-itself"}
NEXT = itertools.count()


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(an, "_DIR", tmp_path)
    monkeypatch.setattr(an, "COUNTS_FILE", tmp_path / "counts.json")
    monkeypatch.setattr(an, "LABELS_FILE", tmp_path / "labels.jsonl")
    monkeypatch.setattr(so, "STATE_FILE", tmp_path / "offers.json")
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    sg.register()
    monkeypatch.setattr(approvals, "_press_granted", lambda approval_id=None: True)


def _client(monkeypatch, principal=None):
    from flask import Flask, g as flask_g

    import agent_friday.core as core
    from agent_friday.routes import goals as route
    monkeypatch.setattr(core, "_is_local_request", lambda: True)
    monkeypatch.setattr(core, "_api_token_valid", lambda t: t == PAGE["X-Friday-Token"])
    app = Flask(__name__)
    app.secret_key = os.urandom(16)
    if principal:
        @app.before_request
        def _mark():
            flask_g.friday_principal = principal
    app.register_blueprint(route.goals_bp)
    return app.test_client()


def _approve_five(**tool_input):
    for _ in range(so.OFFER_AFTER_APPROVALS):
        rec = approvals.create_approval(
            kind="tool_confirm", subject_type="tool_action", subject_id=f"s{next(NEXT)}", title="t",
            action_description="run it", force_gate=True, requested_by="confirmation_gate",
            payload={"tool": "draft_email", "input": {"send": True, "to": "ann@example.com", **tool_input}})
        approvals.decide(rec["approval_id"], "approve")


def _offer():
    return next(c for c in approvals.list_approvals(status="pending") if c["kind"] == sg.OFFER_KIND)


def test_settings_lists_a_standing_grant_with_what_to_whom_who_when_and_uses(monkeypatch):
    _approve_five()
    approvals.decide(_offer()["approval_id"], "approve", decided_by="owner:ui")
    body = _client(monkeypatch).get("/api/governance/standing-grants", headers=PAGE).get_json()
    [row] = body["grants"]
    assert row["tool"] == "draft_email" and "ann@example.com" in row["to"]
    assert row["pressed_by"] == "owner:ui" and row["device"]
    assert row["uses_used"] == 0 and row["uses_max"] == sg.GRANT_USES and row["ends_at"] > row["made_at"]


def test_the_list_is_the_owners_on_the_page_only(monkeypatch):
    assert _client(monkeypatch).get("/api/governance/standing-grants").status_code == 403
    assert _client(monkeypatch, principal="observer").get("/api/governance/standing-grants", headers=PAGE).status_code == 403


def test_revoke_needs_no_press_and_the_grant_no_longer_covers(monkeypatch):
    _approve_five()
    approvals.decide(_offer()["approval_id"], "approve", decided_by="owner:ui")
    client = _client(monkeypatch)
    grant_id = client.get("/api/governance/standing-grants", headers=PAGE).get_json()["grants"][0]["grant_id"]
    monkeypatch.setattr(approvals, "_press_granted", lambda approval_id=None: False)
    assert client.delete("/api/governance/grants/" + grant_id).get_json() == {"revoked": True}
    assert client.get("/api/governance/standing-grants", headers=PAGE).get_json()["grants"] == []
    assert any(e["kind"] == "grant_revoke" for e in action_ledger.entries(["grant_revoke"]))


def test_a_declined_offer_is_listed_and_stop_offering_holds_for_good(monkeypatch):
    _approve_five()
    approvals.decide(_offer()["approval_id"], "deny")
    client = _client(monkeypatch)
    listing = client.get("/api/governance/standing-grants", headers=PAGE).get_json()
    [declined] = listing["declined"]
    assert declined["tool"] == "draft_email" and listing["stopped"] == []
    assert client.post("/api/governance/standing-grants/stop-offering", headers=PAGE,
                       json={"pattern": declined["pattern"]}).get_json() == {"stopped": True}
    after = client.get("/api/governance/standing-grants", headers=PAGE).get_json()
    assert after["declined"] == [] and [s["tool"] for s in after["stopped"]] == ["draft_email"]
    assert client.post("/api/governance/standing-grants/stop-offering", headers=PAGE,
                       json={"pattern": "nope"}).get_json() == {"stopped": False}
    assert client.post("/api/governance/standing-grants/stop-offering",
                       json={"pattern": declined["pattern"]}).status_code == 403
