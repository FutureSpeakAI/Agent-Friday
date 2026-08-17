"""
web_safety — the SSRF guard for every outbound fetch Friday makes.

Why this module exists (deep-research.md P2): `browse_web` fetched any http(s)
URL with no host check at all, so `http://127.0.0.1:3000/api/...`,
`http://169.254.169.254/latest/meta-data/` (cloud metadata) and Friday's own
seat ports were all reachable by anything that could get a URL in front of the
tool — including a web page Friday was asked to summarize.

The design doc prescribed "wire the existing `open_url` validator to the
fetcher". That fix does not work and would have looked like it did:
`agent._validate_url` checks the scheme, that the host contains a dot, the
shape of a YouTube id, and reachability. It has NO address check, and it
explicitly allows `localhost`. Wiring it up would have left the hole open
behind a plausible-looking guard.

What actually closes it:

  * every resolved address must be globally routable — loopback, RFC1918,
    link-local (169.254/16, the metadata range), CGNAT, reserved, multicast
    and unspecified are all refused, for IPv4 and IPv6;
  * refusal is driven by ANY bad address, not all of them. A hostname whose
    DNS returns one public and one private A record is refused. (This is why
    `provider_descriptors.is_private_host` is not reused here: it answers
    "is this host entirely private?", the right question for classifying a
    model provider and the wrong one for refusing a fetch — it returns False
    for a mixed-record host, which would have meant "allow".)
  * credentials in the netloc (`http://evil@127.0.0.1/`) are refused: they are
    a classic way to make a hostile URL read as benign.
  * redirects are validated hop by hop by the caller (see web_fetch), because
    a perfectly public URL is allowed to 302 to loopback and a front-door-only
    check never sees the second request.

KNOWN RESIDUAL RISK, stated rather than papered over: between our resolution
and the HTTP client's own resolution there is a DNS-rebinding window. Closing
it fully means pinning the validated address into the socket, which breaks TLS
hostname verification unless rebuilt carefully. Not closed here; the exposure
is a single-user desktop app fetching pages, and every hop is re-validated.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Hosts that never resolve to anything routable but are worth naming, so the
# refusal reason is legible instead of "DNS failed".
_ALWAYS_REFUSE_HOSTS = {
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
}
_ALWAYS_REFUSE_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost")

MAX_REDIRECT_HOPS = 5


class UnsafeURLError(ValueError):
    """Raised when a URL must not be fetched. Carries a human-readable reason."""


def _address_is_safe(addr: str) -> bool:
    """True only for a globally routable unicast address.

    Deliberately allow-list shaped: anything `ipaddress` does not consider
    global is refused. That covers loopback, private, link-local (including
    169.254.169.254), CGNAT (100.64/10), reserved, multicast, unspecified,
    and the IPv6 equivalents — without this module having to enumerate them
    and get one wrong.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) must be judged on the mapped address,
    # not the wrapper — otherwise loopback smuggles through as a v6 literal.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return False
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return bool(ip.is_global)


def resolve_all(host: str) -> list[str]:
    """Every address `host` resolves to. Raises on resolution failure."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({i[4][0] for i in infos})


def check_url(url: str) -> tuple[bool, str]:
    """Decide whether `url` may be fetched. Returns (ok, reason).

    Fail-closed: anything that cannot be parsed, resolved or judged is refused.
    """
    raw = (url or "").strip()
    if not raw:
        return False, "no URL was provided"
    try:
        p = urlparse(raw)
    except Exception as e:
        return False, f"the URL could not be parsed ({e})"

    if p.scheme not in ("http", "https"):
        return False, (
            f"only http and https can be fetched (got {p.scheme or 'no scheme'!r}) — "
            f"file://, gopher:// and friends are refused"
        )
    if p.username or p.password:
        return False, "the URL carries embedded credentials, which is refused"

    host = (p.hostname or "").strip().strip("[]").lower()
    if not host:
        return False, "the URL has no host"
    if host in _ALWAYS_REFUSE_HOSTS or host.endswith(_ALWAYS_REFUSE_SUFFIXES):
        return False, f"{host!r} is this machine or the local network"

    # An IP literal needs no DNS and must be judged directly — otherwise
    # getaddrinfo happily "resolves" 127.0.0.1 to itself and the literal case
    # rides through on the generic path.
    try:
        ipaddress.ip_address(host)
        if not _address_is_safe(host):
            return False, f"{host} is a private, loopback or link-local address"
        return True, "ok"
    except ValueError:
        pass

    try:
        addrs = resolve_all(host)
    except Exception as e:
        return False, f"{host!r} could not be resolved ({e})"
    if not addrs:
        return False, f"{host!r} resolved to no addresses"

    unsafe = [a for a in addrs if not _address_is_safe(a)]
    if unsafe:
        return False, (
            f"{host!r} resolves to a private, loopback or link-local address "
            f"({', '.join(unsafe)}) — this is how an internal service gets "
            f"reached through a public-looking name"
        )
    return True, "ok"


def assert_safe(url: str) -> None:
    """check_url, as an exception. For call sites that should not forget."""
    ok, why = check_url(url)
    if not ok:
        raise UnsafeURLError(f"refusing to fetch {url!r}: {why}")
