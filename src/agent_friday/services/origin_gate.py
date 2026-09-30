"""Session-token and cross-site gate for state-changing calls and WebSocket upgrades.

Loopback is trusted (core.check_auth), so the browser on this machine is
already "logged in" to every route. A page from some other origin, a web page
or a dev-server preview on another port, can make the owner's browser send
Friday a CORS-simple POST, and nothing about the address alone tells that page
apart from Friday's own. This module is the one place that decides whether a
browser request really came from Friday's page.

Friday's page holds the rotating session token (X-Friday-Token; `?t=` on a
WebSocket URL, which cannot carry headers). A page in another origin cannot
read it. So for every browser request that changes state or upgrades to a
WebSocket, the token is required, on loopback too.

`Sec-Fetch-Site` and `Origin` are forbidden request headers: page script can
neither set nor strip them. A request that carries neither is not a browser
request (the tray, the CLI, a server-to-server call on loopback) and is the
narrow, explicit exemption; it was never the cross-site threat.

For POST/PUT/PATCH/DELETE and any WebSocket upgrade that carries browser
metadata:

  * metadata that says another document (`Sec-Fetch-Site: cross-site` or
    `same-site`, `Origin: null` from a sandboxed frame, an `Origin` that is not
    one of Friday's own origins, another loopback port included) -> refused,
    a valid token or not: a token held by a document that is not Friday's page
    is a leaked token;
  * Friday's own origin with a valid session token -> allowed;
  * Friday's own origin without one -> refused, naming the missing token.

Sensitive reads get the same treatment through `frame_refusal`: under /api/ and
/ws/ a request whose metadata says another document (an opaque-origin sandbox
included) is refused for every method, with two narrow exceptions that cannot
be read by script: a top-level navigation, and a passive media load.

Friday's own origins are exact scheme, host and port: a loopback name on the
port the request arrived on, and the local address (agent.<name>, agent.friday)
on the ports its proxy is configured to listen on. `http://agent.friday:5173`
is a dev server, not Friday.

Reads (GET/HEAD/OPTIONS) are untouched. The gate fails closed: a caller that
cannot evaluate it must refuse (see `refusal_or_closed`).
"""
from __future__ import annotations

from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

REFUSAL_REASON = (
    "That request came from another site, so I refused it. "
    "Open Friday's own page to do that."
)
TOKEN_REASON = (
    "That request didn't carry this session's token, so I refused it. "
    "Reload Friday's page and try again."
)
FRAME_REASON = (
    "That request came from a page running inside a sandbox or another site, "
    "and this part of Friday is only open to Friday's own page, so I refused it."
)
GATE_ERROR_REASON = (
    "I couldn't check where that request came from, so I refused it. "
    "Reload Friday's page and try again."
)


def _split_host(value):
    """(hostname, port or None) from a Host header."""
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


def has_browser_metadata(headers) -> bool:
    return (headers.get("Origin") is not None
            or bool((headers.get("Sec-Fetch-Site") or "").strip()))


def _default_port(scheme) -> int:
    return 443 if scheme in ("https", "wss") else 80


def _normal_origin(origin):
    """(scheme, host, port) of an Origin value, or None when it is not one."""
    try:
        o = urlsplit(origin)
        host, port = o.hostname or "", o.port
    except ValueError:
        return None
    if o.scheme not in ("http", "https") or not host:
        return None
    return o.scheme, host.lower(), port or _default_port(o.scheme)


def _origin_is_own(origin, host_header, *, is_local, own_origins) -> bool:
    norm = _normal_origin(origin)
    if norm is None:
        return False
    scheme, o_host, o_port = norm
    for own in own_origins or ():
        if _normal_origin(str(own)) == norm:
            return True
    h_host, h_port = _split_host(host_header)
    if is_local:
        # Same machine, same port the request arrived on.
        return o_host in _LOOPBACK_NAMES and o_port == (h_port or 80)
    # Not a local request (a tunnel, already behind the remote key): the
    # origin must be the host the client addressed.
    return o_host == h_host and o_port == (h_port or _default_port(scheme))


def _foreign_reason(headers, host, is_local, own_origins):
    """REFUSAL_REASON when the metadata says another site, else None."""
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site in ("cross-site", "same-site"):
        return REFUSAL_REASON
    origin = headers.get("Origin")
    if origin is not None:
        origin = origin.strip()
        if origin.lower() == "null" or not _origin_is_own(
                origin, host, is_local=is_local, own_origins=own_origins):
            return REFUSAL_REASON
    return None


def refusal(method, headers, *, host, is_local, token_valid=False, own_origins=(),
            token_required=True):
    """A plain-language reason to refuse this request, or None to let it on.

    `token_required=False` is for the sign-in form alone: it cannot carry a
    session token (it is how a remote visitor gets one) but is still refused
    when the metadata says another document sent it."""
    if not is_guarded(method, headers) or not has_browser_metadata(headers):
        return None
    foreign = _foreign_reason(headers, host, is_local, own_origins)
    if foreign:
        return foreign
    if token_valid or not token_required:
        return None
    return TOKEN_REASON


# Sensitive surface for `frame_refusal`. Everything Friday exposes to script is
# under these prefixes; the health probe is the one deliberately public read.
SENSITIVE_PREFIXES = ("/api/", "/ws/")
PUBLIC_READ_PATHS = frozenset({"/api/health"})
# Loads a document cannot read back: images, media, fonts, tracks.
PASSIVE_DESTINATIONS = frozenset({"image", "audio", "video", "track", "font"})
# Routes whose whole job is to be shown inside a sandboxed frame.
FRAMEABLE_PREFIXES = ("/api/creations/", "/api/studio-files/raw")
ASSET_PREFIXES = ("/api/creations/",)


def _is_sandboxed_or_foreign(headers, host, is_local, own_origins) -> bool:
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site in ("cross-site", "same-site"):
        return True
    origin = headers.get("Origin")
    if origin is None:
        return False
    return (origin.strip().lower() == "null" or not _origin_is_own(
        origin.strip(), host, is_local=is_local, own_origins=own_origins))


def frame_refusal(method, headers, path, *, host, is_local, own_origins=()):
    """FRAME_REASON when a sandboxed or foreign document is talking to a
    sensitive route, whatever it holds. Not the session token's job: a token
    proves who has it, not what document is using it."""
    path = path or ""
    if not path.startswith(SENSITIVE_PREFIXES) or path.rstrip("/") in PUBLIC_READ_PATHS:
        return None
    if not _is_sandboxed_or_foreign(headers, host, is_local, own_origins):
        return None
    if (method or "").upper() in ("GET", "HEAD"):
        dest = (headers.get("Sec-Fetch-Dest") or "").strip().lower()
        mode = (headers.get("Sec-Fetch-Mode") or "").strip().lower()
        if dest in PASSIVE_DESTINATIONS:
            return None
        if mode == "navigate" and dest in ("document", ""):
            return None
        # A creation shown in a frame, and the files that creation loads next
        # to itself, are served for exactly that. Nothing here is readable by
        # the requesting document, and the markup carries its own sandbox.
        if path.startswith(FRAMEABLE_PREFIXES) and mode == "navigate" \
                and dest in ("iframe", "frame", "embed", "object"):
            return None
        if path.startswith(ASSET_PREFIXES) and dest in ("script", "style"):
            return None
    return FRAME_REASON


def refusal_or_closed(method, headers, **kw):
    """`refusal`, except that any failure to evaluate it refuses a guarded request."""
    try:
        return refusal(method, headers, **kw)
    except Exception:
        try:
            guarded = is_guarded(method, headers)
        except Exception:
            guarded = True
        return GATE_ERROR_REASON if guarded else None


def frame_refusal_or_closed(method, headers, path, **kw):
    """`frame_refusal`, refusing on any failure to evaluate it under a sensitive path."""
    try:
        return frame_refusal(method, headers, path, **kw)
    except Exception:
        return GATE_ERROR_REASON if (path or "").startswith(SENSITIVE_PREFIXES) else None


_CODES = {
    REFUSAL_REASON: "cross_site",
    TOKEN_REASON: "session_token_required",
    FRAME_REASON: "sandboxed_or_foreign_frame",
    GATE_ERROR_REASON: "gate_error",
}


def reason_code(reason) -> str:
    """A stable machine-readable name for a refusal reason. The page retries a
    request once, after fetching the current token, only for
    `session_token_required`."""
    return _CODES.get(reason, "refused")
