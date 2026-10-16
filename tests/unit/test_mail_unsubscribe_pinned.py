"""The Messages unsubscribe button's one-click POST: the address judged public is
the address dialled, and only a 2xx answer is "done".

A redirect is not a confirmation, and following one would send Friday wherever
a mail header pointed; a name that answers a public address to the check and a
private one to the connection (DNS rebinding) must never be dialled.
"""
from __future__ import annotations

import contextlib

import pytest


@pytest.fixture
def mu(monkeypatch):
    from agent_friday.services import mail_unsubscribe
    headers = {"from": "News <news@list.example.test>",
               "list-unsubscribe": "<https://list.example.test/u/1>",
               "list-unsubscribe-post": "List-Unsubscribe=One-Click",
               "authentication-results*": ["mx.google.com; dkim=pass header.i=@list.example.test"]}
    monkeypatch.setattr(mail_unsubscribe, "_headers", lambda a, m: dict(headers))
    monkeypatch.setattr(mail_unsubscribe, "_public_host", lambda url: True)
    from agent_friday.services import web_safety

    @contextlib.contextmanager
    def passthrough(url):
        yield ["93.184.216.34"]
    monkeypatch.setattr(web_safety, "pinned", passthrough)
    return mail_unsubscribe


def _post_returning(monkeypatch, status, seen):
    import requests

    class R:
        status_code = status
    monkeypatch.setattr(requests, "post", lambda url, **kw: seen.append((url, kw)) or R())


def test_a_redirect_is_not_reported_as_unsubscribed(mu, monkeypatch):
    seen = []
    _post_returning(monkeypatch, 302, seen)
    out = mu.unsubscribe("acct", "m1")
    assert len(seen) == 1 and seen[0][1]["allow_redirects"] is False
    assert out["status"] == "error" and "somewhere else" in out["message"]


def test_the_post_is_dialled_through_the_pinned_resolution(mu, monkeypatch):
    from agent_friday.services import web_safety
    pinned = []

    @contextlib.contextmanager
    def fake_pinned(url):
        pinned.append(url)
        yield ["93.184.216.34"]
    monkeypatch.setattr(web_safety, "pinned", fake_pinned)
    seen = []
    _post_returning(monkeypatch, 200, seen)
    assert mu.unsubscribe("acct", "m1")["status"] == "done"
    assert pinned == ["https://list.example.test/u/1"]


def test_a_rebinding_answer_at_dial_time_is_refused(mu, monkeypatch):
    from agent_friday.services import web_safety

    @contextlib.contextmanager
    def refusing(url):
        raise web_safety.UnsafeURLError("refusing: resolves to 10.0.0.5")
        yield  # pragma: no cover
    monkeypatch.setattr(web_safety, "pinned", refusing)
    seen = []
    _post_returning(monkeypatch, 200, seen)
    out = mu.unsubscribe("acct", "m1")
    assert seen == [] and out["status"] == "error" and "not a public web address" in out["message"]


def test_one_click_needs_the_providers_dkim_pass_for_the_sender(mu, monkeypatch):
    seen = []
    _post_returning(monkeypatch, 200, seen)
    forged = {"from": "News <news@list.example.test>",
              "list-unsubscribe": "<https://list.example.test/u/1>",
              "list-unsubscribe-post": "List-Unsubscribe=One-Click",
              "authentication-results*": ["evil.example; dkim=pass header.d=list.example.test"]}
    monkeypatch.setattr(mu, "_headers", lambda a, m: dict(forged))
    out = mu.unsubscribe("acct", "m1")
    assert seen == [] and out["method"] == "link", "no verified sender, no request from Friday"


def test_the_post_takes_no_proxy_from_the_environment(mu, monkeypatch):
    seen = []
    _post_returning(monkeypatch, 200, seen)
    mu.unsubscribe("acct", "m1")
    assert seen[0][1]["proxies"] == {"http": None, "https": None, "all": None}


def test_the_post_goes_only_to_the_entry_the_dkim_rule_approved(mu, monkeypatch):
    """A header listing a third party's address first and the sender's own
    second: classify approves the second, so only the second is dialled."""
    from agent_friday.services import web_safety
    pinned = []

    @contextlib.contextmanager
    def fake_pinned(url):
        pinned.append(url)
        yield ["93.184.216.34"]
    monkeypatch.setattr(web_safety, "pinned", fake_pinned)
    two = {"from": "News <news@list.example.test>",
           "list-unsubscribe": "<https://tracker.example.net/u/9>, <https://list.example.test/u/1>",
           "list-unsubscribe-post": "List-Unsubscribe=One-Click",
           "authentication-results*": ["mx.google.com; dkim=pass header.i=@list.example.test"]}
    monkeypatch.setattr(mu, "_headers", lambda a, m: dict(two))
    seen = []
    _post_returning(monkeypatch, 200, seen)
    out = mu.unsubscribe("acct", "m1")
    assert [u for u, _ in seen] == ["https://list.example.test/u/1"]
    assert pinned == ["https://list.example.test/u/1"]
    assert out["status"] == "done" and out["host"] == "list.example.test"
    opt = mu.options("acct", "m1")
    assert opt["method"] == "one_click" and opt["host"] == "list.example.test"
