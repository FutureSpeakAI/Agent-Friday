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
  introduced (``carry_over_at_boot``, once, at the first start with this code,
  from the configuration as it was at that start; a signed marker says it ran).
  A list that is missing after it existed, or has no signed marker, is never
  rebuilt from the configuration: it is unreadable and nothing is reached.
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
_MARKER = "__carry_over__"

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
    raw = str(url or "").strip()
    try:
        parts = urlsplit(raw)
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            return ""
        # A user part, a backslash or an escape in the address is read differently by
        # different parsers: the host checked here could differ from the host dialled.
        if "@" in parts.netloc or "\\" in raw or "%" in parts.netloc:
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


def _odd_spelling(host: str) -> bool:
    """An address written so that it may mean this computer without saying so: the
    unspecified address (0.0.0.0, ::), an IPv4-mapped IPv6 address, or numbers that are
    not a plain dotted quad (127.1, 2130706433, 0x7f.1). Never allowed, never asked."""
    h = host.strip("[]").lower()
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        labels = h.split(".")
        return all(p and (p.isdigit() or p.startswith("0x")) for p in labels)
    if ip.is_unspecified:
        return True
    return bool(getattr(ip, "ipv4_mapped", None))


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


def _marked(raw: Dict[str, Any]) -> bool:
    return _verified(raw.get(_MARKER), _MARKER)


ANCHOR_NAME = "mcp_hosts"


def carry_over_at_boot(config: Any = None) -> int:
    """Once, at the first start with the host list: the hosts of remote servers configured
    at that start stay reachable (they were the owner's set-up before the list existed),
    each recorded as carried over, and a signed marker records that it ran, even with no
    servers. Called by the MCP start-up before any server starts, never by a reload or a
    check, so a server added later always needs the owner's press. A list that is missing
    after it existed (history on disk) is not rebuilt from the configuration. Returns how
    many hosts were carried over."""
    from agent_friday.governance import trust_anchor
    with _LOCK:
        raw = _read_raw()
        if raw is None:
            return 0
        try:
            # The owner's ruling (2026-10-28): the carry-over is anchored in the append-only
            # action ledger, so deleting this list (and its history) cannot run it again.
            anchored = trust_anchor.carried(ANCHOR_NAME)
        except trust_anchor.AnchorUnavailable:
            return 0                       # the ledger cannot say: carry nothing over
        if anchored is not None:
            if not _marked(raw):
                # Deleted since: rebuilt from the ledger (only what it carried, minus what was
                # released), never from the configuration.
                for key, server in sorted(anchored["items"].items()):
                    if key not in raw:
                        raw[key] = _row(key, CARRIED_OVER, str(server or ""), "rebuilt from the ledger", "")
                raw[_MARKER] = _row(_MARKER, CARRIED_OVER, "", "rebuilt from the ledger", "")
                _write_raw(raw)
                _history({"event": "rebuilt_from_ledger", "carried_over": len(anchored["items"])})
            return 0
        if _marked(raw):
            # Carried over before the anchor existed: anchor what it trusted now (backfill).
            try:
                trust_anchor.record(ANCHOR_NAME, {k: (r or {}).get("server", "") for k, r in raw.items()
                                                  if k != _MARKER and (r or {}).get("made_by") == CARRIED_OVER
                                                  and _verified(r, k)})
            except Exception:
                pass
            return 0
        if raw or _history_path().exists():
            return 0                       # the list existed: never refill it from the config
        if config is None:
            urls = _configured_remote_urls()
        else:
            servers = config.get("servers", config) if isinstance(config, dict) else {}
            urls = [(str(n), str(s["url"])) for n, s in (servers or {}).items()
                    if isinstance(s, dict) and s.get("url") and s.get("enabled") is not False]
        picked = {}
        for name, url in urls:
            key = host_key(url)
            if key and not _is_this_computer(key.rpartition(":")[0]) and key not in raw:
                picked.setdefault(key, name)
        try:
            # Anchored first: if the ledger cannot record it, nothing is carried over.
            trust_anchor.record(ANCHOR_NAME, picked)
        except Exception as e:  # noqa: BLE001
            _history({"event": "carry_over_refused", "why": type(e).__name__})
            picked = {}
        n = 0
        for key, name in picked.items():
            raw[key] = _row(key, CARRIED_OVER, name, "configured before the host list", "")
            _history({"event": "carried_over", "host": key, "row": raw[key]})
            n += 1
        raw[_MARKER] = _row(_MARKER, CARRIED_OVER, "", "%d host(s) carried over" % n, "")
        _write_raw(raw)
        _history({"event": "list_made", "carried_over": n})
        return n


def check(url: str) -> str:
    """"" when Friday may reach this remote server URL, else the reason in plain words."""
    key = host_key(url)
    if not key:
        return ("the server's address is not a plain http or https web address (no user "
                "name, escapes or backslashes in it)")
    host, _, port = key.rpartition(":")
    if _odd_spelling(host):
        return "the server's address is written in a way Friday does not reach (%s)" % display(key)
    if _is_this_computer(host):
        if int(port) == _friday_port():
            return "that address is Friday herself, not an add-on server"
        return ""
    with _LOCK:
        raw = _read_raw()
        lost = raw is not None and not _marked(raw) and (bool(raw) or _history_path().exists())
    if raw is None:
        return ("the list of add-on server hosts could not be read, so Friday reaches none of "
                "them until it can be read again")
    if lost:
        return ("the list of add-on server hosts was lost or changed outside Friday, so each "
                "host needs your approval again (%s)" % display(key))
    row = raw.get(key)
    if _verified(row, key):
        if row.get("made_by") == CARRIED_OVER:
            # Trusted at the first start: only while the action ledger still says so (an older
            # copy of this file put back cannot bring back a host released since).
            try:
                from agent_friday.governance import trust_anchor
                anchored = trust_anchor.carried(ANCHOR_NAME)
                if anchored is None and _marked(raw):
                    # Carried over before the anchor existed (or the ledger was unreadable at
                    # the start, since reviewed): anchor it now rather than wait for a restart.
                    carry_over_at_boot({})
                    anchored = trust_anchor.carried(ANCHOR_NAME, fresh=True)
            except Exception:
                anchored = None
            if not anchored or key not in anchored["items"]:
                return ("%s was trusted when the host list was made, but the action ledger no "
                        "longer says so; it needs your approval again" % display(key))
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
        if not _marked(raw):
            # A new list, or one lost or edited outside Friday: the owner's press starts it again
            # with this host. Only signed rows are kept; nothing is carried over from the config.
            lost = bool(raw) or _history_path().exists()
            raw = {k: v for k, v in raw.items() if not k.startswith("__") and _verified(v, k)}
            raw[_MARKER] = _row(_MARKER, made_by, "", "list started again by the owner" if lost
                                else "list made by the owner", "")
            if lost:
                _history({"event": "list_restarted", "by": made_by})
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
        if raw is None:
            raise HostRefused("The host list could not be read, so nothing was changed.")
        if key.startswith("__") or key not in raw:
            return False
        try:
            # A host the ledger carried over is released there first, whoever made its row now,
            # so a deleted list is never rebuilt with it. If that cannot be recorded, nothing
            # changes.
            from agent_friday.governance import trust_anchor
            try:
                anchored = trust_anchor.carried(ANCHOR_NAME)
                carried_here = bool(anchored) and key in anchored["items"]
            except trust_anchor.AnchorUnavailable:
                carried_here = True                    # cannot tell: record the release anyway
            if carried_here:
                trust_anchor.release(ANCHOR_NAME, key, why="removed by the owner")
        except Exception as e:  # noqa: BLE001
            raise HostRefused("The removal could not be recorded in the action ledger (%s), "
                              "so nothing was changed." % type(e).__name__)
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
