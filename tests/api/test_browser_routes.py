"""Settings controls for Friday's browser: local only, and only her own profile."""
import pytest

from agent_friday.services import browser_session


@pytest.fixture
def browser_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(browser_session, "_SESSION", None)
    return tmp_path


def _make_profile(home):
    p = home / browser_session.PROFILE_DIRNAME
    (p / "Default").mkdir(parents=True)
    (p / browser_session.MARKER).write_text("x", encoding="utf-8")
    (p / "Default" / "Cookies").write_bytes(b"cookie-jar")
    return p


def test_the_blueprint_is_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert {"/api/browser/status", "/api/browser/close",
            "/api/browser/profile/clear"} <= rules


def test_status_and_clear(client, browser_home):
    p = _make_profile(browser_home)
    st = client.get("/api/browser/status").get_json()
    assert st["ok"] and st["profile_exists"] and not st["running"]
    r = client.post("/api/browser/profile/clear", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["cleared"] and not p.exists()


def test_a_form_post_is_refused(client, browser_home):
    p = _make_profile(browser_home)
    r = client.post("/api/browser/profile/clear", data={"x": "1"})
    assert r.status_code == 415 and p.exists()


def test_a_folder_friday_did_not_make_is_not_deleted(client, browser_home):
    p = browser_home / browser_session.PROFILE_DIRNAME
    p.mkdir()
    (p / "Local State").write_text("{}", encoding="utf-8")
    r = client.post("/api/browser/profile/clear", json={})
    assert r.status_code == 400 and (p / "Local State").exists()


@pytest.mark.parametrize("headers", [
    {"CF-Connecting-IP": "203.0.113.9"},
    {"X-Forwarded-For": "203.0.113.9"},
])
def test_a_tunnelled_request_cannot_clear_it(app, browser_home, headers):
    p = _make_profile(browser_home)
    c = app.test_client()
    r = c.post("/api/browser/profile/clear", json={}, headers=headers,
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403) and p.exists()


def test_an_authenticated_remote_session_is_still_refused(app, browser_home, monkeypatch):
    from agent_friday.routes import browser
    p = _make_profile(browser_home)
    monkeypatch.setattr(browser, "_is_local_request", lambda: False)
    r = app.test_client().post("/api/browser/profile/clear", json={})
    assert r.status_code == 403 and p.exists()
