"""A proxied request is never the local user.

A tunnel such as

    cloudflared tunnel --url http://localhost:3000

publishes the whole Friday API on a public `trycloudflare.com` URL.

cloudflared connects to Friday from **loopback**, so every request arriving
through the tunnel has `request.remote_addr == '127.0.0.1'`. If
`_is_local_request()` looked at nothing else, `_loopback_trusted()` would return
True and the request would be auto-authenticated as the machine's owner --
`@login_required` included. Anyone holding the URL would be signed in as the
owner, with the approval endpoints among them. Remote connections, including
tunnelled ones, must always see the login screen.

Disqualifying a request for merely CARRYING a forwarding header is too strict:
it makes Friday's own loopback proxy (`https://agent.friday`, `ops/Caddyfile`)
look remote and puts a login screen in front of the local user. The rule is
"forwarded from somewhere this machine cannot vouch for": the peer must be
loopback, no `CF-*` header may be present, every claimed address in the chain
must be loopback, and a forwarded host must be a local alias. See
test_a_loopback_proxy_is_still_the_local_user.py.

Every trust decision in the app -- the HTTP decorator, the settings gate and the
voice WebSocket -- funnels through `_is_local_request()`, so this is one place.
"""

import pytest

import agent_friday.core as core

#: Headers that only ever appear when something forwarded the request. Any one of
#: them means the peer address belongs to a proxy, not to the client.
PROXY_HEADERS = [
    {"X-Forwarded-For": "203.0.113.9"},
    # NOT here any more: {"X-Forwarded-For": "127.0.0.1"}.
    #
    # Pinning a loopback-looking chain as remote, on the grounds that the
    # chain is attacker-supplied, is too strict: it locks the local user out
    # when browsing `https://agent.friday`, Friday's OWN loopback proxy
    # (`ops/Caddyfile`), because Caddy sends exactly that header.
    #
    # A spoof is now defeated by two other checks instead: any `CF-*` header
    # is disqualifying on its own, and EVERY address in the chain must be
    # loopback -- Cloudflare appends the real client address, which a client
    # cannot strip. Both are exercised below and in
    # test_a_loopback_proxy_is_still_the_local_user.py.
    {"CF-Connecting-IP": "203.0.113.9",
     "X-Forwarded-For": "127.0.0.1"},          # spoofed chain + a CF header
    {"X-Forwarded-For": "127.0.0.1, 203.0.113.9"},  # a real hop in the chain
    {"X-Real-IP": "203.0.113.9"},
    {"Forwarded": "for=203.0.113.9;proto=https"},
    {"X-Forwarded-Host": "example.trycloudflare.com"},
    {"X-Forwarded-Proto": "https"},
    {"CF-Connecting-IP": "203.0.113.9"},
    {"Cf-Ray": "0000000000000000-LHR"},
    {"Cf-Visitor": '{"scheme":"https"}'},
    {"Cf-Ipcountry": "GB"},
]


def _ctx(app, headers=None):
    """A request context whose PEER IS LOOPBACK, which is the whole point.

    `test_request_context` leaves REMOTE_ADDR unset, so without this every case
    below would be "not local" because the address was None -- and the proxied
    cases would pass for entirely the wrong reason while the bug sat untouched.
    Setting it to 127.0.0.1 reproduces what cloudflared actually presents.
    """
    return app.test_request_context(
        "/api/residency/status", headers=headers or {},
        environ_base={"REMOTE_ADDR": "127.0.0.1"})


@pytest.fixture
def flask_app():
    from agent_friday.server import app
    return app


def test_a_plain_loopback_request_is_still_local(flask_app):
    """The fix must not break the ordinary case: Friday opened on this machine
    still skips the login screen."""
    with _ctx(flask_app):
        assert core._is_local_request() is True
        assert core._loopback_trusted() is True


@pytest.mark.parametrize("headers", PROXY_HEADERS,
                         ids=[next(iter(h)) + "=" + next(iter(h.values()))[:18]
                              for h in PROXY_HEADERS])
def test_a_proxied_request_is_not_local(flask_app, headers):
    """Each of these means "someone forwarded me". None may read as local, even
    though the peer address genuinely is 127.0.0.1 -- that is the proxy's
    address, not the client's.

    The spoofed `X-Forwarded-For: 127.0.0.1` case matters: a rule that trusted a
    loopback-looking forwarded chain would hand the tunnel back its bypass.
    """
    with _ctx(flask_app, headers):
        assert core._is_local_request() is False, \
            "%s was treated as a local request" % headers
        assert core._loopback_trusted() is False


def test_cloudflare_headers_are_caught_by_prefix(flask_app):
    """Cloudflare adds a family of Cf-* headers and can add more. Match the
    prefix rather than a list that has to be kept up to date."""
    with _ctx(flask_app, {"Cf-Some-Future-Header": "x"}):
        assert core._is_local_request() is False


def test_a_protected_route_challenges_a_proxied_request(flask_app, client):
    """End to end through the real decorator, read-only.

    A FRESH client per proxied request, which is what a remote caller actually
    is. Reusing one client hides the bug: when a remote key is configured, the
    first LOCAL request mints `session['authenticated']` and the test client
    keeps that cookie, so the next request is authorised by its own session
    rather than by locality. That is correct behaviour -- a browser that logged
    in stays logged in -- and a shared client would return a pass-shaped 200.
    """
    assert client.get("/api/residency/status").status_code == 200, \
        "local access should still work"

    for headers in ({"CF-Connecting-IP": "203.0.113.9",
                     "Cf-Ray": "0000000000000000-LHR"},
                    {"X-Forwarded-For": "203.0.113.9"}):
        fresh = flask_app.test_client()          # no session, like a stranger
        r = fresh.get("/api/residency/status", headers=headers)
        assert r.status_code in (401, 403), (
            "a proxied request with no session was served protected data "
            "(%s -> HTTP %s)" % (headers, r.status_code))


def test_the_trust_switch_still_turns_local_auth_on(monkeypatch, flask_app):
    """FRIDAY_TRUST_LOOPBACK=0 must keep working: it is the existing way to
    require a login even on this machine, and the fix must not shadow it."""
    monkeypatch.setattr(core, "FRIDAY_TRUST_LOOPBACK", False)
    with _ctx(flask_app):
        assert core._is_local_request() is True     # still local...
        assert core._loopback_trusted() is False    # ...but not auto-trusted


def test_a_non_loopback_peer_is_still_not_local(flask_app):
    """The pre-existing rule, pinned so the rewrite cannot lose it."""
    with flask_app.test_request_context(
            "/api/residency/status", environ_base={"REMOTE_ADDR": "203.0.113.9"}):
        assert core._is_local_request() is False
