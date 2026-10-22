"""Hosts where Friday may reach a remote add-on (MCP) server: the owner's list.

A remote MCP server receives the arguments of every tool call made with it, so
the place it lives is the owner's choice, the same way a website task may open
only sites on the owner's list (governance/web_sites.py). A remote server is
contacted only when its host is on this list. Anything else is refused before a
byte is sent, with the reason, and a card asks the owner
(services/mcp_host_cards.py).

* One row per host and port (``mcp.example.com:443``). A row for one host never
  covers another: ``mcp.example.com`` does not cover ``evil.example.com``.
* ``made_by``: only the owner's own press on screen (``owner:ui``) adds a host.
  Page content, a tool result or a model cannot: no tool reaches ``approve``.
  ``carried_over`` marks hosts that were already configured when this list was
  introduced (see ``_carry_over``); they were the owner's set-up before.
* This computer (``localhost``, ``127.0.0.0/8``, ``::1``) needs no row: a server
  there is software already running on the owner's computer and nothing it is
  sent leaves it. Friday's own port is never a server, on any address.

The file is ``<home>/governance/mcp_hosts.json``. Each row is signed with the
governance key (the same HMAC as decision receipts and the site list); a row
whose signature does not match is treated as absent, never as allowed. History
is append-only (``mcp_hosts_history.jsonl``).
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import ipaddress
import json
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

MAKERS = ("owner:ui",)
REMOVERS = ("owner:ui", "owner:voice", "owner:voice-confirmed")
CARRIED_OVER = "carried_over"
_MIGRATED = "__carried_over_at__"

_LOCK = threading.RLock()


class HostRefused(ValueError):
    """The host was not added or reached; the message says why in plain words."""


def _dir():
    from agent_friday.governance.action_gate import _gov_dir
    return _gov_dir()


def _path():
    return _dir() / "mcp_hosts.json"


def _history_path():
    return _dir() / "mcp_hosts_history.jsonl"


def _key() -> bytes:
    from agent_friday.governance.action_gate import _governance_key
    return _governance_key()


def _friday_port() -> int:
    try:
        from agent_friday import core
        return int(getattr(core, "SERVER_PORT", 0) or 0)
    except Exception:
        return 0


def host_key(url: str) -> str:
    """``host:port`` for an http(s) URL, lower case, punycode; "" when there is none."""
    try:
        parts = urlsplit(str(url or "").strip())
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            return ""
        host = (parts.hostname or "").strip().lower().rstrip(".")
        port = parts.port or (443 if scheme == "https" else 80)
    except Exception:
        return ""
    if not host:
        return ""
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        # A name outside ASCII is matched only in its punycode form, so a lookalike
        # cannot borrow a row made for the real name.
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            return ""
    if ":" in host:                      # an IPv6 literal
        host = "[%s]" % host
    return "%s:%d" % (host, port)


def display(key: str) -> str:
    """``mcp.example.com`` for the usual ports, ``host:port`` otherwise."""
    host, _, port = str(key or "").rpartition(":")
    return host if port in ("443", "80") else str(key or "")


def _is_this_computer(host: str) -> bool:
    h = host.strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _sign(row: Dict[str, Any]) -> str:
    body = {k: v for k, v in row.items() if k != "sig"}
    canonical = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return _hmac.new(_key(), canonical, hashlib.sha256).hexdigest()


def _verified(row: Any, key: str) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("sig"), str) or row.get("host") != key:
        return False
    try:
        return _hmac.compare_digest(row["sig"], _sign(row))
    except Exception:
        return False


def _read_raw() -> Optional[Dict[str, Any]]:
    """The rows, {} when there is no file yet, None when it cannot be read."""
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_raw(rows: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _history(event: Dict[str, Any]) -> None:
    entry = dict(event, at=time.time())
    entry["sig"] = _sign(entry)
    with open(_history_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _row(key: str, made_by: str, server: str, note: str, approval_id: str) -> Dict[str, Any]:
    row = {"host": key, "made_by": made_by, "made_at": time.time(),
           "server": str(server or "")[:120], "note": str(note or "")[:200],
           "approval_id": str(approval_id or "")[:64]}
    row["sig"] = _sign(row)
    return row


def _configured_remote_urls() -> list:
    """(name, url) of each enabled remote server in mcp_servers.json, read as a file."""
    try:
        from agent_friday import core
        data = json.loads((core.FRIDAY_DIR / "mcp_servers.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    servers = data.get("servers", data) if isinstance(data, dict) else {}
    out = []
    for name, spec in (servers or {}).items():
        if isinstance(spec, dict) and spec.get("url") and spec.get("enabled") is not False:
            out.append((str(name), str(spec["url"])))
    return out


def _carry_over(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Once, when the list is first made: the hosts of remote servers already configured
    stay reachable (they were the owner's set-up before the list existed), each recorded as
    carried over. Every host added later needs the owner's press."""
    if raw.get(_MIGRATED):
        return raw
    for name, url in _configured_remote_urls():
        key = host_key(url)
        if key and not _is_this_computer(key.rpartition(":")[0]) and key not in raw:
            raw[key] = _row(key, CARRIED_OVER, name, "configured before the host list", "")
            _history({"event": "carried_over", "host": key, "row": raw[key]})
    raw[_MIGRATED] = time.time()
    _write_raw(raw)
    return raw


def check(url: str) -> str:
    """"" when Friday may reach this remote server URL, else the reason in plain words."""
    key = host_key(url)
    if not key:
        return "the server's address is not an http or https web address"
    host, _, port = key.rpartition(":")
    if _is_this_computer(host):
        if int(port) == _friday_port():
            return "that address is Friday herself, not an add-on server"
        return ""
    with _LOCK:
        raw = _read_raw()
        if raw is None:
            return ("the list of add-on server hosts could not be read, so Friday reaches none "
                    "of them until it is fixed")
        raw = _carry_over(raw)
    if _verified(raw.get(key), key):
        return ""
    return "you have not allowed Friday to reach %s" % display(key)


def allowed(url: str) -> bool:
    return check(url) == ""


def approve(url_or_key: str, *, made_by: str, server: str = "", note: str = "",
            approval_id: str = "") -> Dict[str, Any]:
    """Add a host. Only the owner's press on screen can call this."""
    if made_by not in MAKERS:
        raise HostRefused("Only you can allow a new add-on server host, with a press on screen.")
    key = url_or_key if "://" not in str(url_or_key) else host_key(url_or_key)
    if not key or ":" not in key:
        raise HostRefused("That is not a web address Friday can put on the list.")
    with _LOCK:
        raw = _read_raw()
        if raw is None:
            raise HostRefused("The host list could not be read, so nothing was changed.")
        raw = _carry_over(raw)
        old = raw.get(key)
        raw[key] = _row(key, made_by, server, note, approval_id)
        _write_raw(raw)
        _history({"event": "approve", "host": key, "row": raw[key],
                  "previous": old if isinstance(old, dict) else None})
        return dict(raw[key])


def remove(url_or_key: str, *, made_by: str) -> bool:
    """Take a host off the list (a narrowing: it may be spoken)."""
    if made_by not in REMOVERS:
        raise HostRefused("Only you can change the add-on server host list.")
    key = url_or_key if "://" not in str(url_or_key) else host_key(url_or_key)
    with _LOCK:
        raw = _read_raw()
        if not raw or key not in raw:
            return False
        old = raw.pop(key)
        _write_raw(raw)
        _history({"event": "remove", "host": key, "previous": old, "by": made_by})
    return True


def all_hosts() -> Dict[str, Any]:
    """Every row with ``verified`` set; an unverified row is shown, never used."""
    with _LOCK:
        raw = _read_raw() or {}
    return {k: {**(v if isinstance(v, dict) else {}), "verified": _verified(v, k)}
            for k, v in raw.items() if not k.startswith("__")}
