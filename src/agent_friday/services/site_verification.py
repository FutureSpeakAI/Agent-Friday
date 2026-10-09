"""Credential-free HTTPS checks pinned to public DNS answers and a build marker."""
from __future__ import annotations

import http.client
import ipaddress
import json
import queue
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from contextvars import ContextVar

from agent_friday.user_errors import UserFacingValueError
from agent_friday.services import sites_privacy

DNS_TIMEOUT = 3
HTTP_DEADLINE = 10
_GENERATION = ContextVar("site_verification_generation", default=None)


def _current():
    generation = _GENERATION.get()
    if generation is not None:
        sites_privacy.require_generation(generation)
    return generation


def _addresses(hostname):
    generation = _current()
    results = queue.Queue(maxsize=1)
    def resolve():
        try:
            if generation is not None:
                sites_privacy.require_generation(generation)
            results.put(socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM))
        except (OSError, ValueError):
            results.put(None)
    # An expired lookup cannot proceed to TLS or an HTTP request in this worker.
    threading.Thread(target=resolve, daemon=True, name="site-dns-check").start()
    try:
        answers = results.get(timeout=DNS_TIMEOUT)
    except queue.Empty:
        raise OSError("DNS verification deadline elapsed.") from None
    _current()
    if not answers:
        raise OSError("Public DNS is unavailable.")
    addresses = sorted({row[4][0] for row in answers})
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast
                            for address in addresses):
        raise UserFacingValueError("Verification refuses private, loopback, link-local or reserved network destinations.")
    return addresses


def _close_socket(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        sock.close()


def _fetch_marker(url):
    _current()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise UserFacingValueError("Verification requires a plain HTTPS hostname.")
    hostname = parsed.hostname.encode("idna").decode("ascii")
    addresses = _addresses(hostname)
    path = parsed.path.rstrip("/") + "/.well-known/friday-deployment.json"
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in path + hostname):
        raise UserFacingValueError("Invalid verification path.")
    # Connect to the validated IP itself. TLS still verifies the original
    # hostname, preventing a second DNS lookup from rebinding to a local API.
    _current()
    with socket.create_connection((addresses[0], 443), timeout=5) as raw:
        _current()
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with context.wrap_socket(raw, server_hostname=hostname) as secure:
            # Independently close a slow-drip header/body stream even if every
            # individual socket read arrives before its ordinary timeout.
            deadline = threading.Timer(HTTP_DEADLINE, _close_socket, args=(secure,))
            deadline.daemon = True
            deadline.start()
            try:
                _current()
                secure.settimeout(5)
                secure.sendall(("GET " + path + " HTTP/1.1\r\nHost: " + hostname +
                                "\r\nConnection: close\r\nAccept: application/json\r\nUser-Agent: Friday/site-check\r\n\r\n").encode("ascii"))
                response = http.client.HTTPResponse(secure)
                response.begin()
                _current()
                # No redirects are followed and no credentials are sent anywhere.
                if response.status != 200:
                    return {"http_status": response.status, "addresses": [addresses[0]], "marker": None}
                body = response.read(65537)
                _current()
                if len(body) > 65536:
                    raise UserFacingValueError("Verification response exceeded its limit.")
                return {"http_status": 200, "addresses": [addresses[0]], "marker": json.loads(body)}
            finally:
                deadline.cancel()


def verify(site, operation, *, generation):
    sites_privacy.require_generation(generation)
    token = _GENERATION.set(generation)
    try:
        result = _verify(site, operation)
        sites_privacy.require_generation(generation)
        return result
    finally:
        _GENERATION.reset(token)


def _verify(site, operation):
    connection = operation["connection"]
    hostname = (operation.get("domain") or {}).get("hostname")
    if hostname:
        url = "https://" + hostname + "/"
    elif connection["adapter"] == "cloudflare_pages":
        url = "https://" + connection["project"] + ".pages.dev/"
    else:
        owner, repo = connection["repo"].split("/", 1)
        url = "https://" + owner.lower() + ".github.io/" + ("" if repo.lower() == owner.lower() + ".github.io" else repo + "/")
    expected = {"site_id": site["site_id"], "operation_id": operation["operation_id"],
                "build_id": operation["build_id"], "output_hash": operation["output_hash"]}
    try:
        evidence = _fetch_marker(url)
        matches = evidence.get("marker") == expected
        return {"status": "verified_live" if matches else "content_pending", "verified": matches,
                "verified_at": time.time() if matches else None, "checked_at": time.time(),
                "url": url, "https": True, "http_status": evidence["http_status"],
                "addresses": evidence["addresses"], "matching_deployment": matches,
                "note": "Checked one current public address, its TLS hostname and the deployment marker. This does not prove DNS propagation everywhere."}
    except ssl.SSLError:
        return {"status": "certificate_pending", "verified": False, "checked_at": time.time(), "url": url,
                "note": "The hostname's TLS certificate could not be verified."}
    except (OSError, ValueError, http.client.HTTPException):
        return {"status": "verification_pending", "verified": False, "checked_at": time.time(), "url": url,
                "note": "Public HTTPS and deployment content are not verified yet; no write was retried."}
