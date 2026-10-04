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
import json
import logging
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Optional

from agent_friday.paths import contained

_log = logging.getLogger(__name__)

LABELS = {"cloudflare_pages": "Cloudflare Pages", "github_pages": "GitHub Pages"}
_UA = "Friday/publish-to-web"


class AdapterError(RuntimeError):
    """The provider refused or failed. The message never carries the token."""


# ── one HTTP function ────────────────────────────────────────────────────────

def _http(method: str, url: str, *, headers=None, json_body=None, data=None, files=None, timeout=30):
    """(status, parsed json or None, text). Replaced by a fake under test."""
    import requests
    r = requests.request(method, url, headers=headers, json=json_body, data=data, files=files, timeout=timeout)
    try:
        body = r.json()
    except Exception:
        body = None
    return r.status_code, body, r.text


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


def _gh_commit(conn: dict, changes: dict, message: str) -> None:
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
    if base_tree:
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
    _drop_mirror(adapter, slug)
    if adapter == "cloudflare_pages":
        _cf_ensure_project(conn)
        _cf_deploy(conn, _site_files(adapter))
        return
    if paths:
        _gh_commit(conn, {p: None for p in paths}, 'Take down "%s" from Friday' % slug)
