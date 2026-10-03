"""Every workspace route requires login and carries a CSP
(workspace-ecosystem.md Phase 1, pulled forward by the salon §4.9.1).

The global before_request covers loopback today, so this is defence in depth:
these routes will soon carry executable bundles, and a route that accepts
one must sit on the right side of the line on its own. A request from a
non-loopback address with no remote key configured is refused, and a bad
workspace id is a plain 400, never a file under another name.
"""
from __future__ import annotations

import pytest

REMOTE = {"REMOTE_ADDR": "10.0.0.5"}


def _workspace_rules(app):
    return sorted({(r.rule, m) for r in app.url_map.iter_rules() if r.rule.startswith("/api/workspace/")
                   for m in r.methods if m in ("GET", "POST")})


def test_there_are_workspace_routes_to_guard(app):
    assert len(_workspace_rules(app)) >= 8


@pytest.mark.parametrize("rule,method", [
    ("/api/workspace/customizations", "GET"),
    ("/api/workspace/news/chat", "GET"),
    ("/api/workspace/news/chat", "POST"),
    ("/api/workspace/news/chat/clear", "POST"),
    ("/api/workspace/news/revert", "POST"),
    ("/api/workspace/news/reset", "POST"),
    ("/api/workspace/news/history", "GET"),
    ("/api/workspace/news/undo", "POST"),
    ("/api/workspace/news/restore-as-of", "POST"),
])
def test_a_remote_caller_without_a_key_is_refused(client, rule, method):
    r = client.open(rule, method=method, json={} if method == "POST" else None, environ_base=REMOTE)
    # 403 with no remote key configured (fail closed), 401 when one is.
    assert r.status_code in (401, 403), (rule, method, r.status_code)


def test_loopback_still_works_and_carries_a_csp(client):
    r = client.get("/api/workspace/customizations")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers.get("Content-Security-Policy", "")
    r = client.get("/api/workspace/news/history")
    assert r.status_code == 200 and "Content-Security-Policy" in r.headers


@pytest.mark.parametrize("bad", ["my.workspace", "News", "a%20b", "..%2Fnews"])
def test_a_bad_workspace_id_is_a_400_on_every_route(client, bad):
    for rule, method in (("/api/workspace/%s/chat", "GET"), ("/api/workspace/%s/history", "GET"),
                         ("/api/workspace/%s/undo", "POST"), ("/api/workspace/%s/reset", "POST")):
        r = client.open(rule % bad, method=method, json={} if method == "POST" else None)
        assert r.status_code in (400, 404), (rule % bad, r.status_code, r.data[:120])
