"""Short-lived capability origins for manifest-verified static build previews."""
from __future__ import annotations

import atexit
import copy
from dataclasses import dataclass, replace
import html
import mimetypes
import re
import secrets
import socket
import threading
import time
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

from agent_friday.user_errors import UserFacingValueError
from agent_friday.services import sites_privacy

LIFETIME = 300
MAX_SESSIONS = 4
MAX_BYTES = 100 * 1024 * 1024
MAX_CONNECTIONS = 8
REQUEST_SECONDS = 5
MAX_HEADERS = 16 * 1024
MAX_TARGET = 2048
ADMISSION_SECONDS = 20
NAVIGATION_SECONDS = 60
MAX_NAVIGATION = 8


def _parent_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise UserFacingValueError("Open this preview from Friday on this PC.")
    port = parsed.port  # Reject malformed or out-of-range ports.
    if port is not None and port < 1:
        raise UserFacingValueError("Open this preview from Friday's actual local port.")
    host = "[::1]" if parsed.hostname == "::1" else parsed.hostname
    return parsed.scheme + "://" + host + ((":" + str(port)) if port else "")


@dataclass(frozen=True)
class _Owner:
    origin: object
    generation: int
    boot: str
    site: dict
    build: dict
    parent_origin: str

    def check(self, *, build=True):
        from agent_friday.services import conversations, sites_operations as sites
        # This is the original trusted object, never a fresh request capture.
        if sites_privacy.admit({"_sites_origin": self.origin}) != self.generation:
            raise UserFacingValueError("The preview's privacy context ended.")
        sites_privacy.require_operation(self.generation, self.boot)
        with sites.LOCK, conversations._LOCK:
            current = sites.validate_site_owner(sites.get_site(self.site["site_id"]))
            if any(current.get(key) != self.site.get(key) for key in
                   ("site_id", "revision", "conversation_id", "project_id", "codebase_id")):
                raise UserFacingValueError("The preview's owning site changed.")
            if build:
                saved = sites._build(current, self.build["build_id"])
                if any(saved.get(key) != self.build.get(key) for key in
                       ("site_id", "build_id", "status", "output_hash", "files")):
                    raise UserFacingValueError("The frozen build changed.")
            sites_privacy.require_operation(self.generation, self.boot)


@dataclass(frozen=True)
class _Preview:
    owner: _Owner
    files: object
    size: int
    expires: float
    expires_at: float
    host: str
    handle: str


def _path(target):
    if not isinstance(target, str) or len(target) > MAX_TARGET or not target.startswith("/"):
        raise UserFacingValueError("Unavailable preview path.")
    if "#" in target or "\\" in target or re.search(r"%(?:2f|5c|00)", target, re.I):
        raise UserFacingValueError("Unavailable preview path.")
    path = unquote(target.split("?", 1)[0], encoding="utf-8", errors="strict")
    if "%" in path or ":" in path or any(ord(char) < 32 for char in path):
        raise UserFacingValueError("Unavailable preview path.")
    path = path[1:]
    if not path or path.endswith("/"):
        path += "index.html"
    if any(not part or part.startswith(".") for part in path.split("/")):
        raise UserFacingValueError("Unavailable preview path.")
    return path


def _headers(preview=None):
    source = "http://" + preview.host if preview else "'none'"
    return {
        "Content-Security-Policy": (
            "sandbox; default-src 'none'; script-src 'none'; "
            "style-src " + source + " 'unsafe-inline'; img-src " + source + " data:; "
            "font-src " + source + " data:; media-src " + source + "; connect-src 'none'; "
            "worker-src 'none'; webrtc 'block'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'"),
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "X-DNS-Prefetch-Control": "off",
        "Cache-Control": "no-store", "Access-Control-Allow-Origin": "*",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), display-capture=()",
    }


class PreviewService:
    """One loopback listener; bounded sessions, snapshots and owned sockets."""

    def __init__(self):
        self.lock = threading.RLock()
        self.previews = {}
        self.listener = None
        self.thread = None
        self.connections = set()
        self.inflight = {}
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def _expire_locked(self):
        now = time.monotonic()
        for host, preview in list(self.previews.items()):
            if preview.expires <= now:
                self.previews.pop(host, None)

    def add(self, owner, files):
        owner.check()
        if any(not isinstance(data, bytes) for data in files.values()):
            raise UserFacingValueError("The frozen preview contains an invalid file.")
        size = sum(len(data) for data in files.values())
        if size > MAX_BYTES:
            raise UserFacingValueError("The frozen preview exceeds its memory limit.")
        with self.lock:
            self._expire_locked()
            retained = {id(p): p for p in (*self.previews.values(), *self.inflight.values())}
            if len(self.previews) >= MAX_SESSIONS or size + sum(p.size for p in retained.values()) > MAX_BYTES:
                raise UserFacingValueError("Close an existing preview or wait for its five-minute expiry before opening another.")
            owner.check()
            self._listen_locked()
            # 49-character DNS label; the hostname carries authority to root assets.
            host = "p" + secrets.token_hex(24) + ".localhost:" + str(self.listener.getsockname()[1])
            while host in self.previews:
                host = "p" + secrets.token_hex(24) + ".localhost:" + str(self.listener.getsockname()[1])
            preview = _Preview(owner, MappingProxyType(dict(files)), size, time.monotonic() + LIFETIME,
                               time.time() + LIFETIME, host, "p" + secrets.token_hex(24))
            self.previews[host] = preview
            try:
                owner.check()
            except Exception:
                self.previews.pop(host, None)
                raise
            return preview

    def _listen_locked(self):
        if self.listener is not None:
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(MAX_CONNECTIONS)
            listener.settimeout(0.25)
            self.listener = listener
            self.thread = threading.Thread(target=self._serve, args=(listener,), name="site-preview", daemon=True)
            self.thread.start()
        except BaseException:
            self.listener = None
            self.thread = None
            listener.close()
            raise

    def revoke(self, preview):
        with self.lock:
            if self.previews.get(preview.host) is preview:
                self.previews.pop(preview.host, None)

    def check(self, preview, *, build=True):
        with self.lock:
            if self.previews.get(preview.host) is not preview or preview.expires <= time.monotonic():
                raise UserFacingValueError("The preview expired.")
        try:
            preview.owner.check(build=build)
        except Exception:
            self.revoke(preview)
            raise

    def lookup(self, host, target, *, wire=None):
        with self.lock:
            preview = self.previews.get(host)
            if preview is not None and wire is not None:
                self.inflight[wire] = preview
        if preview is None:
            raise UserFacingValueError("The preview is unavailable.")
        self.check(preview)
        path = _path(target)
        data = preview.files.get(path)
        if data is None:
            raise UserFacingValueError("The preview file is unavailable.")
        self.check(preview)
        suffix = path.rsplit(".", 1)[-1].lower()
        content_type = "application/javascript" if suffix in {"js", "mjs"} else "application/wasm" if suffix == "wasm" else (
            mimetypes.guess_type(path)[0] or "application/octet-stream")
        return preview, data, content_type

    def by_handle(self, handle):
        if not re.fullmatch(r"p[a-f0-9]{48}", str(handle)):
            raise UserFacingValueError("The preview is unavailable.")
        with self.lock:
            preview = next((item for item in self.previews.values() if item.handle == handle), None)
        if preview is None:
            raise UserFacingValueError("The preview expired.")
        self.check(preview)
        return preview

    @staticmethod
    def _close(wire):
        try:
            wire.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        wire.close()

    def _serve(self, listener):
        try:
            while True:
                with self.lock:
                    self._expire_locked()
                    if self.listener is not listener or not self.previews:
                        if self.listener is listener:
                            self.listener = None
                        return
                    active = tuple(self.previews.values())
                for preview in active:
                    try:
                        self.check(preview, build=False)
                    except Exception:
                        pass
                try:
                    wire, peer = listener.accept()
                    accepted_at = time.monotonic()
                except socket.timeout:
                    continue
                except OSError:
                    return
                if peer[0] != "127.0.0.1" or not self.slots.acquire(blocking=False):
                    self._close(wire)
                    continue
                cutoff = None
                try:
                    cutoff = threading.Timer(max(0, REQUEST_SECONDS - (time.monotonic() - accepted_at)),
                                             self._close, args=(wire,))
                    cutoff.daemon = True
                    cutoff.start()
                    with self.lock:
                        if self.listener is not listener:
                            cutoff.cancel()
                            self._close(wire)
                            self.slots.release()
                            continue
                        self.connections.add(wire)
                    thread = threading.Thread(target=self._request, args=(wire, cutoff), daemon=True,
                                              name="site-preview-request")
                    thread.start()
                except BaseException:
                    if cutoff is not None:
                        cutoff.cancel()
                    with self.lock:
                        self.connections.discard(wire)
                    self._close(wire)
                    self.slots.release()
                    raise
        finally:
            listener.close()
            with self.lock:
                if self.listener is listener:
                    self.listener = None
                    self.previews.clear()

    def _request(self, wire, cutoff):
        started = False
        try:
            wire.settimeout(REQUEST_SECONDS)
            raw = bytearray()
            while b"\r\n\r\n" not in raw:
                chunk = wire.recv(min(4096, MAX_HEADERS + 1 - len(raw)))
                if not chunk:
                    return
                raw.extend(chunk)
                if len(raw) > MAX_HEADERS:
                    return
            lines = bytes(raw).split(b"\r\n\r\n", 1)[0].decode("iso-8859-1").split("\r\n")
            method, target, version = lines[0].split(" ")
            headers = {}
            for line in lines[1:]:
                name, value = line.split(":", 1)
                if not re.fullmatch(r"[A-Za-z0-9-]+", name) or name.lower() in headers:
                    raise UserFacingValueError("Unsupported headers.")
                headers[name.lower()] = value.strip()
            if (method not in {"GET", "HEAD"} or version not in {"HTTP/1.0", "HTTP/1.1"}
                    or headers.get("content-length", "0") != "0" or "transfer-encoding" in headers
                    or "upgrade" in headers or len(target) > MAX_TARGET):
                raise UserFacingValueError("Unsupported preview request.")
            preview, data, content_type = self.lookup(headers.get("host", ""), target, wire=wire)
            self.check(preview)
            response = {**_headers(preview), "Content-Type": content_type,
                        "Content-Length": str(len(data)), "Connection": "close"}
            started = True
            wire.sendall(("HTTP/1.0 200 OK\r\n" + "".join(key + ": " + value + "\r\n" for key, value in response.items())
                          + "\r\n").encode("ascii"))
            if method == "GET":
                for offset in range(0, len(data), 64 * 1024):
                    self.check(preview, build=False)
                    wire.sendall(data[offset:offset + 64 * 1024])
        except Exception:
            # No access/error logging: Host is a private bearer capability.
            # A closed response carries no sensitive exception or app content.
            if not started:
                try:
                    body = b"Preview unavailable."
                    headers = {**_headers(), "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'",
                               "Content-Type": "text/plain", "Content-Length": str(len(body)), "Connection": "close"}
                    wire.sendall(("HTTP/1.0 404 Not Found\r\n" + "".join(key + ": " + value + "\r\n" for key, value in headers.items())
                                  + "\r\n").encode("ascii") + body)
                except OSError:
                    pass
        finally:
            cutoff.cancel()
            self._close(wire)
            with self.lock:
                self.connections.discard(wire)
                self.inflight.pop(wire, None)
            self.slots.release()

    def shutdown(self):
        with self.lock:
            listener, thread = self.listener, self.thread
            self.listener = None
            self.previews.clear()
            connections = tuple(self.connections)
        if listener is not None:
            self._close(listener)
        for wire in connections:
            self._close(wire)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)


_SERVICE = PreviewService()
_ADMISSION = threading.BoundedSemaphore(1)
_NAVIGATION_LOCK = threading.RLock()
_NAVIGATION = {}
atexit.register(_SERVICE.shutdown)


def issue(site_id, site_revision, build_id, *, origin, parent_origin):
    """Only the authenticated local UI route may receive this temporary URL."""
    if not _ADMISSION.acquire(blocking=False):
        raise UserFacingValueError("Another preview is being prepared. Try again after it finishes.")
    try:
        return _snapshot(_owner(site_id, site_revision, build_id, origin=origin,
                                parent_origin=_parent_origin(parent_origin)))
    finally:
        _ADMISSION.release()


def _owner(site_id, site_revision, build_id, *, origin, parent_origin=""):
    from agent_friday.services import sites_operations as sites
    generation = sites_privacy.admit({"_sites_origin": origin})
    boot = sites_privacy.boot_id()
    if type(site_revision) is not int or site_revision < 1:
        raise UserFacingValueError("Choose the current saved site revision.")
    sites_privacy.require_operation(generation, boot)
    site = copy.deepcopy(sites.validate_site_owner(sites.get_site(site_id)))
    sites_privacy.require_operation(generation, boot)
    if site["revision"] != site_revision:
        raise UserFacingValueError("The saved site changed. Open its current preview again.")
    build = copy.deepcopy(sites._build(site, build_id))
    sites_privacy.require_operation(generation, boot)
    if build.get("status") != "built":
        raise UserFacingValueError("Choose a successful frozen build.")
    owner = _Owner(origin, generation, boot, site, build, parent_origin)
    owner.check()
    return owner


def _expire_navigation():
    now = time.monotonic()
    for request_id, (_owner, expires) in list(_NAVIGATION.items()):
        if expires <= now:
            _NAVIGATION.pop(request_id, None)


def prepare_navigation(site_id, site_revision, build_id, *, origin):
    """One-use desktop-bus ticket; callers must exclude it from model/history."""
    owner = _owner(site_id, site_revision, build_id, origin=origin)
    with _NAVIGATION_LOCK:
        _expire_navigation()
        if len(_NAVIGATION) >= MAX_NAVIGATION:
            raise UserFacingValueError("Too many pending previews. Open Sites directly or wait a minute.")
        owner.check()
        request_id = "n" + secrets.token_hex(24)
        while request_id in _NAVIGATION:
            request_id = "n" + secrets.token_hex(24)
        _NAVIGATION[request_id] = (owner, time.monotonic() + NAVIGATION_SECONDS)
        try:
            owner.check()
        except Exception:
            _NAVIGATION.pop(request_id, None)
            raise
        return request_id


def issue_navigation(site_id, site_revision, build_id, request_id, *, parent_origin):
    """Consume original chat/voice authority; there is no fresh-origin fallback."""
    if not isinstance(request_id, str) or not re.fullmatch(r"n[a-f0-9]{48}", request_id):
        raise UserFacingValueError("The automatic preview request is unavailable.")
    with _NAVIGATION_LOCK:
        _expire_navigation()
        entry = _NAVIGATION.pop(request_id, None)
    if entry is None:
        raise UserFacingValueError("The automatic preview request expired or was already used.")
    owner, expires = entry
    if (type(site_revision) is not int or site_id != owner.site["site_id"]
            or site_revision != owner.site["revision"] or build_id != owner.build["build_id"]):
        raise UserFacingValueError("The automatic preview selection changed.")
    owner.check()
    if not _ADMISSION.acquire(blocking=False):
        raise UserFacingValueError("Another preview is being prepared. Open Sites after it finishes.")
    try:
        return _snapshot(replace(owner, parent_origin=_parent_origin(parent_origin)), expires=expires)
    finally:
        _ADMISSION.release()


def _snapshot(owner, *, expires=None):
    from agent_friday.services import publish_web, site_builds
    site, build = owner.site, owner.build
    deadline = time.monotonic() + ADMISSION_SECONDS
    if expires is not None:
        deadline = min(deadline, expires)

    def guard():
        if time.monotonic() >= deadline:
            raise UserFacingValueError("The build took too long to prepare for preview.")
        owner.check(build=False)
        if time.monotonic() >= deadline:
            raise UserFacingValueError("The preview request expired while validating its owner.")

    owner.check()
    files = site_builds.preview_output(site, build, guard=guard)
    bundle = publish_web.Bundle(files, site["name"], site["site_id"], "site", site["site_id"],
                                site["conversation_id"], site["revision"])
    if not publish_web.scan(bundle)["ok"]:
        raise UserFacingValueError("The frozen build no longer passes its preview scan.")
    guard()
    owner.check()
    preview = _SERVICE.add(owner, files)
    try:
        guard()
        _SERVICE.check(preview)
        guard()
        return {"status": "ok", "site_id": site["site_id"], "site_revision": site["revision"],
                "build_id": build["build_id"], "preview_url": "/api/sites/preview-frame/" + preview.handle,
                "expires_at": preview.expires_at}
    except Exception:
        _SERVICE.revoke(preview)
        raise


def check_response(result):
    """Recheck retained original authority immediately before route serialization."""
    _SERVICE.by_handle(result["preview_url"].rsplit("/", 1)[-1])


def wrapper(handle, *, parent_origin):
    """Fixed scriptless HTML only; the authenticated handle is not the bearer host."""
    preview = _SERVICE.by_handle(handle)
    if _parent_origin(parent_origin) != preview.owner.parent_origin:
        raise UserFacingValueError("Open this preview from its original Friday page.")
    source = "http://" + preview.host
    body = ('<!doctype html><html><head><meta charset="utf-8"><title>Frozen build preview</title>'
            '<style>html,body,iframe{margin:0;width:100%;height:100%;border:0}iframe{display:block}</style>'
            '</head><body><iframe title="Frozen site content" sandbox="" referrerpolicy="no-referrer" src="'
            + html.escape(source + "/", quote=True) + '"></iframe></body></html>')
    headers = {
        "Content-Security-Policy": "sandbox; default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
        "frame-src " + source + "; frame-ancestors 'self'; connect-src 'none'; webrtc 'block'; base-uri 'none'; form-action 'none'",
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
        "X-DNS-Prefetch-Control": "off",
        "X-Friday-Site-Preview": "active",
        "Permissions-Policy": _headers(preview)["Permissions-Policy"],
    }
    _SERVICE.check(preview)
    return body, headers


def close(handle):
    """Authenticated local UI cleanup can discard even an expired/private session."""
    with _SERVICE.lock:
        preview = next((item for item in _SERVICE.previews.values() if item.handle == handle), None)
    if preview is not None:
        _SERVICE.revoke(preview)
