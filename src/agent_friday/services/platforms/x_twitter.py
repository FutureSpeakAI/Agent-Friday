"""
X / Twitter platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.4).

API v2, OAuth 2.0 Authorization Code + PKCE (scopes ``tweet.read tweet.write
users.read offline.access``), loopback redirect
``http://localhost:3000/api/content/platforms/x_twitter/callback``.

The sharp constraints this adapter owns:
  * **Threads** — the ``thread`` format maps to sequential ``POST /2/tweets``
    with ``reply.in_reply_to_tweet_id`` chaining, one segment at a time with
    jitter; every confirmed segment id is persisted so a mid-thread failure
    resumes from the last confirmed segment instead of double-posting.
  * **The paid-tier trap** — write access is metered by paid tier and pricing
    drifts, so the tier is *configuration* (``tier: free|basic|pro``), the
    adapter keeps a hard local budget (daily + monthly windows), and ``status()``
    surfaces posts-remaining-this-month so the user is never surprised.
  * **Media** — v2 chunked upload (INIT → APPEND → FINALIZE), alt text
    best-effort via the metadata endpoint.
  * **Length** — URLs count as 23 characters (t.co wrapping); counting is
    NFC-normalized code points (the composer owns exact grapheme math).

Transport: every HTTP byte goes through the module-level ``_http()``
chokepoint so tests stub it — the adapter itself never opens a socket.
Adapter errors are externalized as fixed content-free strings (§12.5);
full detail goes to the local debug logger only, never to envelopes or
``status()``. Nothing tokenish is ever logged.

Import-safe under FRIDAY_TESTING: stdlib only at import time, no threads
started, no network; credential_store is lazy-imported by the base class.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import secrets
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from agent_friday.services.platforms import base as pbase
from agent_friday.services.platforms.base import PlatformAdapter

_log = logging.getLogger("friday.platforms.x_twitter")

# ── endpoints (design-time snapshot, mid-2026 — §4.2 volatility warning) ─────
AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"          # pragma: allowlist secret
REVOKE_URL = "https://api.x.com/2/oauth2/revoke"
API_BASE = "https://api.x.com/2"
MEDIA_UPLOAD_URL = f"{API_BASE}/media/upload"
MEDIA_METADATA_URL = f"{API_BASE}/media/metadata"
DEFAULT_REDIRECT_URI = "http://localhost:3000/api/content/platforms/x_twitter/callback"
SCOPES = "tweet.read tweet.write users.read offline.access"

STANDARD_CHAR_LIMIT = 280
PREMIUM_CHAR_LIMIT = 25000
_TCO_LEN = 23                       # every URL counts as a t.co link
_CHUNK_BYTES = 4 * 1024 * 1024      # APPEND chunk size
_STATE_TTL_S = 600                  # pending OAuth state is single-use + 10 min
_URL_RE = re.compile(r"https?://\S+")

# §4.4 — tier is configuration; numbers drift, so these are conservative local
# budgets *below* the design-time snapshot, overridable per key in config.
TIER_BUDGETS: Dict[str, Dict[str, int]] = {
    "free":  {"daily": 17,  "monthly": 500},
    "basic": {"daily": 100, "monthly": 3000},
    "pro":   {"daily": 300, "monthly": 10000},
}

# ── fixed content-free error strings (§12.5) ─────────────────────────────────
E_NOT_CONNECTED = "not_connected"
E_AUTH = "auth_error"
E_FORBIDDEN = "forbidden"
E_RATE = "rate_limited"
E_HTTP = "platform_http_error"
E_NETWORK = "network_error"
E_BUDGET = "rate_budget_exhausted"
E_MEDIA = "media_upload_failed"
E_STATE = "oauth_state_mismatch"
E_CONFIG = "missing_client_config"
E_INTERNAL = "adapter_error"


# ── transport chokepoint — tests monkeypatch THIS ────────────────────────────
def _http(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
          body: Optional[bytes] = None, timeout: int = 20) -> Dict[str, Any]:
    """The single wire for every X API call. Returns
    ``{status, json, text, headers}`` (headers lowercased); a network-level
    failure returns ``{status: 0, ..., "error": E_NETWORK}``. Never raises."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, method=method.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
        status = e.code
    except Exception:
        _log.debug("network failure on %s %s", method, url, exc_info=True)
        return {"status": 0, "json": None, "text": "", "headers": {},
                "error": E_NETWORK}
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text else None
    except Exception:
        parsed = None
    return {"status": int(status),
            "json": parsed if isinstance(parsed, dict) else None,
            "text": text[:4096], "headers": hdrs}


# ── X-flavoured length: URLs count as 23, grapheme-cluster counted ───────────
def _x_len(text: Any) -> int:
    """One length metric across the pipeline: the composer's count_chars
    (grapheme clusters + URL=23) is the source of truth, so a body the
    composer trimmed to exactly the limit can never be rejected here over a
    counting disagreement (family emoji = 1, both sides). Falls back to
    NFC code points + URL weighting only if the composer is unimportable."""
    s = unicodedata.normalize("NFC", str(text or ""))
    try:
        from agent_friday.services.content_composer import count_chars
        return int(count_chars(s, "twitter"))
    except Exception:
        total, last = 0, 0
        for m in _URL_RE.finditer(s):
            total += len(s[last:m.start()]) + _TCO_LEN
            last = m.end()
        return total + len(s[last:])


def _append_hashtag_block(text: str, hashtags: Any) -> str:
    """§5.3: the composer suggests the tags, the outbound payload carries them
    — tags already present in the text are never duplicated."""
    base = str(text or "")
    low = base.lower()
    block: List[str] = []
    for t in hashtags or []:
        t = str(t or "").strip().lstrip("#")
        if t and ("#" + t).lower() not in low:
            block.append("#" + t)
    if not block:
        return base
    return (base.rstrip() + "\n\n" + " ".join(block)).strip()


# ── thread progress persistence (per-segment resume, §4.4 / §7.2) ────────────
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_MAX_ENTRIES = 64


def _progress_path() -> Path:
    return pbase.PLATFORMS_DIR / "x_thread_progress.json"


def _read_progress() -> Dict[str, Any]:
    try:
        path = _progress_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_progress(state: Dict[str, Any]) -> None:
    try:
        if len(state) > _PROGRESS_MAX_ENTRIES:
            keep = sorted(state.items(),
                          key=lambda kv: str((kv[1] or {}).get("updated_at") or ""))
            state = dict(keep[-_PROGRESS_MAX_ENTRIES:])
        path = _progress_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _first_of_next_month(now: datetime) -> str:
    if now.month == 12:
        nxt = now.replace(year=now.year + 1, month=1, day=1,
                          hour=0, minute=0, second=0, microsecond=0)
    else:
        nxt = now.replace(month=now.month + 1, day=1,
                          hour=0, minute=0, second=0, microsecond=0)
    return nxt.strftime("%Y-%m-%dT%H:%M:%SZ")


class XTwitterAdapter(PlatformAdapter):
    name = "x_twitter"
    label = "X (Twitter)"
    auth_mode = "oauth2_pkce"
    automation_tier = "api"          # the $ is a budget problem, not a rung
    default_daily_limit = 17         # free-tier conservative default

    # ── config helpers ────────────────────────────────────────────────────────
    def _plan(self) -> str:
        p = str(self._config.get("tier") or "").strip().lower()
        return p if p in TIER_BUDGETS else "free"

    def _is_premium(self) -> bool:
        """Premium entitlement raises the char limit to 25k — read from config
        or the stored account blob (§4.4: the account's actual limit)."""
        if self._config.get("premium"):
            return True
        try:
            creds = self.load_credentials() or {}
            return bool(creds.get("premium"))
        except Exception:
            return False

    def _client_id(self) -> Optional[str]:
        v = self._config.get("client_id")
        return str(v) if v else None

    def _redirect_uri(self) -> str:
        v = self._config.get("redirect_uri")
        return str(v) if v else DEFAULT_REDIRECT_URI

    def _budget_limit(self) -> int:
        try:
            return int(self._config.get("daily_post_limit"))
        except (TypeError, ValueError):
            return int(TIER_BUDGETS[self._plan()]["daily"])

    def _monthly_limit(self) -> int:
        try:
            return int(self._config.get("monthly_post_limit"))
        except (TypeError, ValueError):
            return int(TIER_BUDGETS[self._plan()]["monthly"])

    # ── capability declaration (§4.2 / §4.4) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        plan = self._plan()
        caps.update({
            "formats": ["post", "thread", "image_post"],
            "char_limit": PREMIUM_CHAR_LIMIT if self._is_premium() else STANDARD_CHAR_LIMIT,
            "title_limit": None,
            "thread": True,
            "native_schedule": False,
            "native_delete": True,
            # free-tier reads are nearly nil → honest "counts"; paid → full
            "analytics": "full" if plan in ("basic", "pro") else "counts",
            "hashtags_max": None,     # no API cap; norms (1-2) are composer's job
            "notes": [
                f"tier '{plan}' is configuration — {self._budget_limit()}/day, "
                f"{self._monthly_limit()}/month local budget (verify current X pricing)",
                f"URLs count as {_TCO_LEN} characters (t.co wrapping)",
                "280 chars standard; 25,000 with premium entitlements (config 'premium')",
                "GIFs up to 15 MB (other images 5 MB); no native scheduling",
            ],
        })
        caps["media"] = {
            "images": {"max": 4, "formats": ["png", "jpeg", "webp", "gif"],
                       "max_bytes": 5 * 1024 * 1024, "aspect": (0.25, 4.0)},
            "video": {"max_s": 140, "formats": ["mp4"],
                      "max_bytes": 512 * 1024 * 1024},
            "alt_text": True,
        }
        return caps

    # ── auth lifecycle (OAuth2 + PKCE, loopback redirect, state bound) ───────
    def connect_url(self, state: str) -> Optional[str]:
        try:
            cid = self._client_id()
            if not cid:
                self._last_error = E_CONFIG
                return None
            if not state:
                self._last_error = E_STATE
                return None
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            ).decode("ascii").rstrip("=")
            # pending state rides the encrypted blob — single-use, 10 min TTL
            blob = self.load_credentials() or {}
            blob["pending_auth"] = {
                "state": str(state), "verifier": verifier,
                "created_at": pbase._now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            self.save_credentials(blob)
            q = urlencode({
                "response_type": "code", "client_id": cid,
                "redirect_uri": self._redirect_uri(), "scope": SCOPES,
                "state": str(state), "code_challenge": challenge,
                "code_challenge_method": "S256",
            })
            self._audit("connect_started")
            return f"{AUTH_URL}?{q}"
        except Exception:
            _log.debug("connect_url failed", exc_info=True)
            self._last_error = E_INTERNAL
            return None

    def _write_blob(self, blob: Dict[str, Any]) -> None:
        """Persist a mutated blob; an empty blob is removed outright so it can
        never read back as 'connected'."""
        try:
            if blob:
                self.save_credentials(blob)
            else:
                path = self._cred_path()
                if path.exists():
                    path.unlink()
        except Exception:
            pass

    def _token_request(self, form: Dict[str, str],
                       url: str = TOKEN_URL) -> Dict[str, Any]:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        cid = self._client_id()
        confidential = self.simple_secret()   # optional client secret
        if cid and confidential:
            pair = base64.b64encode(
                f"{cid}:{confidential}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {pair}"
        return _http("POST", url, headers=headers,
                     body=urlencode(form).encode("utf-8"))

    def handle_callback(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            params = dict(params or {})
            blob = self.load_credentials() or {}
            pending = blob.pop("pending_auth", None)
            if pending is not None:          # single-use, whatever happens next
                self._write_blob(blob)
            if params.get("error"):
                self._last_error = "oauth_denied"
                self._audit("callback_denied")
                return self.status()
            pend = pending if isinstance(pending, dict) else {}
            created = pbase._parse_iso(pend.get("created_at"))
            stale = (created is None
                     or (pbase._now_utc() - created).total_seconds() > _STATE_TTL_S)
            if (not params.get("state") or not pend.get("state")
                    or str(params.get("state")) != str(pend.get("state")) or stale):
                self._last_error = E_STATE
                self._audit("callback_state_rejected")
                return self.status()
            code = str(params.get("code") or "")
            cid = self._client_id()
            if not code or not cid:
                self._last_error = E_CONFIG
                return self.status()
            r = self._token_request({
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self._redirect_uri(), "client_id": cid,
                "code_verifier": str(pend.get("verifier") or ""),
            })
            j = r.get("json") or {}
            granted = j.get("access_token")
            if r.get("status") != 200 or not granted:
                self._classify(r)
                self._last_error = E_AUTH
                self._audit("callback_exchange_failed")
                return self.status()
            now = pbase._now_utc()
            ttl = int(j.get("expires_in") or 7200)
            creds: Dict[str, Any] = {
                "access_token": granted,
                "refresh_token": j.get("refresh_token"),
                "expires_at": (now + timedelta(seconds=ttl)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scopes": str(j.get("scope") or SCOPES).split(),
                "account": None,
                "connected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            me = _http("GET", f"{API_BASE}/users/me",
                       headers={"Authorization": f"Bearer {granted}"})
            if me.get("status") == 200:
                creds["account"] = ((me.get("json") or {}).get("data") or {}).get("username")
            self.save_credentials(creds)
            self._last_error = None
            self._audit("connected", account_resolved=bool(creds["account"]))
            return self.status()
        except Exception:
            _log.debug("handle_callback failed", exc_info=True)
            self._last_error = E_INTERNAL
            return self.status()

    def refresh(self) -> bool:
        try:
            creds = self.load_credentials()
            if not isinstance(creds, dict) or not creds.get("access_token"):
                return False
            now = pbase._now_utc()
            exp = pbase._parse_iso(creds.get("expires_at"))
            if exp is None or (exp - now).total_seconds() > 120:
                return True                       # not expiring — usable as-is
            rot = creds.get("refresh_token")
            if not rot:
                self._last_error = E_AUTH
                return (exp - now).total_seconds() > 0
            cid = self._client_id()
            if not cid:
                self._last_error = E_CONFIG
                return False
            r = self._token_request({"grant_type": "refresh_token",
                                     "refresh_token": rot, "client_id": cid})
            j = r.get("json") or {}
            fresh = j.get("access_token")
            if r.get("status") == 200 and fresh:
                creds["access_token"] = fresh
                if j.get("refresh_token"):        # X rotates refresh tokens
                    creds["refresh_token"] = j.get("refresh_token")
                ttl = int(j.get("expires_in") or 7200)
                creds["expires_at"] = (now + timedelta(seconds=ttl)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
                if j.get("scope"):
                    creds["scopes"] = str(j["scope"]).split()
                self.save_credentials(creds)
                self._audit("refreshed")
                return True
            self._classify(r)
            self._last_error = E_AUTH
            return False
        except Exception:
            _log.debug("refresh failed", exc_info=True)
            self._last_error = E_INTERNAL
            return False

    def revoke(self) -> bool:
        try:
            creds = self.load_credentials() or {}
            platform_side = False
            bearer = creds.get("access_token")
            cid = self._client_id()
            if bearer and cid:
                r = self._token_request({"token": str(bearer), "client_id": cid,
                                         "token_type_hint": "access_token"},
                                        url=REVOKE_URL)
                platform_side = r.get("status") == 200
            self.clear_credentials()
            self._audit("revoke", platform_side=platform_side)
            return True
        except Exception:
            self._last_error = E_INTERNAL
            try:
                self.clear_credentials()
            except Exception:
                pass
            return True

    def status(self) -> Dict[str, Any]:
        st = super().status()
        try:
            creds = self.load_credentials() or {}
            # a stored client secret alone is NOT a connection — OAuth is
            st["connected"] = bool(creds.get("access_token"))
            st["plan"] = self._plan()
            st["client_configured"] = bool(self._client_id())
            m = self._monthly_budget()
            lim = m.get("limit", 0)
            st["monthly_posts_remaining"] = (
                max(0, lim - int(m.get("used", 0) or 0))
                if isinstance(lim, int) and lim > 0 else None)
            st["monthly_reset_at"] = m.get("reset_at")
        except Exception:
            pass
        return st

    def _bearer(self) -> Optional[str]:
        """Live access token — auto-refreshes when expiring. None = not usable."""
        creds = self.load_credentials()
        if not isinstance(creds, dict) or not creds.get("access_token"):
            return None
        exp = pbase._parse_iso(creds.get("expires_at"))
        if exp is not None and (exp - pbase._now_utc()).total_seconds() <= 60:
            if not self.refresh():
                return None
            creds = self.load_credentials() or {}
        val = creds.get("access_token")
        return str(val) if val else None

    # ── error classification: fixed content-free strings out (§12.5) ─────────
    def _classify(self, r: Dict[str, Any]) -> Dict[str, Any]:
        status = int(r.get("status") or 0)
        if status == 0:
            err = E_NETWORK
        elif status == 401:
            err = E_AUTH
        elif status == 403:
            err = E_FORBIDDEN
        elif status == 429:
            err = E_RATE
        else:
            err = E_HTTP
        out: Dict[str, Any] = {"ok": False, "error": err, "status": status}
        ra = (r.get("headers") or {}).get("retry-after")
        if ra is not None:
            try:
                out["retry_after"] = int(float(ra))   # §7.5: publisher honors it
            except (TypeError, ValueError):
                pass
        self._last_error = err
        _log.debug("x_twitter API %s: %s", status, (r.get("text") or "")[:500])
        return out

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Hard validation (X length rules, media homogeneity) + v2 chunked
        media upload. {ok, prepared, warnings} — never raises."""
        try:
            target = dict(target or {})
            post = dict(post or {})
            caps = self.capabilities()
            warnings: List[str] = []

            body = str(target.get("adapted_body") or post.get("body") or "")
            segments = [s for s in (target.get("segments") or [])
                        if isinstance(s, str)]
            # §5.3: the suggested hashtag block ships with the payload — on the
            # closing segment for threads. Appended BEFORE the length check so
            # validation covers exactly what publishes (the composer reserved
            # room for the block when it trimmed).
            hashtags = list(target.get("hashtags") or [])
            if hashtags:
                if segments:
                    segments = list(segments)
                    segments[-1] = _append_hashtag_block(segments[-1], hashtags)
                else:
                    body = _append_hashtag_block(body, hashtags)
            limit = target.get("char_limit") or caps.get("char_limit") or STANDARD_CHAR_LIMIT
            if isinstance(limit, int) and limit > 0:
                if segments:
                    over = [i for i, s in enumerate(segments) if _x_len(s) > limit]
                    if over:
                        return {"ok": False, "warnings": warnings,
                                "error": f"segment(s) {over} exceed char_limit "
                                         f"({limit}; URLs count as {_TCO_LEN})"}
                elif _x_len(body) > limit:
                    return {"ok": False, "warnings": warnings,
                            "error": f"body exceeds char_limit "
                                     f"({_x_len(body)}/{limit}; URLs count as {_TCO_LEN})"}

            assets = [a for a in (target.get("adapted_assets")
                                  or post.get("assets") or [])
                      if isinstance(a, dict)]

            def _kind(a: Dict[str, Any]) -> str:
                return str(a.get("kind")
                           or (a.get("source") or {}).get("kind") or "image")

            images = [a for a in assets if _kind(a) == "image"]
            videos = [a for a in assets if _kind(a) == "video"]
            others = len(assets) - len(images) - len(videos)
            if others:
                warnings.append(f"{others} unsupported asset kind(s) skipped")
            if images and videos:
                return {"ok": False, "warnings": warnings,
                        "error": "mixed image+video media not supported"}
            img_max = ((caps.get("media") or {}).get("images") or {}).get("max") or 4
            if len(images) > img_max:
                return {"ok": False, "warnings": warnings,
                        "error": f"too many images ({len(images)}/{img_max})"}
            if len(videos) > 1:
                return {"ok": False, "warnings": warnings,
                        "error": "multiple videos not supported (1 max)"}
            vcaps = (caps.get("media") or {}).get("video") or {}
            for v in videos:
                dur = v.get("duration_s") or (v.get("source") or {}).get("duration_s")
                max_s = vcaps.get("max_s")
                if dur and max_s and float(dur) > float(max_s):
                    return {"ok": False, "warnings": warnings,
                            "error": f"video too long ({float(dur):.0f}s/{max_s}s)"}
            missing_alt = [a for a in images
                           if not (a.get("alt_text")
                                   or (a.get("source") or {}).get("alt_text"))]
            if missing_alt:
                warnings.append(f"{len(missing_alt)} image(s) missing alt text")

            # media upload — transport only when there are local files to send
            wanted = images or videos
            uploadable = [a for a in wanted if a.get("out_path") or a.get("path")]
            media_ids: List[str] = []
            if uploadable:
                bearer = self._bearer()
                if not bearer:
                    self._last_error = E_NOT_CONNECTED
                    return {"ok": False, "warnings": warnings,
                            "error": E_NOT_CONNECTED}
                for a in uploadable:
                    up = self._upload_media(bearer, a, caps)
                    if not up.get("ok"):
                        return {"ok": False, "warnings": warnings,
                                "error": up.get("error") or E_MEDIA}
                    media_ids.append(up["media_id"])
            if len(wanted) > len(uploadable):
                warnings.append(f"{len(wanted) - len(uploadable)} asset(s) have "
                                "no local file yet — not uploaded")

            prepared = {
                "platform": self.name,
                "target_id": target.get("id"),
                "post_id": post.get("id"),
                "format": target.get("format") or "post",
                "title": "",                        # X has no title field
                "body": body,
                "segments": segments,
                "assets": assets,
                "media_ids": media_ids,
                "hashtags": list(target.get("hashtags") or []),
                "mentions": list(target.get("mentions") or []),
                "options": dict(target.get("options") or {}),
            }
            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception:
            _log.debug("prepare failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL, "warnings": []}

    def _upload_media(self, bearer: str, asset: Dict[str, Any],
                      caps: Dict[str, Any]) -> Dict[str, Any]:
        """v2 chunked upload: INIT → APPEND (4 MB chunks) → FINALIZE (+ bounded
        STATUS poll), then best-effort alt text. Fixed error strings out."""
        try:
            p = Path(str(asset.get("out_path") or asset.get("path")))
            try:
                data = p.read_bytes()
            except Exception:
                return {"ok": False, "error": E_MEDIA}
            kind = str(asset.get("kind")
                       or (asset.get("source") or {}).get("kind") or "image")
            suffix = p.suffix.lower().lstrip(".")
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "webp": "image/webp", "gif": "image/gif", "mp4": "video/mp4",
                    "mov": "video/quicktime"}.get(suffix, "application/octet-stream")
            mcaps = caps.get("media") or {}
            if kind == "video":
                category = "tweet_video"
                max_bytes = (mcaps.get("video") or {}).get("max_bytes")
            elif suffix == "gif":
                category = "tweet_gif"
                max_bytes = 15 * 1024 * 1024
            else:
                category = "tweet_image"
                max_bytes = (mcaps.get("images") or {}).get("max_bytes")
            if isinstance(max_bytes, int) and 0 < max_bytes < len(data):
                return {"ok": False, "error": "media_too_large"}

            hdr = {"Authorization": f"Bearer {bearer}",
                   "Content-Type": "application/json"}
            r = _http("POST", MEDIA_UPLOAD_URL, headers=hdr, body=json.dumps({
                "command": "INIT", "total_bytes": len(data),
                "media_type": mime, "media_category": category}).encode("utf-8"))
            mid = self._media_id_from(r)
            if r.get("status") not in (200, 201, 202) or not mid:
                self._classify(r)
                return {"ok": False, "error": E_MEDIA}
            for idx, off in enumerate(range(0, len(data), _CHUNK_BYTES)):
                chunk = base64.b64encode(data[off:off + _CHUNK_BYTES]).decode("ascii")
                r = _http("POST", MEDIA_UPLOAD_URL, headers=hdr, body=json.dumps({
                    "command": "APPEND", "media_id": mid,
                    "segment_index": idx, "media": chunk}).encode("utf-8"))
                if r.get("status") not in (200, 201, 202, 204):
                    self._classify(r)
                    return {"ok": False, "error": E_MEDIA}
            r = _http("POST", MEDIA_UPLOAD_URL, headers=hdr, body=json.dumps({
                "command": "FINALIZE", "media_id": mid}).encode("utf-8"))
            if r.get("status") not in (200, 201):
                self._classify(r)
                return {"ok": False, "error": E_MEDIA}
            # async processing (video/gif) — bounded STATUS poll
            info = self._processing_info(r)
            tries = 0
            while (isinstance(info, dict)
                   and info.get("state") in ("pending", "in_progress")
                   and tries < 10):
                time.sleep(min(5.0, max(1.0, float(info.get("check_after_secs") or 1))))
                r = _http("GET",
                          f"{MEDIA_UPLOAD_URL}?command=STATUS&media_id={quote(str(mid))}",
                          headers={"Authorization": f"Bearer {bearer}"})
                info = self._processing_info(r)
                tries += 1
            if isinstance(info, dict) and info.get("state") == "failed":
                return {"ok": False, "error": E_MEDIA}
            alt = asset.get("alt_text") or (asset.get("source") or {}).get("alt_text")
            if alt:   # best-effort — a metadata failure never blocks the post
                _http("POST", MEDIA_METADATA_URL, headers=hdr, body=json.dumps({
                    "id": mid,
                    "metadata": {"alt_text": {"text": str(alt)[:1000]}}}).encode("utf-8"))
            return {"ok": True, "media_id": mid}
        except Exception:
            _log.debug("media upload failed", exc_info=True)
            return {"ok": False, "error": E_MEDIA}

    @staticmethod
    def _media_id_from(r: Dict[str, Any]) -> Optional[str]:
        j = r.get("json") or {}
        d = j.get("data") if isinstance(j.get("data"), dict) else j
        mid = d.get("id") or d.get("media_id_string") or d.get("media_id")
        return str(mid) if mid else None

    @staticmethod
    def _processing_info(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        j = r.get("json") or {}
        d = j.get("data") if isinstance(j.get("data"), dict) else j
        info = d.get("processing_info")
        return info if isinstance(info, dict) else None

    # ── jitter between thread segments (§4.4 / §12.4 API-citizen posting) ────
    def _jitter_bounds(self) -> tuple:
        v = self._config.get("thread_jitter_s", (1.0, 3.0))
        try:
            if isinstance(v, (int, float)):
                lo = hi = float(v)
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                lo, hi = float(v[0]), float(v[1])
            else:
                lo, hi = 1.0, 3.0
        except (TypeError, ValueError):
            lo, hi = 1.0, 3.0
        lo = min(max(0.0, lo), 10.0)
        hi = min(max(lo, hi), 10.0)
        return lo, hi

    def _thread_pause(self) -> None:
        lo, hi = self._jitter_bounds()
        if hi <= 0:
            return
        time.sleep(random.uniform(lo, hi))

    # thread-progress bookkeeping (persisted so a restart still resumes)
    def _progress_get(self, tid: str, fp: str) -> List[str]:
        with _PROGRESS_LOCK:
            entry = _read_progress().get(tid)
            if isinstance(entry, dict) and entry.get("fp") == fp:
                return [str(x) for x in (entry.get("ids") or [])]
        return []

    def _progress_put(self, tid: str, fp: str, ids: List[str]) -> None:
        with _PROGRESS_LOCK:
            state = _read_progress()
            state[tid] = {"fp": fp, "ids": list(ids),
                          "updated_at": pbase._now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")}
            _write_progress(state)

    def _progress_clear(self, tid: str) -> None:
        with _PROGRESS_LOCK:
            state = _read_progress()
            if tid in state:
                del state[tid]
                _write_progress(state)

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Single post or reply-chain thread. Threads publish one segment at a
        time with jitter; every confirmed id is persisted so a retry resumes
        from the last confirmed segment (never double-posts one)."""
        try:
            prepared = dict(prepared or {})
            bearer = self._bearer()
            if not bearer:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            segments = [s for s in (prepared.get("segments") or [])
                        if isinstance(s, str)]
            texts = segments if segments else [str(prepared.get("body") or "")]
            media_ids = [str(m) for m in (prepared.get("media_ids") or []) if m]
            if not texts[0].strip() and not media_ids:
                self._last_error = "empty_post"
                return {"ok": False, "error": "empty_post"}

            n = len(texts)
            tid = str(prepared.get("target_id") or "")
            fp = hashlib.sha256("\x1f".join(texts).encode("utf-8")).hexdigest()
            done: List[str] = self._progress_get(tid, fp) if (n > 1 and tid) else []
            if done and len(done) >= n:      # crash landed after the last segment
                self._progress_clear(tid)
                return {"ok": True,
                        "post_url": f"https://x.com/i/web/status/{done[0]}",
                        "platform_post_id": done[0],
                        "raw": {"segment_ids": list(done), "resumed_from": n}}

            needed = n - len(done)
            if self.budget_would_exceed(needed):
                b = self.rate_budget()
                self._last_error = E_BUDGET
                return {"ok": False, "error": E_BUDGET,
                        "defer_until": b.get("reset_at"),
                        "monthly": b.get("monthly")}

            headers = {"Authorization": f"Bearer {bearer}",
                       "Content-Type": "application/json"}
            # §12.4 / D4: the composer maps the nsfw tag to X's sensitivity
            # flag in target options — it must actually ride the tweet payload,
            # never be silently dropped.
            sensitive = bool((prepared.get("options") or {}).get("possibly_sensitive"))
            reply_to = done[-1] if done else None
            resumed_from = len(done)
            for i in range(resumed_from, n):
                if i > 0:
                    self._thread_pause()
                payload: Dict[str, Any] = {"text": texts[i]}
                if sensitive:
                    payload["possibly_sensitive"] = True
                if i == 0 and media_ids:
                    payload["media"] = {"media_ids": media_ids}
                if reply_to:
                    payload["reply"] = {"in_reply_to_tweet_id": reply_to}
                r = _http("POST", f"{API_BASE}/tweets", headers=headers,
                          body=json.dumps(payload).encode("utf-8"))
                tweet_id = str(((r.get("json") or {}).get("data") or {}).get("id") or "")
                if r.get("status") in (200, 201) and tweet_id:
                    done.append(tweet_id)
                    reply_to = tweet_id
                    self.consume_budget(1)
                    if n > 1 and tid:
                        self._progress_put(tid, fp, done)
                    continue
                # honest failure: fixed string out, resume state preserved
                env = self._classify(r)
                if done and n > 1:
                    env["resume_index"] = len(done)
                    env["segment_ids"] = list(done)
                return env

            if tid:
                self._progress_clear(tid)
            root = done[0]
            return {"ok": True,
                    "post_url": f"https://x.com/i/web/status/{root}",
                    "platform_post_id": root,
                    "raw": {"segment_ids": list(done),
                            "resumed_from": resumed_from}}
        except Exception:
            _log.debug("publish failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        try:
            bearer = self._bearer()
            if not bearer:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            r = _http("DELETE",
                      f"{API_BASE}/tweets/{quote(str(platform_post_id))}",
                      headers={"Authorization": f"Bearer {bearer}"})
            if r.get("status") == 200:
                deleted = bool(((r.get("json") or {}).get("data") or {}).get("deleted"))
                self._audit("delete", deleted=deleted)
                return {"ok": True, "deleted": deleted}
            return self._classify(r)
        except Exception:
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    # ── analytics path (§8.2 unified keys; missing ≠ zero) ───────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        try:
            bearer = self._bearer()
            pid = str(platform_post_id or "").strip()
            if not bearer or not pid:
                return None
            r = _http("GET",
                      f"{API_BASE}/tweets?ids={quote(pid)}&tweet.fields=public_metrics",
                      headers={"Authorization": f"Bearer {bearer}"})
            if r.get("status") != 200:
                self._classify(r)
                return None
            rows = (r.get("json") or {}).get("data") or []
            if not (isinstance(rows, list) and rows):
                return None
            pm = (rows[0] or {}).get("public_metrics") or {}
            if not isinstance(pm, dict) or not pm:
                return None

            def _num(k: str) -> Optional[int]:
                v = pm.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return None
                return int(v)

            out: Dict[str, Any] = {}
            for src, dst in (("impression_count", "impressions"),
                             ("like_count", "likes"),
                             ("reply_count", "comments"),
                             ("bookmark_count", "saves")):   # bookmarks ≈ saves
                v = _num(src)
                if v is not None:
                    out[dst] = v
            rt, qt = _num("retweet_count"), _num("quote_count")
            if rt is not None or qt is not None:
                out["shares"] = (rt or 0) + (qt or 0)        # retweet + quote
            return out or None
        except Exception:
            return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        try:
            bearer = self._bearer()
            if not bearer:
                return None
            r = _http("GET", f"{API_BASE}/users/me?user.fields=public_metrics",
                      headers={"Authorization": f"Bearer {bearer}"})
            if r.get("status") != 200:
                return None
            pm = ((r.get("json") or {}).get("data") or {}).get("public_metrics") or {}
            out: Dict[str, Any] = {}
            if isinstance(pm.get("followers_count"), int):
                out["followers"] = pm["followers_count"]
            if isinstance(pm.get("tweet_count"), int):
                out["posts"] = pm["tweet_count"]
            return out or None
        except Exception:
            return None

    # ── local rate budgeting: daily window + the monthly paid-tier cap ───────
    def _month_key(self) -> str:
        return f"{self.name}:month"

    def _monthly_budget(self) -> Dict[str, Any]:
        limit = self._monthly_limit()
        try:
            now = pbase._now_utc()
            with pbase._BUDGET_LOCK:
                state = pbase._read_budget_state()
                entry = state.get(self._month_key())
                entry = entry if isinstance(entry, dict) else None
                reset = pbase._parse_iso(entry.get("reset_at")) if entry else None
                if entry is None or reset is None or reset <= now:
                    entry = {"window": "month", "used": 0,
                             "reset_at": _first_of_next_month(now)}
                    state[self._month_key()] = entry
                    pbase._write_budget_state(state)
                return {"window": "month", "used": int(entry.get("used", 0) or 0),
                        "limit": limit, "reset_at": entry.get("reset_at")}
        except Exception:
            return {"window": "month", "used": 0, "limit": limit, "reset_at": None}

    def rate_budget(self) -> Dict[str, Any]:
        env = super().rate_budget()
        env["monthly"] = self._monthly_budget()
        return env

    def budget_would_exceed(self, n: int = 1) -> bool:
        if super().budget_would_exceed(n):
            return True
        m = self._monthly_budget()
        lim = m.get("limit", 0)
        if not isinstance(lim, int) or lim <= 0:
            return False
        return int(m.get("used", 0) or 0) + n > lim

    def consume_budget(self, n: int = 1) -> Dict[str, Any]:
        res = super().consume_budget(n)
        try:
            now = pbase._now_utc()
            with pbase._BUDGET_LOCK:
                state = pbase._read_budget_state()
                entry = state.get(self._month_key())
                entry = entry if isinstance(entry, dict) else None
                reset = pbase._parse_iso(entry.get("reset_at")) if entry else None
                if entry is None or reset is None or reset <= now:
                    entry = {"window": "month", "used": 0,
                             "reset_at": _first_of_next_month(now)}
                entry["used"] = int(entry.get("used", 0) or 0) + max(0, int(n))
                state[self._month_key()] = entry
                pbase._write_budget_state(state)
            if isinstance(res, dict):
                res["monthly"] = {"window": "month", "used": entry["used"],
                                  "limit": self._monthly_limit(),
                                  "reset_at": entry.get("reset_at")}
        except Exception:
            pass
        return res


ADAPTER = XTwitterAdapter
