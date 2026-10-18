"""In-process egress guard for a practice-task child: one destination only.

Installed before the app is imported. Every outbound socket connection is
refused unless it goes to the one model endpoint named for the episode (the
loopback local seat, or with a per-trial owner opt-in the one cloud host).
Refusals are recorded and land in the episode record.

This is in-process enforcement, not OS sandboxing (spec HA-E08). It is
adequate because the agent's only tools are the virtual-world handlers, which
reach neither the filesystem nor the network; the guard makes sure nothing
else in the process (a stray library, a fetch hidden in a code path) can
reach anything either.
"""
from __future__ import annotations

import socket
import threading
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

_REFUSED: List[str] = []
_ALLOWED: Set[Tuple[str, int]] = set()
_LOCAL = threading.local()
_INSTALLED = False


class EgressRefused(ConnectionRefusedError):
    pass


def allowed_from_endpoint(endpoint: str) -> Set[Tuple[str, int]]:
    if not endpoint:
        return set()
    u = urlparse(endpoint)
    host = (u.hostname or "").lower()
    port = u.port or (443 if u.scheme == "https" else 80)
    hosts = {host}
    if host in ("127.0.0.1", "localhost", "::1"):
        hosts = {"127.0.0.1", "localhost", "::1"}
    else:
        # A named cloud host (owner opt-in, HD2): its addresses, resolved once
        # now, before the guard is installed; nothing else resolves later.
        try:
            for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP):
                hosts.add(str(info[4][0]).lower())
        except Exception:
            pass
    return {(h, port) for h in hosts}


def refused() -> List[str]:
    return list(_REFUSED)


def _check(address) -> None:
    if getattr(_LOCAL, "bypass", False):
        return
    try:
        host, port = str(address[0]).lower(), int(address[1])
    except Exception:
        # Unix sockets and other families: no network destination to allow.
        _REFUSED.append(repr(address))
        raise EgressRefused(f"practice episode: connection to {address!r} refused")
    if (host, port) in _ALLOWED:
        return
    _REFUSED.append(f"{host}:{port}")
    raise EgressRefused(f"practice episode: connection to {host}:{port} refused (only the model endpoint is reachable)")


def install(endpoint: Optional[str]) -> None:
    """Allow only `endpoint` (an http(s) URL), or nothing at all when it is empty."""
    global _INSTALLED
    _ALLOWED.clear()
    _ALLOWED.update(allowed_from_endpoint(endpoint or ""))
    if _INSTALLED:
        return
    _INSTALLED = True
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_socketpair = socket.socketpair
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return real_connect_ex(self, address)

    def socketpair(*a, **k):
        # On Windows a socketpair is a loopback connection to itself (asyncio
        # needs one); it never leaves the process.
        _LOCAL.bypass = True
        try:
            return real_socketpair(*a, **k)
        finally:
            _LOCAL.bypass = False

    def getaddrinfo(host, port, *a, **k):
        h = str(host or "").lower()
        if not getattr(_LOCAL, "bypass", False) and h and not any(h == ah for ah, _ in _ALLOWED):
            _REFUSED.append(f"dns:{h}")
            raise EgressRefused(f"practice episode: name lookup for {h} refused")
        return real_getaddrinfo(host, port, *a, **k)

    real_sendto = socket.socket.sendto

    def sendto(self, data, *args):
        # UDP datagrams carry their own destination: the same rule applies.
        address = args[-1] if args else None
        if self.family in (socket.AF_INET, socket.AF_INET6) and address is not None:
            _check(address)
        return real_sendto(self, data, *args)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.socketpair = socketpair
    socket.getaddrinfo = getaddrinfo
