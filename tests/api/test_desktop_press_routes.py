"""Approval routes take an approve only from a press on this PC's own screen.

Each refusal test sends what a browser on another device would send through a
forward that strips its headers and arrives from 127.0.0.1 (the Flask test
client's address): Friday's page metadata and its session token, but not this
launch's desk key, which only a browser on the PC itself can fetch. On the old
code every one of these approves, so each test fails by assertion there.
"""
from __future__ import annotations

import pytest

from agent_friday.services import approvals as ap
from tests.desk_press_support import desk_headers, desk_key, page_headers


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    yield


def _card(kind="tool_confirm", sid="s1", **kw):
    return ap.create_approval(kind=kind, subject_type="desk_test", subject_id=sid,
                              title="Send the weekly note", action_description="send an email to the team",
                              force_gate=True, **kw)


def _status(aid):
    return (ap.get_approval(aid) or {}).get("status")


# ── One card ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["web_task_plan", "web_task_plan_new_sites", "file_grant_request"])
def test_a_screen_only_card_is_not_approved_by_a_press_from_another_device(client, kind):
    card = _card(kind=kind)
    r = client.post("/api/approvals/%s/decide" % card["approval_id"],
                    json={"decision": "approve", "decided_by": "owner:ui"}, headers=page_headers())
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get("code") == "desk_proof_required"
    assert _status(card["approval_id"]) == "pending"


def test_a_send_card_is_not_approved_by_a_press_from_another_device(client):
    card = _card()
    r = client.post("/api/approvals/%s/decide" % card["approval_id"],
                    json={"decision": "approve", "decided_by": "owner:ui"}, headers=page_headers())
    assert r.status_code == 403
    assert _status(card["approval_id"]) == "pending"


def test_a_send_card_is_not_approved_by_a_request_that_is_not_a_page_at_all(client):
    """A bare request through the same forward (or a script): no browser metadata."""
    card = _card()
    r = client.post("/api/approvals/%s/decide" % card["approval_id"], json={"decision": "approve"})
    assert r.status_code == 403
    assert _status(card["approval_id"]) == "pending"


def test_a_press_on_this_desktop_approves_and_its_receipt_says_so(client):
    card = _card()
    r = client.post("/api/approvals/%s/decide" % card["approval_id"],
                    json={"decision": "approve", "decided_by": "owner:ui"}, headers=desk_headers())
    assert r.status_code == 200, r.get_json()
    rec = r.get_json()["approval"]
    assert rec["status"] == "approved"
    assert rec["decided_by"] == "owner:ui"
    assert rec.get("decided_source") == "desktop_press"
    assert ap.get_approval(card["approval_id"]).get("decided_source") == "desktop_press"


def test_a_screen_only_card_is_approved_by_a_press_on_this_desktop(client):
    card = _card(kind="web_task_plan")
    r = client.post("/api/approvals/%s/decide" % card["approval_id"],
                    json={"decision": "approve", "decided_by": "owner:ui"}, headers=desk_headers())
    assert r.status_code == 200, r.get_json()
    assert _status(card["approval_id"]) == "approved"


def test_declining_stays_open_to_any_channel_and_says_where_it_came_from(client):
    card = _card()
    r = client.post("/api/approvals/%s/decide" % card["approval_id"],
                    json={"decision": "deny"}, headers=page_headers())
    assert r.status_code == 200
    rec = r.get_json()["approval"]
    assert rec["status"] == "denied"
    assert rec.get("decided_source") == "page_without_desk_key"


def test_an_unknown_card_is_still_404(client):
    r = client.post("/api/approvals/appr_missing/decide", json={"decision": "approve"})
    assert r.status_code == 404


# ── A batch ────────────────────────────────────────────────────────────────

def _batch():
    for i in range(2):
        ap.create_approval(kind="media_diet_rule", subject_type="desk_test", subject_id="b%d" % i,
                           title="Mute a topic", action_description="mute a topic in the feed",
                           requested_by="media_diet", action_class="internal", force_gate=True)
    from agent_friday.services import approval_batches as ab
    b = ab.pending_plan()["batches"][0]
    return b["batch_id"], b["approval_ids"]


def test_a_batch_is_not_approved_by_a_press_from_another_device(client):
    bid, ids = _batch()
    r = client.post("/api/approvals/batches/%s/decide" % bid,
                    json={"decision": "approve", "approval_ids": ids, "decided_by": "owner:ui"},
                    headers=page_headers())
    assert r.status_code == 403
    assert all(_status(i) == "pending" for i in ids)


def test_a_batch_is_approved_by_a_press_on_this_desktop(client):
    bid, ids = _batch()
    r = client.post("/api/approvals/batches/%s/decide" % bid,
                    json={"decision": "approve", "approval_ids": ids, "decided_by": "owner:ui"},
                    headers=desk_headers())
    assert r.status_code == 200, r.get_json()
    assert all(_status(i) == "approved" for i in ids)


# ── The share card's own buttons ───────────────────────────────────────────

def _share_card():
    return _card(kind="local_context_share", payload={"conversation_id": "conv-desk-test"})


@pytest.mark.parametrize("action", ["send", "allow_conversation"])
def test_a_share_card_is_not_sent_by_a_press_from_another_device(client, action):
    card = _share_card()
    r = client.post("/api/local-context/%s/act" % card["approval_id"],
                    json={"action": action}, headers=page_headers())
    assert r.status_code == 403, r.get_json()
    assert _status(card["approval_id"]) == "pending"


def test_a_share_card_is_sent_by_a_press_on_this_desktop(client):
    card = _share_card()
    r = client.post("/api/local-context/%s/act" % card["approval_id"],
                    json={"action": "send"}, headers=desk_headers())
    assert r.status_code == 200, r.get_json()
    assert _status(card["approval_id"]) == "approved"
    assert ap.get_approval(card["approval_id"]).get("decided_source") == "desktop_press"


# ── Standing approvals ─────────────────────────────────────────────────────

def test_a_standing_grant_for_outward_work_needs_a_press_on_this_desktop(client, tmp_path, monkeypatch):
    from agent_friday.governance import action_gate
    made = []
    monkeypatch.setattr(action_gate, "create_grant", lambda **kw: made.append(kw) or {"grant_id": "g1", **kw})
    body = {"tools": ["send_email"], "scope": "sched_x", "expires_in_seconds": 3600, "max_uses": 1}
    r = client.post("/api/governance/grants", json=body, headers=page_headers())
    assert r.status_code == 403 and made == []
    r = client.post("/api/governance/grants", json=body, headers=desk_headers())
    assert r.status_code == 201 and len(made) == 1


def test_a_pre_approved_send_needs_a_press_on_this_desktop(client, monkeypatch):
    from agent_friday.governance import send_grants
    made = []
    monkeypatch.setattr(send_grants, "create", lambda **kw: made.append(kw) or {"grant_id": "sg1"})
    body = {"scope": "recipients", "recipients": ["someone@example.com"], "actions": ["send_email"]}
    r = client.post("/api/governance/send-grants", json=body, headers=page_headers())
    assert r.status_code == 403 and made == []
    r = client.post("/api/governance/send-grants", json=body, headers=desk_headers())
    assert r.status_code == 201 and len(made) == 1


def test_letting_voice_use_pre_approvals_needs_a_press_but_turning_it_off_does_not(client, monkeypatch):
    from agent_friday.governance import send_grants
    seen = []
    monkeypatch.setattr(send_grants, "set_voice_may_use", lambda v, by: seen.append(v) or {"voice_may_use": v})
    r = client.post("/api/governance/send-grants/options", json={"voice_may_use": True}, headers=page_headers())
    assert r.status_code == 403 and seen == []
    r = client.post("/api/governance/send-grants/options", json={"voice_may_use": False}, headers=page_headers())
    assert r.status_code == 200 and seen == [False]


# ── Where the key comes from, and that the main server never sends it ─────

def test_the_page_learns_where_the_desk_door_is_but_never_the_key(client):
    from agent_friday.services import desk_press as dp
    dp.stop()
    port = dp.start(3000)
    try:
        r = client.get("/api/desk/where", headers=page_headers())
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert r.get_json()["port"] == port
        assert desk_key() not in body
        page = client.get("/")
        assert desk_key() not in page.get_data(as_text=True)
        csp = page.headers.get("Content-Security-Policy", "")
        if page.status_code == 200 and csp:
            assert "http://127.0.0.1:%d" % port in csp
    finally:
        dp.stop()


def test_a_tab_holding_an_old_launchs_key_is_told_to_fetch_a_new_one(client):
    h = page_headers()
    h["X-Friday-Desk"] = "a-key-from-an-earlier-launch"
    r = client.get("/api/desk/where", headers=h)
    assert r.headers.get("X-Friday-Desk-Stale") == "1"
    r = client.get("/api/desk/where", headers=desk_headers())
    assert r.headers.get("X-Friday-Desk-Stale") is None
