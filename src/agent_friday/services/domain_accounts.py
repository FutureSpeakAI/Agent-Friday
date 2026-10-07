"""Named registrar connections and inventory, with secrets in credential_store."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from datetime import date

from agent_friday.services import namecom, sites_privacy

LOCK = threading.RLock()


def _path():
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "domains" / "state.json"


def read():
    path = _path()
    if not path.exists():
        return {"version": 1, "accounts": {}, "inventory": {}, "operations": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Saved domain data could not be read. Nothing was replaced.") from None
    if not isinstance(data, dict) or data.get("version") != 1 or any(not isinstance(data.get(k), dict) for k in ("accounts", "inventory", "operations")):
        raise ValueError("Saved domain data has an unsupported format.")
    return data


def write(data):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".domains-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def require_recording():
    from agent_friday.core import _load_settings
    from agent_friday.services import off_record
    if off_record.active(_load_settings() or {}):
        raise ValueError("Domain management needs a durable record. Leave off-the-record mode first.")


def account(account_id, state=None):
    found = (state or read())["accounts"].get(str(account_id))
    if not found:
        raise ValueError("Choose a saved Name.com account.")
    return copy.deepcopy(found)


def public_account(rec):
    return {k: copy.deepcopy(rec.get(k)) for k in ("account_id", "revision", "label", "environment", "connection_status", "last_synced_at", "sync_status")}


def list_accounts():
    with LOCK:
        return [public_account(a) for a in read()["accounts"].values()]


def _key(account_id, revision):
    return "namecom_" + account_id + "_" + str(revision)


def connect(*, label, username="", token="", environment="production", account_id=None, revision=None, _origin=None):
    """UI-only entry: a metadata-only account can receive private inventory."""
    require_recording()
    generation = sites_privacy.admit({"_sites_origin": _origin if _origin is not None else sites_privacy.capture()})
    from agent_friday.services import credential_store
    if not isinstance(label, str) or not label.strip() or len(label) > 100:
        raise ValueError("Give this account a short name.")
    if environment not in namecom.BASES:
        raise ValueError("Choose production or sandbox.")
    if not isinstance(username, str) or not isinstance(token, str) or len(username) > 200 or len(token) > 4096:
        raise ValueError("Use a valid API username and token.")
    username, token = username.strip(), token.strip()
    if token and (not username or ":" in username or any(ord(c) < 33 for c in username + token)):
        raise ValueError("Enter the API username and token from Name.com API settings.")
    identity = hashlib.sha256((environment + "\0" + username).encode()).hexdigest() if token else None
    with LOCK:
        state = read()
        prior = account(account_id, state) if account_id else None
        if prior and (type(revision) is not int or revision != prior["revision"]):
            raise ValueError("This account changed. Refresh it before connecting.")
        if prior and prior.get("identity_hash") and identity != prior["identity_hash"]:
            raise ValueError("A different API username or environment needs a new named account.")
        aid = prior["account_id"] if prior else "acct_" + uuid.uuid4().hex
        rev = prior["revision"] + 1 if prior else 1
        if token:
            sites_privacy.require_generation(generation)
            namecom.Client(aid, username, token, environment,
                _request_guard=lambda: sites_privacy.require_generation(generation)).hello()
            sites_privacy.require_generation(generation)

        rec = {"account_id": aid, "revision": rev, "label": label.strip(), "environment": environment,
               "identity_hash": identity, "connection_status": "verified" if token else "not_connected",
               "last_synced_at": None, "sync_status": "not_synced"}
        state["accounts"][aid] = rec
        for row in state["inventory"].get(aid, []):
            row["verified"] = False
            row["sync_status"] = "account_changed"
        try:
            sites_privacy.require_generation(generation)
            if token:
                credential_store.set_provider_key(_key(aid, rev), json.dumps({"username": username, "token": token}))
                sites_privacy.require_generation(generation)
            write(state)
        except Exception:
            if token:
                credential_store.delete_provider_key(_key(aid, rev))
            raise
        if prior:
            credential_store.delete_provider_key(_key(aid, prior["revision"]))
        sites_privacy.require_generation(generation)
        return public_account(rec)


def disconnect(account_id, revision, *, _origin=None):
    require_recording()
    generation = sites_privacy.admit({"_sites_origin": _origin if _origin is not None else sites_privacy.capture()})
    from agent_friday.services import credential_store
    with LOCK:
        state = read()
        rec = account(account_id, state)
        if type(revision) is not int or revision != rec["revision"]:
            raise ValueError("This account changed. Refresh before disconnecting.")
        old_revision = rec["revision"]
        rec.update(revision=old_revision + 1, connection_status="not_connected", sync_status="disconnected")
        state["accounts"][account_id] = rec
        for row in state["inventory"].get(account_id, []):
            row.update(verified=False, sync_status="disconnected")
        sites_privacy.require_generation(generation)
        write(state)
        credential_store.delete_provider_key(_key(account_id, old_revision))
        sites_privacy.require_generation(generation)
        return public_account(rec)


def client(rec, *, generation):
    sites_privacy.require_generation(generation)
    from agent_friday.services import credential_store
    if rec.get("connection_status") != "verified":
        raise ValueError("Connect this named account in Sites first.")
    raw = credential_store.get_provider_key(_key(rec["account_id"], rec["revision"]))
    try:
        secret = json.loads(raw or "")
        result = namecom.Client(rec["account_id"], secret["username"], secret["token"], rec["environment"], _request_guard=lambda: sites_privacy.require_generation(generation))
    except (ValueError, KeyError, TypeError):
        raise ValueError("This account's saved credential is unavailable. Reconnect it in Sites.") from None
    sites_privacy.require_generation(generation)
    return result


def domain_view(raw):
    domain = namecom.domain_name(raw.get("domainName"))
    ns = raw.get("nameservers")
    if not isinstance(ns, list) or any(not isinstance(n, str) for n in ns):
        raise namecom.ProviderError("Name.com did not return the domain's nameservers.")
    ns = [namecom.domain_name(n) for n in ns]
    # Name.com documents both ns1.name.com and per-account three-letter
    # variants such as ns1dns.name.com. Other authorities stay read-only;
    # delegation is never changed as a side effect.
    import re
    authority = bool(ns) and all(re.fullmatch(r"ns[1-4](?:[a-z]{3})?\.name\.com", n) for n in ns)
    return {"domain": domain, "nameservers": ns, "dns_authority": "namecom" if authority else "external",
            "expire_date": raw.get("expireDate") if isinstance(raw.get("expireDate"), str) else None,
            "autorenew_enabled": raw.get("autorenewEnabled") if type(raw.get("autorenewEnabled")) is bool else None,
            "locked": raw.get("locked") if type(raw.get("locked")) is bool else None}


def inventory(account_id=None):
    with LOCK:
        state = read()
        if account_id:
            account(account_id, state)
        ids = [account_id] if account_id else list(state["accounts"])
        return copy.deepcopy([row for aid in ids for row in state["inventory"].get(aid, [])])


def import_inventory(account_id, entries, *, _origin=None):
    require_recording()
    generation = sites_privacy.admit({"_sites_origin": _origin if _origin is not None else sites_privacy.capture()})
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError("Import between 1 and 1000 domain rows.")
    now, checked = time.time(), []
    for item in entries:
        if not isinstance(item, dict) or set(item) - {"domain", "next_date_kind", "next_date", "source"}:
            raise ValueError("Each imported row needs a domain, date kind and date.")
        kind = item.get("next_date_kind")
        if kind not in {"renews", "expires"}:
            raise ValueError("Keep each imported date labeled Renews or Expires.")
        try:
            when = date.fromisoformat(item.get("next_date", "")).isoformat()
        except (ValueError, TypeError):
            raise ValueError("Use an imported date in YYYY-MM-DD form.") from None
        source = item.get("source", "user_import")
        if not isinstance(source, str) or len(source) > 200:
            raise ValueError("Use a short source label.")
        checked.append((namecom.domain_name(item.get("domain")), {"next_date_kind": kind, "next_date": when, "source": source, "captured_at": now, "verified": False}))
    with LOCK:
        state = read()
        rec = account(account_id, state)
        rows = {row["domain"]: row for row in state["inventory"].get(account_id, [])}
        for domain, imported in checked:
            row = rows.setdefault(domain, {"account_id": account_id, "account_revision": rec["revision"], "domain": domain,
                                          "verified": False, "last_synced_at": None, "sync_status": "unverified_import"})
            row["imported"] = imported
        state["inventory"][account_id] = list(rows.values())
        sites_privacy.require_generation(generation)
        write(state)
        sites_privacy.require_generation(generation)
        return copy.deepcopy(state["inventory"][account_id])


def sync(account_id, *, _generation):
    require_recording()
    generation = _generation
    sites_privacy.require_generation(generation)
    with LOCK:
        state = read()
        rec = account(account_id, state)
        try:
            values = [domain_view(item) for item in client(rec, generation=generation).domains()]
            sites_privacy.require_generation(generation)
            if len({item["domain"] for item in values}) != len(values):
                raise namecom.ProviderError("Name.com returned duplicate domains; inventory was retained.")
        except namecom.ProviderError:
            sites_privacy.require_generation(generation)
            rec["sync_status"] = "failed"
            state["accounts"][account_id] = rec
            write(state)
            raise
        now = time.time()
        previous = {row["domain"]: row for row in state["inventory"].get(account_id, [])}
        rows = []
        for value in values:
            row = dict(value, account_id=account_id, account_revision=rec["revision"], verified=True, source="namecom_api",
                       last_synced_at=now, sync_status="synced")
            old = previous.pop(value["domain"], {})
            if old.get("imported"):
                row["imported"] = old["imported"]
            rows.append(row)
        for old in previous.values():
            old.update(verified=False, sync_status="not_in_account", last_checked_at=now)
            rows.append(old)
        rec.update(last_synced_at=now, sync_status="synced")
        state["accounts"][account_id], state["inventory"][account_id] = rec, rows
        sites_privacy.require_generation(generation)
        write(state)
        sites_privacy.require_generation(generation)
        return copy.deepcopy(rows)
