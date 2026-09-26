"""The outbound call sites that take a URL from somewhere else apply the
web_safety rules before any request is made, and the host comparisons that
decide how a URL is treated compare hosts, not substrings.

No test here touches the network: every transport is replaced by a recorder,
so a call site that skipped its check shows up as a recorded request.
"""
import pytest

from agent_friday.services import web_safety as ws


# ── news deep dive: the article URL comes from a feed, the page or the model ──

def test_article_fetch_refuses_loopback_without_requesting_it(monkeypatch):
    import requests

    from agent_friday.services import news_engine as ne
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(a) or None)
    with pytest.raises(ws.UnsafeURLError):
        ne._extract_article_text("http://127.0.0.1:3000/api/settings")
    assert calls == []


def test_deep_dive_reports_a_refused_url_as_a_fetch_failure(monkeypatch, tmp_path):
    import requests

    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "DEEP_DIVE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(a) or None)
    result, status = ne._deep_dive_article("http://169.254.169.254/latest/meta-data/",
                                           refresh=True)
    assert status == 502 and result["status"] == "error"
    assert calls == []


def test_google_news_snippet_rule_keys_on_the_host():
    from agent_friday.services import news_engine as ne
    junk = "View Full Coverage on Google News"
    real = ne._normalize_entry({"title": "T", "link": "https://news.google.com/rss/articles/x",
                                "summary": junk})
    lookalike = ne._normalize_entry({"title": "T",
                                     "link": "https://example.com/?via=news.google.com",
                                     "summary": junk})
    assert real["snippet"] == ""
    assert lookalike["snippet"] == junk


# ── DuckDuckGo unwrapping ─────────────────────────────────────────────────────

def test_ddg_redirector_is_unwrapped_only_on_duckduckgo():
    from agent_friday.services import web_search as W
    real = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa"
    lookalike = "https://evilduckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa"
    assert W._unwrap_ddg(real) == "https://example.org/a"
    assert W._unwrap_ddg(lookalike) == lookalike


# ── calendar resources ────────────────────────────────────────────────────────

def test_calendar_resources_are_skipped_by_domain_not_suffix():
    from agent_friday.services import relationship_memory as rm
    ev = {"start": "2026-09-01T10:00:00Z", "attendees": [
        {"email": "c_room1@resource.calendar.google.com"},
        {"email": "pat@notresource.calendar.google.com", "name": "Pat Doe"},
    ]}
    rec = rm._meeting_record("acct", ev, owner=set(), names={}, skip=set(), skip_names=set())
    assert rec is not None
    assert rec["people"] == ["pat@notresource.calendar.google.com"]


# ── federation: a peer endpoint must be a plain http(s) base URL ──────────────

def test_send_to_peer_refuses_a_file_endpoint_without_opening_it(monkeypatch):
    from agent_friday.services import federation_transport as ft
    opened = []
    monkeypatch.setattr(ft.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    res = ft.send_to_peer("file:///C:/Windows", {})
    assert res["ok"] is False and "refused_endpoint" in res["error"]
    assert opened == []


def test_request_job_refuses_a_file_endpoint_before_sending_or_spending(monkeypatch):
    from agent_friday.services import compute_client as cc
    from agent_friday.services import egress_gate
    sealed, opened = [], []
    monkeypatch.setattr(egress_gate, "seal_outbound", lambda p, **k: sealed.append(p) or p)
    monkeypatch.setattr(cc.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    with pytest.raises(ws.UnsafeURLError):
        cc.request_job("file:///C:/Windows", {"prompt": "hello"})
    assert sealed == [] and opened == []


def test_compute_send_route_answers_400_for_a_refused_endpoint(monkeypatch):
    from agent_friday.routes import compute as route
    monkeypatch.setattr(route.client.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("the request was made"))
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(route.compute_bp)
    with app.test_request_context("/api/compute/send", method="POST",
                                  json={"provider_endpoint": "ftp://peer.example",
                                        "task_spec": {"prompt": "hi"}}):
        resp = route.send_job.__wrapped__() if hasattr(route.send_job, "__wrapped__") \
            else route.send_job()
    body, status = resp
    assert status == 400 and body.get_json()["ok"] is False
