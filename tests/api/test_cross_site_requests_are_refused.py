"""A browser request that changes state needs the session token, even on loopback.

Loopback auto-authenticates, so the owner's browser is "logged in" to every
route. A web page, or a dev-server preview on 127.0.0.1:5173, can make that
browser send a CORS-simple POST. The gate in `check_auth` requires Friday's
session token on every browser-originated state-changing request and WebSocket
upgrade (browser-ness is read from `Sec-Fetch-Site` / `Origin`, neither of
which page script can forge or strip), and names the cause when it refuses.
Requests with neither header (tray, CLI, server-to-server) and reads are
unchanged.
"""

import pytest

from agent_friday.services import origin_gate

POST_PATH = "/api/notifications/dismiss"
BASE = "http://127.0.0.1:3000"
_ANY_GATE_REASON = (origin_gate.REFUSAL_REASON, origin_gate.TOKEN_REASON,
                    origin_gate.GATE_ERROR_REASON)


def _refused(resp, reason=None) -> bool:
    if resp.status_code != 403:
        return False
    body = resp.get_json(silent=True) or {}
    return body.get("error") in ((reason,) if reason else _ANY_GATE_REASON)


def _token():
    from agent_friday import core
    return core._current_api_token()


def _post(client, headers=None, path=POST_PATH, method="POST", base=BASE):
    return client.open(path, method=method, base_url=base, json={},
                       headers=headers or {},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"})


def _ws(client, path="/ws/live", **headers):
    hdrs = {"Upgrade": "websocket", "Connection": "Upgrade"}
    hdrs.update(headers)
    return client.get(path, base_url=BASE, headers=hdrs,
                      environ_base={"REMOTE_ADDR": "127.0.0.1"})


# --- refused: another site ---------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_cross_site_state_change_is_refused(client, method):
    r = _post(client, {"Sec-Fetch-Site": "cross-site",
                       "Origin": "https://evil.example"}, method=method)
    assert _refused(r, origin_gate.REFUSAL_REASON), (
        r.status_code, r.get_data(as_text=True)[:200])


def test_cross_site_fetch_metadata_alone_is_refused(client):
    assert _refused(_post(client, {"Sec-Fetch-Site": "cross-site"}),
                    origin_gate.REFUSAL_REASON)


def test_foreign_origin_without_fetch_metadata_is_refused(client):
    assert _refused(_post(client, {"Origin": "https://evil.example"}),
                    origin_gate.REFUSAL_REASON)


def test_null_origin_is_refused(client):
    assert _refused(_post(client, {"Origin": "null"}), origin_gate.REFUSAL_REASON)


def test_dev_server_preview_on_another_port_is_refused(client):
    r = _post(client, {"Sec-Fetch-Site": "same-site",
                       "Origin": "http://127.0.0.1:5173"})
    assert _refused(r, origin_gate.REFUSAL_REASON)
    assert _refused(_post(client, {"Origin": "http://localhost:5173"}),
                    origin_gate.REFUSAL_REASON)


def test_same_site_without_origin_is_refused(client):
    assert _refused(_post(client, {"Sec-Fetch-Site": "same-site"}),
                    origin_gate.REFUSAL_REASON)


def test_rebinding_page_addressing_friday_by_its_own_name_is_refused(client):
    """Origin and Host agree (the attacker's name), but that name is not ours."""
    r = client.open(POST_PATH, method="POST", base_url="http://evil.example:3000",
                    json={}, headers={"Origin": "http://evil.example:3000"},
                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
    # The host gate names the address first; the origin gate would also refuse.
    assert _refused(r, origin_gate.HOST_REASON)


def test_websocket_upgrade_from_a_foreign_origin_is_refused(client):
    r = _ws(client, Origin="https://evil.example", **{"Sec-Fetch-Site": "cross-site"})
    assert _refused(r, origin_gate.REFUSAL_REASON)


def test_websocket_upgrade_from_another_local_port_is_refused(client):
    r = _ws(client, Origin="http://127.0.0.1:5173")
    assert _refused(r, origin_gate.REFUSAL_REASON)


def test_a_valid_token_does_not_unlock_a_foreign_origin_socket_by_accident(client):
    """Only the exact token does; a guess from a foreign page is still refused."""
    r = _ws(client, "/ws/live?t=guess", Origin="https://evil.example")
    assert _refused(r, origin_gate.REFUSAL_REASON)


def test_local_address_name_on_another_port_is_a_dev_server_not_friday(client, monkeypatch):
    """agent.friday:5173 is some other server; only the proxy's ports count."""
    from agent_friday.services import local_address as la
    monkeypatch.setattr(la, "configured_host", lambda settings=None: "agent.jarvis")
    for origin in ("http://agent.friday:5173", "https://agent.jarvis:5173",
                   "http://agent.jarvis:8080"):
        r = _post(client, {"Origin": origin, "Sec-Fetch-Site": "same-site"})
        assert _refused(r, origin_gate.REFUSAL_REASON), origin


def test_another_agent_name_is_not_the_local_address(client, monkeypatch):
    from agent_friday.services import local_address as la
    monkeypatch.setattr(la, "configured_host", lambda settings=None: "agent.jarvis")
    assert _refused(_post(client, {"Origin": "https://agent.smith"}),
                    origin_gate.REFUSAL_REASON)


def test_own_origins_are_the_proxy_ports_and_no_others():
    from agent_friday.services import local_address as la
    own = la.own_origins()
    assert "https://agent.friday" in own and "http://agent.friday" in own
    assert not any(":5173" in o for o in own)


# --- refused: Friday's own origin without the token --------------------

def test_same_origin_post_without_the_token_is_refused_for_the_token(client):
    r = _post(client, {"Sec-Fetch-Site": "same-origin", "Origin": BASE})
    assert _refused(r, origin_gate.TOKEN_REASON), (
        r.status_code, r.get_data(as_text=True)[:200])


def test_every_unsafe_method_needs_the_token_from_a_browser(client):
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        h = {"Sec-Fetch-Site": "same-origin", "Origin": BASE}
        assert _refused(_post(client, h, method=m), origin_gate.TOKEN_REASON), m
        assert not _refused(_post(client, dict(h, **{"X-Friday-Token": _token()}),
                                  method=m)), m


def test_localhost_name_on_the_served_port_needs_the_token(client):
    h = {"Origin": "http://localhost:3000"}
    assert _refused(_post(client, h), origin_gate.TOKEN_REASON)
    assert not _refused(_post(client, dict(h, **{"X-Friday-Token": _token()})))


def test_navigation_metadata_is_still_browser_metadata(client):
    """`none` marks a browser; without the token it is not the exempt caller."""
    assert _refused(_post(client, {"Sec-Fetch-Site": "none"}), origin_gate.TOKEN_REASON)


def test_a_wrong_token_is_refused(client):
    r = _post(client, {"X-Friday-Token": "not-the-token",
                       "Sec-Fetch-Site": "same-origin", "Origin": BASE})
    assert _refused(r, origin_gate.TOKEN_REASON)
    r = _post(client, {"X-Friday-Token": "not-the-token",
                       "Sec-Fetch-Site": "cross-site"})
    assert _refused(r, origin_gate.REFUSAL_REASON)


def test_a_token_in_the_query_does_not_unlock_a_plain_post(client):
    """`?t=` is the WebSocket carrier only; an ordinary request needs the header."""
    r = _post(client, {"Origin": BASE}, path=POST_PATH + "?t=" + _token())
    assert _refused(r, origin_gate.TOKEN_REASON)


def test_websocket_upgrade_from_friday_page_without_the_token_is_refused(client):
    r = _ws(client, Origin=BASE, **{"Sec-Fetch-Site": "same-origin"})
    assert _refused(r, origin_gate.TOKEN_REASON)


def test_a_wrong_websocket_token_is_refused(client):
    r = _ws(client, "/ws/live?t=not-the-token", Origin=BASE,
            **{"Sec-Fetch-Site": "same-origin"})
    assert _refused(r, origin_gate.TOKEN_REASON)


# --- allowed -----------------------------------------------------------

def test_same_origin_post_with_the_token_is_allowed(client):
    r = _post(client, {"Sec-Fetch-Site": "same-origin", "Origin": BASE,
                       "X-Friday-Token": _token()})
    assert not _refused(r)


def test_no_browser_headers_is_allowed(client):
    """CLI, tray and server-to-server loopback calls carry neither header."""
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert not _refused(_post(client, method=m)), m


def test_local_address_proxy_origin_is_allowed(client, monkeypatch):
    from agent_friday.services import local_address as la
    monkeypatch.setattr(la, "configured_host", lambda settings=None: "agent.jarvis")
    for origin in ("https://agent.friday", "https://agent.jarvis"):
        r = _post(client, {"Origin": origin, "Sec-Fetch-Site": "same-origin",
                           "X-Friday-Token": _token(),
                           "X-Forwarded-For": "127.0.0.1",
                           "X-Forwarded-Proto": "https",
                           "X-Forwarded-Host": origin.split("//")[1]},
                  base="http://127.0.0.1:3000")
        assert not _refused(r), origin


def test_websocket_upgrade_from_friday_page_with_the_token_passes_the_gate(client):
    # Past the gate the handler needs a real socket, which the test client
    # cannot supply: reaching it (RuntimeError) is the pass condition.
    try:
        r = _ws(client, "/ws/live?t=" + _token(), Origin=BASE,
                **{"Sec-Fetch-Site": "same-origin"})
    except RuntimeError:
        return
    assert not _refused(r)


def test_websocket_without_browser_headers_passes_the_gate(client):
    try:
        r = _ws(client)
    except RuntimeError:
        return
    assert not _refused(r)


def test_reads_are_unchanged(client):
    r = client.get("/api/health", base_url=BASE,
                   headers={"Sec-Fetch-Site": "cross-site",
                            "Origin": "https://evil.example"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert not _refused(r)


# --- fails closed ------------------------------------------------------

def test_the_gate_fails_closed(client, monkeypatch):
    """If the gate cannot be evaluated, a guarded request is refused."""
    def boom(*a, **k):
        raise RuntimeError("gate broke")
    monkeypatch.setattr(origin_gate, "refusal", boom)
    assert _refused(_post(client), origin_gate.GATE_ERROR_REASON)
    assert _refused(_ws(client), origin_gate.GATE_ERROR_REASON)
    # Reads stay readable even when the gate is broken.
    g = client.get("/api/health", base_url=BASE,
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert not _refused(g)


def test_the_gate_fails_closed_when_its_inputs_cannot_be_read(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("inputs broke")
    monkeypatch.setattr("agent_friday.services.local_address.own_origins", boom)
    assert _refused(_post(client), origin_gate.GATE_ERROR_REASON)


# --- the tunnel keeps its own protection -------------------------------

def test_a_tunnelled_cross_site_post_is_refused_before_auth_is_consulted(client):
    tunnel = {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9",
              "X-Forwarded-Host": "random-words.trycloudflare.com",
              "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert _refused(_post(client, tunnel), origin_gate.REFUSAL_REASON)


def test_a_tunnelled_post_without_the_token_gains_no_loopback_trust(client):
    """The proxy peer is on loopback, but the forwarded chain says otherwise."""
    tunnel = {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9",
              "X-Forwarded-Host": "random-words.trycloudflare.com",
              "Origin": "https://random-words.trycloudflare.com",
              "Sec-Fetch-Site": "same-origin"}
    r = client.open(POST_PATH, method="POST", json={}, headers=tunnel,
                    base_url="https://random-words.trycloudflare.com",
                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403)
