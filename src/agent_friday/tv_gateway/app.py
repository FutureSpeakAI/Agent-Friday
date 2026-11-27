"""The TV gateway's Flask app and lifecycle (D1: hello, sessions, whoami)."""
from __future__ import annotations

import base64
import logging
import os
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
_AUTH_RATE = 600      # requests per paired device per minute (an app that polls stays far below it)

# Body, depth and connection limits (threat assessment 2026-11-21, fix 3).
MAX_OPEN_BODY_BYTES = 16 * 1024          # a body on a path anyone may reach (pair/start ...)
MAX_JSON_BODY_BYTES = 2 * 1024 * 1024    # a JSON body from a paired device
RAW_BODY_PATHS = frozenset({"/tv/v1/stt", "/tv/v1/channels"})   # audio and channel uploads check their own size
MAX_JSON_DEPTH = 32
MAX_PER_SOURCE = 16                      # connections held at once by one address
MAX_CONNECTIONS = 96                     # connections held at once, all addresses
IDLE_TIMEOUT_S = 20                      # a connection that sends nothing for this long is closed
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class _Bucket:
    def __init__(self, rate: int = _RATE):
        self._rate = rate
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self._rate:
                return False
            q.append(now)
            return True

    def wait_s(self, key: str) -> int:
        """Whole seconds until this key may ask again (at least 1)."""
        with self._lock:
            q = self._hits.get(key)
            return max(1, int(60 - (time.monotonic() - q[0]) + 1)) if q else 1


_BUCKET = _Bucket()
_AUTH_BUCKET = _Bucket(_AUTH_RATE)


def _check_depth(text) -> None:
    """ValueError for JSON nested deeper than MAX_JSON_DEPTH. Brackets inside strings are not depth."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "replace")
    if text.count("[") + text.count("{") <= MAX_JSON_DEPTH:
        return
    depth, in_string, escaped, backslash = 0, False, False, chr(92)
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == backslash:
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON nested too deeply")
        elif ch in "]}":
            depth -= 1


def _mark_too_deep() -> None:
    try:
        from flask import g
        g.json_too_deep = True
    except RuntimeError:                 # outside a request: nothing to mark
        pass


def json_was_too_deep() -> bool:
    """True when this request's body was refused for nesting deeper than MAX_JSON_DEPTH."""
    try:
        from flask import g
        return bool(getattr(g, "json_too_deep", False))
    except RuntimeError:
        return False


def _bounded_json_provider(app):
    from flask.json.provider import DefaultJSONProvider

    class _BoundedJSON(DefaultJSONProvider):
        def loads(self, s, **kwargs):
            try:
                _check_depth(s)
            except ValueError:
                # get_json(silent=True) reads this as "no JSON"; a route that answers too-deep JSON with its own rule
                # (pc_userws: a 422) asks json_was_too_deep().
                _mark_too_deep()
                raise
            return super().loads(s, **kwargs)

    return _BoundedJSON(app)


def _refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp


def _slow_down(bucket: "_Bucket", key: str) -> Response:
    resp = _refuse(429, "slow down", code="rate_limited")
    resp.headers["Retry-After"] = str(bucket.wait_s(key))
    return resp


def _bearer() -> str:
    h = request.headers.get("Authorization", "")
    return h[len(AUTH_SCHEME):].strip() if h.startswith(AUTH_SCHEME) else ""


def create_app(tailnet: bool = False) -> Flask:
    """``tailnet=True`` is the second listener on the Tailscale address (tv_gateway/tailnet): same door,
    same device-session checks, but no pairing and only tailnet peers."""
    app = Flask("agent_friday.tv_gateway")
    app.config["SESSION_COOKIE_NAME"] = "__tv_gateway_unused"
    app.config["TAILNET"] = bool(tailnet)
    app.json = _bounded_json_provider(app)

    @app.before_request
    def _gate():
        if any(request.headers.get(h) for h in MAIN_APP_HEADERS):
            return _refuse(401, "main-app credentials are not accepted here")
        g.device = None
        g.via = "tailnet" if tailnet else "lan"
        if tailnet:
            from agent_friday.tv_gateway import tailnet as tn
            if request.path.startswith("/tv/v1/pair/"):
                return _refuse(403, "pairing is only done from your home network", code="pairing_lan_only")
            if not tn.is_tailnet_address(request.remote_addr or ""):
                return _refuse(403, "this door is only for devices on your tailnet", code="not_tailnet")
        if request.method in _BODY_METHODS and request.path not in RAW_BODY_PATHS:
            limit = MAX_OPEN_BODY_BYTES if request.path in OPEN_PATHS else MAX_JSON_BODY_BYTES
            if (request.content_length or 0) > limit:
                return _refuse(413, "that request is too big", code="body_too_large")
            if request.content_length is None and "chunked" in request.headers.get("Transfer-Encoding", "").lower():
                request.max_content_length = limit + 1       # a chunked body has no length to check: read one past
                if len(request.get_data(cache=True)) > limit:
                    return _refuse(413, "that request is too big", code="body_too_large")
        if request.path in OPEN_PATHS:
            if not _BUCKET.allow(request.remote_addr or "?"):
                return _slow_down(_BUCKET, request.remote_addr or "?")
            return None
        dev = device_pairing.check_session(_bearer())
        if dev is None:
            # NOT "unpaired": the device may be paired fine and only lack a live session (a restart, a
            # day gone by). Open a new one (challenge, then open); only a refusal THERE means unpaired.
            return _refuse(401, "device session required", code="session_required", action="open_session")
        g.device = dev
        if not _AUTH_BUCKET.allow(str(dev.get("id"))):
            return _slow_down(_AUTH_BUCKET, str(dev.get("id")))
        return None

    @app.errorhandler(413)
    def _too_big(e):
        return _refuse(413, "that request is too big", code="body_too_large")

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
        try:
            token = device_pairing.open_session(str(body.get("device_id", "")),
                                                str(body.get("nonce", "")), sig)
        except device_pairing.ReconfirmNeeded as e:
            return _refuse(403, "This device's %d days are up. Ask the owner to press Approve on the Keep this "
                                "device paired card on the PC, then try again." % e.days, code="reconfirm_needed")
        if token is None:
            return _refuse(401, "not paired, or the challenge was wrong or used", code="not_paired_or_bad_challenge")
        from agent_friday.tv_gateway import tailnet as tn
        return jsonify({"ok": True, "session": token, "ttl_s": device_pairing.SESSION_TTL_S,
                        "tailnet": tn.info()})                  # where to fail over to away from home, or null

    @app.post("/tv/v1/pair/start")
    def pair_start():
        from agent_friday.tv_gateway import pairing
        body = request.get_json(silent=True) or {}
        try:
            spki = base64.b64decode(str(body.get("device_spki", "")), validate=True)
            return jsonify({"ok": True, **pairing.start(spki, str(body.get("name", "TV")),
                                                        str(body.get("kind") or "tv"),
                                                        remote=request.remote_addr or "")})
        except pairing.PairingRefused as e:
            return _refuse(400 if str(e) == "unknown device kind" else 409, str(e),
                           **({"code": e.code} if e.code else {}))
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

    @app.post("/tv/v1/messages")
    def messages():
        """One TV turn answered by this PC (tv_gateway/turn), in the Messages
        stream format. A refusal before the first event is an HTTP status the
        TV acts on (503: answer with your own brain); after it, an error event."""
        from flask import stream_with_context
        from agent_friday.tv_gateway import turn
        principal = turn.principal_from(request.headers.get("X-Friday-Principal"))
        try:
            events = turn.run(g.device, request.get_json(silent=True) or {}, principal)
            first = next(events, None)
        except turn.TurnRefused as e:
            return _refuse(e.status, str(e), **({"code": e.code} if e.code else {}), **e.extra)
        except Exception as e:                       # the routing mode, a local-only run, a provider down:
            _log.info("TV turn not started: %s", type(e).__name__)
            return _refuse(503, "this computer can't answer that right now")

        def stream():
            if first is not None:
                yield first
            try:
                yield from events
            except Exception as e:
                _log.warning("TV turn ended early: %s", type(e).__name__)
                yield turn._event({"type": "error", "error": {"type": "api_error",
                                                              "message": "the computer stopped answering"}})

        return Response(stream_with_context(stream()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/tv/v1/models")
    def models_slate():
        """The PC's whole chat slate for this device (tv_gateway/models): same source as the desktop
        selector, scoped by the device's rights, each model with whether it can be used now and why not."""
        from agent_friday.tv_gateway import models, owner_turn
        try:
            cat = models.raw_catalog()
            slate = models.rows(g.device, cat)
        except Exception as e:
            _log.warning("model slate failed: %s", type(e).__name__)
            return _refuse(503, "this computer could not list its models right now", code="slate_unavailable")
        owner = owner_turn.is_owner_device(g.device)
        q = (request.args.get("q") or "").strip().lower()
        prov = (request.args.get("provider") or "").strip().lower()
        shown = [r for r in slate if (not q or q in (r["id"] + " " + r["name"]).lower())
                 and (not prov or prov == str(r["provider"]).lower())]
        return jsonify({"ok": True, "scope": "owner" if owner else "household", "count": len(shown),
                        "total": len(slate), "selected": next((r["id"] for r in slate if r["selected"]), None),
                        "voice_default": next((r["id"] for r in slate if r["available"] and r["voice"]["suitable"]
                                               and r["speed"] == "fast" and r["kind"] == "cloud"), None),
                        "providers": [{"name": p.get("name"), "label": p.get("label"),
                                       "available": p.get("available")} for p in cat.get("providers") or []],
                        "models": shown})

    from agent_friday.tv_gateway import voices as _voices
    _voices.register(app, _refuse)               # GET /tv/v1/voices, POST /tv/v1/voice/switch
    from agent_friday.tv_gateway import conversation_state as _conversation_state
    _conversation_state.register(app, _refuse)   # POST /tv/v1/model/switch, GET /tv/v1/conversations/<id>/state
    from agent_friday.tv_gateway import tv_memory_route as _tv_memory_route
    _tv_memory_route.register(app, _refuse)      # GET/POST /tv/v1/tv-memory (TV-P1; a change is owner devices only)

    @app.get("/tv/v1/stt")
    def stt_ready():
        """The TV's probe: is dictation offered here, and with which model."""
        from agent_friday.tv_gateway import speech as sp
        return jsonify({"ok": True, **sp.ready()})

    @app.post("/tv/v1/stt")
    def stt():
        """The TV's microphone audio (16 kHz mono PCM16) heard on this PC
        (tv_gateway/speech). ?cleanup=1 tidies it with this PC's local model."""
        from agent_friday.tv_gateway import speech as sp
        from agent_friday.tv_gateway.turn import principal_from
        limit = sp.MAX_SECONDS * sp.BYTES_PER_SECOND
        if (request.content_length or 0) > limit:
            return _refuse(413, "say it in under a minute")
        pcm = request.stream.read(limit + 1)
        try:
            out = sp.transcribe(g.device, pcm, cleanup=request.args.get("cleanup") == "1",
                                principal=principal_from(request.headers.get("X-Friday-Principal")),
                                hint=request.headers.get("X-Friday-Hint", ""))
        except sp.SpeechRefused as e:
            return _refuse(e.status, str(e))
        finally:
            pcm = None                                   # the audio is not kept past this request
        return jsonify({"ok": True, **out})

    def _channels_right():
        """None when this TV may share channels; else the refusal. A TV without the right makes the
        PC raise the screen-only card for it (once an hour), so the owner can say yes on this PC."""
        if device_pairing.has_right(g.device, "tv_channels"):
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
        from agent_friday.tv_gateway import tailnet as tn
        return jsonify({"ok": True, "device": device_pairing.public_view(dev),
                        "principal": device_pairing.principal(dev), "via": tn.via(), "tailnet": tn.info()})

    from agent_friday.tv_gateway import inherit
    inherit.register(app)
    from agent_friday.tv_gateway import pc_read
    pc_read.register(app)                    # the owner phone/tablet read door (chats, workspaces, jobs, media)
    from agent_friday.tv_gateway import pc_chats
    pc_chats.register(app)                   # start a PC chat from a device (POST /tv/v1/chats); projects
    from agent_friday.tv_gateway import ws_content
    ws_content.register(app)                 # list, search, item and changes of a workspace, from its device block
    from agent_friday.tv_gateway import ws_view
    ws_view.register(app)                    # a workspace as an A2UI document, and the one-tap signed edit
    from agent_friday.tv_gateway import ws_graph
    ws_graph.register(app)                   # the knowledge graph by level of detail
    from agent_friday.tv_gateway import pc_orbs
    pc_orbs.register(app)                    # the same owners' door, live process orbs
    from agent_friday.tv_gateway import pc_act, pc_approve
    pc_approve.register(app)
    pc_act.register(app)
    from agent_friday.tv_gateway import pc_grants
    pc_grants.register(app)             # standing grants: list, revoke, stop an offer (K5 PR D)
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


def make_bounded_server(host: str, port: int, app, ssl_context=None):
    """The gateway's listener: services/pooled_server (a fixed worker pool, the TLS handshake and every read in
    a worker under a timeout, a full queue answered 503) with a cap per source address and in all, and an idle
    time of IDLE_TIMEOUT_S. A connection over a cap is answered 503 at once and closed."""
    import socket
    from agent_friday.services import pooled_server as ps

    class _GatewayServer(ps.PooledWSGIServer):
        def __init__(self, *a, **kw):
            self._held = {}
            self._held_lock = threading.Lock()
            super().__init__(*a, **kw)

        def process_request(self, request, client_address):
            source = str(client_address[0])
            with self._held_lock:
                over = (len(self._held) >= MAX_CONNECTIONS
                        or sum(1 for src in self._held.values() if src == source) >= MAX_PER_SOURCE)
                if not over:
                    self._held[id(request)] = source
            if over:
                self.stats.rejected += 1
                try:
                    request.settimeout(0.2)
                    request.sendall(ps._BUSY_503)
                    request.shutdown(socket.SHUT_WR)        # read what the client sent, or its reset eats the 503
                    request.settimeout(0.05)
                    request.recv(65536)
                except OSError:
                    pass
                try:
                    super().shutdown_request(request)
                except OSError:
                    pass
                return
            super().process_request(request, client_address)

        def finish_request(self, request, client_address):
            request.settimeout(IDLE_TIMEOUT_S)
            super().finish_request(request, client_address)

        def shutdown_request(self, request):
            with self._held_lock:
                self._held.pop(id(request), None)
            super().shutdown_request(request)

    return _GatewayServer(host, int(port), ps.per_request_context(app), ssl_context=ssl_context)


def close_server(srv) -> None:
    """Stop serving and release the port and the worker pool."""
    for step in ("shutdown", "server_close"):
        try:
            getattr(srv, step)()
        except Exception:
            pass


def start(host: str, port: int) -> dict:
    """Serve the gateway over TLS on host:port. Refuses wildcard addresses:
    the gateway binds the one LAN address it was given, never every adapter."""
    global _SERVER
    host = one_address(host)
    from agent_friday.tv_gateway import tls
    with _SRV_LOCK:
        if _SERVER is not None:
            close_server(_SERVER)
            _SERVER = None
        srv = make_bounded_server(host, int(port), create_app(), ssl_context=tls.server_context())
        threading.Thread(target=srv.serve_forever, name="tv-gateway", daemon=True).start()
        _SERVER = srv
        _log.info("TV gateway listening on %s:%s", host, srv.port)
        return {"running": True, "host": host, "port": srv.port}


def stop() -> None:
    global _SERVER
    with _SRV_LOCK:
        if _SERVER is not None:
            close_server(_SERVER)
            _SERVER = None


def lan_address() -> Optional[str]:
    """This PC's IPv4 on the home network: the source address of a route to a
    private address. Chosen from the adapters (tv_gateway/tailnet.lan_address), so a VPN that takes the
    default route (PIA) cannot make its tunnel address "the LAN". Only when the adapters cannot be listed
    does it fall back to the route's source address (no packet is sent: UDP connect only picks a route)."""
    import ipaddress
    import socket
    from agent_friday.tv_gateway import tailnet as tn
    if tn._nics():
        return tn.lan_address()
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
    # Off-LAN reach for already-paired devices: a second listener on the Tailscale address only (never
    # pairing, never discovery), opened now if Tailscale is up and followed if it comes up later.
    from agent_friday.tv_gateway import tailnet
    tn_info = tailnet.ensure(info["port"])
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        tailnet.watch(info["port"])
    if tn_info:
        info = dict(info, tailnet=tn_info)
    return info
