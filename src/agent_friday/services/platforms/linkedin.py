"""
LinkedIn platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.3).

Versioned REST posts (``POST /rest/posts`` + ``LinkedIn-Version`` header —
the successor to ugcPosts), the ``initializeUpload`` media flow (images and
chunked video), OAuth2 three-legged auth with the loopback redirect,
~60-day member-token **expiry surfacing**, counts-level analytics (full only
when an organization page is configured), and a conservative 25/day local
rate budget.

Auth conventions (§4.1 / §12.2):
  * ``auth_mode: oauth2`` — LinkedIn's member flow takes a confidential client
    (no PKCE); ``state`` is bound and single-use. The developer app's client
    secret is this platform's single-string secret (credential_store provider
    key ``platform_linkedin``); the non-secret ``client_id`` rides
    ``~/.friday/platforms.json → linkedin.client_id``.
  * Loopback redirect:
    ``http://localhost:3000/api/content/platforms/linkedin/callback``.
  * The structured token blob lives encrypted at
    ``~/.friday/platforms/linkedin.cred`` (base convention).
  * Member tokens live ~60 days and programmatic refresh is PARTNER-GATED:
    ``refresh()`` renews only when the platform issued a refresh token;
    otherwise ``status()`` surfaces ``expires_in_days`` / ``expiring_soon`` /
    ``needs_reauth`` so the Accounts tab prompts re-auth before it lapses.

Org posting (page-admin + Community Management scopes) is out of scope for
this adapter version — ``organization_urn`` config only upgrades analytics
from ``counts`` to ``full`` (§4.3). Native *articles* have no public write
API: the ``article`` format degrades loudly per §4.14, never silently.

Transport: EVERY HTTP call rides the module-level ``_request()`` seam so
tests stub exactly one function — zero live network in tests. Adapter errors
are externalized as fixed content-free strings (§12.5); details go to the
local logger only, never into envelopes, ``status()``, or the audit trail.

Import-safe under FRIDAY_TESTING: stdlib only at import time, no threads, no
network; urllib and credential_store are lazy-imported inside functions.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.services.platforms.base import (
    PlatformAdapter, _now_utc, _parse_iso)

_log = logging.getLogger("friday.platforms.linkedin")

_OAUTH_BASE = "https://www.linkedin.com/oauth/v2"
_API_BASE = "https://api.linkedin.com"
_REDIRECT_URI = "http://localhost:3000/api/content/platforms/linkedin/callback"
_SCOPES = ("w_member_social", "openid", "profile")

# Design-time snapshot (§4.2 volatility warning) — override via config
# ``api_version`` when LinkedIn sunsets this month tag.
_DEFAULT_VERSION = "202506"

# Surface re-auth this many days before the ~60-day member token lapses.
_REAUTH_WARN_DAYS = 7

# ── fixed content-free error strings (§12.5) ─────────────────────────────────
E_NOT_CONNECTED = "not_connected"
E_AUTH_EXPIRED = "auth_expired"
E_IDENTITY_MISSING = "identity_missing"
E_CLIENT_NOT_CONFIGURED = "client_not_configured"
E_STATE_MISMATCH = "state_mismatch"
E_OAUTH_FAILED = "oauth_exchange_failed"
E_ASSET_MISSING = "asset_missing"
E_ASSET_TOO_LARGE = "asset_too_large"
E_VIDEO_OUT_OF_RANGE = "video_out_of_range"
E_LINK_MISSING = "link_missing"
E_UPLOAD_FAILED = "upload_failed"
E_PUBLISH_FAILED = "publish_failed"
E_DELETE_FAILED = "delete_failed"
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
        _log.warning("linkedin transport error: %s: %s", type(e).__name__, e)
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


def _append_hashtag_block(text: str, hashtags: Any) -> str:
    """§5.3: the composer suggests the tags, the outbound commentary carries
    them — tags already present in the text are never duplicated."""
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


def _split_scopes(raw) -> List[str]:
    """LinkedIn returns comma-separated scope; tolerate spaces too."""
    return [s for s in str(raw or "").replace(",", " ").split() if s]


class LinkedInAdapter(PlatformAdapter):
    name = "linkedin"
    label = "LinkedIn"
    auth_mode = "oauth2"
    automation_tier = "api"
    default_daily_limit = 25    # §4.3 conservative local budget (configurable)

    def __init__(self) -> None:
        super().__init__()
        self._pending_state: Optional[str] = None   # bound + single-use (§12.2)

    # ── capability declaration (§4.2 / §4.3) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        org_connected = bool(self._config.get("organization_urn"))
        caps.update({
            # "article" is deliberately absent: no public write API (§4.3) —
            # prepare() degrades that format loudly instead of pretending.
            "formats": ["post", "image_post", "video_upload", "link_post"],
            "char_limit": 3000,
            "title_limit": None,
            "thread": False,
            "native_schedule": False,
            "native_delete": True,
            "analytics": "full" if org_connected else "counts",
            "hashtags_max": None,      # no hard cap; norms (2–3) live in §5.2
            "notes": [
                "member tokens last ~60 days; re-authenticate from Accounts before expiry",
                "articles have no public write API — link share or assisted handoff",
                "impressions/reach are organization-page only; member analytics are counts",
                "conservative local budget: 25 posts/day (configurable)",
            ],
        })
        caps["media"] = {
            "images": {"max": 9, "formats": ["png", "jpeg", "gif"],
                       "max_bytes": 10 * 1024 * 1024, "aspect": (0.4, 2.6)},
            "video": {"min_s": 3, "max_s": 30 * 60, "formats": ["mp4"],
                      "max_bytes": 500 * 1024 * 1024},
            "alt_text": True,
        }
        return caps

    # ── config accessors ─────────────────────────────────────────────────────
    def _client_id(self) -> Optional[str]:
        cid = str(self._config.get("client_id") or "").strip()
        return cid or None

    def _client_secret_value(self) -> Optional[str]:
        # Base convention: the single-string secret slot holds the developer
        # app's client secret for this platform (module docstring).
        return self.simple_secret()

    def _api_version(self) -> str:
        return str(self._config.get("api_version") or _DEFAULT_VERSION)

    # ── auth lifecycle (§4.1 / §12.2) ────────────────────────────────────────
    def connect_url(self, state: str) -> Optional[str]:
        cid = self._client_id()
        if not cid:
            self._last_error = E_CLIENT_NOT_CONFIGURED
            return None
        from urllib.parse import urlencode
        self._pending_state = str(state or "")
        self._audit("connect_started")
        return f"{_OAUTH_BASE}/authorization?" + urlencode({
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": _REDIRECT_URI,
            "state": self._pending_state,
            "scope": " ".join(_SCOPES),
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
            "grant_type": "authorization_code",
            "code": str(params.get("code")),
            "redirect_uri": _REDIRECT_URI,
            "client_id": cid,
            "client_secret": csec,   # pragma: allowlist secret
        })
        r = _request("POST", f"{_OAUTH_BASE}/accessToken",
                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                     data=form.encode("utf-8"))
        grant = _json_body(r)
        if r.get("status") != 200 or not grant.get("access_token"):
            self._last_error = E_OAUTH_FAILED
            _log.warning("linkedin token exchange failed: http %s", r.get("status"))
            self._audit("callback_rejected", reason="exchange")
            return self.status()

        now = _now_utc()
        blob: Dict[str, Any] = {
            "access_token": grant.get("access_token"),
            "expires_at": _iso(now + timedelta(
                seconds=int(grant.get("expires_in") or 0) or 60 * 86400)),
            "scopes": _split_scopes(grant.get("scope")) or list(_SCOPES),
            "connected_at": _iso(now),
        }
        # Partner-gated: most member apps never receive one — store when given.
        if grant.get("refresh_token"):
            blob["refresh_token"] = grant.get("refresh_token")
            if grant.get("refresh_token_expires_in"):
                blob["refresh_expires_at"] = _iso(now + timedelta(
                    seconds=int(grant.get("refresh_token_expires_in") or 0)))

        # Identity (openid userinfo) — publish needs the member URN as author.
        ui = _request("GET", f"{_API_BASE}/v2/userinfo", headers={
            "Authorization": "Bearer " + str(blob["access_token"])})
        who = _json_body(ui)
        if ui.get("status") == 200 and who.get("sub"):
            blob["person_urn"] = f"urn:li:person:{who.get('sub')}"
            blob["account"] = who.get("name") or who.get("given_name") or who.get("sub")
        else:
            _log.warning("linkedin userinfo unavailable: http %s", ui.get("status"))

        saved = self.save_credentials(blob)
        if not saved.get("ok"):
            return self.status()
        self._last_error = None
        self._audit("connected", scopes=len(blob["scopes"]),
                    identity=bool(blob.get("person_urn")))
        return self.status()

    def _renew(self, creds: Dict[str, Any]) -> bool:
        """Partner-gated refresh-token grant; True only on a stored new token."""
        rtok = creds.get("refresh_token")
        cid, csec = self._client_id(), self._client_secret_value()
        if not rtok or not cid or not csec:
            return False
        from urllib.parse import urlencode
        form = urlencode({
            "grant_type": "refresh_token",
            "refresh_token": rtok,       # pragma: allowlist secret
            "client_id": cid,
            "client_secret": csec,       # pragma: allowlist secret
        })
        r = _request("POST", f"{_OAUTH_BASE}/accessToken",
                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                     data=form.encode("utf-8"))
        grant = _json_body(r)
        if r.get("status") != 200 or not grant.get("access_token"):
            _log.warning("linkedin refresh grant failed: http %s", r.get("status"))
            return False
        updated = dict(creds)
        updated["access_token"] = grant.get("access_token")
        updated["expires_at"] = _iso(_now_utc() + timedelta(
            seconds=int(grant.get("expires_in") or 0) or 60 * 86400))
        if grant.get("refresh_token"):
            updated["refresh_token"] = grant.get("refresh_token")
        if not self.save_credentials(updated).get("ok"):
            return False
        self._audit("refreshed")
        return True

    def refresh(self) -> bool:
        """True = token usable. Expired without a refresh token → False, and
        status() carries the re-auth prompt (§4.3 expiry surfacing)."""
        creds = self.load_credentials()
        if not creds or not creds.get("access_token"):
            return False
        exp = _parse_iso(creds.get("expires_at"))
        now = _now_utc()
        if exp and exp <= now:
            if self._renew(creds):
                return True
            self._last_error = E_AUTH_EXPIRED
            return False
        if exp and (exp - now) <= timedelta(days=_REAUTH_WARN_DAYS):
            self._renew(creds)    # best-effort early renewal; still usable
        return True

    def revoke(self) -> bool:
        """Platform-side revoke (LinkedIn supports it) + local purge."""
        platform_side = False
        creds = self.load_credentials()
        cid, csec = self._client_id(), self._client_secret_value()
        if creds and creds.get("access_token") and cid and csec:
            from urllib.parse import urlencode
            form = urlencode({
                "client_id": cid,
                "client_secret": csec,                    # pragma: allowlist secret
                "token": creds.get("access_token"),       # pragma: allowlist secret
            })
            r = _request("POST", f"{_OAUTH_BASE}/revoke",
                         headers={"Content-Type": "application/x-www-form-urlencoded"},
                         data=form.encode("utf-8"))
            platform_side = r.get("status") == 200
        self.clear_credentials()
        self._audit("revoke", platform_side=platform_side)
        return True

    def status(self) -> Dict[str, Any]:
        """Base facts + the ~60-day expiry countdown the Accounts tab renders
        ("LinkedIn token expires in 6 days") — never token material."""
        out = super().status()
        # The simple-secret slot holds the developer app's client secret here,
        # not a session credential — only the OAuth blob means "connected".
        out["connected"] = self.load_credentials() is not None
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
        _last_error. An expired token tries the (partner-gated) renewal once."""
        creds = self.load_credentials()
        if not creds or not creds.get("access_token"):
            self._last_error = E_NOT_CONNECTED
            return None
        exp = _parse_iso(creds.get("expires_at"))
        if exp is not None and exp <= _now_utc():
            if self._renew(creds):
                return self.load_credentials() or None
            self._last_error = E_AUTH_EXPIRED
            return None
        return creds

    # ── shared HTTP plumbing ─────────────────────────────────────────────────
    def _rest_headers(self, creds: Dict[str, Any], *, json_body: bool = True,
                      ) -> Dict[str, str]:
        h = {
            "Authorization": "Bearer " + str(creds.get("access_token") or ""),
            "LinkedIn-Version": self._api_version(),
            "X-Restli-Protocol-Version": "2.0.0",
        }
        if json_body:
            h["Content-Type"] = "application/json"
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
        _log.warning("linkedin http %s -> %s", st, err)
        env: Dict[str, Any] = {"ok": False, "error": err, "status_code": st}
        retry_after = _header(resp, "Retry-After")
        if retry_after:
            try:
                env["retry_after"] = int(retry_after)
            except (TypeError, ValueError):
                pass
        return env

    # ── media: the initializeUpload flow (§4.3) ──────────────────────────────
    @staticmethod
    def _read_asset(asset: Dict[str, Any]) -> Optional[bytes]:
        source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
        for k in ("out_path", "path", "filename"):
            v = asset.get(k) or source.get(k)
            if not v:
                continue
            try:
                p = Path(str(v))
                if p.is_file():
                    return p.read_bytes()
            except Exception as e:
                _log.warning("linkedin asset read failed: %s", type(e).__name__)
        return None

    @staticmethod
    def _asset_kind(asset: Dict[str, Any]) -> str:
        source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
        return str(asset.get("kind") or source.get("kind") or "image")

    @staticmethod
    def _asset_alt(asset: Dict[str, Any]) -> str:
        source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
        return str(asset.get("alt_text") or source.get("alt_text") or "")

    def _upload_image(self, creds: Dict[str, Any], owner: str,
                      data: bytes) -> Dict[str, Any]:
        """POST /rest/images?action=initializeUpload → PUT bytes → image URN."""
        init = _request(
            "POST", f"{_API_BASE}/rest/images?action=initializeUpload",
            headers=self._rest_headers(creds),
            data=json.dumps({"initializeUploadRequest": {"owner": owner}}).encode("utf-8"))
        value = _json_body(init).get("value") or {}
        upload_url, urn = value.get("uploadUrl"), value.get("image")
        if init.get("status") not in (200, 201) or not upload_url or not urn:
            return self._error_envelope(init, E_UPLOAD_FAILED)
        put = _request("PUT", str(upload_url), headers={
            "Authorization": "Bearer " + str(creds.get("access_token") or ""),
            "Content-Type": "application/octet-stream",
        }, data=data)
        if put.get("status") not in (200, 201):
            return self._error_envelope(put, E_UPLOAD_FAILED)
        return {"ok": True, "urn": str(urn)}

    def _upload_video(self, creds: Dict[str, Any], owner: str,
                      data: bytes) -> Dict[str, Any]:
        """initializeUpload → chunked PUTs per uploadInstructions → finalize."""
        init = _request(
            "POST", f"{_API_BASE}/rest/videos?action=initializeUpload",
            headers=self._rest_headers(creds),
            data=json.dumps({"initializeUploadRequest": {
                "owner": owner, "fileSizeBytes": len(data)}}).encode("utf-8"))
        value = _json_body(init).get("value") or {}
        urn = value.get("video")
        instructions = value.get("uploadInstructions") or []
        if init.get("status") not in (200, 201) or not urn or not instructions:
            return self._error_envelope(init, E_UPLOAD_FAILED)
        part_ids: List[str] = []
        for ins in instructions:
            if not isinstance(ins, dict) or not ins.get("uploadUrl"):
                return self._error_envelope(init, E_UPLOAD_FAILED)
            try:
                first = int(ins.get("firstByte") or 0)
                last = int(ins.get("lastByte") if ins.get("lastByte") is not None
                           else len(data) - 1)
            except (TypeError, ValueError):
                first, last = 0, len(data) - 1
            put = _request("PUT", str(ins["uploadUrl"]), headers={
                "Authorization": "Bearer " + str(creds.get("access_token") or ""),
                "Content-Type": "application/octet-stream",
            }, data=data[first:last + 1])
            if put.get("status") not in (200, 201):
                return self._error_envelope(put, E_UPLOAD_FAILED)
            etag = _header(put, "ETag")
            if etag:
                part_ids.append(etag)
        fin = _request(
            "POST", f"{_API_BASE}/rest/videos?action=finalizeUpload",
            headers=self._rest_headers(creds),
            data=json.dumps({"finalizeUploadRequest": {
                "video": urn, "uploadToken": value.get("uploadToken") or "",
                "uploadedPartIds": part_ids}}).encode("utf-8"))
        if fin.get("status") not in (200, 201, 204):
            return self._error_envelope(fin, E_UPLOAD_FAILED)
        return {"ok": True, "urn": str(urn)}

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Base hard validation, then the real media uploads. Media URNs land
        in prepared["media"] (and platform_media_id per asset) so publish()
        is a single deterministic POST."""
        target = dict(target or {})
        fmt = str(target.get("format") or "post")
        if fmt == "article":
            # §4.3 / §4.14: no public write API — surface the choice, loudly.
            return self.degrade("linkedin articles have no public write API")

        # §5.3: the suggested hashtag block ships with the commentary —
        # materialized onto the body BEFORE base validation so the char check
        # covers exactly what publishes (the composer reserved room for it).
        hashtags = list(target.get("hashtags") or [])
        if hashtags:
            body0 = str(target.get("adapted_body")
                        or (post or {}).get("body") or "")
            target["adapted_body"] = _append_hashtag_block(body0, hashtags)

        base_res = super().prepare(target, post)
        if not base_res.get("ok"):
            return base_res
        prepared = base_res["prepared"]
        warnings = base_res.get("warnings") or []

        assets = prepared.get("assets") or []
        media: List[Dict[str, Any]] = []
        if assets:
            creds = self._auth()
            if creds is None:
                return {"ok": False, "error": self._last_error or E_NOT_CONNECTED,
                        "warnings": warnings}
            owner = str(creds.get("person_urn") or "")
            if not owner:
                self._last_error = E_IDENTITY_MISSING
                return {"ok": False, "error": E_IDENTITY_MISSING, "warnings": warnings}
            caps_media = (self.capabilities().get("media") or {})
            for asset in assets:
                kind = self._asset_kind(asset)
                if kind not in ("image", "video"):
                    warnings.append(f"{kind} assets are not postable on LinkedIn — skipped")
                    continue
                data = self._read_asset(asset)
                if data is None:
                    self._last_error = E_ASSET_MISSING
                    return {"ok": False, "error": E_ASSET_MISSING, "warnings": warnings}
                limits = caps_media.get("images" if kind == "image" else "video") or {}
                max_bytes = limits.get("max_bytes")
                if isinstance(max_bytes, int) and len(data) > max_bytes:
                    self._last_error = E_ASSET_TOO_LARGE
                    return {"ok": False, "error": E_ASSET_TOO_LARGE, "warnings": warnings}
                if kind == "video":
                    duration = asset.get("duration_s")
                    if duration is not None:
                        try:
                            duration = float(duration)
                            if duration < limits.get("min_s", 3) or \
                                    duration > limits.get("max_s", 1800):
                                self._last_error = E_VIDEO_OUT_OF_RANGE
                                return {"ok": False, "error": E_VIDEO_OUT_OF_RANGE,
                                        "warnings": warnings}
                        except (TypeError, ValueError):
                            pass
                    up = self._upload_video(creds, owner, data)
                else:
                    up = self._upload_image(creds, owner, data)
                if not up.get("ok"):
                    up.setdefault("warnings", warnings)
                    return up
                asset["platform_media_id"] = up["urn"]
                media.append({"id": up["urn"], "kind": kind,
                              "alt_text": self._asset_alt(asset)})
        prepared["media"] = media
        return {"ok": True, "prepared": prepared, "warnings": warnings}

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Versioned REST create: POST /rest/posts (§4.3). → {ok, post_url,
        platform_post_id, raw}."""
        try:
            prepared = dict(prepared or {})
            creds = self._auth()
            if creds is None:
                return {"ok": False, "error": self._last_error or E_NOT_CONNECTED}
            author = str(creds.get("person_urn") or "")
            if not author:
                self._last_error = E_IDENTITY_MISSING
                return {"ok": False, "error": E_IDENTITY_MISSING}

            options = dict(prepared.get("options") or {})
            fmt = str(prepared.get("format") or "post")
            payload: Dict[str, Any] = {
                "author": author,
                "commentary": str(prepared.get("body") or ""),
                "visibility": str(options.get("visibility") or "PUBLIC").upper(),
                "distribution": {"feedDistribution": "MAIN_FEED",
                                 "targetEntities": [],
                                 "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": bool(options.get("disable_reshare", False)),
            }

            media = [m for m in (prepared.get("media") or []) if isinstance(m, dict)]
            link = options.get("link") or options.get("link_url")
            if fmt == "link_post" or (link and not media):
                if not link:
                    self._last_error = E_LINK_MISSING
                    return {"ok": False, "error": E_LINK_MISSING}
                article: Dict[str, Any] = {"source": str(link)}
                if prepared.get("title"):
                    article["title"] = str(prepared.get("title"))
                if options.get("link_description"):
                    article["description"] = str(options.get("link_description"))
                payload["content"] = {"article": article}
            elif len(media) == 1:
                one: Dict[str, Any] = {"id": media[0].get("id")}
                if media[0].get("alt_text"):
                    one["altText"] = media[0]["alt_text"]
                payload["content"] = {"media": one}
            elif len(media) > 1:
                images = []
                for m in media:
                    entry: Dict[str, Any] = {"id": m.get("id")}
                    if m.get("alt_text"):
                        entry["altText"] = m["alt_text"]
                    images.append(entry)
                payload["content"] = {"multiImage": {"images": images}}

            r = _request("POST", f"{_API_BASE}/rest/posts",
                         headers=self._rest_headers(creds),
                         data=json.dumps(payload).encode("utf-8"))
            if r.get("status") not in (200, 201):
                env = self._error_envelope(r, E_PUBLISH_FAILED)
                self._audit("publish", ok=False, reason=env.get("error"))
                return env
            urn = _header(r, "x-restli-id") or _json_body(r).get("id")
            if not urn:
                self._last_error = E_PUBLISH_FAILED
                self._audit("publish", ok=False, reason=E_PUBLISH_FAILED)
                return {"ok": False, "error": E_PUBLISH_FAILED,
                        "status_code": int(r.get("status") or 0)}
            urn = str(urn)
            self.consume_budget()
            self._last_error = None
            self._audit("publish", ok=True, format=fmt)
            return {"ok": True,
                    "post_url": f"https://www.linkedin.com/feed/update/{urn}/",
                    "platform_post_id": urn,
                    "raw": {"id": urn}}
        except Exception as e:
            # Never raises — and never leaks detail beyond the local log.
            _log.warning("linkedin publish error: %s: %s", type(e).__name__, e)
            self._last_error = E_PUBLISH_FAILED
            return {"ok": False, "error": E_PUBLISH_FAILED}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        creds = self._auth()
        if creds is None:
            return {"ok": False, "error": self._last_error or E_NOT_CONNECTED}
        from urllib.parse import quote
        r = _request("DELETE",
                     f"{_API_BASE}/rest/posts/{quote(str(platform_post_id), safe='')}",
                     headers=self._rest_headers(creds, json_body=False))
        if r.get("status") not in (200, 204):
            return self._error_envelope(r, E_DELETE_FAILED)
        self._audit("delete", ok=True)
        return {"ok": True}

    # ── analytics path (§4.3 / §8.2 LinkedIn column) ─────────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        """Member counts via socialActions; impressions/clicks only when an
        organization page is configured (§8.2: missing ≠ zero — absent keys
        mean "platform can't report", the UI renders em-dash)."""
        creds = self._auth()
        if creds is None:
            return None
        from urllib.parse import quote
        pid = quote(str(platform_post_id), safe="")
        metrics: Dict[str, Any] = {}

        r = _request("GET", f"{_API_BASE}/rest/socialActions/{pid}",
                     headers=self._rest_headers(creds, json_body=False))
        if r.get("status") == 200:
            social = _json_body(r)
            likes = (social.get("likesSummary") or {}).get("totalLikes")
            if isinstance(likes, (int, float)):
                metrics["likes"] = int(likes)
            comments_summary = social.get("commentsSummary") or {}
            comments = comments_summary.get("aggregatedTotalComments")
            if comments is None:
                comments = comments_summary.get("totalFirstLevelComments")
            if isinstance(comments, (int, float)):
                metrics["comments"] = int(comments)
        else:
            _log.warning("linkedin socialActions http %s", r.get("status"))

        org = str(self._config.get("organization_urn") or "")
        if org:
            stats_url = (f"{_API_BASE}/rest/organizationalEntityShareStatistics"
                         f"?q=organizationalEntity"
                         f"&organizationalEntity={quote(org, safe='')}"
                         f"&shares=List({pid})")
            rr = _request("GET", stats_url,
                          headers=self._rest_headers(creds, json_body=False))
            if rr.get("status") == 200:
                elements = _json_body(rr).get("elements") or []
                stats = (elements[0].get("totalShareStatistics")
                         if elements and isinstance(elements[0], dict) else None) or {}
                mapping = {"impressionCount": "impressions", "clickCount": "clicks",
                           "shareCount": "shares", "likeCount": "likes",
                           "commentCount": "comments"}
                for src, dst in mapping.items():
                    v = stats.get(src)
                    if isinstance(v, (int, float)) and dst not in metrics:
                        metrics[dst] = int(v)
            else:
                _log.warning("linkedin org stats http %s", rr.get("status"))

        return metrics or None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        # Member accounts cannot report follower counts through this API
        # surface — honest None (§8.2 missing ≠ zero).
        return None


ADAPTER = LinkedInAdapter
