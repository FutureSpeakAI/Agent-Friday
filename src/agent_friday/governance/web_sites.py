"""Sites Friday may use for website tasks: the owner's permission slips.

A website task (services/web_tasks.py) may open a page only on a site the
owner put on this list, and act on it (type, click, choose) only where the
owner said it may act. This is a permission slip, never a key: the list holds
no passwords, no cookies and no tokens, only the owner's own choice per site.

What a slip is (one per registrable domain, so ``shop.example.co.uk`` and
``www.shop.example.co.uk`` are one site and ``pay.example-psp.com`` is another):

* ``level``: ``may_read`` (open pages, read, scroll, follow links) or
  ``may_act`` (also type, choose and click). There is no third level: paying,
  sending, submitting, posting and deleting always wait for the owner's yes
  on a card whatever the level (governance/action_gate, browser_session).
* ``keep_sign_in``, ``pictures`` and ``readers``: the owner's other choices
  for the site, stored now so later phases read one record. Nothing here acts
  on them yet: sign-ins are not kept and pictures are never taken in Phase 1.
* ``made_by``: who made it. Only the owner's own press on screen
  (``owner:ui``) or a spoken yes after a read-back (``owner:voice-confirmed``)
  can add or widen a slip. Page content can never add one; a model cannot
  call ``set_site`` because no tool reaches it.

The file is ``<home>/governance/web_sites.json``. Each row is signed with the
governance key (the same HMAC as decision receipts); a row whose signature
does not match is treated as absent and reported, never as allowed.
History is append-only (``web_sites_history.jsonl``): a change supersedes a
row, it never erases what was there before.

Nothing is allowed by default, and nothing in this module proposes a site.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

LEVELS = ("may_read", "may_act")
PICTURES = ("never", "ask")
READERS = ("routing", "local_only")
MAKERS = ("owner:ui", "owner:voice-confirmed")
NARROWERS = MAKERS + ("owner:voice",)

_LOCK = threading.RLock()


class SiteRefused(ValueError):
    """The change was not made; the message says why in plain words."""


def _dir():
    from agent_friday.governance.action_gate import _gov_dir
    return _gov_dir()


def _path():
    return _dir() / "web_sites.json"


def _history_path():
    return _dir() / "web_sites_history.jsonl"


def _key() -> bytes:
    from agent_friday.governance.action_gate import _governance_key
    return _governance_key()


#: Shared-hosting names where each customer has their own subdomain, beyond
#: the list send_grants keeps: a slip for one tenant never covers the others.
#: (The full public suffix list is a known follow-up.)
_EXTRA_SUFFIXES = frozenset({
    "s3.amazonaws.com", "r2.dev", "fly.dev", "onrender.com", "glitch.me", "replit.app",
    "repl.co", "surge.sh", "neocities.org", "wordpress.com", "wixsite.com",
    "squarespace.com", "myshopify.com", "carrd.co", "webflow.io", "framer.app",
    "notion.site", "gitbook.io", "readthedocs.io", "sharepoint.com", "translate.goog"})


def _suffixes():
    from agent_friday.governance.send_grants import PUBLIC_SUFFIXES
    return PUBLIC_SUFFIXES | _EXTRA_SUFFIXES


def site_of(url_or_host: str) -> str:
    """The registrable domain for a URL or host name, or "" when there is none.

    IP addresses and single-label names have no registrable domain: they are
    never a site on this list (the browser's own SSRF rules refuse them too).
    """
    raw = str(url_or_host or "").strip()
    if not raw:
        return ""
    host = raw
    if "://" in raw:
        try:
            host = urlsplit(raw).hostname or ""
        except Exception:
            return ""
    host = host.strip().lower().rstrip(".")
    if not host or "." not in host:
        return ""
    if host.replace(".", "").isdigit() or ":" in host or host.startswith("["):
        return ""
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        # A name outside ASCII is matched only in its punycode form, so a
        # lookalike cannot borrow a slip made for the real name.
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            return ""
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2:
        return ""
    suffixes = _suffixes()
    for n in (3, 2):        # the longest public suffix wins (s3.amazonaws.com before amazonaws.com)
        if len(labels) >= n and ".".join(labels[-n:]) in suffixes:
            return ".".join(labels[-(n + 1):]) if len(labels) > n else ""
    return ".".join(labels[-2:])


def _sign(row: Dict[str, Any]) -> str:
    body = {k: v for k, v in row.items() if k != "sig"}
    canonical = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return _hmac.new(_key(), canonical, hashlib.sha256).hexdigest()


def _verified(row: Any) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("sig"), str):
        return False
    try:
        return _hmac.compare_digest(row["sig"], _sign(row))
    except Exception:
        return False


def _read_raw() -> Dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"__unreadable__": True}
    return data if isinstance(data, dict) else {"__unreadable__": True}


def _write_raw(rows: Dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _history(event: Dict[str, Any]) -> None:
    entry = dict(event, at=time.time())
    entry["sig"] = _sign(entry)
    with open(_history_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def all_sites() -> Dict[str, Any]:
    """Every row with ``verified`` set; an unverified row is shown, never used."""
    with _LOCK:
        raw = _read_raw()
    out = {}
    for site, row in raw.items():
        if site.startswith("__"):
            continue
        good = _verified(row) and isinstance(row, dict) and row.get("site") == site
        out[site] = {**(row if isinstance(row, dict) else {}), "verified": good}
    return out


def get(site: str) -> Optional[Dict[str, Any]]:
    """The verified slip for ``site`` (a registrable domain), or None."""
    site = site_of(site)
    if not site:
        return None
    with _LOCK:
        row = _read_raw().get(site)
    if not _verified(row) or row.get("site") != site or row.get("level") not in LEVELS:
        return None
    return dict(row)


def level_for(url: str) -> Optional[str]:
    """``may_read``, ``may_act`` or None (not on the list, or tampered)."""
    row = get(url)
    return row.get("level") if row else None


def set_site(site: str, level: str, *, made_by: str, keep_sign_in: bool = False,
             pictures: str = "never", readers: str = "routing", note: str = "") -> Dict[str, Any]:
    """Add or change a slip. Only the owner's own action can call this."""
    if made_by not in MAKERS:
        raise SiteRefused("Only you can add a site or widen what Friday may do on it.")
    name = site_of(site)
    if not name:
        raise SiteRefused("That is not a web site Friday can put on the list.")
    if level not in LEVELS:
        raise SiteRefused("Choose 'may read' or 'may act'.")
    if pictures not in PICTURES:
        raise SiteRefused("Pictures of a site are 'never' or 'ask'.")
    if readers not in READERS:
        raise SiteRefused("Readers are 'routing' or 'local_only'.")
    with _LOCK:
        raw = _read_raw()
        if raw.get("__unreadable__"):
            raise SiteRefused("The site list could not be read, so nothing was changed.")
        old = raw.get(name)
        revision = int((old or {}).get("revision") or 0) + 1 if isinstance(old, dict) else 1
        row = {"site": name, "level": level, "keep_sign_in": bool(keep_sign_in),
               "pictures": pictures, "readers": readers, "note": str(note or "")[:200],
               "made_by": made_by, "made_at": time.time(), "revision": revision}
        row["sig"] = _sign(row)
        raw[name] = row
        _write_raw(raw)
        _history({"event": "set", "site": name, "row": row,
                  "previous": old if isinstance(old, dict) else None})
    return dict(row)


def narrow(site: str, *, made_by: str) -> Optional[Dict[str, Any]]:
    """``may_act`` becomes ``may_read``. Narrowing may be spoken."""
    if made_by not in NARROWERS:
        raise SiteRefused("Only you can change the site list.")
    row = get(site)
    if not row:
        return None
    if row["level"] == "may_read":
        return row
    with _LOCK:
        raw = _read_raw()
        new = {**{k: v for k, v in row.items() if k != "sig"}, "level": "may_read",
               "made_by": made_by, "made_at": time.time(),
               "revision": int(row.get("revision") or 0) + 1}
        new["sig"] = _sign(new)
        raw[row["site"]] = new
        _write_raw(raw)
        _history({"event": "narrow", "site": row["site"], "row": new, "previous": row})
    return dict(new)


def remove(site: str, *, made_by: str) -> bool:
    """Take a site off the list. Removing may be spoken."""
    if made_by not in NARROWERS:
        raise SiteRefused("Only you can change the site list.")
    name = site_of(site)
    with _LOCK:
        raw = _read_raw()
        if name not in raw:
            return False
        old = raw.pop(name)
        _write_raw(raw)
        _history({"event": "remove", "site": name, "previous": old})
    return True
