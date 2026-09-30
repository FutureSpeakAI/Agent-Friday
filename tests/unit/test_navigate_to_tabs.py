"""navigate_to opens an item in its own Chrome tab, maximized, and says what
the tab confirmed; on the desktop it can fill the screen with the item.

  * The tab's address carries the same deep-link keys the desktop takes, max=1
    and an id the tab reports under once it shows the item.
  * NAV_OK only when the tab said so; a tab that has not reported is NAV_SENT.
  * Three more kinds: the mail a Gmail search finds, a news story, a creation.
"""
from __future__ import annotations

import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_friday.services import desktop_bus
from agent_friday.services import desktop_targets as dt


@pytest.fixture(autouse=True)
def _bus(monkeypatch):
    desktop_bus.reset()
    from agent_friday.services import local_address
    monkeypatch.setattr(local_address, "page_info",
                        lambda: {"origin": "https://agent.friday.test", "local": "http://127.0.0.1:3000"})
    yield
    desktop_bus.reset()


def test_the_tab_address_carries_the_item_max_and_the_report_id():
    url = dt.tab_url({"workspace": "messages", "thread_id": "t1", "account": "acct_work"}, "tab-1-ab")
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "agent.friday.test"
    assert parts.path == "/w/messages"
    q = parse_qs(parts.query)
    assert q == {"thread_id": ["t1"], "account": ["acct_work"], "max": ["1"], "nav": ["tab-1-ab"]}


def test_a_tab_that_reports_is_nav_ok(monkeypatch):
    opened = []

    def launch(url):
        opened.append(url)
        rid = parse_qs(urlsplit(url).query)["nav"][0]
        threading.Timer(0.1, desktop_bus.ack, args=(rid, {"opened": True, "matched": True})).start()
        return ""
    monkeypatch.setattr(dt, "launch_in_browser", launch)
    r = dt.open_on_desktop("mail_search", query="from:linkedin.com older_than:1m",
                           new_tab=True, maximize=True)
    assert r["status"] == "opened", r
    assert r["text"].startswith("NAV_OK:messages") and "new Chrome tab, maximized" in r["text"]
    assert parse_qs(urlsplit(opened[0]).query)["q"] == ["from:linkedin.com older_than:1m"]


def test_a_tab_that_has_not_reported_is_only_sent(monkeypatch):
    monkeypatch.setattr(dt, "launch_in_browser", lambda url: "")
    monkeypatch.setattr(dt, "TAB_ACK_TIMEOUT_S", 0.2)
    r = dt.open_on_desktop("mail_search", query="is:unread", new_tab=True)
    assert r["status"] == "sent" and r["text"].startswith("NAV_SENT:messages")


def test_a_browser_that_will_not_start_is_nav_fail(monkeypatch):
    monkeypatch.setattr(dt, "launch_in_browser", lambda url: "Chrome would not start (denied)")
    r = dt.open_on_desktop("mail_search", query="is:unread", new_tab=True)
    assert r["status"] == "failed" and "Chrome would not start" in r["text"]


def test_settings_do_not_open_in_a_tab(monkeypatch):
    monkeypatch.setattr(dt, "resolve", lambda *a, **k: {"ok": True, "label": "Settings",
                                                        "target": {"workspace": "settings"}})
    r = dt.open_on_desktop("settings", query="models", new_tab=True)
    assert r["status"] == "failed"


def test_maximize_on_the_desktop_asks_the_window_to_fill_the_screen(monkeypatch):
    sent = []
    monkeypatch.setattr(desktop_bus, "send",
                        lambda actions, verify=None, timeout=6.0: sent.append(actions) or
                        {"delivered": True, "acked": True, "ack": {"opened": True, "matched": True}})
    r = dt.open_on_desktop("mail_search", query="is:unread", maximize=True)
    assert r["status"] == "opened"
    assert sent[0][0]["max"] is True and sent[0][0]["q"] == "is:unread"


def test_a_creation_is_found_by_its_words(monkeypatch, tmp_path):
    from agent_friday import core
    (tmp_path / "moon-over-the-harbor.png").write_bytes(b"x")
    (tmp_path / "sunrise.png").write_bytes(b"x")
    monkeypatch.setattr(core, "CREATIONS_DIR", tmp_path)
    monkeypatch.setattr(core, "DAILY_CREATIONS_DIR", tmp_path / "none")
    r = dt.resolve("creation", query="the moon over the harbor picture")
    assert r["ok"] and r["target"] == {"workspace": "studio", "creation": "moon-over-the-harbor.png"}


def test_a_news_story_is_found_by_its_headline(monkeypatch):
    from agent_friday.services import news_engine as ne
    arts = [{"id": "a1", "title": "Rates hold steady at the Fed", "url": "https://n.example/fed", "source": "n.example"},
            {"id": "a2", "title": "Local team wins", "url": "https://n.example/team", "source": "n.example"}]
    monkeypatch.setattr(ne, "_iter_archive", lambda *a, **k: iter(arts))
    r = dt.resolve("news_article", query="the story about the fed rates")
    assert r["ok"] and r["target"]["article"] == "a1" and r["target"]["workspace"] == "news"
    by_url = dt.resolve("news_article", id="https://n.example/team")
    assert not by_url["ok"] or by_url["target"]["article"] == ne._news_url_hash("https://n.example/team")


def test_the_tool_passes_new_tab_and_max(monkeypatch):
    from agent_friday.services import agent
    seen = {}

    def fake(kind, **kw):
        seen.update(kw, kind=kind)
        return {"text": "NAV_OK:news — opened it"}
    monkeypatch.setattr(dt, "open_on_desktop", fake)
    agent._tool_navigate_to({"kind": "news_article", "query": "fed", "new_tab": True})
    assert seen["new_tab"] is True and seen["maximize"] is True
    agent._tool_navigate_to({"kind": "email", "query": "dana go-live"})
    assert seen["new_tab"] is False and seen["maximize"] is False


# ── By voice (docs/reference/voice-tool-contract.md) ─────────────────────────

def test_voice_declares_the_new_kinds_and_the_tab():
    from agent_friday.services import voice_engine as ve
    _name, desc, props, required = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "navigate_to")
    for kind in ("mail_search", "news_article", "creation"):
        assert kind in props["kind"][1], kind
    assert props["new_tab"][0] == "boolean" and props["max"][0] == "boolean"
    assert required == ["kind"]
    assert "NAV_SENT" in desc, "a tab still loading is not an open one; say so"


PRIVATE = ("Quarterly invoice", "Dana Reyes", "Invoice overdue", "secret-plan")


@pytest.fixture
def an_email(monkeypatch):
    monkeypatch.setattr(dt, "resolve", lambda *a, **k: {
        "ok": True, "label": "the email \u201cQuarterly invoice\u201d from Dana Reyes",
        "target": {"workspace": "messages", "thread_id": "t1"},
        "verify": {"workspace": "messages", "key": "thread_id", "value": "t1"},
        "also": ["Invoice overdue", "Dana Reyes: re invoice"]})


def _acks(monkeypatch, ack):
    monkeypatch.setattr(desktop_bus, "send", lambda actions, verify=None, timeout=6.0:
                        {"delivered": True, "acked": True, "ack": ack})


def test_a_cloud_voice_result_names_no_private_item(monkeypatch, an_email):
    _acks(monkeypatch, {"opened": True, "matched": True})
    quiet = dt.open_on_desktop("email", query="the invoice", name_items=False)["text"]
    assert quiet.startswith("NAV_OK:messages") and "the email" in quiet and "2 more" in quiet
    assert not [w for w in PRIVATE if w in quiet], quiet
    named = dt.open_on_desktop("email", query="the invoice")["text"]
    assert "Quarterly invoice" in named and "Invoice overdue" in named


def test_the_page_note_is_left_out_too(monkeypatch, an_email):
    _acks(monkeypatch, {"opened": True, "matched": False, "note": "it shows file=secret-plan.pdf"})
    quiet = dt.open_on_desktop("email", query="the invoice", name_items=False)["text"]
    assert quiet.startswith("NAV_PARTIAL") and not [w for w in PRIVATE if w in quiet], quiet


def test_closest_matches_are_counted_not_named(monkeypatch):
    monkeypatch.setattr(dt, "resolve", lambda *a, **k: {
        "ok": False, "reason": "no wiki page matches 'plan'",
        "candidates": ["secret-plan", "Dana Reyes"]})
    quiet = dt.open_on_desktop("wiki_page", query="plan", name_items=False)["text"]
    assert "2 near matches" in quiet and not [w for w in PRIVATE if w in quiet], quiet


def test_a_news_story_keeps_its_public_headline(monkeypatch):
    monkeypatch.setattr(dt, "resolve", lambda *a, **k: {
        "ok": True, "label": "the story \u201cRates hold steady\u201d (n.example)",
        "target": {"workspace": "news", "article": "a1"}})
    _acks(monkeypatch, {"opened": True, "matched": True})
    assert "Rates hold steady" in dt.open_on_desktop("news", query="rates", name_items=False)["text"]


def test_a_failed_tab_does_not_quote_its_address(monkeypatch, an_email):
    monkeypatch.setattr(dt, "launch_in_browser",
                        lambda url: "no browser would open it (%s)" % url)
    quiet = dt.open_on_desktop("email", query="the invoice", new_tab=True, name_items=False)["text"]
    assert quiet.startswith("NAV_FAIL") and "t1" not in quiet and "/w/" not in quiet, quiet


def test_spoken_to_the_cloud_the_handler_leaves_names_out(monkeypatch, an_email):
    """End to end: a voice-live call through _voice_tool_run and the real
    _execute_tool, against the same call from chat."""
    import agent_friday.privacy.vault_crypto as vc
    from agent_friday.services import agent
    from agent_friday.services import voice_engine as ve
    monkeypatch.setattr(vc, "sign_entry", lambda e, *a, **k: e, raising=False)
    _acks(monkeypatch, {"opened": True, "matched": True})
    spoken = ve._voice_tool_run("navigate_to", {"kind": "email", "query": "the invoice"},
                                lambda *a, **k: None, {"conversation_id": "c-voice"})
    assert spoken.startswith("NAV_OK:messages") and not [w for w in PRIVATE if w in spoken], spoken
    typed = agent._execute_tool("navigate_to", {"kind": "email", "query": "the invoice"},
                                session_ctx=agent.prepare_confirmation_ctx(
                                    "s-typed", "open the invoice email", {"authenticated": True}))
    assert "Quarterly invoice" in typed, typed
