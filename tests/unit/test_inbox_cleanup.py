"""The inbox clean-up (first-run spec B3): find, classify, approve, act, verify.

Synthetic mail only: invented senders on example domains. No network: every
POST goes through a recording stand-in, and DNS answers are stubbed where a
test needs one.
"""
from __future__ import annotations

import contextlib
import json
import mailbox
import time

import pytest

GOOGLE_AR = "mx.google.com; dkim=pass header.i=@{d} header.s=s1 header.b=abc; spf=pass smtp.mailfrom={d}"


def _msg(ic, domain, *, ts, unread=True, spam=False, one_click=True, url_host=None,
         auth="google", msg_id="", mailto=False):
    host = url_host or ("unsub." + domain)
    lu = "<https://%s/u?t=1>" % host
    if mailto:
        lu = "<mailto:leave@%s>" % domain
    hdrs = {"from": ["Weekly Deals <news@%s>" % domain], "list-unsubscribe": [lu]}
    if one_click:
        hdrs["list-unsubscribe-post"] = ["List-Unsubscribe=One-Click"]
    if auth == "google":
        hdrs["authentication-results"] = [GOOGLE_AR.format(d=domain)]
    elif auth == "forged":
        hdrs["authentication-results"] = ["evil.example; dkim=pass header.d=%s" % domain]
    return ic.normalize(hdrs, msg_id=msg_id or "%s-%s" % (domain, ts), ts=ts, unread=unread, spam=spam)


@pytest.fixture
def ic(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.services import egress_gate
    from agent_friday.services import inbox_cleanup
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "egress-log.jsonl")
    monkeypatch.setattr(inbox_cleanup, "SLEEP", lambda s: None)
    monkeypatch.setattr(inbox_cleanup, "RUNNER", lambda fn: fn())
    monkeypatch.setattr(inbox_cleanup, "_notify", lambda *a, **k: None)
    return inbox_cleanup


def _scan(ic, monkeypatch, msgs):
    monkeypatch.setattr(ic, "PAGES", lambda source, cp: iter([("", msgs)]))
    return ic.scan({"kind": "mbox", "path": "fake.mbox"})


# ── reading the headers ──────────────────────────────────────────────────────

def test_only_the_providers_own_first_verdict_counts(ic):
    good = [GOOGLE_AR.format(d="shop.example"), "evil.example; dkim=pass header.d=bank.example"]
    assert ic.dkim_pass_domains(good) == {"shop.example"}
    # a sender-written header placed first, or a provider verdict lower down, counts for nothing
    assert ic.dkim_pass_domains(["evil.example; dkim=pass header.d=bank.example",
                                 GOOGLE_AR.format(d="shop.example")]) == set()
    assert ic.dkim_pass_domains(["mx.google.com; dkim=fail header.d=shop.example"]) == set()


def test_one_click_needs_dkim_the_rfc_header_and_a_matching_domain(ic):
    def row(**kw):
        s = {}
        ic.add_message(s, _msg(ic, "shop.example", ts=1, **kw))
        return ic.classify(s["shop.example"])
    assert row()["route"] == "one_click"
    assert row(one_click=False)["route"] == "site"
    assert row(url_host="tracker.other.example")["route"] == "site", "a URL on someone else's domain"
    assert row(url_host="127.0.0.1")["route"] == "site", "loopback is never offered"
    assert row(auth="forged")["route"] == "spam", "a verdict the sender wrote is no verdict"
    assert row(auth="none")["route"] == "spam"
    assert row(spam=True)["route"] == "spam"
    assert row(mailto=True)["route"] == "mailto"


def test_the_card_offers_senders_never_opened_lately_with_nothing_ticked(ic, monkeypatch):
    msgs = [_msg(ic, "shop.example", ts=1000 + i) for i in range(5)]
    msgs += [_msg(ic, "friendly.example", ts=1000 + i, unread=(i < 4)) for i in range(5)]
    msgs[-1] = _msg(ic, "friendly.example", ts=2000, unread=False)      # opened the newest one
    msgs += [_msg(ic, "spammy.example", ts=1000 + i, auth="none") for i in range(4)]
    msgs += [_msg(ic, "elsewhere.example", ts=1000 + i, url_host="x.tracker.example") for i in range(3)]
    _scan(ic, monkeypatch, msgs)
    rows = ic.view()["rows"]
    assert [r["id"] for r in rows["one_click"]] == ["shop.example"]
    assert rows["one_click"][0]["streak_words"] == "You haven't opened any of the last 5."
    assert [r["id"] for r in rows["spam"]] == ["spammy.example"]
    assert [r["id"] for r in rows["needs_you"]] == ["elsewhere.example"]
    assert rows["needs_you"][0]["url"].startswith("https://x.tracker.example/")
    for group in rows.values():
        assert all(r["ticked"] is False for r in group)
    assert "never read" not in json.dumps(rows)


def test_a_scan_interrupted_mid_way_resumes_from_its_checkpoint(ic, monkeypatch):
    pages = [("p2", [_msg(ic, "shop.example", ts=10 + i) for i in range(3)]),
             ("p3", [_msg(ic, "shop.example", ts=20 + i) for i in range(3)]),
             ("", [_msg(ic, "shop.example", ts=30 + i) for i in range(3)])]

    class Died(BaseException):
        pass

    def first_run(source, cp):
        yield pages[0]
        raise Died()
    monkeypatch.setattr(ic, "PAGES", first_run)
    src = {"kind": "mbox", "path": "fake.mbox"}
    with pytest.raises(Died):
        ic.scan(src)
    st = ic.load()
    assert st["job"]["state"] == "scanning" and st["job"]["checkpoint"] == "p2"
    seen = []

    def second_run(source, cp):
        seen.append(cp)
        start = {"p2": 1, "p3": 2}[cp]
        yield from pages[start:]
    monkeypatch.setattr(ic, "PAGES", second_run)
    job = ic.scan(src, resume=True)
    assert seen == ["p2"] and job["state"] == "ready" and job["scanned"] == 9
    assert ic.load()["senders"]["shop.example"]["count"] == 9


def test_the_state_is_encrypted_at_rest(ic, monkeypatch):
    _scan(ic, monkeypatch, [_msg(ic, "shop.example", ts=1 + i) for i in range(3)])
    raw = ic.state_path().read_bytes()
    assert b"shop.example" not in raw and b"Weekly Deals" not in raw


def test_a_takeout_mbox_is_read_headers_and_labels(ic, tmp_path):
    path = tmp_path / "All mail.mbox"
    box = mailbox.mbox(str(path))
    now = time.time()
    for i in range(4):
        m = mailbox.mboxMessage()
        m["From"] = "Weekly Deals <news@shop.example>"
        m["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(now - 86400 * (i + 1)))
        m["List-Unsubscribe"] = "<https://unsub.shop.example/u?t=%d>" % i
        m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        m["Authentication-Results"] = GOOGLE_AR.format(d="shop.example")
        m["X-Gmail-Labels"] = "Unread,Category Promotions,Inbox"
        m.set_payload("body text that is never read")
        box.add(m)
    box.flush()
    box.close()
    out = ic.start({"kind": "mbox", "path": str(path)})
    assert out["job"]["state"] == "ready"
    rows = ic.view()["rows"]["one_click"]
    assert [(r["id"], r["streak"]) for r in rows] == [("shop.example", 4)]


# ── acting ───────────────────────────────────────────────────────────────────

@pytest.fixture
def ready(ic, monkeypatch):
    from agent_friday.services import web_safety
    monkeypatch.setattr(web_safety, "resolve_all", lambda host: ["93.184.216.34"])
    msgs = []
    for d in ("shop.example", "deals.example", "news.example"):
        msgs += [_msg(ic, d, ts=1000 + i) for i in range(3)]
    msgs += [_msg(ic, "other.example", ts=1000 + i, url_host="x.tracker.example") for i in range(3)]
    _scan(ic, monkeypatch, msgs)
    posted = []
    statuses = {}

    def poster(url):
        posted.append(url)
        return statuses.get(url.split("/")[2], 200)
    monkeypatch.setattr(ic, "POSTER", poster)
    return {"posted": posted, "statuses": statuses}


def test_ticked_senders_each_get_one_post_a_receipt_and_an_egress_line(ic, ready, tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(ic, "SLEEP", lambda s: sleeps.append(s))
    out = ic.unsubscribe(["shop.example", "deals.example"], decided_by="owner click")
    assert [d["id"] for d in out["done"]] == ["shop.example", "deals.example"]
    assert ready["posted"] == ["https://unsub.shop.example/u?t=1", "https://unsub.deals.example/u?t=1"]
    assert sleeps == [ic.RATE_S]
    events = [r["event"] for r in ic.receipts()]
    assert events.count("unsubscribed") == 2
    log = [json.loads(x) for x in (tmp_path / "egress-log.jsonl").read_text().splitlines()]
    assert [e["provider"] for e in log] == ["unsub.shop.example", "unsub.deals.example"]
    assert all("one-click" in e["field"] for e in log)


def test_a_sender_not_offered_one_click_is_never_contacted(ic, ready):
    out = ic.unsubscribe(["other.example", "nobody.example"], decided_by="owner click")
    assert ready["posted"] == [] and out["done"] == []
    assert {x["id"] for x in out["not_done"]} == {"other.example", "nobody.example"}


def test_a_private_or_rebinding_answer_is_refused_before_any_request(ic, ready, monkeypatch):
    from agent_friday.services import web_safety
    monkeypatch.setattr(web_safety, "resolve_all", lambda host: ["10.0.0.5"])
    out = ic.unsubscribe(["shop.example"], decided_by="owner click")
    assert ready["posted"] == [] and out["done"] == []
    assert "isn't a public web address" in out["not_done"][0]["why"]


def test_a_redirect_is_not_a_confirmation(ic, ready):
    ready["statuses"]["unsub.shop.example"] = 302
    out = ic.unsubscribe(["shop.example"], decided_by="owner click")
    assert out["done"] == [] and "somewhere else" in out["not_done"][0]["why"]
    assert "shop.example" not in ic.load()["unsubscribed"]


def test_the_post_is_the_rfc_one_with_no_cookies_and_no_redirects(ic, monkeypatch):
    import requests

    from agent_friday.services import web_safety
    seen = {}
    pinned = []

    @contextlib.contextmanager
    def fake_pinned(url):
        pinned.append(url)
        yield ["93.184.216.34"]

    class Resp:
        status_code = 200

        def close(self):
            seen["closed"] = True

    def fake_post(url, **kw):
        seen.update(kw, url=url)
        return Resp()
    monkeypatch.setattr(web_safety, "pinned", fake_pinned)
    monkeypatch.setattr(requests, "post", fake_post)
    assert ic._post_one_click("https://unsub.shop.example/u") == 200
    assert pinned == ["https://unsub.shop.example/u"]
    assert seen["data"] == "List-Unsubscribe=One-Click" and seen["allow_redirects"] is False
    assert "cookies" not in seen and "Cookie" not in seen["headers"] and seen["closed"]


def test_found_keeps_confirmed_and_possible_apart_and_checks_after_14_days(ic, ready, monkeypatch):
    ic.unsubscribe(["shop.example", "deals.example"], decided_by="owner click")
    f = ic.found()
    assert f["confirmed"] == [] and len(f["possible"]) == 2
    assert "total" not in json.dumps(f)
    st = ic.load()
    old = time.time() - (ic.VERIFY_AFTER_DAYS + 1) * 86400
    for rec in st["unsubscribed"].values():
        rec["at"] = old
    ic.save(st)
    later = [_msg(ic, "deals.example", ts=time.time() - 3600 + i) for i in range(3)]
    early = [_msg(ic, "shop.example", ts=old - 10 + i) for i in range(3)]
    _scan(ic, monkeypatch, later + early)
    f = ic.found()
    assert [x["id"] for x in f["confirmed"]] == ["shop.example"]
    assert [x["id"] for x in f["still_sending"]] == ["deals.example"]
    assert f["possible"] == []


def test_fridays_own_request_becomes_a_card_and_sends_nothing(ic, ready, monkeypatch):
    from agent_friday.governance import action_gate
    raised = []

    class Verdict:
        action = "card"
        reason = ""

    def gate(action, detail, **kw):
        raised.append((action, detail, kw))
        return Verdict()
    monkeypatch.setattr(action_gate, "authorize_external", gate)
    out = ic.propose(["shop.example", "deals.example"], requested_by="friday")
    assert out["status"] == "pending_approval" and ready["posted"] == []
    action, detail, kw = raised[0]
    assert detail["senders"] == ["deals.example", "shop.example"]
    assert "Weekly Deals (shop.example)" in kw["action_description"]


# ── the routes, the first-run offer, the voice path ─────────────────────────

def _client(monkeypatch):
    from flask import Flask

    import agent_friday.core as core
    from agent_friday.routes import inbox_cleanup as route
    monkeypatch.setattr(core, "_is_local_request", lambda: True)
    app = Flask(__name__)
    app.register_blueprint(route.inbox_cleanup_bp)
    return app.test_client()


def test_the_owners_tick_acts_and_anything_else_asks(ic, ready, monkeypatch):
    calls = []
    monkeypatch.setattr(ic, "propose", lambda ids, **kw: calls.append(ids) or {"status": "pending_approval"})
    c = _client(monkeypatch)
    r = c.post("/api/inbox-cleanup/unsubscribe", json={"senders": ["shop.example"], "requested_by": "friday"})
    assert r.status_code == 202 and calls == [["shop.example"]] and ready["posted"] == []
    r = c.post("/api/inbox-cleanup/unsubscribe", json={"senders": ["shop.example"], "requested_by": "ui:inbox-cleanup"})
    assert r.status_code == 200 and r.get_json()["done"][0]["id"] == "shop.example"
    r = c.post("/api/inbox-cleanup/start", json={"source": {"kind": "mbox", "path": "x.mbox"}})
    assert r.status_code == 403, "reading mail starts from the owner's own click"


def test_the_offer_waits_for_an_inbox_and_starts_when_there_is_one(ic, monkeypatch):
    monkeypatch.setattr(ic, "gmail_accounts", lambda: [])
    out = ic.offer_starter({})
    assert out["state"] == "waiting_for_email" and "Takeout" in out["text"]
    monkeypatch.setattr(ic, "gmail_accounts", lambda: [{"id": "acct-1", "email": "me@example.com"}])
    monkeypatch.setattr(ic, "PAGES", lambda source, cp: iter([("", [])]))
    out = ic.offer_starter({})
    assert out["state"] == "running" and ic.load()["job"]["state"] == "ready"
    assert ic.load()["job"]["source"] == {"kind": "gmail", "account_id": "acct-1"}


def test_the_router_opens_the_clean_up_board_by_voice():
    from agent_friday.services import laya_router as lr
    for phrase in ("open the inbox clean up", "show me my email clean-up"):
        route = lr._tier0(phrase)
        assert route is not None and route.tool == "navigate_to", phrase
        assert route.args["workspace"] == "messages" and route.args["section"] == "clean up"
    # the plain workspace rule is untouched
    assert lr._tier0("open messages").args.get("section") is None
