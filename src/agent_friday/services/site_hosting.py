"""Named hosting connections; credentials never enter Sites records or builds."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from agent_friday.paths import friday_home
from agent_friday.services.workflow_operations import atomic_write
from agent_friday.services import sites_privacy

LOCK = threading.RLock()
SUPPORTED_ADAPTERS = {"github_pages"}
CLOUDFLARE_UNAVAILABLE = "Cloudflare Pages is not available for saved Sites yet; its asset-upload protocol is not implemented here."


def require_supported(adapter):
    if adapter == "cloudflare_pages":
        raise ValueError(CLOUDFLARE_UNAVAILABLE)
    if not isinstance(adapter, str) or adapter not in SUPPORTED_ADAPTERS:
        raise ValueError("Choose GitHub Pages for saved Sites.")


def _path():
    return friday_home() / "sites" / "hosting.json"


def _read():
    path = _path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write(rows):
    atomic_write(_path(), json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"))


def _key(connection_id, revision=None):
    if not re.fullmatch(r"host-[a-f0-9]{32}", str(connection_id)):
        raise ValueError("Choose a saved hosting connection.")
    return "sites_" + connection_id + ("_" + str(revision) if revision is not None else "")


def list_connections():
    from agent_friday.services import credential_store
    with LOCK:
        return [dict(row, connected=credential_store.provider_key_status(_key(key, row["revision"])) == "connected")
                for key, row in _read().items()]


def get_connection(connection_id, *, secret=False):
    from agent_friday.services import credential_store
    _key(connection_id)
    with LOCK:
        row = _read().get(connection_id)
        if not row:
            raise ValueError("That hosting connection is unavailable.")
        result = dict(row)
        if secret:
            token = credential_store.get_provider_key(_key(connection_id, row["revision"]))
            if not token:
                raise ValueError("Reconnect this hosting account in Sites.")
            result["token"] = token
        return result


def connect(data, *, generation):
    """UI-only credential entry. Target changes invalidate every old plan."""
    from agent_friday.services import credential_store
    sites_privacy.require_generation(generation)
    if not isinstance(data, dict):
        raise ValueError("Provide a hosting connection object.")
    adapter = data.get("adapter")
    require_supported(adapter)
    if set(data) - {"name", "adapter", "token", "repo", "branch", "connection_id", "revision"}:
        raise ValueError("Use the hosting connection form fields.")
    token = data.get("token")
    if not isinstance(token, str) or not token.strip() or any(ord(c) < 33 for c in token.strip()):
        raise ValueError("Choose GitHub Pages and enter its token.")
    name = str(data.get("name") or adapter).strip()
    if len(name) > 120 or len(token) > 10000:
        raise ValueError("The connection name or token is too long.")
    repo, branch = str(data.get("repo") or ""), str(data.get("branch") or "gh-pages")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repo) or repo.split("/")[-1] in (".", ".."):
        raise ValueError("Enter the GitHub repository as owner/name.")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_/-]{0,99}", branch) or "//" in branch:
        raise ValueError("Choose a simple publishing branch name.")
    target = {"repo": repo, "branch": branch}
    with LOCK:
        rows = _read()
        connection_id = data.get("connection_id") or "host-" + uuid.uuid4().hex
        _key(connection_id)
        if data.get("connection_id") and connection_id not in rows:
            raise ValueError("That hosting connection no longer exists.")
        prior = rows.get(connection_id) or {}
        if prior and (type(data.get("revision")) is not int or data["revision"] != prior["revision"]):
            raise ValueError("This connection changed. Refresh it before reconnecting.")
        if prior and (prior["adapter"] != adapter or any(prior.get(key) != value for key, value in target.items())):
            raise ValueError("A different hosting target needs a new connection; reconnect only replaces its credential.")
        row = dict(target, connection_id=connection_id, adapter=adapter, name=name,
                   revision=int(prior.get("revision", 0)) + 1, updated_at=time.time())
        rows[connection_id] = row
        key = _key(connection_id, row["revision"])
        sites_privacy.require_generation(generation)
        try:
            credential_store.set_provider_key(key, token.strip())
            sites_privacy.require_generation(generation)
            _write(rows)
        except Exception:
            credential_store.delete_provider_key(key)
            raise
        sites_privacy.require_generation(generation)
        if prior:
            credential_store.delete_provider_key(_key(connection_id, prior["revision"]))
        sites_privacy.require_generation(generation)
        return dict(row, connected=True)


def disconnect(connection_id, revision, *, generation):
    from agent_friday.services import credential_store
    sites_privacy.require_generation(generation)
    _key(connection_id)
    with LOCK:
        rows = _read()
        if connection_id not in rows:
            raise ValueError("That hosting connection no longer exists.")
        if type(revision) is not int or revision != rows[connection_id]["revision"]:
            raise ValueError("This connection changed. Refresh it before disconnecting.")
        # Keep the tombstone and generation so reconnect cannot revive an old approval.
        old_revision = rows[connection_id]["revision"]
        rows[connection_id]["revision"] += 1
        rows[connection_id]["updated_at"] = time.time()
        sites_privacy.require_generation(generation)
        _write(rows)
        sites_privacy.require_generation(generation)
        credential_store.delete_provider_key(_key(connection_id, old_revision))
        sites_privacy.require_generation(generation)
        return dict(rows[connection_id], connected=False)


def target_identity(connection):
    if connection["adapter"] == "cloudflare_pages":
        return "cloudflare_pages:" + connection["account_id"].lower() + "/" + connection["project"]
    return "github_pages:" + connection["repo"].lower()


def require_artifact_target(adapter, connection):
    """A legacy slug portfolio cannot replace a dedicated saved-site root."""
    identity = target_identity(dict(connection, adapter=adapter))
    for path in (friday_home() / "sites").glob("site-*/site.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("hosting_target") == identity:
            raise ValueError("That hosted project/repository belongs to a saved site. Choose a separate target for artifact publishing.")


def require_site_target(connection):
    from agent_friday.services import publish_hosting, publish_web
    adapter = connection["adapter"]
    require_supported(adapter)
    legacy = publish_hosting.connection(adapter)
    if (legacy and target_identity(dict(legacy, adapter=adapter)) == target_identity(connection)
            and any(row.get("adapter") == adapter for row in publish_web.list_published())):
        raise ValueError("That target contains the published artifact portfolio. Choose a dedicated project or repository for this site.")
