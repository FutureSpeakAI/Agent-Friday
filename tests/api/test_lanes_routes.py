"""The routes behind the four places (routes/lanes.py): each one a read over state that already exists,
and the one write that decides a batch of approvals, which goes through the normal checkpoint."""
from __future__ import annotations

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import approvals
from agent_friday.services import notification_policy as pol


@pytest.fixture(autouse=True)
def _clean(friday_dir, tmp_path, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "owner_in_conversation", lambda now=None: False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield


def _card(i, conv="c1"):
    return approvals.create_approval(
        kind="media_diet_rule", subject_type="rule", subject_id="s%d%s" % (i, conv),
        title="Change rule %d" % i, action_description="add the rule 'limit %d'" % i,
        force_gate=True, payload={}, requested_by="svc-" + conv)


def test_the_summary_is_one_small_poll_for_the_chip_and_the_badge(client):
    for i in range(2):
        _card(i)
    ne.push(title="Briefing ready", source="briefing", kind="briefing", dedupe_key="briefing:d1")
    d = client.get("/api/lanes/summary").get_json()
    assert d["needs_you"] == 2 and d["approvals"] == 2 and d["summary"] == "2 approvals waiting"
    assert d["updates"] == 1 and set(d) >= {"running", "queued", "activity", "quiet"}


def test_needs_you_lists_the_batch_with_each_action(client):
    cards = [_card(i) for i in range(3)]
    d = client.get("/api/lanes/needs-you").get_json()
    assert len(d["batches"]) == 1 and d["batches"][0]["count"] == 3
    assert sorted(d["batches"][0]["approval_ids"]) == sorted(c["approval_id"] for c in cards)
    assert client.get("/api/approvals/batches").get_json()["batches"][0]["batch_id"] == d["batches"][0]["batch_id"]


def test_deciding_a_batch_decides_exactly_the_listed_approvals_and_a_stale_one_is_refused(client):
    cards = [_card(i) for i in range(3)]
    b = client.get("/api/approvals/batches").get_json()["batches"][0]
    stale = client.post("/api/approvals/batches/%s/decide" % b["batch_id"],
                        json={"decision": "approve", "approval_ids": b["approval_ids"][:2]})
    assert stale.status_code == 409 and stale.get_json()["error"] == "changed"
    assert {approvals.get_approval(c["approval_id"])["status"] for c in cards} == {"pending"}
    r = client.post("/api/approvals/batches/%s/decide" % b["batch_id"],
                    json={"decision": "approve", "approval_ids": b["approval_ids"], "decided_by": "owner:ui"})
    assert r.status_code == 200 and r.get_json()["decided"] == 3
    assert {approvals.get_approval(c["approval_id"])["status"] for c in cards} == {"approved"}


@pytest.mark.parametrize("body", [{}, {"decision": "maybe", "approval_ids": []}, {"decision": "approve"},
                                  {"decision": "approve", "approval_ids": "all"}])
def test_a_malformed_batch_request_is_a_400_and_decides_nothing(client, body):
    cards = [_card(i) for i in range(2)]
    b = client.get("/api/approvals/batches").get_json()["batches"][0]
    assert client.post("/api/approvals/batches/%s/decide" % b["batch_id"], json=body).status_code == 400
    assert {approvals.get_approval(c["approval_id"])["status"] for c in cards} == {"pending"}


def test_activity_updates_and_receipts_answer_with_their_shapes(client):
    a = client.get("/api/lanes/activity").get_json()
    assert set(a) >= {"tasks", "running", "queued", "count"}
    u = client.get("/api/lanes/updates?today=1").get_json()
    assert u["today_only"] is True and "groups" in u
    r = client.get("/api/lanes/receipts?limit=5")
    assert r.status_code == 200 and "traces" in r.get_json()


def test_why_answers_for_a_known_card_and_404s_for_an_unknown_one(client):
    c = ne.push(title="Briefing ready", source="briefing", kind="briefing", dedupe_key="briefing:d1")
    ok = client.get("/api/lanes/why/%s" % c["id"])
    assert ok.status_code == 200 and "never pops up" in ok.get_json()["sentence"]
    assert client.get("/api/lanes/why/nope").status_code == 404


def test_quiet_until_morning_is_set_and_cleared_through_the_settings_store(client, monkeypatch):
    from agent_friday import core
    saved = {}
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: saved.update(d))
    on = client.post("/api/lanes/quiet", json={"until_morning": True})
    assert on.status_code == 200 and saved["quiet_until_ts"] > 0
    client.post("/api/lanes/quiet", json={"until_morning": False})
    assert saved["quiet_until_ts"] == 0


def test_no_two_routes_claim_the_batch_urls(app):
    rules = [(r.rule, m) for r in app.url_map.iter_rules() for m in r.methods - {"HEAD", "OPTIONS"}
             if r.rule.startswith("/api/approvals/batches") or r.rule.startswith("/api/lanes/")]
    assert len(rules) == len(set(rules)) and ("/api/approvals/batches", "GET") in rules
