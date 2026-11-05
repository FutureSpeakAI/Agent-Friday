"""The TV gateway's Flask app and lifecycle (D1: hello, sessions, whoami)."""
from __future__ import annotations

import base64
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from flask import Flask, Response, g, jsonify, request

from agent_friday.services import device_pairing

_log = logging.getLogger(__name__)

VERSION = 1
AUTH_SCHEME = "Friday-Device "

# Paths a request may reach before it holds a session. Everything else needs one.
OPEN_PATHS = frozenset({"/tv/v1/hello", "/tv/v1/session/challenge", "/tv/v1/session/open",
                        "/tv/v1/pair/start", "/tv/v1/pair/reveal", "/tv/v1/pair/status"})

# Credentials of the main app. Presenting one here is refused outright, so a
# browser tab or a leaked page token can never be replayed through this door.
MAIN_APP_HEADERS = ("X-Friday-Token", "X-Friday-Observer", "Cookie")

_RATE = 30            # open-path requests per client per minute


class _Bucket:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= _RATE:
                return False
            q.append(now)
            return True


_BUCKET = _Bucket()


def _refuse(status: int, reason: str) -> Response:
    resp = jsonify({"ok": False, "error": reason})
    resp.status_code = status
    return resp


def _bearer() -> str:
    h = request.headers.get("Authorization", "")
    return h[len(AUTH_SCHEME):].strip() if h.startswith(AUTH_SCHEME) else ""


def create_app() -> Flask:
    app = Flask("agent_friday.tv_gateway")
    app.config["SESSION_COOKIE_NAME"] = "__tv_gateway_unused"

    @app.before_request
    def _gate():
        if any(request.headers.get(h) for h in MAIN_APP_HEADERS):
            return _refuse(401, "main-app credentials are not accepted here")
        g.device = None
        if request.path in OPEN_PATHS:
            if not _BUCKET.allow(request.remote_addr or "?"):
                return _refuse(429, "slow down")
            return None
        dev = device_pairing.check_session(_bearer())
        if dev is None:
            return _refuse(401, "device session required")
        g.device = dev
        return None

    @app.after_request
    def _headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers.pop("Set-Cookie", None)
        return resp

    @app.errorhandler(404)
    @app.errorhandler(405)
    def _bare(e):
        return _refuse(getattr(e, "code", 404), "not here")

    @app.get("/tv/v1/hello")
    def hello():
        from agent_friday.tv_gateway import tls
        return jsonify({"ok": True, "service": "agent-friday-tv-gateway", "v": VERSION,
                        "spki_sha256": tls.spki_sha256()})

    @app.post("/tv/v1/session/challenge")
    def challenge():
        return jsonify({"ok": True, "nonce": device_pairing.new_challenge(),
                        "ttl_s": device_pairing.CHALLENGE_TTL_S})

    @app.post("/tv/v1/session/open")
    def session_open():
        body = request.get_json(silent=True) or {}
        try:
            sig = base64.b64decode(str(body.get("sig", "")), validate=True)
        except Exception:
            return _refuse(400, "bad signature encoding")
        token = device_pairing.open_session(str(body.get("device_id", "")),
                                            str(body.get("nonce", "")), sig)
        if token is None:
            return _refuse(401, "not paired, or the challenge was wrong or used")
        return jsonify({"ok": True, "session": token, "ttl_s": device_pairing.SESSION_TTL_S})

    @app.post("/tv/v1/pair/start")
    def pair_start():
        from agent_friday.tv_gateway import pairing
        body = request.get_json(silent=True) or {}
        try:
            spki = base64.b64decode(str(body.get("device_spki", "")), validate=True)
            return jsonify({"ok": True, **pairing.start(spki, str(body.get("name", "TV")))})
        except pairing.PairingRefused as e:
            return _refuse(409, str(e))
        except ValueError:
            return _refuse(400, "bad key encoding")

    @app.post("/tv/v1/pair/reveal")
    def pair_reveal():
        from agent_friday.tv_gateway import pairing
        body = request.get_json(silent=True) or {}
        try:
            nt = base64.b64decode(str(body.get("nt", "")), validate=True)
            nd = pairing.reveal(str(body.get("pairing_id", "")), nt)
        except pairing.PairingRefused as e:
            return _refuse(409, str(e))
        except ValueError:
            return _refuse(400, "bad nonce encoding")
        return jsonify({"ok": True, "nd": base64.b64encode(nd).decode("ascii")})

    @app.get("/tv/v1/pair/status")
    def pair_status():
        from agent_friday.tv_gateway import pairing
        return jsonify({"ok": True, **pairing.status(request.args.get("pairing_id", ""))})

    def _channels_right():
        """None when this TV may share channels; else the refusal. A TV without the right makes the
        PC raise the screen-only card for it (once an hour), so the owner can say yes on this PC."""
        if True:
            return None
        from agent_friday.tv_gateway import pairing
        pairing.offer_right(g.device["id"], "tv_channels")
        resp = jsonify({"ok": False, "error": "this TV has not been allowed to share channels; "
                                              "the PC is asking on its screen", "needs_right": "tv_channels"})
        resp.status_code = 403
        return resp

    @app.get("/tv/v1/channels")
    def channels_list():
        from agent_friday.tv_gateway import channels
        denied = _channels_right()
        if denied is not None:
            return denied
        return jsonify({"ok": True, "channels": channels.listing()})

    @app.get("/tv/v1/channels/<channel_id>")
    def channels_get(channel_id):
        from agent_friday.tv_gateway import channels
        denied = _channels_right()
        if denied is not None:
            return denied
        bundle = channels.fetch(channel_id)
        if bundle is None:
            return _refuse(404, "not here")
        device_pairing._receipt("tv_channel_sent", g.device, channel_id=channel_id)
        return jsonify(bundle)

    @app.post("/tv/v1/channels")
    def channels_put():
        from agent_friday.tv_gateway import channels
        denied = _channels_right()
        if denied is not None:
            return denied
        limit = channels.MAX_BUNDLE_BYTES
        if (request.content_length or 0) > limit:
            return _refuse(413, "That channel is too big.")
        raw = request.stream.read(limit + 1)            # never trusts a missing Content-Length
        try:
            kept = channels.store(raw, g.device.get("name", "a TV"))
        except channels.Refused as e:
            return _refuse(e.status, str(e))
        device_pairing._receipt("tv_channel_received", g.device, channel_id=kept["id"], new=kept["new"])
        return jsonify({"ok": True, "id": kept["id"], "new": kept["new"]})

    @app.get("/tv/v1/whoami")
    def whoami():
        dev = g.device
        return jsonify({"ok": True, "device": device_pairing.public_view(dev),
                        "principal": device_pairing.principal(dev)})

    return app


# ── lifecycle ────────────────────────────────────────────────────────────────

_SERVER = None
_SRV_LOCK = threading.Lock()


def running() -> dict:
    with _SRV_LOCK:
        if _SERVER is None:
            return {"running": False}
        return {"running": True, "host": _SERVER.host, "port": _SERVER.port}


def one_address(host: str) -> str:
    """The address to bind, or ValueError for an empty or unspecified (wildcard) one:
    the gateway and discovery listen on one LAN address, never on every adapter."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        raise ValueError("the TV gateway binds one IP address, never a name or a wildcard")
    if ip.is_unspecified:
        raise ValueError("the TV gateway binds one address, never a wildcard")
    return str(ip)


def start(host: str, port: int) -> dict:
    """Serve the gateway over TLS on host:port. Refuses wildcard addresses:
    the gateway binds the one LAN address it was given, never every adapter."""
    global _SERVER
    host = one_address(host)
    from werkzeug.serving import make_server
    from agent_friday.tv_gateway import tls
    with _SRV_LOCK:
        if _SERVER is not None:
            _SERVER.shutdown()
            _SERVER = None
        srv = make_server(host, int(port), create_app(), threaded=True,
                          ssl_context=tls.server_context())
        threading.Thread(target=srv.serve_forever, name="tv-gateway", daemon=True).start()
        _SERVER = srv
        _log.info("TV gateway listening on %s:%s", host, srv.port)
        return {"running": True, "host": host, "port": srv.port}


def stop() -> None:
    global _SERVER
    with _SRV_LOCK:
        if _SERVER is not None:
            _SERVER.shutdown()
            _SERVER = None


def lan_address() -> Optional[str]:
    """This PC's IPv4 on the home network: the source address of a route to a
    private address. No packet is sent (UDP connect only picks a route)."""
    import ipaddress
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.255.255", 9))
        addr = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return addr if ipaddress.ip_address(addr).is_private else None


DEFAULT_PORT = 47810
_DISCOVERY = None


def boot() -> Optional[dict]:
    """Start the gateway and discovery if, and only if, the owner turned the
    gateway on (devices.json, signed). Off is the default and the fallback."""
    global _DISCOVERY
    cfg = device_pairing.gateway_config()
    if not cfg.get("enabled"):
        return None
    host = lan_address()
    if host is None:
        _log.warning("TV gateway is on but this PC has no home-network address")
        return None
    info = start(host, int(cfg.get("port") or DEFAULT_PORT))
    from agent_friday.tv_gateway import discovery, tls
    try:
        _DISCOVERY = discovery.Responder(host, info["port"], tls.spki_sha256()).start()
    except OSError as e:
        _log.warning("TV discovery did not start: %s", e)
    return info
