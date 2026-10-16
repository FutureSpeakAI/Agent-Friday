"""Saving a drafted email to Gmail Drafts, by text and by voice (field report: "can't save drafted
emails to my Gmail accounts", two connected accounts).

Reproduced on a synthetic two-account Gmail (fake Google service objects, no network): asking for a
draft went through the model's only mail-writing tool, draft_email, which raised a SEND approval card
(gmail_send.request_send) and never called users.drafts.create. The owner was offered sending when he
wanted a saved draft, and on a cloud seat a draft whose text mentioned an appointment was refused
outright by the vault zero-trust gate ("vault-zt deny").

Now draft_email SAVES to Gmail Drafts by default (no card: an own-mailbox write, receipted) and only
queues a send, behind its card, when send=true. Synthetic people and addresses only (example.test).
"""
from __future__ import annotations

import base64
import email
import email.policy
import json

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent
from agent_friday.services import approvals
from agent_friday.services import gmail_send as gs
from agent_friday.services import google_accounts as ga

READ = "https://www.googleapis.com/auth/gmail.readonly"
SEND = "https://www.googleapis.com/auth/gmail.send"
MODIFY = "https://www.googleapis.com/auth/gmail.modify"

HOME, WORK = "acct_home", "acct_work"
HOME_ADDR, WORK_ADDR = "owner.home@example.test", "owner.work@example.test"
TID, MINE = "18c0venue0001", "18c0mine00002"
REF = "mail:%s:%s" % (WORK, TID)
DRAFT_MSG_ID = {HOME: "18f3a9c2b4d5e6f7", WORK: "18f3a9c2b4d5e6f8"}


def _accounts(work_scopes=(READ, SEND, MODIFY), home_scopes=(READ, SEND, MODIFY)):
    return [
        {"id": HOME, "email": HOME_ADDR, "label": "Home", "status": "connected",
         "services": {"gmail": True}, "scopes": list(home_scopes)},
        {"id": WORK, "email": WORK_ADDR, "label": "Work", "status": "connected",
         "services": {"gmail": True}, "scopes": list(work_scopes)},
    ]


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _msg(mid, tid, hdrs, body):
    return {"id": mid, "threadId": tid, "internalDate": "1791277920000", "labelIds": ["INBOX"],
            "snippet": body[:40],
            "payload": {"mimeType": "text/plain", "body": {"data": _b64(body), "size": len(body)},
                        "headers": [{"name": k, "value": v} for k, v in hdrs.items()]}}


THREADS = {WORK: {
    TID: [
        _msg("m1", TID, {"From": "Dana Reyes <dana@example.test>", "To": WORK_ADDR + ", sam@example.test",
                         "Cc": "lee@example.test", "Subject": "Venue for the offsite",
                         "Message-ID": "<m1@example.test>"},
             "Shall we book the Mill Loft?"),
        _msg("m2", TID, {"From": "Sam Okafor <sam@example.test>", "To": WORK_ADDR + ", Dana Reyes <dana@example.test>",
                         "Cc": "lee@example.test", "Subject": "Re: Venue for the offsite",
                         "Message-ID": "<m2@example.test>", "References": "<m1@example.test>"},
             "The Mill Loft has no lift; Cedar House instead?"),
    ],
    MINE: [
        _msg("m9", MINE, {"From": "Owner <" + WORK_ADDR + ">", "To": "Dana Reyes <dana@example.test>",
                          "Subject": "Parking", "Message-ID": "<m9@example.test>"},
             "Is there parking at Cedar House?"),
    ],
}, HOME: {}}


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _HttpError(Exception):
    """Shaped like googleapiclient's HttpError."""

    def __init__(self, status, reason, message):
        super().__init__(message)
        self.resp = type("R", (), {"status": status})()
        self.content = json.dumps({"error": {"errors": [{"reason": reason}], "message": message}}).encode()


class FakeGmail:
    """One account's slice of the Gmail v1 API: drafts.create, threads.get, messages.send."""

    def __init__(self, aid):
        self.aid = aid
        self.drafts_created, self.sent = [], []
        self.refuse_drafts = None

    def users(self):
        return self

    def drafts(self):
        g = self

        class _D:
            def create(self, userId, body):
                def run():
                    if g.refuse_drafts:
                        raise g.refuse_drafts
                    g.drafts_created.append(body)
                    m = body["message"]
                    return {"id": "r-%d" % (len(g.drafts_created) + 4000),
                            "message": {"id": DRAFT_MSG_ID[g.aid], "threadId": m.get("threadId") or "18f3new",
                                        "labelIds": ["DRAFT"]}}
                return _Req(run)
        return _D()

    def threads(self):
        g = self

        class _T:
            def get(self, userId, id, format="full"):
                def run():
                    if id not in THREADS[g.aid]:
                        raise _HttpError(404, "notFound", "Requested entity was not found.")
                    return {"id": id, "messages": THREADS[g.aid][id]}
                return _Req(run)
        return _T()

    def messages(self):
        g = self

        class _M:
            def send(self, userId=None, body=None):
                g.sent.append(body)
                return _Req(lambda: {"id": "sent1", "threadId": "t"})
        return _M()


@pytest.fixture
def gmail(tmp_path, monkeypatch):
    """Two connected accounts (Home, Work), each with its own fake Gmail; no card queue, receipt log or
    governance state outside tmp_path."""
    import googleapiclient.discovery as disc
    fakes = {HOME: FakeGmail(HOME), WORK: FakeGmail(WORK)}
    state = {"accounts": _accounts()}
    monkeypatch.setattr(disc, "build", lambda *a, **k: fakes[k["credentials"][1]])
    monkeypatch.setattr(ga, "_migrate_legacy_if_needed", lambda: None)
    monkeypatch.setattr(ga, "has_accounts", lambda: True)
    monkeypatch.setattr(ga, "list_accounts", lambda: [dict(a) for a in state["accounts"]])
    monkeypatch.setattr(ga, "get_account", lambda aid: next(
        (dict(a) for a in state["accounts"] if a["id"] == aid), None))
    monkeypatch.setattr(ga, "_accounts_with", lambda svc: [dict(a) for a in state["accounts"]])
    monkeypatch.setattr(ga, "credentials_for", lambda aid: ("creds", aid))
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": a["id"], "email": a["email"], "label": a["label"]}
        for a in state["accounts"] if SEND in a["scopes"]])
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    from agent_friday.services import action_journal, mail_thread, taint
    monkeypatch.setattr(action_journal, "_path", lambda: tmp_path / "receipts.jsonl")
    action_journal.reset()
    mail_thread.reset()
    taint.reset()
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    fakes["state"] = state
    yield fakes
    action_journal.reset()
    taint.reset()


def _draft(**inp):
    return json.loads(agent.CLAUDE_TOOL_HANDLERS["draft_email"](inp))


def _raw(body):
    return base64.urlsafe_b64decode(body["message"]["raw"])


def _parsed(body):
    return email.message_from_bytes(_raw(body), policy=email.policy.default)


def _cards():
    return approvals.list_approvals(status="pending")


# ── reproduce: on the old code a draft request raised a send card and saved nothing ──

def test_asking_for_a_draft_saves_it_to_gmail_drafts_and_raises_no_card(gmail):
    out = _draft(to="dana@example.test", subject="Venue", body="Cedar House works for me.",
                 account_id=WORK)
    assert out.get("saved") is True and out.get("sent") is False, out
    assert len(gmail[WORK].drafts_created) == 1, "nothing reached users.drafts.create"
    assert gmail[HOME].drafts_created == [] and gmail[WORK].sent == []
    assert _cards() == [], "a draft raised an approval card"
    m = _parsed(gmail[WORK].drafts_created[0])
    assert m["To"] == "dana@example.test" and m["Subject"] == "Venue"
    assert m.get_content().strip() == "Cedar House works for me."


def test_through_the_real_tool_path_a_draft_runs_with_no_card_and_is_receipted(gmail):
    """agent._execute_tool -> hooks -> taint -> action_gate -> handler. The checkpoint calls a draft an
    own-mailbox write; its signed receipt and an action receipt record it."""
    ctx = agent.prepare_confirmation_ctx("sess-draft", "draft a reply to Sam and save it", {"authenticated": True})
    out = json.loads(agent._execute_tool("draft_email", {"thread": REF, "body": "Cedar House it is."},
                                         session_ctx=ctx))
    assert out.get("saved") is True, out
    assert _cards() == [] and approvals.list_approvals(kind=gs.APPROVAL_KIND) == []
    assert "CONFIRMATION REQUIRED" not in json.dumps(out)
    bom = [json.loads(x) for x in action_gate.receipts_path().read_text(encoding="utf-8").splitlines()]
    mine = [r for r in bom if r.get("tool") == "draft_email"]
    assert mine and mine[-1]["class"] == action_gate.INTERNAL and mine[-1]["decision"] == "allow"
    assert "own-mailbox" in mine[-1]["reason"]
    from agent_friday.services import action_journal
    rec = action_journal.recent(1)[0]
    assert rec["tool"] == "draft_email" and rec["status"] == "complete" and "nothing was sent" in rec["summary"]
    assert not action_journal.public_view(rec)["undoable"]


# ── receipt: draft id, account, a link that opens the draft, "nothing was sent" ──

def test_the_receipt_names_the_account_and_links_to_the_draft(gmail):
    out = _draft(to="dana@example.test", subject="Venue", body="Cedar House.", account_id="Work")
    assert out.get("draft_id", "").startswith("r-") and out.get("account") == WORK_ADDR, out
    link = "https://mail.google.com/mail/u/owner.work@example.test/#drafts?compose=18f3a9c2b4d5e6f8"
    assert out["link"] == link and out["message_id"] == DRAFT_MSG_ID[WORK]
    assert link in out["receipt"] and WORK_ADDR in out["receipt"] and "Nothing was sent" in out["receipt"]
    assert out["spoken"] == "Saved as a draft in your Work Gmail; nothing was sent."
    assert out["spoken"].count(".") == 1                    # one sentence for voice
    assert "NOT sent" in out["note"] and "no card" in out["note"]


def test_the_link_names_the_message_id_never_the_draft_id(gmail):
    out = _draft(to="dana@example.test", subject="x", body="y", account_id=HOME_ADDR)
    assert out["link"].endswith("#drafts?compose=" + DRAFT_MSG_ID[HOME]) and out["draft_id"] not in out["link"]
    assert out["link"].startswith("https://mail.google.com/mail/u/" + HOME_ADDR + "/")


# ── which account ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("named,want", [(HOME, HOME), (HOME_ADDR, HOME), ("home", HOME), ("Work", WORK),
                                        (WORK_ADDR.upper(), WORK)])
def test_an_account_named_by_id_address_or_label_gets_the_draft(gmail, named, want):
    out = _draft(to="pat@example.test", subject="s", body="b", account_id=named)
    assert out.get("saved") is True, out
    other = HOME if want == WORK else WORK
    assert len(gmail[want].drafts_created) == 1 and gmail[other].drafts_created == []


def test_two_accounts_and_nothing_to_choose_by_asks_which_and_saves_nothing(gmail):
    out = _draft(to="pat@example.test", subject="s", body="b")
    assert out.get("saved") is False and out.get("sent") is False and out.get("needs_account") is True, out
    assert HOME_ADDR in out["question"] and WORK_ADDR in out["question"] and "Nothing was saved" in out["question"]
    assert {a["id"] for a in out["accounts"]} == {HOME, WORK}
    assert gmail[HOME].drafts_created == gmail[WORK].drafts_created == [] and _cards() == []


def test_an_account_name_that_matches_nothing_asks_rather_than_guessing(gmail):
    out = _draft(to="pat@example.test", subject="s", body="b", account_id="school")
    assert out.get("saved") is False and out.get("needs_account") is True and "school" in out["question"]
    assert gmail[HOME].drafts_created == gmail[WORK].drafts_created == []


def test_the_owners_default_account_is_used_when_nothing_else_says(gmail, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings", lambda: {"mail_default_account": HOME_ADDR})
    out = _draft(to="pat@example.test", subject="s", body="b")
    assert out.get("saved") is True and out["account"] == HOME_ADDR and out["account_chosen_by"] == "your default account"


def test_one_account_needs_no_question(gmail):
    gmail["state"]["accounts"] = _accounts()[:1]
    out = _draft(to="pat@example.test", subject="s", body="b")
    assert out.get("saved") is True and out["account"] == HOME_ADDR


# ── replies on an existing conversation ─────────────────────────────────────────

def test_a_reply_draft_threads_on_the_last_message_and_goes_to_its_sender(gmail):
    out = _draft(thread=REF, body="Cedar House works. I will book it.")
    assert out.get("saved") is True and out["reply"] is True and out["account"] == WORK_ADDR, out
    assert out["account_chosen_by"] == "the account the conversation is in"
    (body,) = gmail[WORK].drafts_created
    assert body["message"]["threadId"] == TID
    m = _parsed(body)
    assert m["To"] == "sam@example.test" and m["Cc"] is None
    assert m["Subject"] == "Re: Venue for the offsite"                     # no "Re: Re:"
    assert m["In-Reply-To"] == "<m2@example.test>"
    assert m["References"].split() == ["<m1@example.test>", "<m2@example.test>"]
    assert m.get_content().strip() == "Cedar House works. I will book it."  # quotes nothing
    assert gmail[HOME].drafts_created == []


def test_reply_all_adds_everyone_else_but_never_the_owner(gmail):
    out = _draft(thread=REF, body="Agreed.", reply_all=True)
    assert out.get("saved") is True, out
    m = _parsed(gmail[WORK].drafts_created[0])
    assert m["To"] == "sam@example.test"
    assert [a.strip() for a in m["Cc"].split(",")] == ["dana@example.test", "lee@example.test"]
    assert WORK_ADDR not in str(m["To"]) + str(m["Cc"])


def test_a_reply_to_the_owners_own_last_message_goes_to_whom_it_went_to(gmail):
    out = _draft(thread=MINE, body="Following up on parking.")
    assert out.get("saved") is True and out["account"] == WORK_ADDR, out   # a bare thread id finds its account
    m = _parsed(gmail[WORK].drafts_created[0])
    assert m["To"] == "dana@example.test" and m["Subject"] == "Re: Parking"


def test_the_reply_headers_survive_mime_with_utf8_and_crlf(gmail):
    out = _draft(to="dana@example.test", subject="Café plan — Zürich ✓", body="Grüße aus Köln.\nSee you there.",
                 account_id=WORK)
    assert out.get("saved") is True, out
    raw = _raw(gmail[WORK].drafts_created[0])
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), "bare LF line endings"
    head = raw.split(b"\r\n\r\n", 1)[0]
    assert "Café".encode() not in head and b"=?utf-8?" in head.lower()     # RFC 2047 encoded subject
    m = _parsed(gmail[WORK].drafts_created[0])
    assert m["Subject"] == "Café plan — Zürich ✓"
    assert m.get_content_charset() == "utf-8" and "Grüße aus Köln." in m.get_content()


# ── a missing permission says so, names the account, offers the reconnect ─────

def test_an_account_without_gmail_modify_is_named_and_nothing_is_claimed(gmail):
    gmail["state"]["accounts"] = _accounts(work_scopes=(READ, SEND))
    out = _draft(to="dana@example.test", subject="s", body="b", account_id=WORK)
    assert out.get("saved") is False and out.get("sent") is False, out
    assert out["missing_permission"] == "gmail.modify" and out["account"] == WORK_ADDR
    assert "Reconnect with sending" in out["error"] and WORK_ADDR in out["error"]
    assert "nothing was saved" in out["error"].lower() and "Never say it was saved" in out["note"]
    assert gmail[WORK].drafts_created == [] and _cards() == []


def test_google_refusing_the_scope_is_reported_the_same_way(gmail):
    """The recorded scopes can be wrong (they were the REQUESTED ones); Google's own refusal decides."""
    gmail[WORK].refuse_drafts = _HttpError(403, "insufficientPermissions",
                                           "Request had insufficient authentication scopes.")
    out = _draft(to="dana@example.test", subject="s", body="b", account_id=WORK)
    assert out.get("saved") is False and out.get("missing_permission") == "gmail.modify", out
    assert "Reconnect with sending" in out["error"] and "Google refused" in out["error"]


def test_the_account_record_keeps_the_scopes_google_granted_not_the_ones_asked_for(monkeypatch):
    """google-auth-oauthlib puts the REQUESTED scopes on creds.scopes and the granted ones on
    creds.granted_scopes. An unticked permission must not be recorded as granted."""
    index = {"accounts": []}
    monkeypatch.setattr(ga, "_load_index", lambda: index)
    monkeypatch.setattr(ga, "_save_index", lambda i: None)
    monkeypatch.setattr(ga, "_persist_token", lambda aid, creds: "test")
    monkeypatch.setattr(ga.cs, "audit_event", lambda *a, **k: None)
    creds = type("C", (), {"scopes": [READ, SEND, MODIFY], "granted_scopes": [READ, SEND]})()
    rec = ga.upsert_account(creds, label="Work", email=WORK_ADDR)
    assert MODIFY not in rec["scopes"] and SEND in rec["scopes"]


# ── sending still asks ────────────────────────────────────────────────────────

def test_send_true_still_raises_the_send_card_and_saves_no_draft(gmail):
    out = _draft(to="dana@example.test", subject="Venue", body="Cedar House.", account_id=WORK, send=True)
    assert out.get("queued") is True and out.get("sent") is False and out.get("saved") is None, out
    (card,) = approvals.list_approvals(status="pending", kind=gs.APPROVAL_KIND)
    assert card["payload"]["from_email"] == WORK_ADDR
    assert gmail[WORK].drafts_created == [] and gmail[WORK].sent == []


def test_a_reply_sent_with_send_true_carries_its_threading_onto_the_card(gmail):
    out = _draft(thread=REF, body="Booked.", send=True)
    assert out.get("queued") is True, out
    (card,) = approvals.list_approvals(status="pending", kind=gs.APPROVAL_KIND)
    p = card["payload"]
    assert p["to"] == ["sam@example.test"] and p["subject"] == "Re: Venue for the offsite"
    assert p["extras"]["thread_id"] == TID and p["extras"]["in_reply_to"] == "<m2@example.test>"
    assert gmail[WORK].drafts_created == []


def test_the_gate_classifies_a_draft_as_an_own_mailbox_write_and_a_send_as_outward():
    save = {"to": "a@example.test", "subject": "s", "body": "b"}
    assert action_gate.classify("draft_email", save)[0] == action_gate.INTERNAL
    assert action_gate.classify("draft_email", dict(save, send=True))[0] == action_gate.OUTWARD
    assert action_gate.classify("draft_email", dict(save, send="false"))[0] == action_gate.INTERNAL
    # A recipient read in an email (tainted) does not put a draft on a card; a send keeps its own card.
    assert action_gate.authorize("draft_email", save, {"session_id": "s1"}, tainted=True).action == "allow"
    v = action_gate.authorize("draft_email", dict(save, send=True), {"session_id": "s1"}, tainted=True)
    assert v.klass == action_gate.OUTWARD and "own approval card" in v.reason


def test_a_draft_never_uses_a_pre_approved_send(monkeypatch):
    from agent_friday.governance import send_grants
    monkeypatch.setattr(send_grants, "covering_tool", lambda *a, **k: pytest.fail("a draft consulted send grants"))
    v = action_gate.authorize("draft_email", {"to": "team@example.test", "body": "b"},
                              {"is_background_task": True, "schedule_id": "sch_x"}, clean=True)
    assert v.action == "allow" and v.grant is None


# ── the vault zero-trust gate on a cloud seat (the 10-09 refusals) ───────────────

def test_a_draft_that_mentions_an_appointment_is_not_refused_on_a_cloud_seat(tmp_path):
    from agent_friday.privacy.vault_access import VaultAccessControl, refusal_text
    v = VaultAccessControl()
    args = {"to": "clinic@example.test", "subject": "Appointment",
            "body": "Could I move my appointment to Friday morning?"}
    log = tmp_path / "access-log.jsonl"
    ok, detail, tier = v.check_action("anthropic", "draft_email", json.dumps(args), access_log_path=str(log),
                                      model="claude-sonnet-5")
    assert ok is True and detail.startswith("own_mailbox_draft_") and tier > 1, (ok, detail)
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["detail"] == detail   # logged
    # Sending the same words to someone else goes to the owner's card (round 3: an approval card showing
    # exactly what would be sent replaces the outright refusal); the never-send floor still refuses.
    ok2, detail2, _ = v.check_action("anthropic", "draft_email", json.dumps(dict(args, send=True)),
                                     model="claude-sonnet-5")
    assert ok2 is True and detail2.startswith("cloud_send_card_"), (ok2, detail2)
    assert "Saving it as a draft" in refusal_text("draft_email", "cloud_denied_tier_SENSITIVE")


# ── voice: the same tool, reached the way voice reaches mail drafting ──────────

def test_voice_draft_a_reply_and_save_it_delegates_and_the_delegated_call_saves(gmail):
    """Voice has no draft tool of its own: the routed plan hands the request to the full chat agent
    (delegate_to_friday), which runs as a background task. That task's draft_email call saves the
    draft with no card and returns one sentence to speak."""
    from agent_friday.routes import voice as rv
    from agent_friday.services import laya_router as lr
    from agent_friday.services import voice_engine as ve
    held = ve.build_voice_tool_contract()["names"]
    plan = rv._voice_route_plan("draft a reply to Sam and save it", held,
                                route_fn=lambda t, **kw: lr.rules_route(t, tools=kw.get("tools")))
    assert plan["tool"] == "delegate_to_friday" and "save it" in plan["args"]["request"], plan
    task_ctx = {"authenticated": True, "is_background_task": True, "task_id": "task_voice_1"}
    out = json.loads(agent._execute_tool("draft_email", {"thread": REF, "body": "Cedar House it is."},
                                         session_ctx=task_ctx))
    assert out.get("saved") is True and _cards() == [], out
    assert out["spoken"] == "Saved as a draft in your Work Gmail; nothing was sent."
    assert len(gmail[WORK].drafts_created) == 1


def test_the_delegated_agent_can_find_draft_email_for_a_save_request():
    from agent_friday.services import tool_catalogue as tc
    found = [t["name"] for t in tc.search(agent.CLAUDE_TOOLS, "draft a reply to Dana and save it")]
    assert "draft_email" in found[:3], found
    desc = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "draft_email")
    assert "SAVES it to the user's Gmail Drafts" in desc["description"] and "nothing sent" in desc["description"]
    props = desc["input_schema"]["properties"]
    assert props["send"]["type"] == "boolean" and props["reply_all"]["type"] == "boolean" and "thread" in props
    assert desc["input_schema"]["required"] == ["body"]


def test_a_draft_call_written_as_text_on_a_local_seat_is_routed_with_typed_arguments():
    from agent_friday.services import text_toolcalls as tt
    desc = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "draft_email")
    tools = [{"type": "function", "function": {"name": "draft_email", "parameters": desc["input_schema"]}}]
    text = ('<invoke name="draft_email"><parameter name="thread">' + REF + '</parameter>'
            '<parameter name="body">Cedar House it is.</parameter>'
            '<parameter name="reply_all">true</parameter></invoke>')
    calls, rest, refused = tt.extract(text, tools)
    assert [c["function"]["name"] for c in calls] == ["draft_email"] and refused == []
    assert calls[0]["function"]["arguments"] == {"thread": REF, "body": "Cedar House it is.", "reply_all": True}


# ── eval: the router hands drafting to the model, text and voice alike ─────────

DRAFT_ASKS = ["draft a reply to Dana and save it", "save a draft email to the team about the venue change"]


@pytest.mark.parametrize("text", DRAFT_ASKS)
def test_text_draft_a_reply_and_save_it_defers_to_the_model_with_draft_email_in_reach(text):
    from agent_friday.services import laya_router as lr
    r = lr.rule_route(text)
    assert r is not None and r.decision == "defer", r
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    cases = json.loads((root / "tests/fixtures/laya_router_eval.json").read_text(encoding="utf-8"))["cases"]
    assert {"text": text, "decision": "defer"} in cases
    m = json.loads((root / "src/agent_friday/resources/laya_manifests/messages.json").read_text(encoding="utf-8"))
    compose = next(v for v in m["verbs"] if v["id"] == "messages.compose")
    assert text in compose["phrasings"] and "laya_router_eval.json#" + text in compose["eval"]
    assert compose["gate"]["card"] == "by_argument"


@pytest.mark.parametrize("text", DRAFT_ASKS)
def test_voice_draft_a_reply_and_save_it_delegates_to_the_chat_agent(text):
    from agent_friday.routes import voice as rv
    from agent_friday.services import laya_router as lr
    from agent_friday.services import voice_engine as ve
    held = ve.build_voice_tool_contract()["names"]
    plan = rv._voice_route_plan(text, held, route_fn=lambda t, **kw: lr.rules_route(t, tools=kw.get("tools")))
    assert plan["tool"] == "delegate_to_friday" and plan["args"]["request"] == text, plan
    # Parity holds because the delegated agent saves: the same handler, no card (see above).
    assert action_gate.classify("draft_email", {"body": "x"})[0] == action_gate.INTERNAL
