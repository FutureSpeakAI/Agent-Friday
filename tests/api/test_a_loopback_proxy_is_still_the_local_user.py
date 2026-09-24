"""Friday's own loopback proxy is still the local user; a tunnel is not.

Regression found 2026-09-24, minutes after the tunnel fix shipped. Stephen:
"I'm being presented with an authentication screen when loading Agent Friday. I
have not set a username or password so this is not correct."

He browses `https://agent.friday`, which is Friday's OWN presentation proxy:
`ops/Caddyfile`, `bind 127.0.0.1 ::1`, `reverse_proxy 127.0.0.1:3000`. Caddy adds
`X-Forwarded-For: 127.0.0.1`, `X-Forwarded-Proto: https` and
`X-Forwarded-Host: agent.friday`. The tunnel fix treated the mere PRESENCE of a
forwarding header as proof of remoteness, so his own machine started asking him
for a password that only exists because a launcher sets `FRIDAY_PASSWORD`.

Measured live before this fix: `https://agent.friday/` -> 302 to /login and
`https://agent.friday/api/health` -> 401, while `http://127.0.0.1:3000/` -> 200.

The distinction that actually matters is not "was this forwarded" but "was it
forwarded from somewhere on this machine". So a forwarded request is local only
when ALL of these hold:

  * the immediate peer is loopback (the proxy is running here), AND
  * no CDN/tunnel header is present -- any `CF-*` still means remote, AND
  * every claimed client address in the chain is itself loopback, AND
  * the forwarded host, if given, is a local alias rather than a public name.

That keeps the hole shut. cloudflared cannot satisfy it: Cloudflare's edge sets
`CF-Connecting-IP` and appends the real client IP to `X-Forwarded-For`, and the
client cannot strip either. Two independent checks, not one.

The previously-pinned case "a spoofed `X-Forwarded-For: 127.0.0.1` is treated
exactly like any other" is deliberately changed here -- see
`test_proxied_requests_are_never_local.py`, updated in the same commit. A spoof is
now defeated by the CF check and the whole-chain check rather than by refusing
every loopback claim, because refusing them all locked the user out of his own
machine.
"""

import io

import pytest

import agent_friday.core as core


#: Exactly what Caddy sends for `https://agent.friday`, verified against the
#: running proxy.
CADDY = {
    "X-Forwarded-For": "127.0.0.1",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "agent.friday",
}

#: The shape of a cloudflared quick tunnel: CF-* headers the client cannot
#: remove, plus a real public address in the chain.
CLOUDFLARED = {
    "CF-Connecting-IP": "203.0.113.9",
    "CF-Ray": "0000000000000000-LHR",
    "CF-Visitor": '{"scheme":"https"}',
    "X-Forwarded-For": "203.0.113.9",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "random-words-1234.trycloudflare.com",
}

LOCAL_PROXY_SHAPES = [
    pytest.param(CADDY, id="caddy-https-agent.friday"),
    pytest.param({"X-Forwarded-For": "127.0.0.1"}, id="xff-loopback-only"),
    pytest.param({"X-Forwarded-For": "::1"}, id="xff-ipv6-loopback"),
    pytest.param({"X-Forwarded-For": "127.0.0.1, ::1"}, id="xff-chain-all-loopback"),
    pytest.param({"X-Real-IP": "127.0.0.1"}, id="x-real-ip-loopback"),
    pytest.param({"Forwarded": "for=127.0.0.1;proto=https;host=agent.friday"},
                 id="rfc7239-loopback"),
    pytest.param({"X-Forwarded-For": "127.0.0.1",
                  "X-Forwarded-Host": "localhost:3000"}, id="xff-host-localhost"),
]

REMOTE_SHAPES = [
    pytest.param(CLOUDFLARED, id="cloudflared-quick-tunnel"),
    pytest.param({"CF-Connecting-IP": "203.0.113.9",
                  "X-Forwarded-For": "127.0.0.1"},
                 id="cf-header-with-spoofed-loopback-chain"),
    pytest.param({"Cf-Some-Future-Header": "x",
                  "X-Forwarded-For": "127.0.0.1"},
                 id="unknown-cf-header-still-remote"),
    pytest.param({"X-Forwarded-For": "127.0.0.1, 203.0.113.9"},
                 id="loopback-then-public-in-chain"),
    pytest.param({"X-Forwarded-For": "203.0.113.9, 127.0.0.1"},
                 id="public-then-loopback-in-chain"),
    pytest.param({"X-Forwarded-For": "10.0.0.5"}, id="private-lan-address"),
    pytest.param({"X-Forwarded-For": "127.0.0.1",
                  "X-Forwarded-Host": "abc.trycloudflare.com"},
                 id="loopback-chain-but-public-forwarded-host"),
    pytest.param({"Forwarded": "for=203.0.113.9;proto=https"},
                 id="rfc7239-public"),
    pytest.param({"True-Client-IP": "203.0.113.9"}, id="true-client-ip-public"),
]


def _ctx(app, headers=None, peer="127.0.0.1"):
    """A request context whose peer is loopback, which is the whole point.

    `test_request_context` leaves REMOTE_ADDR unset, so without `environ_base`
    every case would be "not local" because the address was None, and the remote
    cases would pass for entirely the wrong reason.
    """
    return app.test_request_context(
        "/api/residency/status", headers=headers or {},
        environ_base={"REMOTE_ADDR": peer})


@pytest.fixture
def flask_app():
    from agent_friday.server import app
    return app


# ─────────────────────────────────────────────────────────────────────────────
# The regression: Friday's own proxy must read as local.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers", LOCAL_PROXY_SHAPES)
def test_a_loopback_proxy_is_the_local_user(flask_app, headers):
    with _ctx(flask_app, headers):
        assert core._is_local_request() is True, (
            "%s was treated as remote, which is what put a login screen in "
            "front of Stephen on his own machine" % headers)
        assert core._loopback_trusted() is True


def test_the_local_user_is_not_asked_to_log_in_through_his_own_proxy(
        flask_app, client):
    """End to end through the real decorator, with a FRESH client so no session
    cookie can answer for locality."""
    fresh = flask_app.test_client()
    r = fresh.get("/api/residency/status", headers=CADDY,
                  environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200, (
        "a request through agent.friday got HTTP %s; measured 401 live before "
        "this fix" % r.status_code)


# ─────────────────────────────────────────────────────────────────────────────
# The hole stays shut.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers", REMOTE_SHAPES)
def test_a_tunnelled_or_foreign_request_is_never_local(flask_app, headers):
    with _ctx(flask_app, headers):
        assert core._is_local_request() is False, (
            "%s was treated as the local user" % headers)
        assert core._loopback_trusted() is False


@pytest.mark.parametrize("headers", REMOTE_SHAPES)
def test_a_tunnelled_request_is_challenged_end_to_end(flask_app, headers):
    """A fresh client per request, which is what a remote caller is."""
    fresh = flask_app.test_client()
    r = fresh.get("/api/residency/status", headers=headers,
                  environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403), (
        "%s was served protected data (HTTP %s)" % (headers, r.status_code))


# ─────────────────────────────────────────────────────────────────────────────
# The address follows the agent's name (services/local_address): an agent
# named JARVIS lives at agent.jarvis, and its own proxy must read as local
# exactly as agent.friday does -- while a tunnel stays remote whatever host it
# claims.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def named_jarvis(monkeypatch):
    from agent_friday.services import local_address as la
    monkeypatch.setattr(la, "configured_host", lambda settings=None: "agent.jarvis")


def test_the_configured_address_through_a_local_proxy_is_the_local_user(flask_app, named_jarvis):
    with _ctx(flask_app, dict(CADDY, **{"X-Forwarded-Host": "agent.jarvis"})):
        assert core._is_local_request() is True


@pytest.mark.parametrize("headers", [
    pytest.param(dict(CLOUDFLARED, **{"X-Forwarded-Host": "agent.jarvis"}),
                 id="cloudflared-claiming-the-configured-host"),
    pytest.param({"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "127.0.0.1",
                  "X-Forwarded-Host": "agent.jarvis"}, id="cf-header-loopback-chain"),
    pytest.param({"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Host": "agent.jarvis"},
                 id="public-client-configured-host"),
    pytest.param(dict(CADDY, **{"X-Forwarded-Host": "agent.smith"}),
                 id="a-name-that-is-not-the-configured-one"),
])
def test_the_configured_address_does_not_make_a_tunnel_local(flask_app, named_jarvis, headers):
    with _ctx(flask_app, headers):
        assert core._is_local_request() is False


# ─────────────────────────────────────────────────────────────────────────────
# The pre-existing rules this must not lose.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_plain_loopback_request_is_still_local(flask_app):
    with _ctx(flask_app):
        assert core._is_local_request() is True


def test_a_non_loopback_peer_is_never_local(flask_app):
    """Even with a perfectly loopback-looking forwarded chain: if the proxy
    itself is not on this machine, none of it is trustworthy."""
    with _ctx(flask_app, CADDY, peer="203.0.113.9"):
        assert core._is_local_request() is False


def test_the_trust_switch_still_forces_a_login(monkeypatch, flask_app):
    monkeypatch.setattr(core, "FRIDAY_TRUST_LOOPBACK", False)
    with _ctx(flask_app, CADDY):
        assert core._is_local_request() is True      # still local...
        assert core._loopback_trusted() is False     # ...but not auto-trusted

# ─────────────────────────────────────────────────────────────────────────────
# Google OAuth must keep working through the alias.
#
# The redirect_uri is pinned to loopback on purpose: Google's secure-response
# policy rejects ANY plain-HTTP non-loopback redirect_uri, so a hosts-file alias
# like http://agent.friday/... fails every time. That means a consent STARTED on
# agent.friday finishes on localhost:3000 -- a different origin, whose cookie the
# browser will not send. Pinned here because it constrains two things at once:
# the callbacks must not sit behind the login wall, and the redirect must not be
# "improved" into following request.host_url.
# ─────────────────────────────────────────────────────────────────────────────

OAUTH_CALLBACKS = ("/api/google/auth/callback", "/api/google/accounts/callback")


@pytest.mark.parametrize("path", OAUTH_CALLBACKS)
@pytest.mark.parametrize("headers", [
    pytest.param({}, id="direct-loopback"),
    pytest.param(CADDY, id="through-the-local-proxy"),
])
def test_the_oauth_callback_is_never_behind_the_login_wall(
        flask_app, path, headers):
    """Google sends the browser here. A 401 would end the connect flow with a
    password prompt the user never set.

    Called with no `code`, so each callback answers with its OWN 4xx error. What
    matters is that it is the endpoint's error and not the auth decorator's.
    """
    fresh = flask_app.test_client()
    r = fresh.get(path, headers=headers,
                  environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code not in (401, 403), (
        "%s behind %s returned HTTP %s -- Google's redirect would hit a login "
        "screen" % (path, headers or "direct loopback", r.status_code))
    body = r.get_data(as_text=True).lower()
    assert 'name="password"' not in body, (
        "%s served a login form to Google's redirect" % path)


@pytest.mark.parametrize("host_header", [
    "agent.friday", "localhost:3000", "127.0.0.1:3000",
])
def test_the_google_redirect_uri_stays_pinned_to_loopback(flask_app, host_header):
    """Never derived from the request's Host.

    If this ever followed `request.host_url`, a user reaching Friday through the
    alias would send Google `http://agent.friday/...`, which Google rejects
    outright with 400 invalid_request -- not a DNS problem, and not something
    propagation fixes.
    """
    from agent_friday.services.calendar_engine import _google_redirect_uri

    with flask_app.test_request_context(
            "/api/google/auth", headers={"Host": host_header},
            environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        uri = _google_redirect_uri({})

    assert uri.startswith(("http://localhost:", "http://127.0.0.1:")), (
        "redirect_uri %r is not loopback-pinned (Host was %r)"
        % (uri, host_header))
    assert "agent.friday" not in uri
    assert uri.endswith("/api/google/auth/callback")


def test_the_multi_account_flow_does_not_need_the_session_cookie():
    """Why the CURRENT connector survives the origin change.

    `routes/google_accounts.py` keeps the PKCE verifier in a server-side
    `_PENDING` map keyed by `state`, and reads `state` from the query string, so
    a consent begun on agent.friday can complete on localhost:3000.

    The legacy single-account flow in `routes/google.py` keeps the same kind
    of server-side record (the verifier never leaves the server; only `state`
    crosses in the URL), so it too completes when begun on agent.friday -- see
    tests/api/test_google_oauth_pkce.py for the flow end to end.
    """
    import agent_friday.routes.google as g_routes
    import agent_friday.routes.google_accounts as ga_routes

    for mod in (ga_routes, g_routes):
        assert hasattr(mod, "_PENDING"), (
            "%s lost its server-side pending-auth map; its flow would now depend "
            "on a cookie that does not cross the origin change" % mod.__name__)
    src = io.open(ga_routes.__file__, encoding="utf-8", errors="replace").read()
    assert 'request.args.get("state")' in src, (
        "the callback no longer prefers the state from the query string")
