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

  * the address that was judged is the address that is dialled. `pinned`
    resolves the host once, judges every answer, and makes the connection
    dial only those addresses, so a name that answers a public address to the
    check and a private one to the connection (DNS rebinding) never reaches
    the second. TLS is untouched: the URL, the Host header, SNI and the
    certificate check still use the host name. Every redirect hop is vetted
    and pinned on its own.
"""
from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
from urllib.parse import urljoin, urlparse

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


def _vet(url: str) -> tuple[bool, str, str, list[str]]:
    """Judge `url`. Returns (ok, reason, host, addresses).

    `addresses` is the set the host resolved to, every one of them judged safe
    (the host itself for an IP literal); it is empty whenever `ok` is False.
    `pinned` connects to exactly these addresses, so the answer that was
    judged is the answer that is dialled.

    Fail-closed: anything that cannot be parsed, resolved or judged is refused.
    """
    raw = (url or "").strip()
    if not raw:
        return False, "no URL was provided", "", []
    try:
        p = urlparse(raw)
    except Exception as e:
        return False, f"the URL could not be parsed ({e})", "", []

    if p.scheme not in ("http", "https"):
        return False, (
            f"only http and https can be fetched (got {p.scheme or 'no scheme'!r}) — "
            f"file://, gopher:// and friends are refused"
        ), "", []
    if p.username or p.password:
        return False, "the URL carries embedded credentials, which is refused", "", []

    host = (p.hostname or "").strip().strip("[]").lower()
    if not host:
        return False, "the URL has no host", "", []
    if host in _ALWAYS_REFUSE_HOSTS or host.endswith(_ALWAYS_REFUSE_SUFFIXES):
        return False, f"{host!r} is this machine or the local network", host, []

    # An IP literal needs no DNS and must be judged directly — otherwise
    # getaddrinfo happily "resolves" 127.0.0.1 to itself and the literal case
    # rides through on the generic path.
    try:
        ipaddress.ip_address(host)
        if not _address_is_safe(host):
            return False, f"{host} is a private, loopback or link-local address", host, []
        return True, "ok", host, [host]
    except ValueError:
        pass

    try:
        addrs = resolve_all(host)
    except Exception as e:
        return False, f"{host!r} could not be resolved ({e})", host, []
    if not addrs:
        return False, f"{host!r} resolved to no addresses", host, []

    unsafe = [a for a in addrs if not _address_is_safe(a)]
    if unsafe:
        return False, (
            f"{host!r} resolves to a private, loopback or link-local address "
            f"({', '.join(unsafe)}) — this is how an internal service gets "
            f"reached through a public-looking name"
        ), host, []
    return True, "ok", host, list(addrs)


def check_url(url: str) -> tuple[bool, str]:
    """Decide whether `url` may be fetched. Returns (ok, reason)."""
    ok, why, _host, _addrs = _vet(url)
    return ok, why


# ── Pinning: the address that was judged is the address that is dialled ─────
#
# `check_url` alone leaves a window: the HTTP client resolves the name again
# when it connects, and a name with a short TTL can answer a public address to
# the check and a private one to the connection (DNS rebinding). `pinned`
# closes it without touching TLS: urllib3 is told which addresses to dial for
# the vetted host, while the URL, the Host header, SNI and certificate
# verification keep the host name.
_pin = threading.local()
_install_lock = threading.Lock()


def _host_keys(host: str) -> list[str]:
    keys = [host]
    try:
        keys.append(host.encode("idna").decode("ascii"))
    except Exception:
        pass
    return [k.rstrip(".").lower() for k in keys]


def _install_pin_hook() -> None:
    """Wrap urllib3's connect once; the wrapper only acts on a thread that is
    inside `pinned` and only for the vetted host."""
    from urllib3.util import connection as _conn
    with _install_lock:
        if getattr(_conn.create_connection, "_friday_pin_hook", False):
            return
        original = _conn.create_connection

        def create_connection(address, *args, **kwargs):
            pins = getattr(_pin, "map", None)
            if pins:
                host, port = address[0], address[1]
                addrs = pins.get(str(host).strip("[]").rstrip(".").lower())
                if addrs is not None:
                    last: Exception | None = None
                    for ip in addrs:
                        try:
                            return original((ip, port), *args, **kwargs)
                        except OSError as e:
                            last = e
                    raise last or OSError(f"no vetted address for {host!r} accepted a connection")
            return original(address, *args, **kwargs)

        create_connection._friday_pin_hook = True  # type: ignore[attr-defined]
        _conn.create_connection = create_connection


@contextlib.contextmanager
def pinned(url: str):
    """Vet `url`, then make every connection to its host on this thread dial
    only the vetted addresses. Raises `UnsafeURLError` for a refused URL.

    Wrap exactly one request (one redirect hop) in it: each hop is vetted and
    pinned on its own. A request made through a proxy dials the proxy, not the
    host, and is not pinned; the proxy is the owner's own setting.
    """
    ok, why, host, addrs = _vet(url)
    if not ok:
        raise UnsafeURLError(f"refusing to fetch {url!r}: {why}")
    _install_pin_hook()
    previous = getattr(_pin, "map", None)
    merged = dict(previous or {})
    for key in _host_keys(host):
        merged[key] = list(addrs)
    _pin.map = merged
    try:
        yield addrs
    finally:
        _pin.map = previous


def check_host_literal(host: str) -> tuple[bool, str]:
    """The part of `check_url` that needs no DNS: names that are always this
    machine or its network, and IP literals judged directly.

    For checks that run on every request a page makes (Friday's browser
    judges each subresource), where a DNS lookup per request is too slow. A
    hostname that passes here can still resolve to a private address; the
    full `check_url` stays the rule for every navigation.
    """
    h = (host or "").strip().strip("[]").lower()
    if not h:
        return False, "no host"
    if h in _ALWAYS_REFUSE_HOSTS or h.endswith(_ALWAYS_REFUSE_SUFFIXES):
        return False, f"{h!r} is this machine or the local network"
    try:
        ipaddress.ip_address(h)
    except ValueError:
        return True, "ok"
    if not _address_is_safe(h):
        return False, f"{h} is a private, loopback or link-local address"
    return True, "ok"


def assert_safe(url: str) -> None:
    """check_url, as an exception. For call sites that should not forget."""
    ok, why = check_url(url)
    if not ok:
        raise UnsafeURLError(f"refusing to fetch {url!r}: {why}")


def safe_get(url: str, *, timeout: float = 15, headers: dict | None = None,
             stream: bool = False):
    """`requests.get` for a URL that came from untrusted content (a feed, a web
    page, model output), with `check_url` applied to the URL and to every
    redirect hop. Returns the final `requests.Response`; raises
    `UnsafeURLError` for a refused URL or hop and `requests` errors otherwise.

    Redirects are followed here, one at a time, because automatic following
    would let a public URL 302 to loopback after the front-door check passed.
    """
    import requests

    current = (url or "").strip()
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        with pinned(current):
            resp = requests.get(current, timeout=timeout, headers=headers or {},
                                allow_redirects=False, stream=stream)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        loc = resp.headers.get("location") or ""
        if not loc:
            return resp
        nxt = urljoin(current, loc)
        ok, why = check_url(nxt)
        if not ok:
            raise UnsafeURLError(f"redirect to {nxt!r} refused: {why}")
        current = nxt
    raise UnsafeURLError(f"too many redirects (>{MAX_REDIRECT_HOPS})")


def hostname_matches(host: str, domain: str) -> bool:
    """True when `host` IS `domain` or a subdomain of it.

    The comparison a substring test (`"google.com" in host`) gets wrong:
    `google.com.evil.example` and `notgoogle.com` both contain it.
    """
    h = (host or "").strip().rstrip(".").lower()
    d = (domain or "").strip().strip(".").lower()
    return bool(h and d) and (h == d or h.endswith("." + d))


def url_host_matches(url: str, domain: str) -> bool:
    """`hostname_matches` applied to a URL's host; False for anything unparseable."""
    raw = (url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        host = urlparse(raw).hostname or ""
    except Exception:
        return False
    return hostname_matches(host, domain)


def check_peer_endpoint(url: str) -> tuple[bool, str]:
    """Decide whether `url` may be used as a federation peer's base endpoint.

    Deliberately NOT `check_url`: a paired peer is routinely on the owner's
    LAN, so private addresses are allowed. What is refused is anything that is
    not a plain http(s) base URL (`urllib.request` would otherwise open
    `file://` and `ftp://` as well) and credentials hidden in the netloc.
    """
    raw = (url or "").strip()
    if not raw:
        return False, "no endpoint was provided"
    try:
        p = urlparse(raw)
    except Exception:
        return False, "the endpoint could not be parsed"
    if p.scheme not in ("http", "https"):
        return False, (f"a peer endpoint must be http or https "
                       f"(got {p.scheme or 'no scheme'!r})")
    if p.username or p.password:
        return False, "the endpoint carries embedded credentials, which is refused"
    if not (p.hostname or "").strip():
        return False, "the endpoint has no host"
    return True, "ok"
