"""
Instagram platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.5).

Instagram API with Instagram Login (business/creator accounts on
``graph.instagram.com`` — no Facebook Page required, the preferred §4.5
path): container-based publishing (``POST /{ig-user}/media`` →
``POST /{ig-user}/media_publish``) for JPEG feed images (aspect 4:5 …
1.91:1 with an auto-crop/convert plan), carousels (≤10 children), Reels via
resumable binary upload (rupload) or a public ``video_url``, and Stories.
Caption ≤2,200 chars, ≤30 hashtags, ≤20 mentions.

The sharp constraint (§4.5 / §7.4): feed/carousel/story media must be
**publicly reachable URLs**. Friday runs on localhost, so the adapter
resolves, in order: (1) resumable binary upload where the platform allows
it (Reels); (2) an asset already staged to the user's configured public
host (``staging_base_url`` config — the publisher stages, we verify);
(3) **hold** with a clear message via the §4.14 degrade envelope — never a
silent failure, never a silent fall down the ladder.

Rate budget: 100 API-published posts per rolling 24 h (carousel = 1). The
local budget defaults to that ceiling and, when connected, best-effort
syncs ``used`` from the platform's own ``content_publishing_limit``
endpoint — never trust ourselves to remember, and never trust *only*
ourselves either.

Auth conventions (§4.1 / §12.2):
  * ``auth_mode: oauth2`` — Instagram's business-login code flow takes a
    confidential client (no PKCE support on this platform); ``state`` is
    bound and single-use. The app secret is this platform's single-string
    secret (provider key ``platform_instagram``); the non-secret
    ``client_id`` (app id) rides ``~/.friday/platforms.json →
    instagram.client_id``.
  * Loopback redirect:
    ``http://localhost:3000/api/content/platforms/instagram/callback``.
  * The long-lived (~60-day) token blob lives encrypted at
    ``~/.friday/platforms/instagram.cred``; ``refresh()`` renews via
    ``refresh_access_token`` before expiry — an *expired* token cannot be
    renewed, so ``status()`` surfaces ``needs_reauth`` / ``expiring_soon``
    for the Accounts tab.
  * A professional (business/creator) account is mandatory — ``status()``
    says so plainly (§4.5) and publishing is refused otherwise.

Instagram exposes no media-delete or token-revoke API surface: ``delete()``
stays honestly unsupported and ``revoke()`` is a local purge (§12.6 lists
the platforms with real revoke — Instagram is not one of them).

Transport: EVERY HTTP call rides the module-level ``_request()`` seam so
tests stub exactly one function — zero live network in tests. Adapter
errors are externalized as fixed content-free strings (§12.5); details go
to the local logger only, never into envelopes, ``status()``, or the audit
trail.

Import-safe under FRIDAY_TESTING: stdlib only at import time, no threads,
no network; urllib and credential_store are lazy-imported inside functions.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_friday.services.platforms.base import (
    PlatformAdapter, _now_utc, _parse_iso)

_log = logging.getLogger("friday.platforms.instagram")

_GRAPH_BASE = "https://graph.instagram.com"
_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_RUPLOAD_BASE = "https://rupload.facebook.com/ig-api-upload"
_REDIRECT_URI = "http://localhost:3000/api/content/platforms/instagram/callback"
_SCOPES = ("instagram_business_basic",
           "instagram_business_content_publish",
           "instagram_business_manage_insights")

# Design-time snapshot (§4.2 volatility warning) — override via config
# ``api_version`` when Meta sunsets this version tag.
_DEFAULT_VERSION = "v23.0"

# Surface re-auth this many days before the ~60-day long-lived token lapses.
_REAUTH_WARN_DAYS = 7

# §4.5: feed images are JPEG only, aspect 4:5 (0.8) … 1.91:1.
_ASPECT_LO, _ASPECT_HI = 0.8, 1.91
_JPEG_EXTS = (".jpg", ".jpeg")

# §4.5 caption norms.
_CAPTION_LIMIT = 2200
_HASHTAG_LIMIT = 30
_MENTION_LIMIT = 20
_HASHTAG_RE = re.compile(r"#\w+")
_MENTION_RE = re.compile(r"@[A-Za-z0-9_.]+")

# Professional account types allowed to publish via the API (§4.5).
_PROFESSIONAL_TYPES = ("BUSINESS", "CREATOR", "MEDIA_CREATOR")

# §7.4 rung-3 hold — the exact clear message the spec demands (§4.5).
_STAGING_HOLD_MSG = ("Instagram needs a public asset URL — "
                     "configure staging or post manually")

# Re-sync the local budget from content_publishing_limit at most this often.
_QUOTA_SYNC_INTERVAL_S = 300.0

# ── fixed content-free error strings (§12.5) ─────────────────────────────────
E_NOT_CONNECTED = "not_connected"
E_AUTH_EXPIRED = "auth_expired"
E_IDENTITY_MISSING = "identity_missing"
E_CLIENT_NOT_CONFIGURED = "client_not_configured"
E_STATE_MISMATCH = "state_mismatch"
E_OAUTH_FAILED = "oauth_exchange_failed"
E_PROFESSIONAL_REQUIRED = "professional_account_required"
E_UNSUPPORTED_FORMAT = "unsupported_format"
E_MEDIA_REQUIRED = "media_required"
E_TRANSFORM_REQUIRED = "transform_required"
E_ASSET_MISSING = "asset_missing"
E_ASSET_TOO_LARGE = "asset_too_large"
E_ASSET_NOT_STAGED = "asset_not_staged"
E_VIDEO_OUT_OF_RANGE = "video_out_of_range"
E_CAPTION_TOO_LONG = "caption_too_long"
E_TOO_MANY_HASHTAGS = "too_many_hashtags"
E_CONTAINER_FAILED = "container_failed"
E_UPLOAD_FAILED = "upload_failed"
E_PUBLISH_FAILED = "publish_failed"
E_PREPARE_FAILED = "prepare_failed"
E_NOT_PREPARED = "not_prepared"
E_AUTH_ERROR = "auth_error"
E_PERMISSION = "permission_denied"
E_RATE_LIMITED = "rate_limited"
E_SERVER_ERROR = "server_error"
E_NETWORK_ERROR = "network_error"


# ── module-level transport seam (tests stub THIS — nothing else talks HTTP) ──
def _request(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
             data: Optional[bytes] = None, timeout: float = 30.0) -> Dict[str, Any]:
    """One HTTP exchange → ``{status, headers, body}`` — never raises.
    ``status: 0`` means the request never completed (network/DNS/timeout)."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": int(resp.status), "headers": dict(resp.headers),
                    "body": resp.read()}
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        return {"status": int(e.code), "headers": dict(e.headers or {}), "body": body}
    except Exception as e:
        # Detail stays local; callers externalize a fixed string.
        _log.warning("instagram transport error: %s: %s", type(e).__name__, e)
        return {"status": 0, "headers": {}, "body": b""}


def _json_body(resp: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads((resp.get("body") or b"").decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _header(resp: Dict[str, Any], name: str) -> Optional[str]:
    for k, v in (resp.get("headers") or {}).items():
        if str(k).lower() == name.lower():
            return str(v)
    return None


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# §8.2 Instagram column: platform insight name → unified metric key.
# (Meta's impressions→views migration: ``views`` is the impressions-class
# number; ``plays`` maps to video_views where the platform still reports it.)
_METRIC_MAP = {
    "views": "impressions",
    "plays": "video_views",
    "reach": "reach",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "saved": "saves",
    "follows": "follows_gained",
}
_INSIGHT_METRICS = "views,reach,likes,comments,shares,saved,follows"
# Fallback set when a media kind rejects one of the richer metric names.
_INSIGHT_METRICS_CORE = "reach,likes,comments,shares,saved"


class InstagramAdapter(PlatformAdapter):
    name = "instagram"
    label = "Instagram"
    auth_mode = "oauth2"
    automation_tier = "api"
    default_daily_limit = 100   # §4.5: 100 API-published posts per rolling 24 h

    def __init__(self) -> None:
        super().__init__()
        self._pending_state: Optional[str] = None   # bound + single-use (§12.2)
        self._quota_synced_at: float = 0.0

    # ── capability declaration (§4.2 / §4.5) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({
            "formats": ["image_post", "reel", "story"],
            "char_limit": _CAPTION_LIMIT,
            "title_limit": None,
            "thread": False,
            "native_schedule": False,
            "native_delete": False,     # no media-delete API surface
            "analytics": "full",        # per-media insights (§4.5)
            "hashtags_max": _HASHTAG_LIMIT,
            "notes": [
                "professional (business/creator) account required — personal accounts cannot publish via the API",
                "feed images are JPEG only, aspect 4:5 … 1.91:1 — non-compliant assets get an auto-crop/convert plan",
                "image/carousel/story media must be publicly reachable URLs — reels upload directly; otherwise configure staging or the target holds",
                "100 API-published posts per rolling 24 h (a carousel counts as 1); live quota via content_publishing_limit",
            ],
        })
        caps["media"] = {
            "images": {"max": 10, "formats": ["jpeg"],
                       "max_bytes": 8 * 1024 * 1024,
                       "aspect": (_ASPECT_LO, _ASPECT_HI)},
            "video": {"min_s": 3, "max_s": 15 * 60, "formats": ["mp4", "mov"],
                      "max_bytes": 1024 * 1024 * 1024},
            "alt_text": True,
        }
        return caps

    # ── config accessors ─────────────────────────────────────────────────────
    def _client_id(self) -> Optional[str]:
        cid = str(self._config.get("client_id") or "").strip()
        return cid or None

    def _client_secret_value(self) -> Optional[str]:
        # Base convention: the single-string secret slot holds the developer
        # app's secret for this platform (module docstring).
        return self.simple_secret()

    def _api_version(self) -> str:
        return str(self._config.get("api_version") or _DEFAULT_VERSION)

    def _staging_base_url(self) -> str:
        return str(self._config.get("staging_base_url") or "").strip()

    # ── auth lifecycle (§4.1 / §12.2) ────────────────────────────────────────
    def connect_url(self, state: str) -> Optional[str]:
        cid = self._client_id()
        if not cid:
            self._last_error = E_CLIENT_NOT_CONFIGURED
            return None
        from urllib.parse import urlencode
        self._pending_state = str(state or "")
        self._audit("connect_started")
        return _AUTHORIZE_URL + "?" + urlencode({
            "client_id": cid,
            "redirect_uri": _REDIRECT_URI,
            "response_type": "code",
            "scope": ",".join(_SCOPES),
            "state": self._pending_state,
        })

    def handle_callback(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params or {})
        expected, self._pending_state = self._pending_state, None   # single-use
        if not expected or str(params.get("state") or "") != expected:
            self._last_error = E_STATE_MISMATCH
            self._audit("callback_rejected", reason="state")
            return self.status()
        if params.get("error") or not params.get("code"):
            self._last_error = E_OAUTH_FAILED
            self._audit("callback_rejected", reason="provider_error")
            return self.status()
        cid, csec = self._client_id(), self._client_secret_value()
        if not cid or not csec:
            self._last_error = E_CLIENT_NOT_CONFIGURED
            self._audit("callback_rejected", reason="client_config")
            return self.status()

        from urllib.parse import urlencode
        form = urlencode({
            "client_id": cid,
            "client_secret": csec,   # pragma: allowlist secret
            "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT_URI,
            "code": str(params.get("code")),
        })
        r = _request("POST", _TOKEN_URL,
                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                     data=form.encode("utf-8"))
        grant = _json_body(r)
        short_lived = grant.get("access_token")
        if r.get("status") != 200 or not short_lived:
            self._last_error = E_OAUTH_FAILED
            _log.warning("instagram token exchange failed: http %s", r.get("status"))
            self._audit("callback_rejected", reason="exchange")
            return self.status()

        now = _now_utc()
        blob: Dict[str, Any] = {
            "access_token": short_lived,
            "expires_at": _iso(now + timedelta(hours=1)),   # short-lived default
            "scopes": self._grant_scopes(grant),
            "connected_at": _iso(now),
        }
        if grant.get("user_id"):
            blob["user_id"] = str(grant.get("user_id"))

        # Long-lived exchange (~60 days) — best-effort; a failure keeps the
        # short-lived token and status() shows the near expiry honestly.
        ll = _request("GET", f"{_GRAPH_BASE}/access_token?" + urlencode({
            "grant_type": "ig_exchange_token",
            "client_secret": csec,           # pragma: allowlist secret
            "access_token": short_lived,     # pragma: allowlist secret
        }))
        ll_grant = _json_body(ll)
        if ll.get("status") == 200 and ll_grant.get("access_token"):
            blob["access_token"] = ll_grant.get("access_token")
            blob["expires_at"] = _iso(now + timedelta(
                seconds=int(ll_grant.get("expires_in") or 0) or 60 * 86400))
        else:
            _log.warning("instagram long-lived exchange unavailable: http %s",
                         ll.get("status"))

        # Identity — publish needs the IG user id; the Accounts tab needs the
        # professional-account fact stated plainly (§4.5).
        ui = _request("GET",
                      f"{_GRAPH_BASE}/{self._api_version()}/me"
                      "?fields=user_id,username,name,account_type",
                      headers={"Authorization": "Bearer " + str(blob["access_token"])})
        who = _json_body(ui)
        if ui.get("status") == 200 and (who.get("user_id") or who.get("username")):
            if who.get("user_id"):
                blob["user_id"] = str(who.get("user_id"))
            blob["account"] = who.get("username") or who.get("name")
            blob["account_type"] = str(who.get("account_type") or "").upper()
        else:
            _log.warning("instagram identity unavailable: http %s", ui.get("status"))

        saved = self.save_credentials(blob)
        if not saved.get("ok"):
            return self.status()
        self._last_error = None
        self._audit("connected", scopes=len(blob.get("scopes") or []),
                    professional=self._is_professional(blob))
        return self.status()

    @staticmethod
    def _grant_scopes(grant: Dict[str, Any]) -> List[str]:
        perms = grant.get("permissions")
        if isinstance(perms, list):
            return [str(p) for p in perms if p]
        if isinstance(perms, str):
            return [p for p in perms.replace(",", " ").split() if p]
        return list(_SCOPES)

    @staticmethod
    def _is_professional(creds: Dict[str, Any]) -> bool:
        """§4.5: professional account mandatory. Unknown type (older blob or
        identity call unavailable) is not treated as a block — the platform
        itself is the final arbiter and answers with a clear 4xx."""
        kind = str(creds.get("account_type") or "").upper()
        return (not kind) or kind in _PROFESSIONAL_TYPES

    def _renew(self, creds: Dict[str, Any]) -> bool:
        """Long-lived token refresh (``ig_refresh_token``); only a still-valid
        token can be renewed. True only when the new token is stored."""
        tok = creds.get("access_token")
        if not tok:
            return False
        r = _request("GET",
                     f"{_GRAPH_BASE}/refresh_access_token"
                     "?grant_type=ig_refresh_token",
                     headers={"Authorization": "Bearer " + str(tok)})
        grant = _json_body(r)
        if r.get("status") != 200 or not grant.get("access_token"):
            _log.warning("instagram token refresh failed: http %s", r.get("status"))
            return False
        updated = dict(creds)
        updated["access_token"] = grant.get("access_token")
        updated["expires_at"] = _iso(_now_utc() + timedelta(
            seconds=int(grant.get("expires_in") or 0) or 60 * 86400))
        if not self.save_credentials(updated).get("ok"):
            return False
        self._audit("refreshed")
        return True

    def refresh(self) -> bool:
        """True = token usable. An expired long-lived token cannot be renewed
        (refresh requires a valid token) → False + re-auth surfacing."""
        creds = self.load_credentials()
        if not creds or not creds.get("access_token"):
            return False
        exp = _parse_iso(creds.get("expires_at"))
        now = _now_utc()
        if exp and exp <= now:
            self._last_error = E_AUTH_EXPIRED
            return False
        if exp and (exp - now) <= timedelta(days=_REAUTH_WARN_DAYS):
            self._renew(creds)    # best-effort early renewal; still usable
        return True

    def revoke(self) -> bool:
        """Instagram exposes no token-revoke endpoint (§12.6) — local purge
        only; the Accounts tab links the platform's own app-permissions page."""
        self.clear_credentials()
        self._audit("revoke", platform_side=False)
        return True

    def status(self) -> Dict[str, Any]:
        """Base facts + the ~60-day expiry countdown and the §4.5
        professional-account fact — never token material (§12.2)."""
        out = super().status()
        # The simple-secret slot holds the developer app's secret here, not a
        # session credential — only the OAuth blob means "connected".
        creds = self.load_credentials()
        out["connected"] = creds is not None
        out["professional_account"] = None
        if isinstance(creds, dict) and creds.get("account_type"):
            out["professional_account"] = (
                str(creds.get("account_type")).upper() in _PROFESSIONAL_TYPES)
        out["needs_reauth"] = False
        out["expiring_soon"] = False
        out["expires_in_days"] = None
        exp = _parse_iso(out.get("expires_at"))
        if exp is not None:
            remaining = (exp - _now_utc()).total_seconds()
            out["expires_in_days"] = max(0, int(remaining // 86400))
            if remaining <= 0:
                out["needs_reauth"] = True
            elif remaining <= _REAUTH_WARN_DAYS * 86400:
                out["expiring_soon"] = True
        return out

    def _auth(self) -> Optional[Dict[str, Any]]:
        """Usable credentials or None; the fixed-string reason lands in
        _last_error. Expired = unusable (renewal needs a valid token)."""
        creds = self.load_credentials()
        if not creds or not creds.get("access_token"):
            self._last_error = E_NOT_CONNECTED
            return None
        exp = _parse_iso(creds.get("expires_at"))
        if exp is not None and exp <= _now_utc():
            self._last_error = E_AUTH_EXPIRED
            return None
        return creds

    # ── shared HTTP plumbing ─────────────────────────────────────────────────
    @staticmethod
    def _auth_headers(creds: Dict[str, Any], *, form: bool = False) -> Dict[str, str]:
        h = {"Authorization": "Bearer " + str(creds.get("access_token") or "")}
        if form:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        return h

    def _error_envelope(self, resp: Dict[str, Any], fallback: str) -> Dict[str, Any]:
        """HTTP status → fixed content-free string (§12.5); the platform body
        never leaves the local log."""
        st = int(resp.get("status") or 0)
        if st == 0:
            err = E_NETWORK_ERROR
        elif st == 401:
            err = E_AUTH_ERROR
        elif st == 403:
            err = E_PERMISSION
        elif st == 429:
            err = E_RATE_LIMITED
        elif st >= 500:
            err = E_SERVER_ERROR
        else:
            err = fallback
        self._last_error = err
        _log.warning("instagram http %s -> %s", st, err)
        env: Dict[str, Any] = {"ok": False, "error": err, "status_code": st}
        retry_after = _header(resp, "Retry-After")
        if retry_after:
            try:
                env["retry_after"] = int(retry_after)
            except (TypeError, ValueError):
                pass
        return env

    # ── asset helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _asset_source(asset: Dict[str, Any]) -> Dict[str, Any]:
        source = asset.get("source")
        return source if isinstance(source, dict) else {}

    @classmethod
    def _asset_kind(cls, asset: Dict[str, Any]) -> str:
        return str(asset.get("kind") or cls._asset_source(asset).get("kind")
                   or "image")

    @classmethod
    def _asset_alt(cls, asset: Dict[str, Any]) -> str:
        return str(asset.get("alt_text")
                   or cls._asset_source(asset).get("alt_text") or "")

    @classmethod
    def _asset_ref(cls, asset: Dict[str, Any]) -> str:
        source = cls._asset_source(asset)
        for k in ("out_path", "path", "filename", "url", "public_url"):
            v = asset.get(k) or source.get(k)
            if v:
                return str(v)
        return "asset"

    @classmethod
    def _read_asset(cls, asset: Dict[str, Any]) -> Optional[bytes]:
        source = cls._asset_source(asset)
        for k in ("out_path", "path", "filename"):
            v = asset.get(k) or source.get(k)
            if not v:
                continue
            try:
                p = Path(str(v))
                if p.is_file():
                    return p.read_bytes()
            except Exception as e:
                _log.warning("instagram asset read failed: %s", type(e).__name__)
        return None

    @classmethod
    def _local_size(cls, asset: Dict[str, Any]) -> Optional[int]:
        source = cls._asset_source(asset)
        for k in ("out_path", "path", "filename"):
            v = asset.get(k) or source.get(k)
            if not v:
                continue
            try:
                p = Path(str(v))
                if p.is_file():
                    return p.stat().st_size
            except Exception:
                pass
        return None

    @classmethod
    def _public_url(cls, asset: Dict[str, Any]) -> Optional[str]:
        """An already-public location for this asset: an explicit staging
        result, or a source URL that is genuinely reachable from outside."""
        source = cls._asset_source(asset)
        for k in ("public_url", "staged_url"):
            v = asset.get(k) or source.get(k)
            if v:
                return str(v)
        v = asset.get("url") or source.get("url")
        if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
            return v
        return None

    def _image_transform_plan(self, asset: Dict[str, Any],
                              ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """§4.5 auto-crop plan: JPEG-only + aspect 4:5…1.91:1. The adapter
        plans, the pipeline's transform step (PIL) executes — we never upload
        an asset Instagram would reject."""
        plan: List[Dict[str, Any]] = []
        warnings: List[str] = []
        ref = self._asset_ref(asset)
        source = self._asset_source(asset)

        declared = str(asset.get("format") or source.get("format") or "").lower()
        suffix = ""
        try:
            suffix = Path(ref).suffix.lower()
        except Exception:
            pass
        if declared in ("jpg", "jpeg") or suffix in _JPEG_EXTS:
            pass                    # compliant
        elif declared or suffix:    # known and NOT jpeg → conversion plan
            plan.append({"ref": ref, "action": "convert", "to": "jpeg"})
            warnings.append(f"{ref}: Instagram feed accepts JPEG only — "
                            "conversion planned")
        # unknown format → nothing to plan; the platform is the final check

        try:
            w = int(asset.get("width") or source.get("width") or 0)
            h = int(asset.get("height") or source.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0
        if w > 0 and h > 0:
            aspect = w / h
            if aspect < _ASPECT_LO:
                plan.append({"ref": ref, "action": "crop",
                             "target_aspect": _ASPECT_LO})
                warnings.append(f"{ref}: aspect {aspect:.2f} is below 4:5 — "
                                "auto-crop planned")
            elif aspect > _ASPECT_HI:
                plan.append({"ref": ref, "action": "crop",
                             "target_aspect": _ASPECT_HI})
                warnings.append(f"{ref}: aspect {aspect:.2f} is above 1.91:1 — "
                                "auto-crop planned")
        return plan, warnings

    # ── caption assembly (§4.5 / §5.2 hashtag block) ─────────────────────────
    @staticmethod
    def _build_caption(body: str, hashtags: List[str]) -> str:
        caption = str(body or "")
        block: List[str] = []
        for t in hashtags or []:
            t = str(t or "").strip().lstrip("#")
            if t and ("#" + t).lower() not in caption.lower():
                block.append("#" + t)
        if block:
            caption = (caption + "\n\n" if caption else "") + " ".join(block)
        return caption

    # ── container flow (§4.5) ────────────────────────────────────────────────
    def _create_container(self, creds: Dict[str, Any],
                          params: Dict[str, Any]) -> Dict[str, Any]:
        from urllib.parse import urlencode
        uid = str(creds.get("user_id") or "")
        r = _request("POST",
                     f"{_GRAPH_BASE}/{self._api_version()}/{uid}/media",
                     headers=self._auth_headers(creds, form=True),
                     data=urlencode(params).encode("utf-8"))
        body = _json_body(r)
        if r.get("status") not in (200, 201) or not body.get("id"):
            return self._error_envelope(r, E_CONTAINER_FAILED)
        return {"ok": True, "id": str(body.get("id")), "uri": body.get("uri")}

    def _wait_container(self, creds: Dict[str, Any], container_id: str,
                        ) -> Dict[str, Any]:
        """Poll the container until FINISHED (reels/video need processing
        time); ERROR/EXPIRED → fixed-string failure, bounded attempts."""
        try:
            attempts = max(1, min(60, int(self._config.get(
                "container_poll_attempts", 20))))
        except (TypeError, ValueError):
            attempts = 20
        try:
            interval = max(0.0, min(30.0, float(self._config.get(
                "container_poll_interval_s", 2.0))))
        except (TypeError, ValueError):
            interval = 2.0
        for i in range(attempts):
            if i and interval:
                time.sleep(interval)
            r = _request("GET",
                         f"{_GRAPH_BASE}/{self._api_version()}/{container_id}"
                         "?fields=status_code",
                         headers=self._auth_headers(creds))
            if r.get("status") != 200:
                return self._error_envelope(r, E_CONTAINER_FAILED)
            code = str(_json_body(r).get("status_code") or "").upper()
            if code in ("FINISHED", "PUBLISHED"):
                return {"ok": True}
            if code in ("ERROR", "EXPIRED"):
                self._last_error = E_CONTAINER_FAILED
                _log.warning("instagram container %s", code)
                return {"ok": False, "error": E_CONTAINER_FAILED}
        self._last_error = E_CONTAINER_FAILED
        return {"ok": False, "error": E_CONTAINER_FAILED}

    def _resolve_public_urls(self, assets: List[Dict[str, Any]],
                             warnings: List[str]) -> Dict[str, Any]:
        """§7.4 staging resolution for URL-pull media. Staged/public URL →
        proceed; unconfigured staging → HOLD via the §4.14 degrade envelope
        (clear message, user chooses); configured-but-unstaged → fixed error
        so the publisher stages and retries. Never a silent failure."""
        urls: List[str] = []
        for asset in assets:
            u = self._public_url(asset)
            if u:
                urls.append(u)
                continue
            if not self._staging_base_url():
                self._last_error = E_ASSET_NOT_STAGED
                env = self.degrade(_STAGING_HOLD_MSG)
                env["warnings"] = warnings
                return env
            self._last_error = E_ASSET_NOT_STAGED
            return {"ok": False, "error": E_ASSET_NOT_STAGED,
                    "warnings": warnings}
        return {"ok": True, "urls": urls}

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Base hard validation → caption norms → JPEG/aspect plan → staging
        resolution → container creation (media upload). The creation id lands
        in prepared["creation_id"] so publish() is deterministic: wait for
        FINISHED, media_publish, done."""
        try:
            target = dict(target or {})
            fmt = str(target.get("format") or "post").lower()
            if fmt == "post":
                fmt = "image_post"      # tolerate the generic format name
            caps = self.capabilities()
            if fmt not in caps["formats"]:
                self._last_error = E_UNSUPPORTED_FORMAT
                return {"ok": False, "error": E_UNSUPPORTED_FORMAT, "warnings": []}
            target["format"] = fmt

            base_res = super().prepare(target, post)
            if not base_res.get("ok"):
                return base_res
            prepared = base_res["prepared"]
            warnings: List[str] = base_res.get("warnings") or []

            # Caption norms (§4.5): ≤2,200 chars, ≤30 hashtags, ≤20 mentions.
            caption = self._build_caption(prepared.get("body") or "",
                                          prepared.get("hashtags") or [])
            if len(caption) > _CAPTION_LIMIT:
                self._last_error = E_CAPTION_TOO_LONG
                return {"ok": False, "error": E_CAPTION_TOO_LONG,
                        "warnings": warnings}
            if len(_HASHTAG_RE.findall(caption)) > _HASHTAG_LIMIT:
                self._last_error = E_TOO_MANY_HASHTAGS
                return {"ok": False, "error": E_TOO_MANY_HASHTAGS,
                        "warnings": warnings}
            if len(_MENTION_RE.findall(caption)) > _MENTION_LIMIT:
                warnings.append(f"caption exceeds Instagram's "
                                f"{_MENTION_LIMIT}-mention limit")
            prepared["caption"] = caption

            assets = prepared.get("assets") or []
            images = [a for a in assets if self._asset_kind(a) == "image"]
            videos = [a for a in assets if self._asset_kind(a) == "video"]

            # Instagram has no text-only posts — media is mandatory.
            if ((fmt == "image_post" and not images)
                    or (fmt == "reel" and not videos)
                    or (fmt == "story" and not images and not videos)):
                self._last_error = E_MEDIA_REQUIRED
                return {"ok": False, "error": E_MEDIA_REQUIRED,
                        "warnings": warnings}

            # §4.5 JPEG-only + aspect window → auto-crop/convert plan. A
            # non-compliant asset never reaches the platform: the pipeline's
            # transform step runs the plan and prepare() is called again.
            if fmt in ("image_post", "story") and images:
                plan: List[Dict[str, Any]] = []
                for asset in images:
                    p, w = self._image_transform_plan(asset)
                    plan.extend(p)
                    warnings.extend(w)
                    size = self._local_size(asset)
                    max_bytes = (caps["media"]["images"] or {}).get("max_bytes")
                    if (isinstance(size, int) and isinstance(max_bytes, int)
                            and size > max_bytes):
                        self._last_error = E_ASSET_TOO_LARGE
                        return {"ok": False, "error": E_ASSET_TOO_LARGE,
                                "warnings": warnings}
                if plan:
                    self._last_error = E_TRANSFORM_REQUIRED
                    return {"ok": False, "error": E_TRANSFORM_REQUIRED,
                            "transform_plan": plan, "warnings": warnings}

            creds = self._auth()
            if creds is None:
                return {"ok": False, "error": self._last_error or E_NOT_CONNECTED,
                        "warnings": warnings}
            if not self._is_professional(creds):
                self._last_error = E_PROFESSIONAL_REQUIRED
                return {"ok": False, "error": E_PROFESSIONAL_REQUIRED,
                        "warnings": warnings}
            if not str(creds.get("user_id") or ""):
                self._last_error = E_IDENTITY_MISSING
                return {"ok": False, "error": E_IDENTITY_MISSING,
                        "warnings": warnings}

            if fmt == "image_post":
                res = self._prepare_feed_images(creds, prepared, images, warnings)
            elif fmt == "reel":
                res = self._prepare_reel(creds, prepared, videos, warnings, caps)
            else:
                res = self._prepare_story(creds, prepared, images, videos, warnings)
            if not res.get("ok"):
                res.setdefault("warnings", warnings)
                return res

            prepared["creation_id"] = res["creation_id"]
            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception as e:
            # Never raises — and never leaks detail beyond the local log.
            _log.warning("instagram prepare error: %s: %s", type(e).__name__, e)
            self._last_error = E_PREPARE_FAILED
            return {"ok": False, "error": E_PREPARE_FAILED, "warnings": []}

    def _prepare_feed_images(self, creds: Dict[str, Any], prepared: Dict[str, Any],
                             images: List[Dict[str, Any]],
                             warnings: List[str]) -> Dict[str, Any]:
        """Single image → one container; 2–10 images → child containers +
        one CAROUSEL parent (a carousel = 1 against the 100/24 h budget)."""
        resolved = self._resolve_public_urls(images, warnings)
        if not resolved.get("ok"):
            return resolved
        urls = resolved["urls"]
        caption = str(prepared.get("caption") or "")

        if len(images) == 1:
            params: Dict[str, Any] = {"image_url": urls[0], "caption": caption}
            alt = self._asset_alt(images[0])
            if alt:
                params["alt_text"] = alt
            made = self._create_container(creds, params)
            if not made.get("ok"):
                return made
            return {"ok": True, "creation_id": made["id"]}

        children: List[str] = []
        for asset, url in zip(images, urls):
            params = {"image_url": url, "is_carousel_item": "true"}
            alt = self._asset_alt(asset)
            if alt:
                params["alt_text"] = alt
            made = self._create_container(creds, params)
            if not made.get("ok"):
                return made
            children.append(made["id"])
        parent = self._create_container(creds, {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
        })
        if not parent.get("ok"):
            return parent
        return {"ok": True, "creation_id": parent["id"]}

    def _prepare_reel(self, creds: Dict[str, Any], prepared: Dict[str, Any],
                      videos: List[Dict[str, Any]], warnings: List[str],
                      caps: Dict[str, Any]) -> Dict[str, Any]:
        """Reels prefer resumable binary upload (§7.4 rung 1 — no staging at
        all); a staged/public URL is the fallback; neither → asset_missing."""
        if len(videos) > 1:
            warnings.append("only the first video is used for a reel")
        video = videos[0]
        limits = (caps.get("media") or {}).get("video") or {}

        duration = video.get("duration_s")
        if duration is None:
            duration = self._asset_source(video).get("duration_s")
        if duration is not None:
            try:
                duration = float(duration)
                if duration < limits.get("min_s", 3) or \
                        duration > limits.get("max_s", 900):
                    self._last_error = E_VIDEO_OUT_OF_RANGE
                    return {"ok": False, "error": E_VIDEO_OUT_OF_RANGE}
            except (TypeError, ValueError):
                pass

        options = dict(prepared.get("options") or {})
        params: Dict[str, Any] = {"media_type": "REELS",
                                  "caption": str(prepared.get("caption") or "")}
        if "share_to_feed" in options:
            params["share_to_feed"] = "true" if options.get("share_to_feed") else "false"
        if options.get("cover_url"):
            params["cover_url"] = str(options.get("cover_url"))

        data = self._read_asset(video)
        if data is not None:
            max_bytes = limits.get("max_bytes")
            if isinstance(max_bytes, int) and len(data) > max_bytes:
                self._last_error = E_ASSET_TOO_LARGE
                return {"ok": False, "error": E_ASSET_TOO_LARGE}
            params["upload_type"] = "resumable"
            made = self._create_container(creds, params)
            if not made.get("ok"):
                return made
            upload_uri = str(made.get("uri") or "") or (
                f"{_RUPLOAD_BASE}/{self._api_version()}/{made['id']}")
            up = _request("POST", upload_uri, headers={
                # rupload speaks "OAuth <token>", not Bearer.
                "Authorization": "OAuth " + str(creds.get("access_token") or ""),
                "offset": "0",
                "file_size": str(len(data)),
            }, data=data)
            if up.get("status") not in (200, 201):
                return self._error_envelope(up, E_UPLOAD_FAILED)
            return {"ok": True, "creation_id": made["id"]}

        url = self._public_url(video)
        if not url:
            self._last_error = E_ASSET_MISSING
            return {"ok": False, "error": E_ASSET_MISSING}
        params["video_url"] = url
        made = self._create_container(creds, params)
        if not made.get("ok"):
            return made
        return {"ok": True, "creation_id": made["id"]}

    def _prepare_story(self, creds: Dict[str, Any], prepared: Dict[str, Any],
                       images: List[Dict[str, Any]], videos: List[Dict[str, Any]],
                       warnings: List[str]) -> Dict[str, Any]:
        """Stories take one media item via public URL (no resumable path, no
        caption field) — §7.4 staging rules apply in full."""
        asset = (images or videos)[0]
        if len(images) + len(videos) > 1:
            warnings.append("only the first asset is used for a story")
        resolved = self._resolve_public_urls([asset], warnings)
        if not resolved.get("ok"):
            return resolved
        key = "image_url" if self._asset_kind(asset) == "image" else "video_url"
        made = self._create_container(creds, {"media_type": "STORIES",
                                              key: resolved["urls"][0]})
        if not made.get("ok"):
            return made
        return {"ok": True, "creation_id": made["id"]}

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Container publish (§4.5): wait for FINISHED → media_publish →
        permalink. → {ok, post_url, platform_post_id, raw}."""
        try:
            prepared = dict(prepared or {})
            creds = self._auth()
            if creds is None:
                return {"ok": False, "error": self._last_error or E_NOT_CONNECTED}
            if not self._is_professional(creds):
                self._last_error = E_PROFESSIONAL_REQUIRED
                return {"ok": False, "error": E_PROFESSIONAL_REQUIRED}
            uid = str(creds.get("user_id") or "")
            if not uid:
                self._last_error = E_IDENTITY_MISSING
                return {"ok": False, "error": E_IDENTITY_MISSING}
            creation_id = str(prepared.get("creation_id") or "")
            if not creation_id:
                self._last_error = E_NOT_PREPARED
                return {"ok": False, "error": E_NOT_PREPARED}

            waited = self._wait_container(creds, creation_id)
            if not waited.get("ok"):
                self._audit("publish", ok=False, reason=waited.get("error"))
                return waited

            from urllib.parse import urlencode
            r = _request("POST",
                         f"{_GRAPH_BASE}/{self._api_version()}/{uid}/media_publish",
                         headers=self._auth_headers(creds, form=True),
                         data=urlencode({"creation_id": creation_id}).encode("utf-8"))
            body = _json_body(r)
            if r.get("status") not in (200, 201) or not body.get("id"):
                env = self._error_envelope(r, E_PUBLISH_FAILED)
                self._audit("publish", ok=False, reason=env.get("error"))
                return env
            media_id = str(body.get("id"))

            # Canonical URL — best-effort; the id is the durable fact.
            post_url = f"https://www.instagram.com/media/{media_id}"
            pr = _request("GET",
                          f"{_GRAPH_BASE}/{self._api_version()}/{media_id}"
                          "?fields=permalink",
                          headers=self._auth_headers(creds))
            permalink = _json_body(pr).get("permalink")
            if pr.get("status") == 200 and permalink:
                post_url = str(permalink)

            # First-comment-with-hashtags via the comments edge (§4.5) —
            # best-effort: a failed comment never fails the publish.
            options = dict(prepared.get("options") or {})
            first_comment = str(options.get("first_comment") or "")
            if first_comment:
                cr = _request(
                    "POST",
                    f"{_GRAPH_BASE}/{self._api_version()}/{media_id}/comments",
                    headers=self._auth_headers(creds, form=True),
                    data=urlencode({"message": first_comment[:_CAPTION_LIMIT]}
                                   ).encode("utf-8"))
                if cr.get("status") not in (200, 201):
                    _log.warning("instagram first comment failed: http %s",
                                 cr.get("status"))

            self.consume_budget()   # carousel = 1 (§4.5)
            self._last_error = None
            self._audit("publish", ok=True,
                        format=str(prepared.get("format") or "image_post"))
            return {"ok": True, "post_url": post_url,
                    "platform_post_id": media_id, "raw": {"id": media_id}}
        except Exception as e:
            # Never raises — and never leaks detail beyond the local log.
            _log.warning("instagram publish error: %s: %s", type(e).__name__, e)
            self._last_error = E_PUBLISH_FAILED
            return {"ok": False, "error": E_PUBLISH_FAILED}

    # delete(): base default {"ok": False, "error": "not_supported"} — the
    # Instagram API has no media-delete surface; honesty over pretence (§4.14).

    # ── local rate budgeting + platform quota sync (§4.5) ────────────────────
    def rate_budget(self) -> Dict[str, Any]:
        """Local bookkeeping (base), synced best-effort from the platform's
        ``content_publishing_limit`` quota when connected — the platform's
        rolling-24 h count wins when it is higher than ours."""
        budget = super().rate_budget()
        try:
            now = time.time()
            if now - self._quota_synced_at < _QUOTA_SYNC_INTERVAL_S:
                return budget
            creds = self.load_credentials()
            if not creds or not creds.get("access_token") \
                    or not creds.get("user_id"):
                return budget
            uid = str(creds.get("user_id"))
            r = _request("GET",
                         f"{_GRAPH_BASE}/{self._api_version()}/{uid}"
                         "/content_publishing_limit?fields=quota_usage,config",
                         headers=self._auth_headers(creds))
            if r.get("status") != 200:
                return budget
            data = _json_body(r).get("data") or []
            entry = data[0] if data and isinstance(data[0], dict) else {}
            usage = entry.get("quota_usage")
            quota_cfg = entry.get("config") if isinstance(entry.get("config"),
                                                          dict) else {}
            self._quota_synced_at = now
            if isinstance(usage, (int, float)) and int(usage) > budget["used"]:
                # Platform remembers publishes we did not (other clients,
                # restored machine, …) — sync the local ledger up to truth.
                self.consume_budget(int(usage) - budget["used"])
                budget = super().rate_budget()
            total = quota_cfg.get("quota_total")
            if isinstance(total, (int, float)) and int(total) > 0:
                limit = budget.get("limit")
                budget["limit"] = (min(int(limit), int(total))
                                   if isinstance(limit, int) and limit > 0
                                   else int(total))
        except Exception as e:
            _log.warning("instagram quota sync failed: %s", type(e).__name__)
        return budget

    # ── analytics path (§4.5 / §8.2 Instagram column) ────────────────────────
    @staticmethod
    def _parse_insights(body: Dict[str, Any]) -> Dict[str, int]:
        metrics: Dict[str, int] = {}
        for item in body.get("data") or []:
            if not isinstance(item, dict):
                continue
            dst = _METRIC_MAP.get(str(item.get("name") or ""))
            if not dst:
                continue
            value = None
            total = item.get("total_value")
            if isinstance(total, dict) and isinstance(total.get("value"),
                                                      (int, float)):
                value = total.get("value")
            else:
                values = item.get("values") or []
                if values and isinstance(values[0], dict) \
                        and isinstance(values[0].get("value"), (int, float)):
                    value = values[0].get("value")
            if value is not None and dst not in metrics:
                metrics[dst] = int(value)
        return metrics

    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        """Per-media insights → unified keys (§8.2: missing ≠ zero — absent
        keys mean "platform can't report", the UI renders em-dash)."""
        creds = self._auth()
        if creds is None:
            return None
        from urllib.parse import quote
        pid = quote(str(platform_post_id), safe="")
        r = _request("GET",
                     f"{_GRAPH_BASE}/{self._api_version()}/{pid}/insights"
                     f"?metric={_INSIGHT_METRICS}",
                     headers=self._auth_headers(creds))
        if r.get("status") == 400:
            # Some media kinds reject the richer metric names — one honest
            # retry with the core set, never a guess.
            r = _request("GET",
                         f"{_GRAPH_BASE}/{self._api_version()}/{pid}/insights"
                         f"?metric={_INSIGHT_METRICS_CORE}",
                         headers=self._auth_headers(creds))
        if r.get("status") != 200:
            _log.warning("instagram insights http %s", r.get("status"))
            return None
        metrics = self._parse_insights(_json_body(r))
        return metrics or None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        creds = self._auth()
        if creds is None:
            return None
        uid = str(creds.get("user_id") or "") or "me"
        r = _request("GET",
                     f"{_GRAPH_BASE}/{self._api_version()}/{uid}"
                     "?fields=followers_count,media_count",
                     headers=self._auth_headers(creds))
        if r.get("status") != 200:
            _log.warning("instagram account metrics http %s", r.get("status"))
            return None
        body = _json_body(r)
        out: Dict[str, Any] = {}
        if isinstance(body.get("followers_count"), (int, float)):
            out["followers"] = int(body["followers_count"])
        if isinstance(body.get("media_count"), (int, float)):
            out["posts"] = int(body["media_count"])
        return out or None


ADAPTER = InstagramAdapter
