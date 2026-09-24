"""Settings -> Local address, over HTTP (routes/local_address.py).

  * Every page carries the proven address (window.__FRIDAY_LOCAL_ADDRESS__), so
    tab links can be built inside the click with nothing to wait for.
  * Nothing that changes anything answers a tunnel, another machine, or a
    request without the page's own token -- above all not the two steps
    Windows asks about.
  * Under a test, those two steps fail closed without touching Windows.

No test here resolves or probes the real agent.friday: name resolution is
stubbed, so nothing reaches a Friday already running on this machine.
"""
from __future__ import annotations

import json
import re
import time

import pytest

import agent_friday.core as core
from agent_friday.services import local_address as la

CLOUDFLARED = {
    "CF-Connecting-IP": "203.0.113.9",
    "CF-Ray": "0000000000000000-LHR",
    "X-Forwarded-For": "203.0.113.9",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "random-words-1234.trycloudflare.com",
}


@pytest.fixture(autouse=True)
def _nothing_leaves_the_test(monkeypatch):
    monkeypatch.setattr(la, "resolves_to_loopback", lambda host: False)
    monkeypatch.setattr(la, "probe", lambda *a, **k: pytest.fail("probed a real address"))
    monkeypatch.setitem(la._STATUS, "value", None)
    yield
    la.stop_listeners()


def _token():
    return {"X-Friday-Token": core._current_api_token()}


def _page_info(html: str) -> dict:
    m = re.search(r"window\.__FRIDAY_LOCAL_ADDRESS__=(\{.*?\});</script>", html)
    assert m, "the page does not carry Friday's local address"
    return json.loads(m.group(1))


def test_ping_names_this_process(client):
    r = client.get("/api/local-address/ping")
    assert r.status_code == 200
    assert r.get_json() == {"friday": True, "instance": la.INSTANCE_ID}


@pytest.mark.parametrize("path", ["/", "/w/news"])
def test_every_page_carries_the_local_address(client, monkeypatch, path):
    monkeypatch.setattr(la, "page_info", lambda: {"host": "agent.friday",
                                                  "origin": "https://agent.friday", "secure": True})
    info = _page_info(client.get(path).get_data(as_text=True))
    assert info == {"host": "agent.friday", "origin": "https://agent.friday", "secure": True}


def test_the_injected_address_cannot_break_out_of_its_script(client, monkeypatch):
    monkeypatch.setattr(la, "page_info", lambda: {"host": "</script><script>alert(1)</script>",
                                                  "origin": None, "secure": False})
    html = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)" not in html
    assert "\\u003c/script>" in html


def test_status_describes_the_address(client):
    r = client.get("/api/local-address?refresh=1")
    assert r.status_code == 200
    st = r.get_json()
    assert st["host"].startswith("agent.")
    assert st["resolves"] is False and st["preferred_origin"] is None
    assert st["oauth"]["returns_to"].startswith("http://localhost:")


CHANGES = [
    ("/api/local-address/host", {"host": "agent.jarvis"}),
    ("/api/local-address/serve", {"on": False}),
    ("/api/local-address/hosts-entry", {"add": True}),
    ("/api/local-address/trust", {}),
    ("/api/local-address/untrust", {}),
    ("/api/local-address/google", {"use": False}),
]


@pytest.mark.parametrize("path, body", CHANGES)
def test_nothing_changes_without_the_pages_own_token(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 403
    assert "did not come from Friday's page" in r.get_json()["message"]


@pytest.mark.parametrize("path, body", CHANGES)
def test_nothing_changes_through_a_tunnel_even_with_the_token(app, path, body):
    fresh = app.test_client()
    r = fresh.post(path, json=body, headers=dict(CLOUDFLARED, **_token()),
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403)


def test_a_bad_address_is_refused_in_plain_words(client):
    r = client.post("/api/local-address/host", json={"host": "agent.com"}, headers=_token())
    assert r.status_code == 400
    assert "real internet domain ending" in r.get_json()["message"]


def test_a_custom_address_is_saved_and_cleared(client):
    try:
        r = client.post("/api/local-address/host", json={"host": "agent.jarvis"}, headers=_token())
        assert r.status_code == 200 and r.get_json()["status"]["host"] == "agent.jarvis"
        assert core._load_settings()["local_address"]["host"] == "agent.jarvis"
        assert la.local_hosts() == {"agent.jarvis"}
    finally:
        r = client.post("/api/local-address/host", json={"host": ""}, headers=_token())
    assert r.status_code == 200 and r.get_json()["status"]["source"] == "agent name"


def test_the_trust_step_under_a_test_fails_closed(client, monkeypatch):
    """The real route, the real job, the real runner -- which refuses."""
    monkeypatch.setattr(la, "refresh_in_background", lambda *a, **k: None)
    from agent_friday.services import local_ca
    local_ca.ensure(la.state_dir(), la.configured_host())
    r = client.post("/api/local-address/trust", headers=_token())
    assert r.status_code == 200 and r.get_json()["ok"] is True
    for _ in range(100):
        job = la.job_status()
        if job["state"] != "waiting":
            break
        time.sleep(0.05)
    assert job["kind"] == "trust" and job["state"] == "failed"
    assert "never changes" in job["message"]


def test_google_sign_in_stays_on_localhost_until_the_secure_address_works(client):
    r = client.post("/api/local-address/google", json={"use": True, "confirmed": True},
                    headers=_token())
    assert r.status_code == 400
    assert "secure address has to work first" in r.get_json()["message"]
    assert not (core._load_settings().get("google_oauth") or {}).get("redirect_base_override")


def test_a_desktop_google_client_can_never_move_off_localhost(client, monkeypatch):
    monkeypatch.setattr(la, "status", lambda refresh=False: {
        "secure": True, "host": "agent.friday", "ports": {"https": 443, "http": 80}})
    monkeypatch.setattr(la, "_client_type", lambda: "installed")
    r = client.post("/api/local-address/google", json={"use": True, "confirmed": True},
                    headers=_token())
    assert r.status_code == 400 and "Desktop client" in r.get_json()["message"]


def test_a_web_google_client_moves_only_when_the_person_confirms(client, monkeypatch):
    monkeypatch.setattr(la, "status", lambda refresh=False: {
        "secure": True, "host": "agent.friday", "ports": {"https": 443, "http": 80}})
    monkeypatch.setattr(la, "_client_type", lambda: "web")
    try:
        r = client.post("/api/local-address/google", json={"use": True}, headers=_token())
        assert r.status_code == 400
        r = client.post("/api/local-address/google", json={"use": True, "confirmed": True},
                        headers=_token())
        assert r.status_code == 200
        assert core._load_settings()["google_oauth"]["redirect_base_override"] == "https://agent.friday"
    finally:
        client.post("/api/local-address/google", json={"use": False}, headers=_token())
    assert core._load_settings()["google_oauth"]["redirect_base_override"] == ""
