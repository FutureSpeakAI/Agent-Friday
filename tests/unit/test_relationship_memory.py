"""Relationship memory: a local timeline from mail and calendar headers,
follow-ups, cold-thread nudges, the LinkedIn import, Google Contacts writes,
and forgetting.

Every Google call is faked. The fakes carry message snippets, bodies and event
descriptions on purpose, so the tests can prove none of it is stored.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
OWNER = "owner@example.com"


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _msg(mid, thread, frm, to, subject, days_ago, cc="", labels=None, bulk=False):
    return {"gmail_id": mid, "thread_id": thread, "from": frm, "to": to, "cc": cc,
            "subject": subject, "at": _iso(days_ago), "labels": labels or ["INBOX"],
            "bulk": bulk,
            # Present in what Google returns; must never reach the store.
            "snippet": "SECRET-SNIPPET private words", "body": "SECRET-BODY text"}


@pytest.fixture
def rel(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import relationship_memory as rm

    home = tmp_path / ".friday"
    home.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", home)

    state = {"gmail": [], "calendar": [], "queries": [], "cal_windows": [],
             "pushed": [], "gmail_error": None}

    def fake_gmail(account, query):
        state["queries"].append(query)
        if state["gmail_error"]:
            raise RuntimeError(state["gmail_error"])
        return {"messages": list(state["gmail"]), "truncated": False}

    def fake_calendar(account, start, end):
        state["cal_windows"].append((start, end))
        return list(state["calendar"])

    monkeypatch.setattr(rm, "_owner_addresses", lambda: {OWNER})
    monkeypatch.setattr(rm, "_sync_accounts",
                        lambda service: [{"id": "acct_a", "email": OWNER, "label": "main"}])
    monkeypatch.setattr(rm, "_fetch_gmail", fake_gmail)
    monkeypatch.setattr(rm, "_fetch_calendar", fake_calendar)
    monkeypatch.setattr(rm, "_push", lambda **kw: state["pushed"].append(kw))
    monkeypatch.setattr(rm, "state", state, raising=False)
    monkeypatch.setattr(rm, "home", home, raising=False)
    return rm


def _seed_mail(rel):
    rel.state["gmail"] = [
        _msg("m1", "t1", "Dana Okafor <dana@acme.example.com>", OWNER, "Q3 roadmap", 10),
        _msg("m2", "t1", OWNER, "Dana Okafor <dana@acme.example.com>", "Re: Q3 roadmap", 9,
             labels=["SENT"]),
        _msg("m3", "t2", "Sam Reyes <sam@globex.example.org>", OWNER, "Intro call", 4,
             cc="Dana Okafor <dana@acme.example.com>"),
        _msg("m4", "t3", "News <news@shop.example.com>", OWNER, "SALE", 3, bulk=True),
        _msg("m5", "t4", "no-reply@service.example.com", OWNER, "Your receipt", 2),
    ]
    rel.state["calendar"] = [
        {"id": "e1", "title": "Roadmap review", "start": _iso(8),
         "description": "SECRET-AGENDA do not store",
         "attendees": [{"email": OWNER, "self": True},
                       {"email": "dana@acme.example.com", "name": "Dana Okafor"},
                       {"email": "lee@acme.example.com", "name": "Lee Park"}]},
    ]


# ── the timeline ─────────────────────────────────────────────────────────────

def test_the_timeline_holds_headers_and_never_bodies(rel):
    _seed_mail(rel)
    out = rel.sync(NOW)
    assert out["added"] == 4 and not out["errors"]
    raw = (rel.home / "relationships" / "timeline.json").read_text(encoding="utf-8")
    assert "SECRET" not in raw, "a body, snippet or event description was stored"
    tl = json.loads(raw)
    keys = set().union(*(e.keys() for e in tl["interactions"]))
    assert not keys & {"snippet", "body", "description"}
    kinds = sorted(e["kind"] for e in tl["interactions"])
    assert kinds == ["email", "email", "email", "meeting"]
    sent = next(e for e in tl["interactions"] if e["id"].endswith(":m2"))
    assert sent["direction"] == "out" and sent["people"] == ["dana@acme.example.com"]
    # Bulk mail and automated senders are not relationships.
    assert not any(e["id"].endswith((":m4", ":m5")) for e in tl["interactions"])


def test_sync_is_incremental_per_account(rel):
    _seed_mail(rel)
    rel.sync(NOW)
    assert "newer_than:30d" in rel.state["queries"][0]
    high = int(datetime.fromisoformat(_iso(2)).timestamp())
    rel.sync(NOW + timedelta(hours=1))
    assert rel.state["queries"][1].startswith("after:%d" % high), rel.state["queries"][1]
    # Re-seeing the same messages adds nothing.
    tl = json.loads((rel.home / "relationships" / "timeline.json").read_text(encoding="utf-8"))
    assert len(tl["interactions"]) == 4
    # The calendar resumes from where it stopped, with a day of overlap.
    start2, _ = rel.state["cal_windows"][1]
    assert start2 == NOW - timedelta(days=1)


def test_a_failed_fetch_keeps_the_watermark(rel):
    _seed_mail(rel)
    rel.sync(NOW)
    rel.state["gmail_error"] = "rate limited"
    out = rel.sync(NOW + timedelta(hours=1))
    assert out["errors"] and out["errors"][0]["service"] == "gmail"
    rel.state["gmail_error"] = None
    rel.sync(NOW + timedelta(hours=2))
    assert rel.state["queries"][2] == rel.state["queries"][1]


def test_the_real_gmail_fetch_asks_for_metadata_only(monkeypatch):
    import googleapiclient.discovery as disc
    from agent_friday.services import gmail_api
    from agent_friday.services import relationship_memory as rm
    seen = {}

    class _Req:
        def __init__(self, r):
            self.r = r

    class _Svc:
        def users(self):
            return self

        def messages(self):
            return self

        def list(self, **kw):
            seen["list"] = kw
            return _Req({"messages": [{"id": "x1"}]})

    def fake_batch(svc, ids, fmt="full", headers=None, **kw):
        seen["fmt"], seen["headers"] = fmt, headers
        return {"x1": {"threadId": "t9", "internalDate": "1790000000000",
                       "snippet": "SECRET-SNIPPET", "labelIds": ["INBOX"],
                       "payload": {"headers": [{"name": "From", "value": "a@b.example.com"},
                                               {"name": "Subject", "value": "Hi"}]}}}, []

    monkeypatch.setattr(disc, "build", lambda *a, **k: _Svc(), raising=False)
    monkeypatch.setattr(gmail_api, "execute", lambda req: req.r)
    monkeypatch.setattr(gmail_api, "batch_get", fake_batch)
    monkeypatch.setattr(rm, "_credentials", lambda aid: object())
    got = rm._fetch_gmail({"id": "acct_a"}, "after:1")
    assert seen["fmt"] == "metadata" and seen["list"]["q"] == "after:1"
    assert got["messages"][0]["subject"] == "Hi" and got["truncated"] is False
    assert "SECRET" not in json.dumps(got)


# ── questions ────────────────────────────────────────────────────────────────

def test_person_timeline_resolves_a_name_to_addresses(rel):
    _seed_mail(rel)
    rel.sync(NOW)
    t = rel.person_timeline("Dana Okafor")
    assert t["found"] and t["emails"] == ["dana@acme.example.com"]
    assert t["count"] == 4 and t["meetings"] == 1
    assert t["emails_received"] == 1 and t["emails_sent"] == 1
    assert t["last_contact"] == _iso(4), "being copied on a message is contact"
    assert t["last_heard_from"] == _iso(10) and t["last_wrote_to"] == _iso(9)
    by_addr = rel.person_timeline("dana@acme.example.com")
    assert by_addr["count"] == 4
    assert rel.person_timeline("Nobody Known")["found"] is False


def test_person_timeline_uses_a_people_graph_record(rel):
    from agent_friday.people_graph import PeopleGraph
    PeopleGraph(friday_dir=rel.home).merge_profile(
        "Lee Park", emails=["lee@acme.example.com"], source="linkedin", company="Acme")
    _seed_mail(rel)
    rel.sync(NOW)
    t = rel.person_timeline("lee park")
    assert t["company"] == "Acme" and t["meetings"] == 1


def test_people_at_matches_domain_and_company(rel, monkeypatch):
    from agent_friday.people_graph import PeopleGraph
    # A stand-in webmail domain, so no real provider's addresses appear here.
    monkeypatch.setattr(rel, "FREE_MAIL", rel.FREE_MAIL | {"webmail.example.com"})
    PeopleGraph(friday_dir=rel.home).merge_profile(
        "Ari Stone", emails=["ari@webmail.example.com"], source="linkedin", company="Acme Corp")
    _seed_mail(rel)
    rel.state["gmail"].append(
        _msg("m6", "t6", "Pat <pat@webmail.example.com>", OWNER, "hello", 1))
    rel.sync(NOW)
    rows = rel.people_at("Acme")["people"]
    emails = [r["email"] for r in rows]
    assert "dana@acme.example.com" in emails and "lee@acme.example.com" in emails
    assert "ari@webmail.example.com" in emails, "a contact whose company matches is included"
    assert "pat@webmail.example.com" not in emails, "webmail domains say nothing about employers"
    assert "sam@globex.example.org" not in emails
    assert rows[0]["email"] == "dana@acme.example.com", "most recent contact first"
    dom = [r["email"] for r in rel.people_at("globex.example.org")["people"]]
    assert dom == ["sam@globex.example.org"]


# ── follow-ups ───────────────────────────────────────────────────────────────

def test_a_follow_up_is_local_and_reminds_once(rel):
    _seed_mail(rel)
    rel.sync(NOW)
    res = rel.set_follow_up("Sam Reyes", note="send the deck", in_days=2, now=NOW)
    assert res["ok"] and res["follow_up"]["emails"] == ["sam@globex.example.org"]
    assert rel.person_timeline("Sam Reyes")["follow_ups"][0]["note"] == "send the deck"
    rel.set_config({"sync_enabled": False})
    rel.tick(NOW + timedelta(days=1))
    assert not rel.state["pushed"], "reminded before it was due"
    rel.tick(NOW + timedelta(days=3))
    rel.tick(NOW + timedelta(days=4))
    reminders = [p for p in rel.state["pushed"] if p["kind"] == "follow_up"]
    assert len(reminders) == 1 and "Sam Reyes" in reminders[0]["title"]
    assert rel.complete_follow_up(res["follow_up"]["id"])
    assert rel.list_follow_ups() == []


def test_a_follow_up_with_a_bad_date_is_refused(rel):
    assert rel.set_follow_up("Sam", due="next-ish")["ok"] is False
    assert rel.set_follow_up("")["ok"] is False


# ── cold threads ─────────────────────────────────────────────────────────────

def _cold_mail(rel):
    rel.state["gmail"] = [
        # Owner wrote last, 6 days ago: cold at a 5-day threshold.
        _msg("c1", "ct1", "Rae <rae@initech.example.com>", OWNER, "Offer details", 8),
        _msg("c2", "ct1", OWNER, "Rae <rae@initech.example.com>", "Re: Offer details", 6,
             labels=["SENT"]),
        # Owner wrote, and they replied: not cold.
        _msg("c3", "ct2", OWNER, "Jo <jo@initech.example.com>", "Lunch?", 7, labels=["SENT"]),
        _msg("c4", "ct2", "Jo <jo@initech.example.com>", OWNER, "Re: Lunch?", 6),
        # Went cold a long time ago: outside the window, never nudged.
        _msg("c5", "ct3", OWNER, "Old <old@initech.example.com>", "Ancient", 29,
             labels=["SENT"]),
    ]


def test_cold_thread_nudges_are_off_by_default(rel):
    _cold_mail(rel)
    rel.tick(NOW)
    assert rel.get_config()["cold_after_days"] == 0
    assert not [p for p in rel.state["pushed"] if p["kind"] == "cold_thread"]


def test_cold_thread_nudges_follow_the_threshold(rel):
    _cold_mail(rel)
    rel.set_config({"cold_after_days": 5})
    rel.tick(NOW)
    nudges = [p for p in rel.state["pushed"] if p["kind"] == "cold_thread"]
    assert len(nudges) == 1, nudges
    assert nudges[0]["title"] == "No reply from Rae in 6 days"
    assert "Offer details" in nudges[0]["body"]
    rel.tick(NOW + timedelta(hours=1))
    assert len([p for p in rel.state["pushed"] if p["kind"] == "cold_thread"]) == 1, \
        "the same cold thread was nudged twice"
    rel.set_config({"cold_after_days": 7})
    assert rel.cold_threads(NOW) == [], "6 days is not cold at a 7-day threshold"


# ── LinkedIn import ──────────────────────────────────────────────────────────

LINKEDIN_CSV = (
    "﻿Notes:\n"
    "\"When exporting your connection data, you may notice that some of the email addresses are missing.\"\n"
    "\n"
    "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
    "Dana,Okafor,https://www.linkedin.com/in/dana-example,dana@acme.example.com,Acme,Engineer,01 Jan 2026\n"
    "Evil,Cell,javascript:alert(1),not-an-email,=HYPERLINK(\"http://x.example\"),+SUM(A1:A9),02 Jan 2026\n"
    ",,https://www.linkedin.com/in/nobody,,Nowhere,,\n"
    "Short,Row\n"
    "\n"
    "Dana,Okafor,,dana.o@home.example.net,Other Co,,\n"
)


def _people(rel):
    return json.loads((rel.home / "people_graph.json").read_text(encoding="utf-8"))["people"]


def test_linkedin_import_reads_the_export(rel):
    res = rel.import_linkedin_csv(LINKEDIN_CSV)
    assert res["ok"], res
    people = _people(rel)
    dana = people["dana_okafor"]
    assert dana["company"] == "Acme", "an import never overwrites an existing field"
    assert dana["emails"] == ["dana@acme.example.com", "dana.o@home.example.net"]
    assert dana["linkedin_url"] == "https://www.linkedin.com/in/dana-example"
    assert dana["sources"] == ["linkedin"]
    assert res["created"] == 3 and res["updated"] == 1
    assert res["skipped_count"] == 1 and res["skipped"][0]["reason"] == "no name"


def test_linkedin_cells_are_stored_as_text(rel):
    rel.import_linkedin_csv(LINKEDIN_CSV)
    evil = _people(rel)["evil_cell"]
    assert evil["company"] == '=HYPERLINK("http://x.example")'
    assert evil["position"] == "+SUM(A1:A9)"
    assert "linkedin_url" not in evil, "a non-LinkedIn URL is not kept as a profile link"
    assert "emails" not in evil, "an invalid address is not kept"


def test_a_file_that_is_not_the_export_is_refused(rel):
    res = rel.import_linkedin_csv("name,phone\nA,1\n")
    assert res["ok"] is False and "First Name" in res["error"]
    assert rel.import_linkedin_csv(None)["ok"] is False


def test_linkedin_import_skips_forgotten_people(rel):
    from agent_friday.services import forget_person as fp
    fp.forget("Dana Okafor")
    res = rel.import_linkedin_csv(LINKEDIN_CSV)
    assert res["skipped_forgotten"] == 2
    assert "dana_okafor" not in _people(rel)


# ── forgetting ───────────────────────────────────────────────────────────────

def test_forget_erases_the_timeline_and_follow_ups(rel):
    from agent_friday.services import forget_person as fp
    _seed_mail(rel)
    rel.sync(NOW)
    rel.set_follow_up("Dana Okafor", note="ping", now=NOW)
    rel.set_follow_up("Sam Reyes", note="deck", now=NOW)
    report = fp.find("Dana Okafor")
    assert report["relationships"] == {"interactions": 4, "follow_ups": 1}

    receipt = fp.forget("Dana Okafor")
    assert receipt["removed"]["timeline_removed"] == 2
    assert receipt["removed"]["timeline_edited"] == 2
    assert receipt["removed"]["follow_ups_removed"] == 1
    raw = (rel.home / "relationships" / "timeline.json").read_text(encoding="utf-8")
    assert "dana@acme.example.com" not in raw and "Dana" not in raw
    # Shared entries keep the other people.
    assert rel.person_timeline("Sam Reyes")["count"] == 1
    assert rel.person_timeline("Lee Park")["meetings"] == 1
    assert [f["person"] for f in rel.list_follow_ups()] == ["Sam Reyes"]
    # A later sync of the same mail does not bring her back.
    rel.sync(NOW + timedelta(hours=1))
    tl = json.loads((rel.home / "relationships" / "timeline.json").read_text(encoding="utf-8"))
    rel.state["gmail"] = [_msg("m9", "t9", "D <dana@acme.example.com>", OWNER, "new", 0.5)]
    rel.sync(NOW + timedelta(hours=2))
    tl2 = json.loads((rel.home / "relationships" / "timeline.json").read_text(encoding="utf-8"))
    assert "dana@acme.example.com" not in json.dumps(tl) + json.dumps(tl2)
    assert rel.set_follow_up("Dana Okafor")["ok"] is False


# ── Google Contacts writes ───────────────────────────────────────────────────

@pytest.fixture
def contacts(monkeypatch):
    from agent_friday.services import google_accounts as ga
    from agent_friday.services import google_contacts_write as gcw
    import googleapiclient.discovery as disc
    accounts = [{"id": "acct_a", "email": OWNER, "label": "main",
                 "scopes": [ga.CONTACTS_READ]}]
    made = []

    class _Svc:
        def people(self):
            return self

        def createContact(self, body=None):   # noqa: N802
            made.append(body)
            return type("E", (), {"execute": lambda s: {"resourceName": "people/c1"}})()

    monkeypatch.setattr(ga, "list_accounts", lambda: accounts)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(disc, "build", lambda *a, **k: _Svc(), raising=False)
    return gcw, ga, accounts, made


def test_a_contact_write_without_the_scope_is_refused_plainly(contacts):
    gcw, ga, accounts, made = contacts
    res = gcw.save_contact(name="Sam Reyes", email="sam@globex.example.org")
    assert res["ok"] is False and "read-only" in res["error"]
    assert made == [], "a save was attempted on a read-only account"


def test_a_contact_write_with_the_scope_saves(contacts):
    gcw, ga, accounts, made = contacts
    accounts[0]["scopes"] = [ga.CONTACTS_READ, ga.CONTACTS_RW]
    res = gcw.save_contact(name="Sam Reyes", email="sam@globex.example.org",
                           company="Globex", phone="+1 555 0100")
    assert res["ok"] and res["resource_name"] == "people/c1"
    assert made[0]["emailAddresses"] == [{"value": "sam@globex.example.org"}]
    assert gcw.save_contact(email="not an address")["ok"] is False


def test_the_contacts_scope_is_never_requested_by_default():
    from agent_friday.services import google_accounts as ga
    assert ga.CONTACTS_RW not in ga.GOOGLE_MULTI_SCOPES


def test_a_contact_write_waits_for_a_decision_off_chat(contacts, monkeypatch, tmp_path):
    import agent_friday.services.agent as agent
    from agent_friday.services import approvals, taint
    gcw, ga, accounts, made = contacts
    accounts[0]["scopes"] = [ga.CONTACTS_READ, ga.CONTACTS_RW]
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._hooks.reset_rate_limiter()
    taint.reset()
    ctx = {"authenticated": True, "is_background_task": True, "task_id": "t-contact"}
    agent._execute_tool("save_google_contact", {"name": "Sam Reyes",
                                                "email": "sam@globex.example.org"},
                        session_ctx=ctx)
    assert made == [], "a contact was written to Google with nobody deciding"
    taint.reset()


# ── governance classification ────────────────────────────────────────────────

def test_each_tool_has_its_class():
    from agent_friday.governance import action_gate as g
    import agent_friday.services.agent as agent
    for name in ("person_timeline", "people_at", "set_follow_up"):
        assert g.classify(name, {})[0] == g.INTERNAL, name
        assert name in agent.CLAUDE_TOOL_HANDLERS
    assert g.classify("save_google_contact", {})[0] == g.OUTWARD
    assert "save_google_contact" not in g.SELF_GATED
