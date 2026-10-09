"""The static server behind "This PC" (docs/design/active/vibe-coding-salon.md §4.10.1).

A separate, minimal process. It shares no process, port, cookies or origin
with Friday's app server, and this file imports nothing from Friday, so the
process that runs it never loads the app (`python published_server.py --root
… --port …`). services/publish_hosting.py starts it and its own cloudflared
tunnel, and stops both on the owner's switch.

What it serves, and only this:

* `GET` and `HEAD` of `/<slug>/` (the site's `index.html`) and `/<slug>/<file>`
  under a read-only published/ folder that Friday fills only through the
  approval card (services/publish_web). A slug is a plain lowercase name.
* Nothing else: no root page, no directory listing, no dotfiles, no
  in-progress `.tmp-*` folders, no other method, no upgrade, no query-driven
  behaviour, and nothing outside the folder however the path is spelled
  (`..`, percent-encoding, backslashes, drive letters, NUL, doubled slashes).
* Every response carries the strict headers: a CSP that sandboxes the page
  into an opaque origin and allows only the pinned package host, nosniff,
  no-referrer, and locked-down permissions.
* A small per-client rate limit (`CF-Connecting-IP` when the tunnel sets it,
  else the peer) kept in memory. No access log. Nothing is written anywhere.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import posixpath
import re
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PACKAGE_HOST = "https://esm.sh"

HEADERS = {
    "Content-Security-Policy": (
        "sandbox allow-scripts allow-downloads; default-src 'none'; "
        "script-src 'unsafe-inline' 'wasm-unsafe-eval' " + PACKAGE_HOST + "; "
        "style-src 'unsafe-inline' " + PACKAGE_HOST + "; connect-src " + PACKAGE_HOST + "; "
        "img-src 'self' data: blob: " + PACKAGE_HOST + "; font-src data: " + PACKAGE_HOST + "; "
        "media-src 'self' data: blob:; worker-src blob:; form-action 'none'; base-uri 'none'; "
        "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "public, max-age=60",
}

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/csv", ".csv")
mimetypes.add_type("application/javascript", ".mjs")


class _RateLimit:
    """Fixed window per client. Memory only; never written anywhere."""

    def __init__(self, per_minute: int):
        self.per_minute = max(1, int(per_minute))
        self._hits: dict = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            window, count = self._hits.get(client, (now, 0))
            if now - window >= 60:
                window, count = now, 0
            count += 1
            self._hits[client] = (window, count)
            if len(self._hits) > 5000:          # forget the oldest windows
                for k in sorted(self._hits, key=lambda k: self._hits[k][0])[:1000]:
                    self._hits.pop(k, None)
        return count <= self.per_minute


def resolve(root: str, raw_path: str):
    """The file for a request path, or None. Only `/<slug>/` and
    `/<slug>/<file>` resolve; everything else is not found."""
    if "\x00" in raw_path or "\\" in raw_path:
        return None
    path = raw_path.split("?", 1)[0].split("#", 1)[0]
    if "%" in path:
        return None                                   # published names are plain; nothing is encoded
    try:
        path = urllib.parse.unquote(path, errors="strict")
    except Exception:
        return None
    if "\x00" in path or "\\" in path or "//" in path:
        return None
    if path.startswith("/") is False:
        return None
    parts = path.split("/")[1:]
    if len(parts) == 1 and parts[0] == "":
        return None                                   # the root itself
    slug = parts[0]
    if not _SLUG.match(slug):
        return None
    if len(parts) == 2 and parts[1] == "":
        name = "index.html"
    elif len(parts) == 2:
        name = parts[1]
    else:
        return None                                   # no nested paths
    if not _FILE.match(name) or name.startswith(".") or name in (".", ".."):
        return None
    if posixpath.normpath("/" + slug + "/" + name) != "/" + slug + "/" + name:
        return None
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, slug, name))
    if not full.startswith(base + os.sep):
        return None
    site = os.path.realpath(os.path.join(base, slug))
    if os.path.dirname(full) != site:
        return None
    if not os.path.isfile(full):
        return None
    return full


def make_handler(root: str, limiter: _RateLimit):
    class Handler(BaseHTTPRequestHandler):
        server_version = "static"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):               # no access log, by design
            pass

        def _client(self) -> str:
            return (self.headers.get("CF-Connecting-IP") or self.client_address[0] or "").strip()

        def _send(self, status: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8", head_only=False):
            if "\r" in ctype or "\n" in ctype:
                ctype = "application/octet-stream"
            self.send_response(status)
            for k, v in HEADERS.items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only and body:
                self.wfile.write(body)

        def _serve(self, head_only: bool):
            if (self.headers.get("Upgrade") or "").lower() or "upgrade" in (self.headers.get("Connection") or "").lower():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", head_only=head_only)
            if not limiter.allow(self._client()):
                return self._send(HTTPStatus.TOO_MANY_REQUESTS, b"slow down", head_only=head_only)
            full = resolve(root, self.path)
            if full is None:
                return self._send(HTTPStatus.NOT_FOUND, b"not found", head_only=head_only)
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                return self._send(HTTPStatus.NOT_FOUND, b"not found", head_only=head_only)
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, data, ctype, head_only=head_only)

        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

        def _refuse(self):
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"method not allowed")

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_PROPFIND = do_TRACE = do_CONNECT = _refuse

    return Handler


def make_server(bind: str, port: int, root, rate_per_minute: int = 600) -> ThreadingHTTPServer:
    limiter = _RateLimit(rate_per_minute)
    srv = ThreadingHTTPServer((bind, int(port)), make_handler(str(root), limiter))
    srv.daemon_threads = True
    return srv


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Friday's published-pages static server (read-only).")
    ap.add_argument("--root", required=True, help="the published/ folder")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--rate", type=int, default=600, help="requests per minute per client")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        os.makedirs(args.root, exist_ok=True)
    srv = make_server(args.bind, args.port, args.root, args.rate)
    sys.stdout.write("serving %s on %s:%d\n" % (args.root, args.bind, srv.server_address[1]))
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
