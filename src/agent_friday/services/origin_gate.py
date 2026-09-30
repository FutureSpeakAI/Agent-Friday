"""Cross-site request refusal for state-changing calls and WebSocket upgrades.

Loopback is trusted (core.check_auth), so the browser on this machine is
already "logged in" to every route. Nothing about that stops a page from some
other origin, a web page or a dev-server preview on another port, from making
the owner's browser send Friday a CORS-simple POST. This module is the one
place that decides whether a browser request is really from Friday's own page.

Inputs are the browser's own unforgeable metadata: `Sec-Fetch-Site` and
`Origin` are forbidden request headers, so page script cannot set or strip
them. Requests that carry neither (the tray, the CLI, server-to-server calls on
loopback) are not browser requests and pass; they were never the threat.

Rules, for POST/PUT/PATCH/DELETE and for any WebSocket upgrade:

  * a valid session token proves the caller read Friday's page -> allowed;
  * `Sec-Fetch-Site: cross-site` -> refused, whatever `Origin` says;
  * `Origin: null` (sandboxed frame, cross-origin redirect) -> refused;
  * an `Origin` must be one of Friday's own: a loopback name on the same port
    the request arrived on, or the configured local address (agent.<name>,
    agent.friday) through its proxy; for a request that is not local (a
    tunnel, already behind the remote key) the origin must match the host the
    client addressed. Anything else, including another loopback port, is
    refused. A DNS-rebinding page addresses Friday by its own name, so it
    fails the "known own name" test rather than passing on Host equality;
  * with no `Origin`, `Sec-Fetch-Site` of `same-site` is refused, and
    `same-origin` / `none` (address bar, bookmark) pass.

Reads (GET/HEAD/OPTIONS) are untouched.
"""
from __future__ import annotations

from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_PROXY_ALIASES = frozenset({"agent.friday"})

REFUSAL_REASON = (
    "That request came from another site, so I refused it. "
    "Open Friday's own page to do that."
)


def _split_host(value):
    """(hostname, port or None) from a Host header or an origin authority."""
    value = (value or "").strip().lower()
    if not value:
        return "", None
    try:
        parts = urlsplit("//" + value)
        return (parts.hostname or ""), parts.port
    except ValueError:
        return "", None


def _is_upgrade(headers) -> bool:
    return "websocket" in (headers.get("Upgrade") or "").lower()


def is_guarded(method, headers) -> bool:
    return (method or "").upper() in UNSAFE_METHODS or _is_upgrade(headers)


def _default_port(scheme) -> int:
    return 443 if scheme in ("https", "wss") else 80


def _origin_is_own(origin, host_header, *, is_local, own_hosts) -> bool:
    try:
        o = urlsplit(origin)
        o_host, o_port = o.hostname or "", o.port
    except ValueError:
        return False
    if o.scheme not in ("http", "https") or not o_host:
        return False
    o_port = o_port or _default_port(o.scheme)
    h_host, h_port = _split_host(host_header)
    aliases = _PROXY_ALIASES | {str(h).lower() for h in (own_hosts or ())}
    if o_host in aliases:
        # Through the local proxy the browser's origin is the alias itself.
        return True
    if is_local:
        if o_host in _LOOPBACK_NAMES:
            return o_port == (h_port or 80)
        return False
    # Not a local request: the origin must be the host the client addressed.
    return o_host == h_host and o_port == (h_port or _default_port(o.scheme))


def refusal(method, headers, *, host, is_local, token_valid=False, own_hosts=()):
    """A plain-language reason to refuse this request, or None to let it on."""
    if not is_guarded(method, headers) or token_valid:
        return None
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    origin = headers.get("Origin")
    if site == "cross-site":
        return REFUSAL_REASON
    if origin is not None:
        origin = origin.strip()
        if origin.lower() == "null" or not _origin_is_own(
                origin, host, is_local=is_local, own_hosts=own_hosts):
            return REFUSAL_REASON
        return None
    if site == "same-site":
        return REFUSAL_REASON
    return None
