"""A page in another origin cannot make Friday act, even though loopback is trusted.

Loopback auto-authenticates, so the owner's browser is "logged in" to every
route. A web page, or a dev-server preview on 127.0.0.1:5173, can make that
browser send a CORS-simple POST. The gate in `check_auth` refuses state-changing
requests and WebSocket upgrades whose browser-set metadata (`Sec-Fetch-Site`,
`Origin`, neither of which page script can forge or strip) says they did not
come from Friday's own page. Requests with neither header (tray, CLI,
server-to-server) and reads are unchanged.
"""

import pytest

from agent_friday.services import origin_gate

POST_PATH = "/api/notifications/dismiss"
BASE = "http://127.0.0.1:3000"


def _refused(resp) -> bool:
    if resp.status_code != 403:
        return False
    body = resp.get_json(silent=True) or {}
    return body.get("error") == origin_gate.REFUSAL_REASON


def _post(client, headers=None, path=POST_PATH, method="POST", base=BASE):
    return client.open(path, method=method, base_url=base, json={},
                       headers=headers or {},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"})


# --- refused ------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_cross_site_state_change_is_refused(client, method):
    r = _post(client, {"Sec-Fetch-Site": "cross-site",
                       "Origin": "https://evil.example"}, method=method)
    assert _refused(r), (r.status_code, r.get_data(as_text=True)[:200])


def test_cross_site_fetch_metadata_alone_is_refused(client):
    assert _refused(_post(client, {"Sec-Fetch-Site": "cross-site"}))


def test_foreign_origin_without_fetch_metadata_is_refused(client):
    assert _refused(_post(client, {"Origin": "https://evil.example"}))


def test_null_origin_is_refused(client):
    assert _refused(_post(client, {"Origin": "null"}))


def test_dev_server_preview_on_another_port_is_refused(client):
    r = _post(client, {"Sec-Fetch-Site": "same-site",
                       "Origin": "http://127.0.0.1:5173"})
    assert _refused(r)
    assert _refused(_post(client, {"Origin": "http://localhost:5173"}))


def test_same_site_without_origin_is_refused(client):
    assert _refused(_post(client, {"Sec-Fetch-Site": "same-site"}))


def test_rebinding_page_addressing_friday_by_its_own_name_is_refused(client):
    """Origin and Host agree (the attacker's name), but that name is not ours."""
    r = client.open(POST_PATH, method="POST", base_url="http://evil.example:3000",
                    json={}, headers={"Origin": "http://evil.example:3000"},
                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert _refused(r)


def test_websocket_upgrade_from_a_foreign_origin_is_refused(client):
    r = client.get("/ws/live", base_url=BASE,
                   headers={"Upgrade": "websocket", "Connection": "Upgrade",
                            "Origin": "https://evil.example",
                            "Sec-Fetch-Site": "cross-site"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert _refused(r)


def test_websocket_upgrade_from_another_local_port_is_refused(client):
    r = client.get("/ws/live", base_url=BASE,
                   headers={"Upgrade": "websocket", "Connection": "Upgrade",
                            "Origin": "http://127.0.0.1:5173"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert _refused(r)


# --- allowed ------------------------------------------------------------

def test_same_origin_post_is_allowed(client):
    r = _post(client, {"Sec-Fetch-Site": "same-origin", "Origin": BASE})
    assert not _refused(r)


def test_localhost_name_on_the_served_port_is_allowed(client):
    r = _post(client, {"Origin": "http://localhost:3000"})
    assert not _refused(r)


def test_no_browser_headers_is_allowed(client):
    """CLI, tray and server-to-server loopback calls carry neither header."""
    assert not _refused(_post(client))


def test_address_bar_navigation_metadata_is_allowed(client):
    assert not _refused(_post(client, {"Sec-Fetch-Site": "none"}))


def test_friday_page_with_its_session_token_is_allowed(client):
    from agent_friday import core
    r = _post(client, {"X-Friday-Token": core._current_api_token(),
                       "Sec-Fetch-Site": "same-site"})
    assert not _refused(r)


def test_a_wrong_token_does_not_unlock_a_cross_site_request(client):
    r = _post(client, {"X-Friday-Token": "not-the-token",
                       "Sec-Fetch-Site": "cross-site"})
    assert _refused(r)


def test_local_address_proxy_origin_is_allowed(client, monkeypatch):
    from agent_friday.services import local_address as la
    monkeypatch.setattr(la, "configured_host", lambda settings=None: "agent.jarvis")
    for origin in ("https://agent.friday", "https://agent.jarvis"):
        r = _post(client, {"Origin": origin, "Sec-Fetch-Site": "same-origin",
                           "X-Forwarded-For": "127.0.0.1",
                           "X-Forwarded-Proto": "https",
                           "X-Forwarded-Host": origin.split("//")[1]},
                  base="http://127.0.0.1:3000")
        assert not _refused(r), origin


def test_another_agent_name_is_not_the_local_address(client, monkeypatch):
    from agent_friday.services import local_address as la
    monkeypatch.setattr(la, "configured_host", lambda settings=None: "agent.jarvis")
    assert _refused(_post(client, {"Origin": "https://agent.smith"}))


def test_websocket_upgrade_from_friday_page_passes_the_gate(client):
    # Past the gate the handler needs a real socket, which the test client
    # cannot supply: reaching it (RuntimeError) is the pass condition.
    try:
        r = client.get("/ws/live", base_url=BASE,
                       headers={"Upgrade": "websocket", "Connection": "Upgrade",
                                "Origin": BASE, "Sec-Fetch-Site": "same-origin"},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"})
    except RuntimeError:
        return
    assert not _refused(r)


def test_reads_are_unchanged(client):
    r = client.get("/api/health", base_url=BASE,
                   headers={"Sec-Fetch-Site": "cross-site",
                            "Origin": "https://evil.example"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert not _refused(r)


# --- the tunnel keeps its own protection -------------------------------

def test_a_tunnelled_cross_site_post_is_refused_before_auth_is_consulted(client):
    tunnel = {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9",
              "X-Forwarded-Host": "random-words.trycloudflare.com",
              "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert _refused(_post(client, tunnel))


def test_a_tunnelled_same_origin_post_still_meets_the_remote_key_gate(client):
    """The gate lets it through to auth, which still refuses it (no key)."""
    tunnel = {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9",
              "X-Forwarded-Host": "random-words.trycloudflare.com",
              "Origin": "https://random-words.trycloudflare.com",
              "Sec-Fetch-Site": "same-origin"}
    r = client.open(POST_PATH, method="POST", json={}, headers=tunnel,
                    base_url="https://random-words.trycloudflare.com",
                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert not _refused(r)
    assert r.status_code in (401, 403)
