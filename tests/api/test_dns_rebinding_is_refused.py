"""A DNS-rebound page cannot read Friday, the session token included.

After a rebind the attacker's page is same-origin with *its own* name, which
resolves to loopback. The browser then sends `Sec-Fetch-Site: same-origin` and
no foreign `Origin`, so metadata alone cannot tell it from Friday's page. What
it cannot change is the `Host` header: it is the attacker's name. A request from
this machine whose Host is not a loopback name or one of Friday's own names is
refused before loopback trust, for reads as well as writes, and for the page
that carries the token.
"""
import pytest

from agent_friday.services import origin_gate

REBOUND = "http://rebind.evil.example:3000"
SAME = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty"}


def _req(client, path, base, method="GET", headers=None, **kw):
    return client.open(path, method=method, base_url=base, headers=headers or {},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"}, **kw)


@pytest.mark.parametrize("path", [
    "/api/session/token", "/api/vault/status", "/api/settings", "/",
])
def test_a_rebound_host_cannot_read_anything(client, path):
    r = _req(client, path, REBOUND, headers=SAME)
    assert r.status_code == 403, (path, r.status_code, r.get_data(as_text=True)[:120])
    body = r.get_json(silent=True) or {}
    assert body.get("code") == "foreign_host", body
    assert "rebind.evil.example" not in r.get_data(as_text=True)


def test_a_rebound_host_cannot_learn_the_token(client):
    from agent_friday import core
    r = _req(client, "/api/session/token", REBOUND, headers=SAME)
    assert core._current_api_token() not in r.get_data(as_text=True)


def test_a_rebound_host_cannot_write_even_without_browser_headers(client):
    r = _req(client, "/api/vault/passphrase", REBOUND, "POST", json={})
    assert r.status_code == 403
    assert (r.get_json(silent=True) or {}).get("code") == "foreign_host"


@pytest.mark.parametrize("base", [
    "http://127.0.0.1:3000", "http://localhost:3000", "http://[::1]:3000",
])
def test_loopback_names_still_reach_the_token(client, base):
    r = _req(client, "/api/session/token", base, headers=SAME)
    assert r.status_code == 200, r.get_data(as_text=True)[:120]
    assert r.get_json()["token"]


def test_friday_own_names_still_work(client):
    r = _req(client, "/api/session/token", "http://agent.friday", headers=SAME)
    assert r.status_code == 200


def test_the_health_probe_keeps_working(client):
    assert _req(client, "/api/health", "http://127.0.0.1:3000").status_code == 200


def test_host_refusal_unit():
    own = {"http://agent.friday", "https://agent.friday"}

    def ok(h, local=True):
        return origin_gate.host_refusal(h, is_local=local, own_origins=own)

    assert ok("127.0.0.1:3000") is None
    assert ok("localhost") is None
    assert ok("[::1]:3000") is None
    assert ok("agent.friday") is None
    assert ok("") is None
    assert ok("rebind.evil.example:3000") == origin_gate.HOST_REASON
    assert ok("127.0.0.1.evil.example") == origin_gate.HOST_REASON
    assert ok("evil.localhost.example") == origin_gate.HOST_REASON
    # A request that did not come from this machine is the remote-key gate's.
    assert ok("anything.example", local=False) is None
    assert origin_gate.reason_code(origin_gate.HOST_REASON) == "foreign_host"
