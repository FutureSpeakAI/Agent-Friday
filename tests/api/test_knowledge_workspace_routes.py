"""Knowledge holds the wiki's pages. The server side of that:

  * the old Wiki tab address lands on Knowledge's Pages view, still pointing
    at the same page, behind the same auth as every tab;
  * a saved page tells open Knowledge views to refresh (`wiki_changed`).

Reading and writing the pages themselves: test_wiki_page_guards.py.
"""
import queue
from urllib.parse import parse_qs, urlsplit

import pytest

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


# ── the old tab address ──────────────────────────────────────────────────

def _redirect(client, url):
    r = client.get(url, environ_base=LOCAL)
    assert r.status_code == 302, r.status_code
    loc = urlsplit(r.headers["Location"])
    return loc.path, {k: v[0] for k, v in parse_qs(loc.query).items()}


def test_the_old_wiki_tab_opens_knowledge_on_its_pages_view(client):
    assert _redirect(client, "/w/wiki") == ("/w/knowledge", {"view": "pages"})


def test_the_old_wiki_tab_keeps_the_page_it_pointed_at(client):
    path, q = _redirect(client, "/w/wiki?path=brain/bootstrap.md")
    assert path == "/w/knowledge"
    assert q == {"view": "pages", "path": "brain/bootstrap.md"}


def test_an_explicit_view_survives_the_redirect(client):
    assert _redirect(client, "/w/wiki?view=split")[1] == {"view": "split"}


def test_the_knowledge_tab_is_served_standalone(client):
    r = client.get("/w/knowledge", environ_base=LOCAL)
    assert r.status_code == 200
    head = r.get_data(as_text=True).split("</head>")[0]
    assert 'window.__FRIDAY_STANDALONE__="knowledge"' in head


@pytest.mark.parametrize("env,headers", [
    ({"REMOTE_ADDR": "203.0.113.9"}, {}),
    (LOCAL, {"X-Forwarded-For": "203.0.113.9"}),
])
def test_the_old_address_needs_a_session_like_any_tab(app, env, headers):
    c = app.test_client()                                                # a fresh visitor
    r = c.get("/w/wiki", environ_base=env, headers=headers)
    assert r.status_code != 200
    assert "__FRIDAY_API_TOKEN" not in r.get_data(as_text=True)
    # Refused by the auth gate, before the alias could point anywhere.
    assert not r.headers.get("Location", "").startswith("/w/")


# ── live refresh ─────────────────────────────────────────────────────────

def test_saving_a_page_tells_open_knowledge_views_to_refresh(client, server_module):
    import agent_friday.routes.knowledge_graph as kg_routes
    q = queue.Queue(maxsize=100)
    with kg_routes._sub_lock:
        kg_routes._subscribers.append(q)
    try:
        r = client.put("/api/wiki/edit", json={"file": "kwtest/live.md", "content": "# Live\n"})
        assert r.status_code == 200
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert any(e.get("type") == "wiki_changed" for e in events), events
    finally:
        with kg_routes._sub_lock:
            kg_routes._subscribers.remove(q)
        (server_module.WIKI_DIR / "kwtest" / "live.md").unlink(missing_ok=True)
