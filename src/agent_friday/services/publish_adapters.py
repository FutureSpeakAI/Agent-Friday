"""Hosted adapters for publish to web: Cloudflare Pages and GitHub Pages.

docs/design/active/vibe-coding-salon.md §4.10.1. These are for pages that must
stay up when the user's PC is off. They use the user's OWN account, connected
once in Settings (services/publish_hosting.connect_adapter); Friday never
hosts, never registers a domain and never picks a vendor for the user.

One deployment is a full snapshot of every page published through that
adapter. A local mirror under `~/.friday/published-remote/<adapter>/<slug>/`
is the source of truth, so a second page redeploys both and taking one down
is a redeploy without it. The token travels only in the Authorization header;
it never appears in a URL, an error message or a log line.

Every request goes through `_http`, one function, so tests replace it with a
fake and nothing here touches the network under a test. The first real run
against each provider is a gated, owner-approved check: the request shapes
below follow the providers' documented REST forms and are verified then.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import mimetypes
import re
import queue
import shutil
import socket
import ssl
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from agent_friday.paths import contained

_log = logging.getLogger(__name__)

LABELS = {"cloudflare_pages": "Cloudflare Pages", "github_pages": "GitHub Pages"}
_UA = "Friday/publish-to-web"
_SITE_DEADLINE = ContextVar("site_publish_deadline", default=None)
_SITE_GENERATION = ContextVar("site_publish_generation", default=None)
_SITE_RESPONSE_LIMIT = 4_000_000


class AdapterError(RuntimeError):
    """The provider refused or failed. The message never carries the token."""


# ── one HTTP function ────────────────────────────────────────────────────────

def _site_http(method, url, *, headers, json_body, deadline, generation):
    """Fixed-host HTTPS JSON with one absolute cutoff, including headers/body.

    The resolver worker can outlive its wait but has no request or credential
    and can never continue into a connection. The timer shuts down the owned
    socket; an already received remote mutation cannot be undone by a timeout.
    """
    from agent_friday.services import sites_privacy
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "api.cloudflare.com"}
            or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
        raise AdapterError("Unsupported hosting API destination.")
    host = parsed.hostname
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    expired, socket_lock = threading.Event(), threading.Lock()
    current_socket = [None]

    def close(sock):
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                sock.close()

    def cutoff():
        expired.set()
        with socket_lock:
            sock = current_socket[0]
        close(sock)

    def remaining():
        left = deadline - time.monotonic()
        if expired.is_set() or left <= 0:
            raise AdapterError("The publishing deadline elapsed; reconcile before retrying.")
        sites_privacy.require_generation(generation)
        return left

    def own(sock):
        with socket_lock:
            current_socket[0] = sock
            late = expired.is_set()
        if late:
            close(sock)
        remaining()

    timer = threading.Timer(remaining(), cutoff)
    timer.daemon = True
    response, connection = None, None
    timer.start()
    try:
        payload = None if json_body is None else json.dumps(json_body, allow_nan=False).encode("utf-8")
        request_headers = dict(headers or {}, **{"Accept-Encoding": "identity", "Connection": "close"})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        answers = queue.Queue(maxsize=1)
        def resolve():
            try:
                remaining()
                answers.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
            except (OSError, ValueError, AdapterError):
                answers.put(None)
        threading.Thread(target=resolve, daemon=True, name="site-provider-dns").start()
        try:
            addresses = answers.get(timeout=remaining())
        except queue.Empty:
            raise AdapterError("The hosting API lookup deadline elapsed; reconcile before retrying.") from None
        remaining()
        if not addresses:
            raise AdapterError("The hosting API address is unavailable.")
        raw = None
        for family, kind, protocol, _canonical, address in addresses:
            remaining()
            raw = socket.socket(family, kind, protocol)
            own(raw)
            try:
                raw.settimeout(min(remaining(), 10))
                raw.connect(address)
                break
            except OSError:
                close(raw)
                raw = None
        if raw is None:
            remaining()
            raise AdapterError("The hosting API connection is unavailable.")
        remaining()
        # Register TLS before its handshake so cutoff also interrupts peers
        # that drip handshake or header bytes before each inactivity timeout.
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        secure = context.wrap_socket(raw, server_hostname=host, do_handshake_on_connect=False)
        own(secure)
        secure.settimeout(min(remaining(), 30))
        secure.do_handshake()
        remaining()
        connection = http.client.HTTPSConnection(host)
        connection.sock = secure
        connection.auto_open = 0  # Never reconnect implicitly after cutoff.
        remaining()
        connection.request(method, target, body=payload, headers=request_headers)
        remaining()
        response = connection.getresponse()
        remaining()
        length = response.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > _SITE_RESPONSE_LIMIT):
            raise AdapterError("The hosting API response exceeded its size limit.")
        if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
            raise AdapterError("The hosting API returned an unsupported response encoding.")
        content = response.read(_SITE_RESPONSE_LIMIT + 1)
        remaining()
        if len(content) > _SITE_RESPONSE_LIMIT:
            raise AdapterError("The hosting API response exceeded its size limit.")
        if length and len(content) != int(length):
            raise AdapterError("The hosting API response was incomplete; reconcile before retrying.")
        text = content.decode("utf-8", errors="replace")
        try:
            body = json.loads(content)
        except (ValueError, UnicodeError):
            body = None
        remaining()
        return response.status, body, text
    except (OSError, http.client.HTTPException):
        raise AdapterError("The hosting API response was not confirmed; reconcile before retrying.") from None
    finally:
        timer.cancel()
        # Release the TLS stream on parsing, privacy, and response-size errors.
        try:
            if response is not None:
                response.close()
        finally:
            try:
                if connection is not None:
                    connection.close()
            finally:
                with socket_lock:
                    sock = current_socket[0]
                close(sock)


def _http(method: str, url: str, *, headers=None, json_body=None, data=None, files=None, timeout=30):
    """(status, parsed json or None, text). Replaced by a fake under test."""
    import requests
    generation = _SITE_GENERATION.get()
    if generation is not None:
        from agent_friday.services import sites_privacy
        sites_privacy.require_generation(generation)
    deadline = _SITE_DEADLINE.get()
    if deadline is not None:
        from agent_friday.services import sites_privacy
        if data is not None or files is not None:
            raise AdapterError("Saved Sites requires the supported JSON hosting API.")
        result = _site_http(method, url, headers=headers, json_body=json_body, deadline=deadline, generation=generation)
        sites_privacy.require_generation(generation)
        return result
    r = requests.request(method, url, headers=headers, json=json_body, data=data, files=files,
                         timeout=timeout, allow_redirects=False)
    try:
        if generation is not None:
            sites_privacy.require_generation(generation)
        try:
            body = r.json()
        except Exception:
            body = None
        return r.status_code, body, r.text
    finally:
        r.close()


def _fail(label: str, status: int, body, text: str) -> AdapterError:
    msg = ""
    if isinstance(body, dict):
        errs = body.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict):
            msg = str(errs[0].get("message") or "")
        msg = msg or str(body.get("message") or "")
    msg = msg or (text or "")[:120]
    return AdapterError("%s: HTTP %d %s" % (label, status, msg.strip()))


# ── the local mirror ─────────────────────────────────────────────────────────

def _mirror_root() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "published-remote"


def _mirror(adapter: str) -> Path:
    return _mirror_root() / adapter


def _write_mirror(adapter: str, bundle) -> None:
    root = _mirror(adapter)
    root.mkdir(parents=True, exist_ok=True)
    site = contained(root, bundle.slug)
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    for path, data in bundle.files.items():
        t = contained(site, path)
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(data)


def _drop_mirror(adapter: str, slug: str) -> None:
    site = contained(_mirror(adapter), slug)
    if site.exists():
        shutil.rmtree(site, ignore_errors=True)


def _site_files(adapter: str) -> dict:
    """Every file of every page published through this adapter: '<slug>/<file>' -> bytes."""
    root = _mirror(adapter)
    out: dict = {}
    if not root.exists():
        return out
    for site in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for f in sorted(site.rglob("*")):
            if f.is_file():
                out[site.name + "/" + f.relative_to(site).as_posix()] = f.read_bytes()
    return out


_PLACEHOLDER = (b"<!doctype html><meta charset=\"utf-8\"><meta name=\"referrer\" content=\"no-referrer\">"
                b"<title>Nothing published</title><body style=\"background:#0b0e14;color:#e6eef8;"
                b"font-family:Inter,system-ui,sans-serif;padding:40px\"><p>Nothing is published here right now.</p>")


# ── Cloudflare Pages (Direct Upload) ─────────────────────────────────────────

_CF_API = "https://api.cloudflare.com/client/v4"


def _cf_headers(conn: dict) -> dict:
    return {"Authorization": "Bearer " + conn["token"], "User-Agent": _UA}


def _cf_ensure_project(conn: dict) -> None:
    base = "%s/accounts/%s/pages/projects" % (_CF_API, conn["account_id"])
    status, body, text = _http("GET", base + "/" + conn["project"], headers=_cf_headers(conn))
    if status == 200:
        return
    if status != 404:
        raise _fail(LABELS["cloudflare_pages"], status, body, text)
    status, body, text = _http("POST", base, headers=_cf_headers(conn),
                               json_body={"name": conn["project"], "production_branch": "main"})
    if status not in (200, 201):
        raise _fail(LABELS["cloudflare_pages"], status, body, text)


def _cf_deploy(conn: dict, files: dict) -> None:
    """One deployment carrying the whole site: a manifest of '/path' -> sha256
    and each file as a form part named by its path (the Direct Upload form)."""
    if not files:
        files = {"index.html": _PLACEHOLDER}
    manifest = {"/" + p: hashlib.sha256(b).hexdigest() for p, b in files.items()}
    parts = {"/" + p: (p.rsplit("/", 1)[-1], b, mimetypes.guess_type(p)[0] or "application/octet-stream")
             for p, b in files.items()}
    url = "%s/accounts/%s/pages/projects/%s/deployments" % (_CF_API, conn["account_id"], conn["project"])
    status, body, text = _http("POST", url, headers=_cf_headers(conn),
                               data={"manifest": json.dumps(manifest), "branch": "main"}, files=parts, timeout=120)
    if status not in (200, 201) or (isinstance(body, dict) and body.get("success") is False):
        raise _fail(LABELS["cloudflare_pages"], status, body, text)


def _cf_url(conn: dict, slug: str) -> str:
    return "https://%s.pages.dev/%s/" % (conn["project"], slug)


# ── GitHub Pages (a branch, through the Git Data API) ────────────────────────

_GH_API = "https://api.github.com"


def _gh_headers(conn: dict) -> dict:
    return {"Authorization": "Bearer " + conn["token"], "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": _UA}


def _gh_base(conn: dict) -> str:
    return "%s/repos/%s" % (_GH_API, conn["repo"])


def _gh_check(status: int, body, text: str, ok=(200, 201)) -> None:
    if status not in ok:
        raise _fail(LABELS["github_pages"], status, body, text)


def _gh_commit(conn: dict, changes: dict, message: str, *, replace_tree: bool = False) -> Optional[str]:
    """`changes`: 'path' -> bytes to write, or None to delete. One commit on
    the pages branch; an orphan first commit when the branch does not exist."""
    base, hdr, branch = _gh_base(conn), _gh_headers(conn), conn.get("branch") or "gh-pages"
    status, body, text = _http("GET", "%s/git/ref/heads/%s" % (base, branch), headers=hdr)
    head = base_tree = None
    if status == 200:
        head = ((body or {}).get("object") or {}).get("sha")
        status2, body2, text2 = _http("GET", "%s/git/commits/%s" % (base, head), headers=hdr)
        _gh_check(status2, body2, text2)
        base_tree = ((body2 or {}).get("tree") or {}).get("sha")
    elif status != 404:
        raise _fail(LABELS["github_pages"], status, body, text)
    entries = []
    for path, data in changes.items():
        if data is None:
            if head is None:
                continue                              # nothing to delete on a new branch
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        status, body, text = _http("POST", base + "/git/blobs", headers=hdr,
                                   json_body={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        _gh_check(status, body, text)
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": body["sha"]})
    if not entries:
        return
    tree_body = {"tree": entries}
    if base_tree and not replace_tree:
        tree_body["base_tree"] = base_tree
    status, body, text = _http("POST", base + "/git/trees", headers=hdr, json_body=tree_body)
    _gh_check(status, body, text)
    tree_sha = body["sha"]
    status, body, text = _http("POST", base + "/git/commits", headers=hdr,
                               json_body={"message": message, "tree": tree_sha, "parents": [head] if head else []})
    _gh_check(status, body, text)
    commit_sha = body["sha"]
    if head is None:
        status, body, text = _http("POST", base + "/git/refs", headers=hdr,
                                   json_body={"ref": "refs/heads/" + branch, "sha": commit_sha})
        _gh_check(status, body, text)
    else:
        status, body, text = _http("PATCH", "%s/git/refs/heads/%s" % (base, branch), headers=hdr,
                                   json_body={"sha": commit_sha, "force": False})
        _gh_check(status, body, text)
    return commit_sha


def _gh_enable_pages(conn: dict) -> None:
    """Idempotent: 201 when enabled now, 409 when it already was."""
    branch = conn.get("branch") or "gh-pages"
    status, body, text = _http("POST", _gh_base(conn) + "/pages", headers=_gh_headers(conn),
                               json_body={"source": {"branch": branch, "path": "/"}})
    if status not in (201, 409):
        _log.warning("GitHub Pages could not be enabled: HTTP %s", status)


def _gh_url(conn: dict, slug: str) -> str:
    owner, name = conn["repo"].split("/", 1)
    owner_l, name_l = owner.lower(), name.lower()
    if name_l == owner_l + ".github.io":
        return "https://%s.github.io/%s/" % (owner_l, slug)
    return "https://%s.github.io/%s/%s/" % (owner_l, name, slug)


# ── the two verbs ────────────────────────────────────────────────────────────

def publish(adapter: str, bundle, conn: dict) -> str:
    """Publish one bundle through a connected adapter. Returns the page URL."""
    if adapter not in LABELS:
        raise AdapterError("unknown adapter %r" % adapter)
    if not conn or not conn.get("token"):
        raise AdapterError("%s is not connected" % LABELS[adapter])
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", bundle.slug or ""):
        raise AdapterError("invalid slug")
    _write_mirror(adapter, bundle)
    if adapter == "cloudflare_pages":
        _cf_ensure_project(conn)
        _cf_deploy(conn, _site_files(adapter))
        return _cf_url(conn, bundle.slug)
    changes = {bundle.slug + "/" + p: b for p, b in bundle.files.items()}
    changes[".nojekyll"] = b""
    _gh_commit(conn, changes, 'Publish "%s" (%s, v%d) from Friday' % (bundle.title, bundle.slug, bundle.version))
    _gh_enable_pages(conn)
    return _gh_url(conn, bundle.slug)


def unpublish(adapter: str, slug: str, conn: dict) -> None:
    """Take one page down: the mirror forgets it and the site is redeployed."""
    if adapter not in LABELS:
        raise AdapterError("unknown adapter %r" % adapter)
    if not conn or not conn.get("token"):
        raise AdapterError("%s is not connected" % LABELS[adapter])
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", slug or ""):
        raise AdapterError("invalid slug")
    site = _mirror(adapter) / slug
    paths = [slug + "/" + f.relative_to(site).as_posix() for f in site.rglob("*") if f.is_file()] if site.exists() else []
    if adapter == "cloudflare_pages":
        _cf_ensure_project(conn)
        remaining = {path: data for path, data in _site_files(adapter).items() if not path.startswith(slug + "/")}
        _cf_deploy(conn, remaining)
        _drop_mirror(adapter, slug)
        return
    if paths:
        _gh_commit(conn, {p: None for p in paths}, 'Take down "%s" from Friday' % slug)
    _drop_mirror(adapter, slug)


def site_dns_requirements(conn: dict, hostname: str, domain: str) -> dict:
    """Provider requirements only; no DNS changes or implicit host selection."""
    from agent_friday.services.site_hosting import target_identity, require_supported
    require_supported(conn["adapter"])
    if hostname != domain and not hostname.endswith("." + domain):
        raise ValueError("The hostname must belong to the selected domain.")
    host = hostname[:-len(domain)].rstrip(".")
    if host:
        records = [{"host": host, "type": "CNAME", "answer": conn["repo"].split("/")[0].lower() + ".github.io", "ttl": 300}]
    else:
        records = [{"host": "", "type": "A", "answer": address, "ttl": 300}
                   for address in ("185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153")]
    return {"hostname": hostname, "domain": domain, "target_identity": target_identity(conn),
            "records": records, "host_association_required": True,
            "note": "Approve hosting association before changing DNS. Other DNS records must be preserved."}


def publish_site(bundle, conn: dict, *, privacy_generation: int, hostname: str = "") -> dict:
    from agent_friday.services import sites_privacy, site_hosting
    sites_privacy.require_generation(privacy_generation)
    site_hosting.require_supported(conn["adapter"])
    token = _SITE_DEADLINE.set(time.monotonic() + 120)
    origin = _SITE_GENERATION.set(privacy_generation)
    try:
        result = _publish_site(bundle, conn, hostname=hostname)
        sites_privacy.require_generation(privacy_generation)
        return result
    finally:
        _SITE_DEADLINE.reset(token)
        _SITE_GENERATION.reset(origin)


def _publish_site(bundle, conn: dict, *, hostname: str = "") -> dict:
    """Publish one site's complete root to its dedicated target, without slug mirrors."""
    adapter = conn["adapter"]
    if adapter == "github_pages":
        files = dict(bundle.files, **{".nojekyll": b""})
        if hostname:
            files["CNAME"] = (hostname + "\n").encode("ascii")
        commit = _gh_commit(conn, files, "Publish saved site build from Friday", replace_tree=True)
        receipt = {"provider_deployment_id": commit, "provider_url": _gh_url(conn, "").replace("//", "/").replace("https:/", "https://")}
        pages = _gh_base(conn) + "/pages"
        status, _body, _text = _http("GET", pages, headers=_gh_headers(conn))
        config = {"source": {"branch": conn.get("branch") or "gh-pages", "path": "/"}}
        create = status == 404
        if status not in (200, 404):
            receipt.update(status="partial", error="Files committed; GitHub Pages configuration could not be read.")
            return receipt
        if create:
            status, _body, _text = _http("POST", pages, headers=_gh_headers(conn), json_body=config)
            if status not in (200, 201, 204):
                receipt.update(status="partial", error="Files committed; GitHub Pages could not be enabled.")
                return receipt
        # cname is supported by the update endpoint, not by create.
        config["cname"] = hostname or None
        status, _body, _text = _http("PUT", pages, headers=_gh_headers(conn), json_body=config)
        if status not in (200, 201, 204):
            receipt.update(status="partial", error="Files committed; GitHub Pages configuration needs review.")
            return receipt
    else:
        raise ValueError("Choose a supported saved hosting connection.")
    receipt.update(status="provider_accepted", verified=False, hostname=hostname or None)
    return receipt


def site_deployment_status(conn: dict, deployment: dict, *, privacy_generation: int) -> dict:
    """Read-back retains the caller's admitted generation across every request."""
    from agent_friday.services import sites_privacy
    sites_privacy.require_generation(privacy_generation)
    deadline = _SITE_DEADLINE.set(time.monotonic() + 30)
    origin = _SITE_GENERATION.set(privacy_generation)
    try:
        result = _site_deployment_status(conn, deployment)
        sites_privacy.require_generation(privacy_generation)
        return result
    finally:
        _SITE_DEADLINE.reset(deadline)
        _SITE_GENERATION.reset(origin)


def _site_deployment_status(conn: dict, deployment: dict) -> dict:
    """Read provider evidence. Provider readiness alone never proves live content."""
    identity = str(deployment.get("provider_deployment_id") or "")
    if not identity or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity):
        return {"status": "unknown", "verified": False, "note": "No confirmed provider identity; inspect the hosting account before retrying."}
    if conn["adapter"] == "cloudflare_pages":
        url = "%s/accounts/%s/pages/projects/%s/deployments/%s" % (_CF_API, conn["account_id"], conn["project"], identity)
        status, body, _text = _http("GET", url, headers=_cf_headers(conn))
        if status != 200:
            raise AdapterError("Cloudflare deployment status is unavailable (HTTP %d)." % status)
        result = (body or {}).get("result") or {}
        state = (result.get("latest_stage") or {}).get("status")
        return {"status": "provider_ready" if state == "success" else "failed" if state == "failure" else "provider_pending",
                "provider_state": state, "provider_deployment_id": identity, "verified": False}
    status, body, _text = _http("GET", _gh_base(conn) + "/pages/builds/latest", headers=_gh_headers(conn))
    if status != 200:
        raise AdapterError("GitHub Pages build status is unavailable (HTTP %d)." % status)
    matches = (body or {}).get("commit") == identity
    state = (body or {}).get("status")
    return {"status": "provider_ready" if matches and state == "built" else "failed" if matches and state == "errored" else "provider_pending",
            "provider_state": state, "matching_revision": matches, "provider_deployment_id": identity, "verified": False}
