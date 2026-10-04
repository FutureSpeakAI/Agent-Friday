"""No public exposure from a test run: the guards the root conftest installs.

A unit test once approved a publish card, and the code under test launched a
real static server and a real cloudflared quick tunnel from every worker,
exposing temporary folders on public addresses with no approval. Two guards
make that impossible in every test process:

* process creation refuses tunnel binaries (`TUNNEL_BINARIES`), in argv lists
  and shell strings alike, by basename;
* a socket refuses to bind anything but loopback.

This lives in its own module, not in conftest, because pytest imports the
root conftest as `conftest` and tests import it as `tests.conftest`: two
module objects, two copies of anything defined there. An exception class
must be one object for `pytest.raises` to catch it, so it is defined here and
imported by both. Both guards are idempotent and marked, so
tests/unit/test_no_public_exposure_under_tests.py can prove they are live.
"""
from __future__ import annotations

import os
import socket
import subprocess


class PublicExposureRefused(RuntimeError):
    """A test tried to start a tunnel or bind a public host."""


#: Binaries that publish a local port to the internet. Matched by basename,
#: case-insensitively, with or without .exe, in argv lists and shell strings.
TUNNEL_BINARIES = frozenset({
    "cloudflared", "ngrok", "caddy", "tailscale", "frpc", "localtunnel", "lt",
    "bore", "zrok", "pagekite", "expose", "loophole",
})

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def names_in(argv) -> set:
    """The lowercase basenames (without .exe) named by an argv list or a shell string."""
    if isinstance(argv, (str, bytes)):
        text = argv.decode("utf-8", "replace") if isinstance(argv, bytes) else argv
        parts = text.replace('"', " ").replace("'", " ").split()
    else:
        try:
            parts = [os.fspath(a) for a in argv]
        except TypeError:
            return set()
    out = set()
    for a in parts:
        base = os.path.basename(str(a)).lower()
        if base.endswith(".exe"):
            base = base[:-4]
        out.add(base)
    return out


def install() -> None:
    if not getattr(subprocess.Popen.__init__, "__friday_no_tunnel__", False):
        _prev_init = subprocess.Popen.__init__

        def _guarded_init(self, args, *a, **kw):
            hit = names_in(args) & TUNNEL_BINARIES
            if hit:
                raise PublicExposureRefused(
                    "a test tried to start %s; nothing under the test run may publish a port "
                    "to the internet" % ", ".join(sorted(hit)))
            return _prev_init(self, args, *a, **kw)

        _guarded_init.__friday_no_tunnel__ = True
        _guarded_init.__friday_no_console__ = getattr(_prev_init, "__friday_no_console__", False)
        subprocess.Popen.__init__ = _guarded_init

    if not getattr(socket.socket.bind, "__friday_loopback_only__", False):
        _prev_bind = socket.socket.bind

        def _guarded_bind(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
                host = str(address[0] or "")
                if host not in LOOPBACK_HOSTS and not host.startswith("127."):
                    raise PublicExposureRefused(
                        "a test tried to bind %r; nothing under the test run may listen on anything "
                        "but loopback" % (host or "<all interfaces>"))
            return _prev_bind(self, address)

        _guarded_bind.__friday_loopback_only__ = True
        socket.socket.bind = _guarded_bind
