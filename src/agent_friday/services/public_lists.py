"""The public lists FutureSpeak maintains, fetched, verified and cached here.

Some of what Friday offers depends on facts about the outside world that go
stale: where each platform's "download your data" page is, how to cancel a
subscription with a given company, a retailer's return window, the states'
registered data-broker lists. FutureSpeak maintains those as small signed JSON
files (owner decision O3, 2026-10-15; open class-action settlements are not
among them for now). Friday fetches one only when it needs it and only under
the person's "check for new versions" answer, the same consent that covers
fetching release information: nothing about the person goes out, the request
is a plain public GET.

What has to hold before a list is used:

* it was fetched through the SSRF guard (no loopback, private or rebinding
  answers, every redirect hop vetted), and is no larger than its limit;
* its minisign signature verifies against a key in trust_keys.PUBLIC_LIST_KEYS;
* the signed trusted comment names THIS list (a signed list cannot be served
  under another list's name), carries a version no lower than the cached one
  (no rollback) and an expiry that has not passed;
* the body is JSON of the expected shape.

A verified list is cached under ``<friday home>/public_lists/`` with its
signature, and re-verified every time it is read, so an edited cache file is
refused rather than trusted. A failure keeps the cached copy and says why.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from agent_friday.paths import friday_home
from agent_friday.services import minisign

_log = logging.getLogger("friday.public_lists")
_LOCK = threading.RLock()

#: The lists Friday knows, and the most each may weigh. Settlements are not
#: here: they need weekly care and are deferred (decision O3).
LISTS = {
    "platform_exports": {"max_bytes": 512 * 1024, "what": "where each platform's own data export page is"},
    "cancellation_pages": {"max_bytes": 2 * 1024 * 1024, "what": "how to cancel a subscription with each company"},
    "retailer_windows": {"max_bytes": 1024 * 1024, "what": "stores' return and price-adjustment windows"},
    "broker_registries": {"max_bytes": 4 * 1024 * 1024, "what": "the states' registered data-broker lists"},
}

#: Where the lists are published. Empty until FutureSpeak publishes them; the
#: setting ``public_lists.base_url`` can point at a mirror the owner chose.
DEFAULT_BASE_URL = ""


class ListUnavailable(RuntimeError):
    """Plain words for why a list is not available."""


def cache_dir() -> Path:
    return friday_home() / "public_lists"


def _paths(name: str) -> tuple[Path, Path]:
    return cache_dir() / ("%s.json" % name), cache_dir() / ("%s.json.minisig" % name)


def _keys():
    from agent_friday.services import trust_keys
    return trust_keys.PUBLIC_LIST_KEYS


def _base_url() -> str:
    try:
        from agent_friday.core import _load_settings
        block = (_load_settings() or {}).get("public_lists") or {}
        url = str(block.get("base_url") or "").strip() if isinstance(block, dict) else ""
    except Exception:
        url = ""
    return (url or DEFAULT_BASE_URL).rstrip("/")


def _consented() -> bool:
    try:
        from agent_friday.services import update_check
        return update_check.is_enabled()
    except Exception:
        return False


def _check(name: str, body: bytes, sig_text: str, *, min_version: int = 0,
           today: date | None = None) -> dict:
    """Verify one list; returns {"version", "expires", "data"}; raises ListUnavailable."""
    try:
        out = minisign.verify(body, sig_text, _keys())
    except minisign.SignatureError as e:
        raise ListUnavailable("the %s list did not verify: %s" % (name, e)) from e
    meta = out["meta"]
    if meta.get("name") != name:
        raise ListUnavailable("the signed list is %r, not %r" % (meta.get("name"), name))
    try:
        version = int(meta.get("version") or "")
        expires = date.fromisoformat(meta.get("expires") or "")
    except ValueError as e:
        raise ListUnavailable("the %s list's signature carries no version or expiry" % name) from e
    if version < min_version:
        raise ListUnavailable("the %s list offered (version %d) is older than the one Friday has "
                              "(version %d), so it was refused" % (name, version, min_version))
    if expires < (today or datetime.now(timezone.utc).date()):
        raise ListUnavailable("the %s list expired on %s" % (name, expires.isoformat()))
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ListUnavailable("the %s list is not readable JSON" % name) from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ListUnavailable("the %s list is not in the expected shape" % name)
    return {"version": version, "expires": expires.isoformat(), "data": data}


def cached(name: str) -> dict | None:
    """The cached list, re-verified now; None when there is none or it no
    longer verifies (an edited or expired cache is not used)."""
    if name not in LISTS:
        raise ValueError(name)
    body_p, sig_p = _paths(name)
    with _LOCK:
        try:
            body, sig = body_p.read_bytes(), sig_p.read_text(encoding="utf-8")
        except OSError:
            return None
    try:
        return _check(name, body, sig)
    except ListUnavailable as e:
        _log.warning("cached public list not used: %s", e)
        return None


def _cached_version(name: str) -> int:
    body_p, sig_p = _paths(name)
    try:
        meta = minisign.verify(body_p.read_bytes(), sig_p.read_text(encoding="utf-8"), _keys())["meta"]
        return int(meta.get("version") or 0)
    except Exception:
        return 0


#: Test seam: callable(url, max_bytes) -> bytes. None means the guarded GET.
FETCH = None


def _get(url: str, max_bytes: int) -> bytes:
    from agent_friday.services import web_safety
    resp = web_safety.safe_get(url, timeout=15, stream=True)
    try:
        if resp.status_code != 200:
            raise ListUnavailable("the list server answered HTTP %d" % resp.status_code)
        out = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            out += chunk
            if len(out) > max_bytes:
                raise ListUnavailable("the list is larger than Friday accepts")
        return bytes(out)
    finally:
        resp.close()


def refresh(name: str) -> dict:
    """Fetch, verify and cache one list. Returns {"ok", "version", "message"};
    a failure leaves the cached copy as it was and says why."""
    if name not in LISTS:
        raise ValueError(name)
    if not _consented():
        return {"ok": False, "message": "Fetching lists is covered by your \"check for new versions\" "
                                        "answer, which is off."}
    base = _base_url()
    if not base:
        return {"ok": False, "message": "FutureSpeak hasn't published the %s list yet." % name}
    if not _keys():
        return {"ok": False, "message": "No signing key for the lists is configured yet, so nothing "
                                        "could be verified."}
    url = "%s/%s.json" % (base, name)
    cap = LISTS[name]["max_bytes"]
    get = FETCH or _get
    try:
        body = get(url, cap)
        sig = get(url + ".minisig", 4096).decode("utf-8", "replace")
        out = _check(name, body, sig, min_version=_cached_version(name))
    except ListUnavailable as e:
        _egress(url, "block", str(e))
        return {"ok": False, "message": str(e)[:1].upper() + str(e)[1:] + "."}
    except Exception as e:  # noqa: BLE001
        _egress(url, "block", type(e).__name__)
        return {"ok": False, "message": "The %s list could not be fetched (%s)." % (name, type(e).__name__)}
    _egress(url, "allow", "public list fetched and verified, version %d" % out["version"])
    body_p, sig_p = _paths(name)
    with _LOCK:
        body_p.parent.mkdir(parents=True, exist_ok=True)
        for p, data in ((body_p, body), (sig_p, sig.encode("utf-8"))):
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
    return {"ok": True, "version": out["version"], "expires": out["expires"],
            "message": "The %s list is up to date (version %d)." % (name, out["version"]),
            "at": time.time()}


def _egress(url: str, action: str, reason: str) -> None:
    try:
        from urllib.parse import urlsplit

        from agent_friday.services import egress_gate
        egress_gate.record_binary_egress(urlsplit(url).hostname or url, "public list fetch (nothing personal)",
                                         action=action, reason=reason, byte_len=0)
    except Exception:
        pass


def status() -> list:
    """One line per list for the Locks page: cached version and expiry, or why not."""
    out = []
    for name, spec in LISTS.items():
        c = cached(name)
        out.append({"name": name, "what": spec["what"],
                    "version": c["version"] if c else None,
                    "expires": c["expires"] if c else None,
                    "state": "verified" if c else "not fetched"})
    return out
