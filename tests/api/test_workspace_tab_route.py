"""/w/<workspace> serves a workspace as its own browser tab: the same page and
token as '/', marked standalone before anything renders. It is the same
auth as every other page: a remote or proxied visitor without a session does
not get it. Settings stays in the desktop; anything that is not a plain
workspace name is refused."""
import pytest

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


def test_a_workspace_tab_is_the_page_marked_standalone(client):
    r = client.get("/w/news", environ_base=LOCAL)
    assert r.status_code == 200 and r.mimetype == "text/html"
    html = r.get_data(as_text=True)
    head = html[:html.index("</head>")]
    assert 'window.__FRIDAY_STANDALONE__="news"' in head
    assert "ws-standalone" in head
    assert "window.__FRIDAY_API_TOKEN=" in head              # same token handling as '/'


def test_the_desktop_itself_is_not_marked(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    assert "window.__FRIDAY_STANDALONE__=" not in html[:html.index("</head>")]


def test_settings_stays_in_the_desktop(client):
    r = client.get("/w/settings", environ_base=LOCAL)
    assert r.status_code == 302 and r.headers["Location"].endswith("/?workspace=settings")


@pytest.mark.parametrize("bad", ["News", "..", "a" * 40, "x%3Cscript%3E", "1abc"])
def test_only_plain_workspace_names(client, bad):
    assert client.get("/w/" + bad, environ_base=LOCAL).status_code == 404


@pytest.mark.parametrize("env,headers", [
    ({"REMOTE_ADDR": "203.0.113.9"}, {}),                                # another machine
    (LOCAL, {"X-Forwarded-For": "203.0.113.9"}),                         # through a tunnel
    (LOCAL, {"CF-Connecting-IP": "203.0.113.9", "Cf-Ray": "0-LHR"}),
])
def test_no_session_no_workspace_tab(app, env, headers):
    c = app.test_client()                                                # a fresh visitor
    r = c.get("/w/messages", environ_base=env, headers=headers)
    assert r.status_code != 200
    assert "__FRIDAY_API_TOKEN" not in r.get_data(as_text=True)
