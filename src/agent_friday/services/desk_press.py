"""Was this press made on this PC's own desktop?

A card that only "a press on the screen" may approve (sending, paying, running a
command, a website-task plan, a change to Friday's own instructions) used to be
satisfied by any request from Friday's own page: same-origin browser headers and
the page's session token. A page served through a tunnel, a reverse proxy, a
`tailscale serve` forward or a LAN bind is also Friday's own page, holding the
same token, so a tap on a phone counted as a press at the desk.

This module adds the part a remote device cannot have:

  * **A per-launch desk key.** A random secret made when this process starts,
    kept only in memory, never written to disk, never put in a page Friday
    serves and never sent in any response of the main server.
  * **A loopback-only side door that hands it out.** A tiny listener bound to
    127.0.0.1 on a random port, whatever FRIDAY_BIND_HOST says. Friday's page,
    when it is open in a browser on this PC, fetches the key from it. A browser
    on a phone or a laptop cannot: "127.0.0.1" there is that device itself. A
    tunnel or proxy forwards Friday's main port, never this one, and the side
    door also refuses any Host or Origin that is not Friday's own on this PC.
  * **The press must carry the key, arrive over loopback with no forwarding
    evidence, and name Friday by a local address** (screen_click.screen_session).

A forward that strips its headers and arrives from 127.0.0.1 passes the loopback
test, but the browser behind it never had the key, so its press is not a press.

What this does not claim: it does not stop a program already running on this
PC as the owner, which can read process memory or forge a browser. Nothing short
of an OS-level prompt would, and the screen-click module never claimed that.
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlsplit

_log = logging.getLogger("friday.desk_press")

#: The request header Friday's page sends the desk key in.
HEADER = "X-Friday-Desk"
#: The side door's one path.
KEY_PATH = "/desk-key"
#: The refusal code a page reads to fetch a fresh key and try the press once more.
REFUSAL_CODE = "desk_proof_required"

#: Names that address this PC's loopback interface.
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

#: Made once per process. Rotates on every launch, so a key copied out of an old
#: browser tab is dead after the next restart.
_KEY = secrets.token_urlsafe(32)
#: A non-secret name for this launch's key, so a page can tell its key is stale.
EPOCH = secrets.token_hex(6)

_state_lock = threading.Lock()
_server: Optional[ThreadingHTTPServer] = None
_thread: Optional[threading.Thread] = None
_main_port: Optional[int] = None
_main_tls = False


# ── The key ────────────────────────────────────────────────────────────────

def key_matches(value) -> bool:
    """Constant-time check of a presented desk key against this launch's."""
    if not value or not isinstance(value, str):
        return False
    return hmac.compare_digest(value.encode("utf-8", "replace"), _KEY.encode("ascii"))


def presented(request) -> str:
    try:
        return str(request.headers.get(HEADER) or "")
    except Exception:
        return ""


def stale(request) -> bool:
    """True when the request carried a desk key, but not this launch's (Friday restarted)."""
    got = presented(request)
    return bool(got) and not key_matches(got)


# ── Where the request came from ────────────────────────────────────────────

def _bare_host(host: str) -> str:
    h = str(host or "").strip().lower()
    if not h:
        return ""
    try:
        return (urlsplit("//" + h).hostname or "").lower()
    except ValueError:
        return ""


def local_names() -> set:
    """Every name Friday answers to on this PC: loopback, and its own local address."""
    names = set(LOOPBACK_NAMES)
    try:
        from agent_friday.services.local_address import local_hosts
        names |= {str(h).lower() for h in local_hosts() if h}
    except Exception:
        pass
    names.add("agent.friday")
    return names


def host_is_local(request) -> bool:
    """The request named Friday by an address of this PC (not a tunnel or LAN name)."""
    try:
        return _bare_host(request.host) in local_names()
    except Exception:
        return False


#: Seconds after Friday's own mouse or keyboard action (Computer Control) during which no
#: press counts: a click Friday made on this PC's screen is not the owner's press.
MACHINE_INPUT_QUIET_S = 5.0
_last_machine_input = 0.0


def note_machine_input() -> None:
    """Called by every Computer Control action that moves the mouse, clicks or types."""
    global _last_machine_input
    import time
    _last_machine_input = time.time()


def machine_input_recent() -> bool:
    import time
    return (time.time() - _last_machine_input) < MACHINE_INPUT_QUIET_S


def check(request) -> dict:
    """Each part of the desk test, by name, for a refusal message and the receipt.

    Never raises: a part that cannot be read is False."""
    out = {"local": False, "host_local": False, "desk_key": False, "no_machine_input": False}
    out["no_machine_input"] = not machine_input_recent()
    try:
        import agent_friday.core as core
        out["local"] = bool(core._is_local_request())
    except Exception:
        pass
    out["host_local"] = host_is_local(request)
    out["desk_key"] = key_matches(presented(request))
    return out


def on_this_desk(request) -> bool:
    """Loopback with no forwarding evidence, a local Host, this launch's desk key, and no
    mouse or keyboard action by Friday herself in the last few seconds."""
    c = check(request)
    return bool(c["local"] and c["host_local"] and c["desk_key"] and c["no_machine_input"])


# ── The side door ──────────────────────────────────────────────────────────

def _normal_origin(origin: str):
    try:
        o = urlsplit(str(origin or "").strip())
        host, port = (o.hostname or "").lower(), o.port
    except ValueError:
        return None
    if o.scheme not in ("http", "https") or not host:
        return None
    return o.scheme, host, port or (443 if o.scheme == "https" else 80)


def allowed_origins() -> set:
    """The exact origins Friday's own page has on this PC.

    A loopback name on Friday's main port, and the local address on the ports its
    own proxy listens on. Another loopback port (a dev server, another app) is
    not Friday's page and is refused."""
    out = set()
    if _main_port:
        schemes = ("https",) if _main_tls else ("http",)
        for scheme in schemes:
            for name in ("localhost", "127.0.0.1", "[::1]"):
                norm = _normal_origin("%s://%s:%d" % (scheme, name, _main_port))
                if norm:
                    out.add(norm)
    try:
        from agent_friday.services.local_address import own_origins
        for o in own_origins() or ():
            norm = _normal_origin(o)
            if norm:
                out.add(norm)
    except Exception:
        pass
    return out


def origin_allowed(origin: str) -> bool:
    norm = _normal_origin(origin)
    return norm is not None and norm in allowed_origins()


class _Handler(BaseHTTPRequestHandler):
    server_version = "FridayDesk"
    sys_version = ""

    def log_message(self, fmt, *args):   # noqa: D401 - quiet: never log the request line
        return

    def _expected_host(self) -> bool:
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "").strip().lower() == "127.0.0.1:%d" % port

    def _refuse(self, code: int = 403) -> None:
        body = b'{"ok": false}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _cors(self, origin: str) -> None:
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET")
        self.send_header("Access-Control-Max-Age", "60")
        # A browser asking for a loopback target from a loopback page needs no
        # private-network grant, but an older Chrome preflight asks anyway.
        if self.headers.get("Access-Control-Request-Private-Network"):
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _gate(self) -> Optional[str]:
        """The page's Origin when every rule holds, else None."""
        # The peer is this machine: the socket is bound to 127.0.0.1, so it can be
        # nothing else, and it is checked anyway.
        peer = (self.client_address or ("",))[0]
        if peer not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return None
        # A DNS-rebound name, or a forward pointed here, names another host.
        if not self._expected_host():
            return None
        # Only Friday's own page on this PC, as the browser reports it. A page
        # script cannot set or change Origin.
        origin = (self.headers.get("Origin") or "").strip()
        if not origin or not origin_allowed(origin):
            return None
        # Anything that relays requests says so; a direct browser fetch never does.
        for name in ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "Via", "X-Real-IP"):
            if self.headers.get(name) is not None:
                return None
        if any(k.lower().startswith("cf-") for k in self.headers.keys()):
            return None
        return origin

    def do_OPTIONS(self):  # noqa: N802 - http.server naming
        origin = self._gate()
        if origin is None or self.path.split("?", 1)[0] != KEY_PATH:
            return self._refuse()
        self.send_response(204)
        self._cors(origin)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - http.server naming
        origin = self._gate()
        if origin is None or self.path.split("?", 1)[0] != KEY_PATH:
            return self._refuse()
        # A browser fetch() from the page is mode "cors"; a navigation, a frame or
        # an <img> is not, and must never see the key.
        if (self.headers.get("Sec-Fetch-Mode") or "").strip().lower() != "cors":
            return self._refuse()
        body = json.dumps({"key": _KEY, "epoch": EPOCH}).encode("utf-8")
        self.send_response(200)
        self._cors(origin)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        return self._refuse(405)

    do_PUT = do_DELETE = do_PATCH = do_POST


def start(main_port: int, *, tls: bool = False) -> Optional[int]:
    """Open the side door on 127.0.0.1 (always, whatever Friday's own bind is) and
    return its port, or None when it could not open (then no press counts as
    made on this desk: approvals fail closed, and the log says why)."""
    global _server, _thread, _main_port, _main_tls
    with _state_lock:
        _main_port, _main_tls = int(main_port), bool(tls)
        if _server is not None:
            return _server.server_address[1]
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        except OSError as e:
            _log.error("desk key listener could not open on 127.0.0.1 (%s): "
                       "no on-screen press can be confirmed until Friday restarts", e)
            return None
        srv.daemon_threads = True
        t = threading.Thread(target=srv.serve_forever, name="friday-desk-key", daemon=True)
        t.start()
        _server, _thread = srv, t
        return srv.server_address[1]


def stop() -> None:
    global _server, _thread
    with _state_lock:
        srv, _server, _thread = _server, None, None
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass


def port() -> Optional[int]:
    srv = _server
    return srv.server_address[1] if srv is not None else None


def bound_address() -> Optional[tuple]:
    srv = _server
    return tuple(srv.server_address[:2]) if srv is not None else None


def where() -> dict:
    """What Friday's page needs to fetch the key: the side door's port and this
    launch's epoch. Neither is secret."""
    return {"port": port(), "epoch": EPOCH, "path": KEY_PATH}


def connect_source() -> str:
    """The CSP connect-src entry that lets Friday's page reach the side door.

    Any port on 127.0.0.1, not this launch's: the door opens on a new port each launch,
    and a tab left open across a restart keeps the policy it was served with, so an exact
    port would lock that tab out of the new door (and out of every press) until a reload.
    Loopback is this PC; it is not a way out to anyone else."""
    return "http://127.0.0.1:*" if port() else ""
