"""A saved workflow run on request has the same "Its emails: save as drafts / send" switch as a
scheduled job (synthetic data only, example.test).

* The switch is the workflow record's own `email_send`, shown on its Workflows card and set with
  PATCH /api/workflows/chains/<slug>; the workflow's runs on request follow it, draft_email becoming a
  send before any gate exactly as for a scheduled job (the card, or a pre-approval, still decides).
* A workflow the one-time mark kept sending shows "send" and can be switched back to drafts.
* The flag is outside what a job pre-approval fingerprints: switching it never voids one.
* A scheduled workflow's one switch (on the job) decides its runs on request too.
"""
from __future__ import annotations

import json
import threading
import uuid

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approvals, gmail_send

TEAM = "team@example.test"
OWNER = {"id": "acct_owner", "email": "owner@example.test", "label": "Owner", "services": {"gmail": True},
         "scopes": ["https://www.googleapis.com/auth/gmail.modify"]}


@pytest.fixture
def client(app):
    import agent_friday.core as core
    c = app.test_client()
    c.environ_base["HTTP_X_FRIDAY_TOKEN"] = core._current_api_token()
    return c


@pytest.fixture(autouse=True)
def saved(tmp_path, monkeypatch, friday_dir):
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: {"id": "n1"})
    monkeypatch.setattr(ne, "resolve", lambda key: 0)
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(gmail_send, "sendable_accounts", lambda: [dict(OWNER)])
    monkeypatch.setattr(gmail_send, "mail_accounts", lambda: [dict(OWNER)])
    out = []
    monkeypatch.setattr(gmail_send, "save_draft", lambda **kw: out.append(kw) or {
        "ok": True, "draft_id": "d1", "message_id": "m1", "thread_id": "t1", "account_id": OWNER["id"],
        "account_email": OWNER["email"], "account_label": OWNER["label"], "link": "https://mail.example.test/d1"})
    held = friday_dir / "mail_outbox" / "held.json"
    if held.exists():
        held.unlink()
    from agent_friday.services import scheduler, agent, email_jobs
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "schedule_runs.jsonl")
    monkeypatch.setattr(agent, "WORKFLOWS_DIR", tmp_path / "workflows")
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(agent, "_workflows_dir", lambda: tmp_path / "workflows")
    monkeypatch.setattr(agent, "_make_task_thread",
                        lambda *a, **k: threading.Thread(target=lambda: None, daemon=True))
    monkeypatch.setattr(email_jobs, "_state_path", lambda: tmp_path / "email_send_migration.json")
    from agent_friday.services import taint, turn_plan
    from agent_friday.governance import outside_content
    outside_content.reset_cache()
    taint.reset()
    turn_plan.begin_turn(None)
    before = set(agent.TASKS)
    yield out
    for tid in set(agent.TASKS) - before:
        agent.TASKS.pop(tid, None)
        getattr(agent, "TASK_THREADS", {}).pop(tid, None)
    taint.reset()
    turn_plan.begin_turn(None)


def _workflow(name, prompt):
    from agent_friday.services import agent
    return agent.save_workflow_chain({"name": name, "steps": [{"name": "Write and send", "prompt": prompt}]})["slug"]


def _first_start(monkeypatch):
    from agent_friday.services import scheduler
    monkeypatch.setattr(scheduler, "_STARTED", False)
    scheduler.start_scheduler()


def _run_on_request(slug):
    """A step of the workflow, run on request (no schedule), calls draft_email with no send flag."""
    from agent_friday.services import agent
    tid = "task_" + uuid.uuid4().hex[:8]
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "id": tid, "chain": slug, "chain_step": 0, "status": "running"}
    ctx = {"authenticated": True, "is_background_task": True, "task_id": tid}
    args = {"to": TEAM, "subject": "Weekly status " + uuid.uuid4().hex[:4], "body": "Synthetic weekly status."}
    return json.loads(agent._execute_tool("draft_email", args, session_ctx=ctx))


def _job_draft(sid):
    from agent_friday.services import agent
    ctx = {"authenticated": True, "is_background_task": True, "schedule_id": sid,
           "task_id": "task_" + uuid.uuid4().hex[:6]}
    args = {"to": TEAM, "subject": "Weekly status " + uuid.uuid4().hex[:4], "body": "Synthetic weekly status."}
    return json.loads(agent._execute_tool("draft_email", args, session_ctx=ctx))


def _card_row(slug):
    from agent_friday.services import workflow_overview
    return next(w for w in workflow_overview.overview()["workflows"] if w.get("slug") == slug)


def test_a_workflow_kept_sending_shows_send_and_can_be_switched_back_to_drafts(monkeypatch, client, saved):
    slug = _workflow("Weekly status to the team", "Write the weekly status and email it to the team.")
    _first_start(monkeypatch)                         # the one-time mark: made to send, so it keeps sending
    row = _card_row(slug)
    assert row.get("schedule_id") is None
    assert row.get("email_send") is True, "the workflow's card does not show that its emails are sent: %r" % row
    assert _run_on_request(slug).get("queued") is True

    r = client.patch("/api/workflows/chains/" + slug, json={"email_send": False})
    assert r.status_code == 200, "the workflow's card has no switch to go back to drafts: %s" % r.status_code
    assert _card_row(slug)["email_send"] is False
    res = _run_on_request(slug)
    assert res.get("saved") is True and not res.get("queued"), res
    cards = [a for a in approvals.list_approvals(status="pending")
             if (a.get("payload") or {}).get("handler") == "gmail_send"]
    assert len(saved) == 1 and len(cards) == 1, "the send asked once and the draft was saved once"


def test_a_new_workflow_saves_drafts_until_switched_to_send_and_an_edit_keeps_the_choice(monkeypatch, client, saved):
    from agent_friday.services import agent
    _first_start(monkeypatch)
    slug = _workflow("Weekly status to the team", "Write the weekly status and email it to the team.")
    assert _card_row(slug)["email_send"] is False
    assert _run_on_request(slug).get("saved") is True
    r = client.patch("/api/workflows/chains/" + slug, json={"email_send": True})
    assert r.status_code == 200 and r.get_json()["email_send"] is True, r.get_data(as_text=True)
    assert _run_on_request(slug).get("queued") is True, "switched to send, the workflow still saved a draft"
    rev = agent.load_workflow_chain(slug)["revision"]
    agent.save_workflow_chain({"name": "Weekly status to the team", "slug": slug,
                               "steps": [{"name": "Write and send", "prompt": "Write the status and email it."}]})
    assert agent.load_workflow_chain(slug)["revision"] == rev + 1
    assert _card_row(slug)["email_send"] is True, "editing the workflow lost the owner's email choice"
    assert client.patch("/api/workflows/chains/" + slug, json={"email_send": "yes"}).status_code == 400
    assert client.patch("/api/workflows/chains/no-such-workflow", json={"email_send": True}).status_code == 404


def test_switching_a_workflows_emails_never_voids_a_job_pre_approval_and_the_jobs_switch_covers_it(
        monkeypatch, client, saved):
    from agent_friday.governance import send_grants
    from agent_friday.services import scheduler, agent
    slug = _workflow("Weekly status to the team", "Write the weekly status and email it to the team.")
    job = scheduler.register_schedule({"id": "sch_" + uuid.uuid4().hex[:8], "name": "Weekly status",
                                       "trigger": "weekly", "spec": {"hour": 7, "minute": 0, "weekdays": [0]},
                                       "task": {"kind": "workflow", "ref": slug}})
    r = client.post("/api/governance/send-grants", json={"scope": "job", "schedule_id": job["id"],
                                                         "recipients": [TEAM]})
    assert r.status_code == 201, r.get_data(as_text=True)
    _first_start(monkeypatch)
    before = send_grants.job_fingerprint(job["id"])
    rev = agent.load_workflow_chain(slug)["revision"]
    for v in (False, True):
        r = client.patch("/api/workflows/chains/" + slug, json={"email_send": v})
        assert r.status_code == 200, r.get_data(as_text=True)
    assert send_grants.job_fingerprint(job["id"]) == before, "switching the workflow's emails voided the job's pre-approval"
    assert agent.load_workflow_chain(slug)["revision"] == rev, "a switch counted as an edit of the workflow"
    assert _job_draft(job["id"]).get("pre_approved"), "the job's pre-approval stopped covering it"
    # The scheduled workflow's card has one switch, on the job: it decides its runs on request too.
    assert client.patch("/api/schedules/" + job["id"], json={"email_send": False}).status_code == 200
    assert _run_on_request(slug).get("saved") is True, "the job was switched to drafts but Run now still sent"
    assert _job_draft(job["id"]).get("saved") is True
    assert send_grants.job_fingerprint(job["id"]) == before


# ── one source of truth: the workflow record ────────────────────────────────

def _record(slug):
    from agent_friday.services import agent
    return agent.load_workflow_chain(slug) or {}


def test_the_one_time_mark_writes_send_onto_the_workflow_record(monkeypatch, client, saved):
    from agent_friday.services import email_jobs
    slug = _workflow("Monthly invoices to accounts", "Write the summary and email it to accounts.")
    _first_start(monkeypatch)
    assert _record(slug).get("email_send") is True, "the workflow's own record does not say it sends"
    st = email_jobs.state()
    assert slug in st.get("send_workflows", []), "the migration record lost what it did"
    # The record alone decides: dropping the migration list changes nothing.
    email_jobs._write_state(dict(st, send_workflows=[]))
    assert email_jobs.workflow_sends(slug) is True


def test_the_migration_list_alone_no_longer_decides(monkeypatch, client, saved):
    from agent_friday.services import email_jobs
    slug = _workflow("Notes", "Tidy my notes.")
    email_jobs._write_state({"done": True, "flags_written": True, "send_workflows": [slug]})
    assert email_jobs.workflow_sends(slug) is False, "a list in a state file still decides over the record"


def test_an_install_marked_before_the_record_flag_gets_it_written_once(monkeypatch, client, saved):
    from agent_friday.services import email_jobs
    sends = _workflow("Weekly status to the team", "Write the weekly status and email it to the team.")
    chose = _workflow("Daily digest", "Write the digest and email it to me.")
    client.patch("/api/workflows/chains/" + chose, json={"email_send": False})   # the owner chose drafts
    email_jobs._write_state({"done": True, "send_workflows": [sends, chose]})       # an older install
    email_jobs.migrate()
    assert _record(sends).get("email_send") is True
    assert _record(chose).get("email_send") is False, "the owner's own choice was overwritten"
    assert email_jobs.state().get("flags_written") is True
    client.patch("/api/workflows/chains/" + sends, json={"email_send": False})
    email_jobs.migrate()                                                              # idempotent
    assert _record(sends).get("email_send") is False, "a second start undid the owner's switch"
