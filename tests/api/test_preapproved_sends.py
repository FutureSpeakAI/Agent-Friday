"""Pre-approved sends: the owner's standing yes for named recipients, made in
Settings, checked in the governance checkpoint, logged, revocable.

Sends ask by default (every email is a card). What this file holds, through the
real tool path (`agent._execute_tool` -> hooks -> governance/action_gate ->
the draft_email handler -> gmail_send) and the owner's Settings routes:

* a send whose every recipient a live grant names goes WITHOUT a card, is
  queued for the undo window, and every use is logged (who, what, when);
* a send to anyone a grant does not name still asks (a card), and so does a
  lookalike of a pre-approved @domain;
* revoking a grant makes the same send ask again, including a grant revoked
  between the checkpoint and the handler;
* a job grant covers that scheduled job only; a tainted send never uses one;
* a skill's declared `permissions` grant nothing until the owner approves them
  (in one press), and then only while that skill is in use;
* without a grant, a scheduled send waits on its card and its result says so.
Synthetic addresses only (example.test).
"""
from __future__ import annotations

import json
import shutil
import uuid
import subprocess
from pathlib import Path

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approvals, gmail_send

REPO = Path(__file__).resolve().parents[2]
TEAM = "team@example.test"
SCHEDULED = {"is_background_task": True, "schedule_id": "sch_weekly_status", "task_id": "task_weekly_1"}


@pytest.fixture
def client(app):
    """The test client as Friday's own page: it carries the page's X-Friday-Token,
    which the send-grant routes demand even from loopback."""
    import agent_friday.core as core
    c = app.test_client()
    c.environ_base["HTTP_X_FRIDAY_TOKEN"] = core._current_api_token()
    return c


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    monkeypatch.setattr(gmail_send, "sendable_accounts",
                        lambda: [{"id": "acct_owner", "email": "owner@example.test", "label": "Owner"}])
    held = friday_dir / "mail_outbox" / "held.json"
    if held.exists():
        held.unlink()
    # A job grant names a scheduled job that exists, and is bound to what it runs.
    from agent_friday.services import scheduler
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    for sid in ('sch_weekly_status', 'sch_other'):
        scheduler.register_schedule({"id": sid, "name": "Synthetic " + sid, "trigger": "weekly",
                                     "spec": {"hour": 7, "minute": 0, "weekdays": [0]},
                                     "task": {"kind": "agent_prompt", "prompt": "Write the weekly status."}})
    from agent_friday.services import taint
    from agent_friday.governance import outside_content
    taint.reset()
    outside_content.reset_cache()
    yield
    taint.reset()
    outside_content.reset_cache()


def _send(to, *, ctx=None, subject=None, body="Synthetic weekly status."):
    from agent_friday.services import agent
    # A fresh subject: an identical message reuses its live card (gmail_send._live_card).
    subject = subject or "Weekly status " + uuid.uuid4().hex[:6]
    out = agent._execute_tool("draft_email", {"to": to, "subject": subject, "body": body},
                              session_ctx=dict(ctx or SCHEDULED))
    return json.loads(out)


def _pending_for(approval_id):
    return [a for a in approvals.list_approvals(status="pending") if a.get("approval_id") == approval_id]


def _grant(client, **body):
    r = client.post("/api/governance/send-grants", json=body)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["grant"]


def test_a_covered_send_goes_without_a_card_and_every_use_is_logged(client):
    g = _grant(client, scope="recipients", recipients=[TEAM])
    res = _send(TEAM)
    assert res.get("pre_approved") == g["grant_id"], res
    appr = approvals.get_approval(res["approval_id"])
    assert appr["status"] == "approved" and appr["decided_by"] == "grant:" + g["grant_id"]
    assert _pending_for(res["approval_id"]) == [], "a card was raised for a pre-approved send"
    assert res["approval_id"] in [h["approval_id"] for h in gmail_send.held()], "it was not queued to go"
    listed = client.get("/api/governance/send-grants").get_json()
    used = [l for l in listed["log"] if l["event"] == "used"]
    assert used and used[0]["grant_id"] == g["grant_id"] and used[0]["recipients"] == [TEAM]
    assert used[0]["approval_id"] == res["approval_id"] and used[0]["at"] > 0
    assert used[0]["schedule_id"] == "sch_weekly_status"
    assert listed["grants"][0]["uses"] == 1
    # The checkpoint's signed receipt names the grant behind the call.
    bom = [json.loads(x) for x in action_gate.receipts_path().read_text(encoding="utf-8").splitlines()]
    assert any(r.get("tool") == "draft_email" and r.get("grant") == g["grant_id"] for r in bom)


def test_a_send_a_grant_does_not_fully_cover_still_asks(client):
    _grant(client, scope="recipients", recipients=[TEAM, "@partner.example.test"])
    for to in ("stranger@example.test", TEAM + ", stranger@example.test",
               "someone@notpartner.example.test"):
        res = _send(to)
        assert not res.get("pre_approved"), to
        assert _pending_for(res["approval_id"]), "no card for an uncovered send: %s" % to
    assert _send("anyone@partner.example.test").get("pre_approved")


def test_revoking_a_grant_makes_the_send_ask_again(client):
    g = _grant(client, scope="recipients", recipients=[TEAM])
    assert client.delete("/api/governance/send-grants/" + g["grant_id"]).get_json() == {"revoked": True}
    res = _send(TEAM)
    assert not res.get("pre_approved") and _pending_for(res["approval_id"])
    events = [l["event"] for l in client.get("/api/governance/send-grants").get_json()["log"]]
    assert events[:2] == ["revoked", "created"]
    assert client.get("/api/governance/send-grants").get_json()["grants"] == []


def test_a_grant_revoked_between_the_checkpoint_and_the_handler_is_not_used(client):
    from agent_friday.governance import send_grants
    g = _grant(client, scope="recipients", recipients=[TEAM])
    v = action_gate.authorize("draft_email", {"to": TEAM}, dict(SCHEDULED), clean=True)
    assert v.action == "allow" and v.grant["grant_id"] == g["grant_id"]
    send_grants.revoke(g["grant_id"])
    tok = action_gate.DECIDED.set(g["grant_id"])
    try:
        out = gmail_send.request_send(to=TEAM, subject="s", body="Synthetic.")
    finally:
        action_gate.DECIDED.reset(tok)
    assert not out.get("pre_approved") and out["approval"]["status"] == "pending"


def test_a_job_grant_covers_that_scheduled_job_only_and_a_tainted_send_never(client):
    _grant(client, scope="job", schedule_id="sch_weekly_status", recipients=[TEAM])
    assert _send(TEAM).get("pre_approved")
    other = dict(SCHEDULED, schedule_id="sch_other", task_id="task_other")
    res = _send(TEAM, ctx=other)
    assert not res.get("pre_approved") and _pending_for(res["approval_id"])
    assert action_gate.authorize("draft_email", {"to": TEAM}, dict(SCHEDULED), tainted=True).grant is None


def _write_skill(name, perms):
    from agent_friday import skill_registry
    return skill_registry.save_skill(name, description="Weekly status to the team",
                                     body="Write the weekly status and email it to the team.",
                                     permissions=perms)


def test_a_skills_declared_permissions_grant_nothing_until_the_owner_approves_them(client):
    from agent_friday.services import agent, workflow_tools
    _write_skill("weekly-status-synthetic", {"send_email": [TEAM]})
    # Skills are read in the owner's own conversation (read_skill refuses scoped background callers).
    ctx = {"session_id": "chat_skill_1", "authenticated": True}
    tok = agent._CURRENT_TOOL_CONTEXT.set(ctx)
    try:
        read = json.loads(workflow_tools.read_skill({"name": "weekly-status-synthetic"}))
    finally:
        agent._CURRENT_TOOL_CONTEXT.reset(tok)
    assert read["permissions_needed"] == {"send_email": [TEAM]}
    res = _send(TEAM, ctx=ctx)
    assert not res.get("pre_approved") and _pending_for(res["approval_id"]), \
        "a skill's own declaration let a send skip its card"
    listed = client.get("/api/governance/send-grants").get_json()
    sk = next(s for s in listed["skills"] if s["name"] == "weekly-status-synthetic")
    assert sk["needs"] == {"send_email": [TEAM]} and sk["granted"] == []
    g = _grant(client, scope="skill", skill="weekly-status-synthetic", recipients=[TEAM])  # one press: what it needs
    assert (g["scope"], g["skill"], g["recipients"], g["actions"]) == (
        "skill", "weekly-status-synthetic", [TEAM], ["send_email"])
    assert _send(TEAM, ctx=ctx).get("pre_approved") == g["grant_id"]
    elsewhere = {"session_id": "chat_without_the_skill", "authenticated": True}
    assert not _send(TEAM, ctx=elsewhere).get("pre_approved"), "a skill grant applied outside its skill"


def test_without_a_grant_a_scheduled_send_waits_on_its_card_and_says_so(client):
    res = _send(TEAM)
    assert res["queued"] is True and res["sent"] is False and not res.get("pre_approved")
    assert _pending_for(res["approval_id"])
    assert "scheduled or background run: it waits on that card" in res["note"]
    assert "pre-approve" in res["note"]


@pytest.mark.parametrize("body", [
    {"scope": "recipients", "recipients": []},
    {"scope": "recipients", "recipients": ["not an address"]},
    {"scope": "job", "recipients": [TEAM]},
    {"scope": "recipients", "recipients": [TEAM], "actions": ["delete_everything"]},
    {"scope": "skill", "skill": "no-such-skill-synthetic"},
])
def test_a_pre_approval_always_names_recipients_and_a_real_scope(client, body):
    assert client.post("/api/governance/send-grants", json=body).status_code == 400


def test_both_pages_show_pre_approvals_in_settings_and_on_the_approvals_card():
    for rel in ("index.html", "ui_parts/app.html"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "function SendGrantsPanel(" in text and "function PreApprovedSendsList(" in text, rel
        assert "Pre-approved sends" in text, rel
        assert ("React.createElement(SendGrantsPanel, null)" in text or "<SendGrantsPanel/>" in text), rel
        assert ("React.createElement(PreApprovedSendsList, null)" in text or "<PreApprovedSendsList/>" in text), rel
        assert "Approve what it needs" in text and "Declaring it grants nothing." in text, rel
        assert "Email is never covered" not in text, rel


@pytest.mark.skipif(not shutil.which("node"), reason="node is not on PATH")
def test_the_grant_sentence_names_who_and_where_under_node():
    src = (REPO / "index.html").read_text(encoding="utf-8")
    i = src.index("function sendGrantWords(")
    j = src.index("function SendGrantsPanel(")
    script = src[i:j] + "\nconsole.log(JSON.stringify([" \
        "sendGrantWords({scope:'job',schedule_id:'s1',recipients:['a@example.test','@b.example.test']}, id => id === 's1' ? 'Weekly status' : id)," \
        "sendGrantWords({scope:'skill',skill:'weekly',recipients:['a@example.test']}, id => id)," \
        "sendGrantWords({scope:'recipients',recipients:['a@example.test']}, id => id)]));"
    cp = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert cp.returncode == 0, cp.stderr[-1500:]
    assert json.loads(cp.stdout) == [
        "Email to a@example.test, @b.example.test \xb7 only for the scheduled job Weekly status",
        "Email to a@example.test \xb7 only while the skill weekly is in use",
        "Email to a@example.test \xb7 for any job or chat"]
