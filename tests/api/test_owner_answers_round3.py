"""The owner's third round of answers, each held by its behaviour (synthetic data only, example.test).

1. Scheduled email jobs made before draft_email saved drafts keep SENDING: a one-time, logged,
   idempotent mark (services/email_jobs) on the first start, one Activity notice listing them; a
   job made afterwards saves drafts; the owner can switch a job either way on its card.
2. A send written on a cloud seat whose text rates above PUBLIC goes to the approval card showing
   exactly what would go (no outright refusal, no pre-approval); the never-send floor still refuses,
   with the plain reason.
3. The default account for drafts is listed for a Settings picker and saved like any setting.
4. A pre-approval withheld after outside content says why, naming the kind of source.
6. The weekly podcast's one-voice offer is made once, and never changes the format by itself.
7. The money-paragraph card answered by voice: a read-back of exactly what would go, then a yes
   sends once; "remember this thread" in that yes remembers it.
8. A job card's "Follow my default" says what the default is now, and choosing it follows it.
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
CHAT = {"session_id": "chat_r3", "authenticated": True, "conversation_id": "conv_r3",
        "taint_key": "conversation:conv_r3"}


@pytest.fixture
def client(app):
    import agent_friday.core as core
    c = app.test_client()
    c.environ_base["HTTP_X_FRIDAY_TOKEN"] = core._current_api_token()
    return c


@pytest.fixture
def pushed(monkeypatch):
    import agent_friday.notifications_engine as ne
    out = []
    monkeypatch.setattr(ne, "push", lambda **kw: out.append(kw) or {"id": "n%d" % len(out)})
    monkeypatch.setattr(ne, "resolve", lambda key: 0)
    return out


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(gmail_send, "sendable_accounts", lambda: [dict(OWNER)])
    monkeypatch.setattr(gmail_send, "mail_accounts", lambda: [dict(OWNER)])
    saved = []
    monkeypatch.setattr(gmail_send, "save_draft", lambda **kw: saved.append(kw) or {
        "ok": True, "draft_id": "d1", "message_id": "m1", "thread_id": "t1", "account_id": OWNER["id"],
        "account_email": OWNER["email"], "account_label": OWNER["label"], "link": "https://mail.example.test/d1"})
    held = friday_dir / "mail_outbox" / "held.json"
    if held.exists():
        held.unlink()
    from agent_friday.services import scheduler, agent
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "schedule_runs.jsonl")
    monkeypatch.setattr(agent, "WORKFLOWS_DIR", tmp_path / "workflows")
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(agent, "_workflows_dir", lambda: tmp_path / "workflows")
    monkeypatch.setattr(agent, "_make_task_thread",
                        lambda *a, **k: threading.Thread(target=lambda: None, daemon=True))
    try:
        from agent_friday.services import email_jobs
        monkeypatch.setattr(email_jobs, "_state_path", lambda: tmp_path / "email_send_migration.json")
    except ImportError:
        pass                      # the code before this change: no mark at all (the tests say so)
    try:
        from agent_friday.services import cloud_release
        monkeypatch.setattr(cloud_release, "_path", lambda: tmp_path / "cloud_release.json")
    except ImportError:
        pass
    from agent_friday.services import taint, turn_plan
    from agent_friday.governance import outside_content
    outside_content.reset_cache()
    taint.reset()
    turn_plan.begin_turn(None)
    before = set(agent.TASKS)
    yield saved
    for tid in set(agent.TASKS) - before:
        agent.TASKS.pop(tid, None)
        getattr(agent, "TASK_THREADS", {}).pop(tid, None)
    taint.reset()
    turn_plan.begin_turn(None)


def _schedule(name, prompt, sid=None):
    from agent_friday.services import scheduler
    return scheduler.register_schedule({"id": sid or "sch_" + uuid.uuid4().hex[:8], "name": name,
                                        "trigger": "weekly", "spec": {"hour": 7, "minute": 0, "weekdays": [0]},
                                        "task": {"kind": "agent_prompt", "prompt": prompt}})


def _first_start(monkeypatch):
    """Friday's start after the upgrade: the scheduler starts (FRIDAY_TESTING: no thread)."""
    from agent_friday.services import scheduler
    monkeypatch.setattr(scheduler, "_STARTED", False)
    scheduler.start_scheduler()


def _job_draft(sid, **extra):
    """The job's own run calls draft_email the way a job written before the change did: no send flag."""
    from agent_friday.services import agent
    ctx = {"authenticated": True, "is_background_task": True, "schedule_id": sid,
           "task_id": "task_" + uuid.uuid4().hex[:6]}
    args = {"to": TEAM, "subject": "Weekly status " + uuid.uuid4().hex[:4], "body": "Synthetic weekly status."}
    args.update(extra)
    return json.loads(agent._execute_tool("draft_email", args, session_ctx=ctx))


def _pending(handler="gmail_send"):
    return [a for a in approvals.list_approvals(status="pending")
            if (a.get("payload") or {}).get("handler") == handler]


# ── 1. existing email jobs keep sending; new jobs save drafts ────────────────────

def test_an_existing_email_job_keeps_sending_after_the_first_start_and_the_owner_is_told_once(
        monkeypatch, pushed, _isolated):
    mail = _schedule("Weekly status to the team", "Write the weekly status and email it to the team.")
    tidy = _schedule("Tidy notes", "Re-index my notes and tag new pages.")
    _first_start(monkeypatch)
    res = _job_draft(mail["id"])
    assert res.get("queued") is True and not res.get("saved"), (
        "a job made to send email saved a draft instead after the change: %r" % res)
    cards = _pending()
    assert len(cards) == 1 and cards[0]["payload"]["to"] == [TEAM]
    assert _isolated == [], "the sending job also saved a draft"
    notices = [n for n in pushed if n.get("emitter") == "MJ1"]
    assert len(notices) == 1, "the owner was not told once which jobs keep sending: %r" % pushed
    assert "Weekly status to the team" in notices[0]["body"] and "Tidy notes" not in notices[0]["body"]
    # Idempotent: a second start marks nothing again and says nothing again.
    _first_start(monkeypatch)
    from agent_friday.services import scheduler
    assert len([n for n in pushed if n.get("emitter") == "MJ1"]) == 1
    assert scheduler.get_schedule(mail["id"]).get("email_send") is True
    assert "email_send" not in scheduler.get_schedule(tidy["id"])


def test_a_job_made_after_the_change_saves_drafts_and_the_owner_can_switch_it_on_its_card(
        monkeypatch, pushed, client, _isolated):
    _first_start(monkeypatch)
    new = _schedule("Weekly status to the team", "Write the weekly status and email it to the team.")
    res = _job_draft(new["id"])
    assert res.get("saved") is True and not res.get("queued"), res
    assert _pending() == [] and len(_isolated) == 1
    r = client.patch("/api/schedules/" + new["id"], json={"email_send": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _job_draft(new["id"]).get("queued") is True
    client.patch("/api/schedules/" + new["id"], json={"email_send": False})
    assert _job_draft(new["id"]).get("saved") is True


def test_a_job_whose_runs_queued_email_counts_as_sending_and_its_pre_approval_still_covers_it(
        monkeypatch, pushed, client):
    from agent_friday.services import scheduler
    job = _schedule("Friday thing", "Do the usual Friday thing.")
    scheduler._append_run({"id": job["id"], "status": "complete", "ts": 1,
                           "summary": "Queued an email to team@example.test; it is waiting for your approval."})
    r = client.post("/api/governance/send-grants", json={"scope": "job", "schedule_id": job["id"],
                                                         "recipients": [TEAM]})
    assert r.status_code == 201, r.get_data(as_text=True)
    _first_start(monkeypatch)
    assert scheduler.get_schedule(job["id"]).get("email_send") is True
    res = _job_draft(job["id"])
    assert res.get("pre_approved"), "the job's own pre-approval stopped covering it after the mark: %r" % res


# ── 2. a sensitive send from a cloud model goes to the card; the floor still refuses ──

def test_a_sensitive_send_on_a_cloud_seat_goes_to_the_card_not_a_refusal():
    from agent_friday.privacy.vault_access import VaultAccessControl
    v = VaultAccessControl()
    args = {"to": "clinic@example.test", "subject": "Appointment", "send": True,
            "body": "Could I move my appointment about my blood test results to Friday morning?"}
    ok, detail, tier = v.check_action("anthropic", "draft_email", json.dumps(args), model="claude-sonnet-5")
    assert tier > 1, "the synthetic body did not rate above PUBLIC"
    assert ok is True and detail.startswith("cloud_send_card_"), (ok, detail)


@pytest.mark.parametrize("body,kind,words", [
    ("Pay with card 4111 1111 1111 1111 please.", "card", "payment card number"),
    ("My number is 955-55-1234 for the form.", "ssn", "ID number"),  # pragma: allowlist secret
])
def test_the_never_send_floor_still_refuses_a_cloud_send_with_the_plain_reason(body, kind, words):
    from agent_friday.privacy.vault_access import VaultAccessControl, refusal_text
    v = VaultAccessControl()
    args = {"to": TEAM, "subject": "Details", "body": body, "send": True}
    ok, detail, _ = v.check_action("anthropic", "draft_email", json.dumps(args), model="claude-sonnet-5")
    assert ok is False and detail == "cloud_send_floor:" + kind, (ok, detail)
    text = refusal_text("draft_email", detail)
    assert words in text and "NOT sent" in text


def test_a_sensitive_cloud_send_shows_its_card_even_to_a_pre_approved_recipient(client):
    from agent_friday.services import agent
    r = client.post("/api/governance/send-grants", json={"scope": "recipients", "recipients": [TEAM]})
    assert r.status_code == 201, r.get_data(as_text=True)
    ctx = dict(CHAT, sensitive_cloud_send=True)
    res = json.loads(agent._execute_tool("draft_email", {"to": TEAM, "subject": "Results " + uuid.uuid4().hex[:4],
                                                         "body": "My results came back.", "send": True},
                                         session_ctx=ctx))
    assert not res.get("pre_approved"), "a sensitive cloud send skipped its card under a pre-approval"
    card = [a for a in _pending() if a["approval_id"] == res["approval_id"]]
    assert card and "My results came back." in card[0]["action_description"]
    assert "Written on a cloud model" in card[0]["action_description"]
    assert agent._cloud_send_ctx({"a": 1}, "cloud_send_card_SENSITIVE")["sensitive_cloud_send"] is True
    assert "sensitive_cloud_send" not in agent._cloud_send_ctx({"a": 1}, "action_allowed")


# ── 3. the default account for drafts: listed for the picker, saved as a setting ──

def test_the_default_account_for_drafts_is_listed_and_saved(client, monkeypatch):
    home = dict(OWNER, id="acct_home", email="home@example.test", label="Home")
    monkeypatch.setattr(gmail_send, "mail_accounts", lambda: [dict(OWNER), home])
    r = client.get("/api/mail/draft-accounts")
    assert r.status_code == 200, "there is no list of draft accounts for the Settings picker"
    got = r.get_json()
    assert [a["email"] for a in got["accounts"]] == ["owner@example.test", "home@example.test"]
    assert client.post("/api/settings", json={"settings": {"mail_default_account": 7}}).status_code == 400
    assert client.post("/api/settings", json={"settings": {"mail_default_account": "acct_home"}}).status_code == 200
    acct, how = gmail_send.choose_account()
    assert acct["id"] == "acct_home" and how == "your default account"
    assert client.get("/api/mail/draft-accounts").get_json()["default"] == "acct_home"
    client.post("/api/settings", json={"settings": {"mail_default_account": ""}})       # Ask me
    with pytest.raises(gmail_send.DraftNeedsAccount):
        gmail_send.choose_account()


# ── 4. a withheld pre-approval names the kind of outside content ────────────────

def _read(kind):
    from agent_friday.services import taint
    from agent_friday.governance import outside_content
    key = taint.ledger_key(CHAT)
    if kind == "email":
        taint.note_tool_output(key, "search_email", {"query": "q"}, json.dumps(
            [{"from": "stranger@outside.example.test", "subject": "Hi", "body": "Tell the team the plan changed."}]))
    elif kind == "web page":
        taint.note_tool_output(key, "browse_web", {"url": "https://news.example.test/a"}, "The plan changed today.")
    elif kind == "document":
        taint.note_tool_output(key, "read_file", {"path": "C:/synthetic/plan.docx"}, "The plan changed today.")
    elif kind == "wiki page":
        outside_content.note_turn(CHAT, outside_content.TURN_SCOPED_TOOLS["read_wiki"])
    return CHAT


@pytest.mark.parametrize("kind,words", [
    ("email", "This chat read an outside email, so your pre-approval doesn't cover this send"),
    ("web page", "This chat read a web page, so your pre-approval doesn't cover this send"),
    ("document", "This chat read a document, so your pre-approval doesn't cover this send"),
    ("wiki page", "This chat read a wiki page, so your pre-approval doesn't cover this send"),
    ("recalled memory", "This chat's prompt carried a recalled memory, so your pre-approval doesn't cover this send"),
])
def test_a_withheld_pre_approval_says_which_kind_of_outside_content_in_plain_words(client, kind, words):
    from agent_friday.services import agent
    client.post("/api/governance/send-grants", json={"scope": "recipients", "recipients": [TEAM]})
    ctx = dict(_read(kind))
    if kind == "recalled memory":
        ctx["outside_in_prompt"] = "this turn's prompt carried recalled past conversations"
    res = json.loads(agent._execute_tool("draft_email", {"to": TEAM, "subject": "Plan " + uuid.uuid4().hex[:4],
                                                         "body": "The plan is the same as before.", "send": True},
                                         session_ctx=ctx))
    assert not res.get("pre_approved")
    card = [a for a in _pending() if a["approval_id"] == res["approval_id"]]
    assert card and words in card[0]["action_description"], card and card[0]["action_description"]
    assert words in res["note"]


def test_a_task_started_from_such_a_chat_says_so(client):
    from agent_friday.services import agent
    from agent_friday.governance import outside_content
    client.post("/api/governance/send-grants", json={"scope": "recipients", "recipients": [TEAM]})
    outside_content.mark_task("task_r3", "spawned from work that read outside content (your email)")
    ctx = {"authenticated": True, "is_background_task": True, "task_id": "task_r3"}
    res = json.loads(agent._execute_tool("draft_email", {"to": TEAM, "subject": "Plan " + uuid.uuid4().hex[:4],
                                                         "body": "The plan is the same as before.", "send": True},
                                         session_ctx=ctx))
    card = [a for a in _pending() if a["approval_id"] == res["approval_id"]]
    assert card and ("This task was started from a chat that read an outside email, so your pre-approval "
                     "doesn't cover this send") in card[0]["action_description"], card and card[0]["action_description"]


# ── 6. the weekly podcast: offered one voice once; the format never changes by itself ──

@pytest.fixture
def podsettings(monkeypatch):
    import agent_friday.core as core
    box = {"s": {"podcasts": {"format": {"briefing": "solo", "front_page": "solo", "editorial": "solo",
                                         "weekly": "duo", "any": "solo"}, "format_migrated": True}}}

    def merge(base, over):
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = json.loads(json.dumps(v))
    monkeypatch.setattr(core, "_load_settings", lambda: box["s"])
    monkeypatch.setattr(core, "_save_settings", lambda delta, **kw: merge(box["s"], json.loads(json.dumps(delta))))
    return box


def _offers(pushed):
    return [n for n in pushed if "Weekly is two voices" in str(n.get("title"))]


def test_the_weekly_one_voice_offer_appears_once_and_never_changes_the_format(monkeypatch, pushed, podsettings):
    from agent_friday.services import podcast_engine as pe
    _first_start(monkeypatch)
    assert len(_offers(pushed)) == 1, "the one-voice offer was not made: %r" % pushed
    assert pe.format_for("weekly") == "duo", "the offer changed the weekly format by itself"
    _first_start(monkeypatch)
    assert len(_offers(pushed)) == 1, "offered twice"
    assert pe.format_for("weekly") == "duo"
    # An install whose weekly is already one voice is never asked.
    podsettings["s"]["podcasts"] = {"format": {"weekly": "solo"}, "format_migrated": True}
    _first_start(monkeypatch)
    assert len(_offers(pushed)) == 1


def test_switch_keep_and_dismiss_each_close_the_offer_for_good(monkeypatch, client, pushed, podsettings):
    from agent_friday.services import podcast_engine as pe
    _first_start(monkeypatch)
    r = client.get("/api/podcasts/weekly-one-voice")
    assert r.status_code == 200 and r.get_json()["state"] == "offered", "the Podcasts page cannot see the offer"
    r = client.post("/api/podcasts/weekly-one-voice", json={"choice": "keep"})
    assert r.status_code == 200 and r.get_json()["format"] == "duo"
    _first_start(monkeypatch)
    assert len(_offers(pushed)) == 1                                       # never again after Keep
    assert client.post("/api/podcasts/weekly-one-voice", json={"choice": "switch"}).status_code == 409
    assert pe.format_for("weekly") == "duo"
    podsettings["s"]["podcasts"].pop("weekly_one_voice_offer")              # (a fresh install, for Switch)
    _first_start(monkeypatch)
    assert client.post("/api/podcasts/weekly-one-voice", json={"choice": "switch"}).get_json()["format"] == "solo"
    podsettings["s"]["podcasts"]["format"]["weekly"] = "duo"
    podsettings["s"]["podcasts"].pop("weekly_one_voice_offer")              # (and again, for close)
    _first_start(monkeypatch)
    client.post("/api/podcasts/weekly-one-voice", json={"choice": "dismiss"})
    _first_start(monkeypatch)
    assert len(_offers(pushed)) == 3 and pe.format_for("weekly") == "duo"


# ── 7. the money-paragraph card answered by voice, after a read-back ─────────────

MONEY = "The deposit of $400 for the hall is due by 1 May."


def _money_card():
    from agent_friday.services import cloud_release as cr
    card = cr._raise_card([{"hash": "h" + uuid.uuid4().hex[:8], "text": MONEY, "thread": "mail:acct_owner:t-hall",
                            "sender": "robin@example.test", "path": "messages[0]"}], provider="anthropic")
    return card["approval_id"]


def _say(words, card_id, decision="approve", surface="voice"):
    from agent_friday.services import agent
    toks = [agent._CURRENT_SURFACE.set(surface), agent._CURRENT_OWNER_TEXT.set(words)]
    try:
        return agent._tool_answer_card({"card_id": card_id, "decision": decision})
    finally:
        agent._CURRENT_OWNER_TEXT.reset(toks[1])
        agent._CURRENT_SURFACE.reset(toks[0])


def test_a_spoken_yes_sends_the_money_paragraph_once_after_the_read_back(monkeypatch):
    from agent_friday.services import agent, cloud_release as cr
    monkeypatch.setattr(agent, "_voice_room", lambda: False)
    cid = _money_card()
    first = _say("yes, send it", cid)
    assert MONEY in first, "Friday did not read back exactly what would go: %r" % first
    assert approvals.get_approval(cid)["status"] == "pending", "a spoken yes sent it before the read-back"
    again = _say("yes, send it", cid)                    # the same words: not an answer to the read-back
    assert approvals.get_approval(cid)["status"] == "pending", again
    done = _say("yes, send it, and remember this thread", cid)
    assert approvals.get_approval(cid)["status"] == "approved", done
    assert [r["scope"] for r in cr.remembered()] == ["thread"]
    assert "sent once" in done.lower()


def test_a_spoken_no_keeps_it_here_and_a_cloud_voice_is_never_read_the_money(monkeypatch):
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_voice_room", lambda: False)
    cid = _money_card()
    live = _say("yes, send it", cid, surface="voice-live")
    assert MONEY not in live and "cloud model" in live
    assert approvals.get_approval(cid)["status"] == "pending"
    kept = _say("no, keep it here", cid, decision="decline")
    assert approvals.get_approval(cid)["status"] == "denied", kept


# ── 8. a job card's "Follow my default" names the default and follows it ─────────

def test_follow_my_default_names_the_current_default_and_the_job_follows_it(client, monkeypatch):
    from agent_friday.services import scheduler, workflow_overview
    job = _schedule("Synthetic digest", "Summarise the synthetic notes.")
    assert client.patch("/api/schedules/" + job["id"], json={"missed": "skip"}).status_code == 200
    row = next(r for r in scheduler.list_schedules() if r["id"] == job["id"])
    assert row["missed_own"] is True and row["missed"] == "skip"
    assert row["missed_default"] == "run_once" and row["missed_default_text"].startswith("If a run is missed")
    assert client.patch("/api/schedules/" + job["id"], json={"missed": "default"}).status_code == 200
    row = next(r for r in scheduler.list_schedules() if r["id"] == job["id"])
    assert row["missed_own"] is False and row["missed"] == "run_once"
    view = workflow_overview._job_view(row)
    assert view["missed_own"] is False and view["missed_default"] == "run_once"
    assert view["held_default"] == "retry" and "on_held_own" in view
