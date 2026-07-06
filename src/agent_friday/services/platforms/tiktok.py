"""
TikTok platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.12).

Content Posting API (``open.tiktokapis.com``), OAuth2 + PKCE. The sharp
constraint: **unaudited apps can only create SELF_ONLY (private/draft) posts**
— the adapter runs an honesty pre-audit against ``creator_info/query`` at
prepare time and, when the requested privacy level is not grantable, surfaces
the §4.14 ladder choice ("save as private draft, or hold?") instead of ever
silently falling a rung. With explicit consent (``options.accept_self_only``)
posts land as drafts in the user's TikTok inbox for one-tap manual publishing.

Publish flows:
  * Video — chunked ``FILE_UPLOAD``: ``video/init`` (returns publish_id +
    upload_url) → 5–64 MB chunk PUTs with Content-Range → ``status/fetch``
    polling until PUBLISH_COMPLETE / SEND_TO_USER_INBOX / FAILED.
  * Photo mode (≤35 images) — ``content/init`` with ``PULL_FROM_URL`` only,
    which needs publicly reachable URLs (§7.4 staging): no staged URLs →
    the degradation choice is surfaced, never a silent failure.

Analytics: Display API ``video/query`` → counts (§8.2 TikTok column).
Local rate budget: conservative ≤5 posts/day (single-digit pending-share caps).

ALL transport rides the module-level ``_http`` indirection — tests stub that
one symbol and nothing else in the module can open a socket. Adapter errors
are externalized as fixed content-free strings (§12.5); full detail goes to
the local debug log only, and nothing tokenish ever reaches a log line,
``status()``, or an error envelope.

Config keys (non-secret, via ~/.friday/platforms.json → "tiktok"):
  client_key: str              — TikTok app client key (public app id)
  daily_post_limit: int        — local budget override (base convention)
  status_poll_interval_s: float— publish status poll cadence (default 2.0)
  status_poll_max: int         — max status polls before timing out (default 30)

The app's client secret (when the registration has one — PKCE-only desktop
apps may not) is stored via the simple-secret rail
(``credential_store.set_provider_key("platform_tiktok", …)``); OAuth token
state lives in the encrypted ``~/.friday/platforms/tiktok.cred`` blob.

Import-safe under FRIDAY_TESTING: stdlib only, no threads, no network at
import time; urllib and credential_store are lazy-imported inside functions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from agent_friday.services.platforms.base import (
    PlatformAdapter, _now_utc, _parse_iso)

_LOG = logging.getLogger("friday.platforms.tiktok")

# ── endpoints (design-time snapshot, mid-2026 — §4.2 volatility warning) ─────
_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
_API_BASE = "https://open.tiktokapis.com"
_TOKEN_URL = _API_BASE + "/v2/oauth/token/"
_REVOKE_URL = _API_BASE + "/v2/oauth/revoke/"
_CREATOR_INFO_URL = _API_BASE + "/v2/post/publish/creator_info/query/"
_VIDEO_INIT_URL = _API_BASE + "/v2/post/publish/video/init/"
_CONTENT_INIT_URL = _API_BASE + "/v2/post/publish/content/init/"
_STATUS_URL = _API_BASE + "/v2/post/publish/status/fetch/"
_VIDEO_QUERY_URL = _API_BASE + "/v2/video/query/"
_USER_INFO_URL = _API_BASE + "/v2/user/info/"

# OAuth loopback (§4.1) — Friday's Flask server is the redirect target.
_REDIRECT_URI = "http://localhost:3000/api/content/platforms/tiktok/callback"
# Minimum scopes for the declared capabilities (§12.2): publish + upload,
# identity for the Accounts card, video.list/user.info.stats for counts.
_SCOPES = "user.info.basic,user.info.stats,video.publish,video.upload,video.list"

_DEFAULT_PRIVACY = "PUBLIC_TO_EVERYONE"
_SELF_ONLY = "SELF_ONLY"
_INBOX_URL = "https://www.tiktok.com/tiktokstudio/content"  # where drafts land

# FILE_UPLOAD chunking (§4.12): chunks are 5–64 MB; TikTok floors the chunk
# count and the trailing chunk absorbs the remainder (must stay < 128 MB).
_ONE_CHUNK_MAX = 64 * 1024 * 1024
_CHUNK_SIZE = 50 * 1024 * 1024          # worst-case trailer < 100 MB — safe

_VIDEO_FORMATS = ("video_upload", "short", "reel", "video", "post")
_PHOTO_FORMATS = ("image_post", "photo", "carousel")

# ── §12.5 — adapter errors externalized as fixed, content-free strings ───────
_E_NOT_CONNECTED = "not_connected"
_E_NO_CLIENT_KEY = "client_key_missing"
_E_STATE = "oauth_state_mismatch"
_E_CALLBACK = "oauth_callback_failed"
_E_AUTH = "auth_failed"
_E_TRANSPORT = "transport_error"
_E_API = "tiktok_api_error"
_E_ASSET = "asset_unreadable"
_E_UPLOAD = "upload_failed"
_E_PUBLISH = "publish_failed"
_E_TIMEOUT = "publish_status_timeout"
_E_PREPARE = "prepare_failed"


def _http(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
          body: Optional[bytes] = None, timeout: int = 30,
          sensitive: bool = False) -> Dict[str, Any]:
    """Module-level transport indirection — the ONLY place bytes leave for
    TikTok; tests stub this symbol and no other code path opens a socket.

    Returns {"status": int, "json": dict|None, "body": bytes, "error": str|None}
    and never raises. ``sensitive=True`` (token endpoints) suppresses body
    detail in the local debug log — nothing tokenish may reach a log line.
    """
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, method=method.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        status = int(getattr(e, "code", 0) or 0)
    except Exception as e:
        try:
            _LOG.debug("tiktok transport failure: %s", type(e).__name__)
        except Exception:
            pass
        return {"status": 0, "json": None, "body": b"", "error": _E_TRANSPORT}
    parsed: Optional[Dict[str, Any]] = None
    try:
        obj = json.loads(raw.decode("utf-8"))
        parsed = obj if isinstance(obj, dict) else None
    except Exception:
        parsed = None
    if status >= 400 and not sensitive:
        try:  # full detail stays local (§12.5); token endpoints log nothing
            _LOG.debug("tiktok http %s %s -> %s %r",
                       method, url.split("?")[0], status, raw[:300])
        except Exception:
            pass
    return {"status": status, "json": parsed, "body": raw,
            "error": None if 200 <= status < 400 else _E_API}


def _tt_data(resp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Unwrap TikTok's {"data": …, "error": {"code": "ok"}} envelope.
    None unless the call succeeded AND error.code is "ok"."""
    if not isinstance(resp, dict) or resp.get("error"):
        return None
    j = resp.get("json") or {}
    err = j.get("error")
    if isinstance(err, dict) and str(err.get("code", "ok")).lower() != "ok":
        return None
    data = j.get("data")
    return data if isinstance(data, dict) else {}


def _chunk_plan(video_size: int) -> Tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok FILE_UPLOAD rules: files
    that fit one 64 MB chunk upload whole; larger files use fixed 50 MB
    chunks with a floored count — the final chunk absorbs the remainder."""
    size = max(1, int(video_size))
    if size <= _ONE_CHUNK_MAX:
        return size, 1
    return _CHUNK_SIZE, size // _CHUNK_SIZE


class TikTokAdapter(PlatformAdapter):
    name = "tiktok"
    label = "TikTok"
    auth_mode = "oauth2_pkce"
    automation_tier = "api"          # audit-gated: degrades loudly, never silently
    default_daily_limit = 5          # §4.12 — single-digit pending-share caps

    def __init__(self) -> None:
        super().__init__()
        self._auth_lock = threading.Lock()
        self._pending_auth: Dict[str, Dict[str, Any]] = {}   # state → verifier

    # ── capability declaration (§4.2 / §4.12) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({
            "formats": ["video_upload", "short", "reel", "image_post"],
            "char_limit": 2200,          # caption incl. hashtags
            "title_limit": 90,           # photo-mode title field
            "thread": False,
            "native_schedule": False,
            "native_delete": False,      # Content Posting API has no delete
            "analytics": "counts",       # Display API video.query
            "hashtags_max": None,        # hashtags ride the caption budget
            "notes": [
                "unaudited apps publish SELF_ONLY private drafts only — the "
                "adapter surfaces the choice instead of pretending (§4.14)",
                "photo mode needs publicly reachable image URLs (staging §7.4)",
                "local budget defaults to 5 posts/day (pending-share caps)",
            ],
        })
        caps["media"] = {
            "images": {"max": 35, "formats": ["jpeg", "webp"],
                       "max_bytes": 20 * 1024 * 1024, "aspect": (0.5, 2.0)},
            "video": {"max_s": 600, "formats": ["mp4", "webm", "mov"],
                      "max_bytes": 4 * 1024 * 1024 * 1024},
            "alt_text": False,
        }
        return caps

    # ── auth lifecycle (OAuth2 + PKCE, state bound + single-use) ──────────────
    def connect_url(self, state: str) -> Optional[str]:
        client_key = str(self._config.get("client_key") or "").strip()
        if not client_key:
            self._last_error = _E_NO_CLIENT_KEY
            return None
        verifier = secrets.token_urlsafe(48)
        # TikTok's PKCE deviates from RFC 7636: code_challenge is the
        # HEX-encoded SHA-256 digest of the verifier, not base64url.
        challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
        now = time.time()
        with self._auth_lock:
            # single-use entries; anything older than 10 min is dropped
            stale = [k for k, v in self._pending_auth.items()
                     if now - float(v.get("ts", 0)) > 600]
            for k in stale:
                self._pending_auth.pop(k, None)
            self._pending_auth[str(state)] = {"verifier": verifier, "ts": now}
        self._audit("connect_started")
        return _AUTH_URL + "?" + urlencode({
            "client_key": client_key,
            "response_type": "code",
            "scope": _SCOPES,
            "redirect_uri": _REDIRECT_URI,
            "state": str(state),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })

    def handle_callback(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            params = dict(params or {})
            with self._auth_lock:   # pop = single-use, replay-proof
                pending = self._pending_auth.pop(str(params.get("state") or ""), None)
            if params.get("error"):
                self._last_error = _E_CALLBACK
                self._audit("callback_denied")
                return self.status()
            code = str(params.get("code") or "")
            if pending is None or not code:
                self._last_error = _E_STATE
                self._audit("callback_rejected",
                            reason="state" if pending is None else "no_code")
                return self.status()
            blob = self._exchange_code(code, str(pending.get("verifier") or ""))
            if not blob:
                self._last_error = _E_AUTH
                self._audit("callback_exchange_failed")
                return self.status()
            saved = self.save_credentials(blob)
            if not saved.get("ok"):
                self._last_error = _E_CALLBACK
                return self.status()
            self._last_error = None
            self._audit("connected", account=str(blob.get("account") or ""))
            return self.status()
        except Exception as e:
            _LOG.debug("tiktok callback failed: %s", type(e).__name__)
            self._last_error = _E_CALLBACK
            return self.status()

    def refresh(self) -> bool:
        try:
            creds = self.load_credentials()
            if not isinstance(creds, dict) or not creds.get("access_token"):
                return False
            exp = _parse_iso(creds.get("expires_at"))
            if exp is not None and exp - _now_utc() > timedelta(seconds=300):
                return True                       # comfortably fresh
            rt = creds.get("refresh_token")
            if not rt:
                self._last_error = _E_AUTH
                return False
            fields = self._token_request({"grant_type": "refresh_token",
                                          "refresh_token": rt})
            if not fields:
                self._last_error = _E_AUTH
                self._audit("refresh_failed")
                return False
            self.save_credentials(
                self._blob_from_token(fields, previous=creds, fetch_identity=False))
            self._audit("refreshed")
            return True
        except Exception:
            self._last_error = _E_AUTH
            return False

    def revoke(self) -> bool:
        platform_side = False
        try:
            creds = self.load_credentials() or {}
            bearer = creds.get("access_token")
            if bearer:
                form = {"client_key": str(self._config.get("client_key") or ""),
                        "token": str(bearer)}
                cs = self.simple_secret()
                if cs:
                    form["client_secret"] = cs  # pragma: allowlist secret
                resp = _http("POST", _REVOKE_URL,
                             headers={"Content-Type":
                                      "application/x-www-form-urlencoded"},
                             body=urlencode(form).encode("ascii"), sensitive=True)
                platform_side = 200 <= int(resp.get("status") or 0) < 300
        except Exception:
            pass
        self.clear_credentials()
        self._audit("revoke", platform_side=platform_side)
        return True

    def status(self) -> Dict[str, Any]:
        st = super().status()
        try:
            # connected means a usable OAuth blob — the simple-secret slot only
            # holds the app's client secret and must not read as "connected".
            creds = self.load_credentials()
            st["connected"] = bool(isinstance(creds, dict)
                                   and creds.get("access_token"))
        except Exception:
            st["connected"] = False
        return st

    # ── auth internals ─────────────────────────────────────────────────────────
    def _bearer(self) -> Optional[str]:
        creds = self.load_credentials()
        if isinstance(creds, dict) and creds.get("access_token"):
            return str(creds["access_token"])
        return None

    def _token_request(self, extra: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """POST the OAuth token endpoint (form-encoded). Returns the flat
        token fields, or None. Always sensitive — never logged."""
        form = {"client_key": str(self._config.get("client_key") or "")}
        cs = self.simple_secret()             # client secret, when the app has one
        if cs:
            form["client_secret"] = cs  # pragma: allowlist secret
        form.update({k: str(v) for k, v in extra.items()})
        resp = _http("POST", _TOKEN_URL,
                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                     body=urlencode(form).encode("ascii"), sensitive=True)
        j = resp.get("json") or {}
        if isinstance(j.get("access_token"), str):
            return j                            # v2 flat shape
        d = j.get("data")
        if isinstance(d, dict) and isinstance(d.get("access_token"), str):
            return d                            # wrapped shape, just in case
        return None

    def _exchange_code(self, code: str, verifier: str) -> Optional[Dict[str, Any]]:
        fields = self._token_request({
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        })
        if not fields:
            return None
        return self._blob_from_token(fields)

    def _blob_from_token(self, fields: Dict[str, Any],
                         previous: Optional[Dict[str, Any]] = None,
                         fetch_identity: bool = True) -> Dict[str, Any]:
        prev = dict(previous or {})
        now = _now_utc()

        def _iso_in(seconds) -> Optional[str]:
            try:
                return (now + timedelta(seconds=int(seconds))
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError):
                return None

        scopes = [s for s in
                  str(fields.get("scope") or "").replace(",", " ").split() if s]
        blob = dict(prev)
        blob.update({
            "access_token": fields.get("access_token"),
            "refresh_token": fields.get("refresh_token") or prev.get("refresh_token"),
            "open_id": fields.get("open_id") or prev.get("open_id"),
            "scopes": scopes or list(prev.get("scopes") or []),
            "expires_at": _iso_in(fields.get("expires_in") or 0),
        })
        if fields.get("refresh_expires_in") is not None:
            blob["refresh_expires_at"] = _iso_in(fields.get("refresh_expires_in"))
        if fetch_identity:   # account identity for the Accounts card — best-effort
            user = self._user_info(str(blob.get("access_token") or ""))
            if user:
                blob["account"] = (user.get("display_name")
                                   or user.get("open_id") or blob.get("open_id"))
        blob.setdefault("account", prev.get("account") or blob.get("open_id"))
        return blob

    def _api(self, bearer: str, url: str,
             payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        resp = _http("POST", url, headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json; charset=UTF-8",
        }, body=json.dumps(payload or {}).encode("utf-8"))
        return _tt_data(resp)

    def _user_info(self, bearer: str,
                   fields: str = "open_id,display_name") -> Optional[Dict[str, Any]]:
        if not bearer:
            return None
        resp = _http("GET", _USER_INFO_URL + "?" + urlencode({"fields": fields}),
                     headers={"Authorization": f"Bearer {bearer}"})
        data = _tt_data(resp)
        if data is None:
            return None
        user = data.get("user")
        return user if isinstance(user, dict) else None

    def _creator_info(self, bearer: str) -> Optional[Dict[str, Any]]:
        """POST creator_info/query — privacy_level_options is the honesty
        pre-audit source: unaudited apps only ever see SELF_ONLY here."""
        return self._api(bearer, _CREATOR_INFO_URL, {})

    # ── publish path ───────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        base_res = super().prepare(target, post)
        if not base_res.get("ok"):
            return base_res
        prepared = base_res["prepared"]
        warnings: List[str] = list(base_res.get("warnings") or [])
        try:
            bearer = self._bearer()
            if not bearer:
                return {"ok": False, "error": _E_NOT_CONNECTED, "warnings": warnings}

            caption = self._caption(prepared, warnings)
            prepared["caption"] = caption
            fmt = str(prepared.get("format") or "post").lower()
            photo = fmt in _PHOTO_FORMATS

            # resolve media before spending an API call
            duration_s = None
            if photo:
                urls = self._photo_urls(prepared)
                if not urls:
                    # §7.4: PULL_FROM_URL needs public URLs — surface, don't fake
                    env = self.degrade("photo mode needs public image URLs — "
                                       "configure staging or hold")
                    env["warnings"] = warnings
                    return env
                prepared["photo_urls"] = urls[:35]
            else:
                path, size, duration_s = self._video_file(prepared)
                if not path:
                    return {"ok": False, "error": _E_ASSET, "warnings": warnings}
                chunk_size, chunk_count = _chunk_plan(size)
                prepared.update({"video_path": path, "video_size": size,
                                 "chunk_size": chunk_size,
                                 "total_chunk_count": chunk_count})

            # ── honesty pre-audit (§4.12 / §4.14) ────────────────────────────
            info = self._creator_info(bearer)
            if info is None:
                self._last_error = _E_API
                return {"ok": False, "error": _E_API, "warnings": warnings}
            allowed = [str(x) for x in (info.get("privacy_level_options") or [])]
            requested = str(prepared["options"].get("privacy_level")
                            or _DEFAULT_PRIVACY)
            if allowed and requested not in allowed:
                if _SELF_ONLY in allowed and prepared["options"].get("accept_self_only"):
                    # the user already chose the api_constrained rung — loudly
                    prepared["options"]["privacy_level"] = _SELF_ONLY
                    prepared["self_only_downgrade"] = True
                    warnings.append(
                        "app not audited by TikTok — publishing as a private "
                        "draft (SELF_ONLY); finish posting in the TikTok app")
                else:
                    # NEVER silently fall a rung: surface the choice (§4.14)
                    env = self.degrade(
                        "TikTok app not audited: requested privacy level is "
                        "unavailable — save as private draft, or hold?")
                    env["warnings"] = warnings
                    return env
            else:
                prepared["options"]["privacy_level"] = requested
            prepared["creator_username"] = str(info.get("creator_username") or "")

            max_s = info.get("max_video_post_duration_sec")
            if (not photo and isinstance(max_s, (int, float)) and max_s
                    and isinstance(duration_s, (int, float))
                    and duration_s > max_s):
                warnings.append("video exceeds this account's maximum duration")

            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception as e:
            _LOG.debug("tiktok prepare failed: %s", e)
            self._last_error = _E_PREPARE
            return {"ok": False, "error": _E_PREPARE, "warnings": warnings}

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        try:
            prepared = dict(prepared or {})
            bearer = self._bearer()
            if not bearer:
                return {"ok": False, "error": _E_NOT_CONNECTED}
            if prepared.get("photo_urls"):
                res = self._publish_photo(bearer, prepared)
            elif prepared.get("video_path"):
                res = self._publish_video(bearer, prepared)
            else:
                return {"ok": False, "error": _E_ASSET}
            if res.get("ok"):
                # inbox drafts consume the pending-share quota too
                self.consume_budget()
                self._audit("published",
                            format=str(prepared.get("format") or "post"),
                            self_only=bool(prepared.get("self_only_downgrade")))
            return res
        except Exception as e:
            _LOG.debug("tiktok publish failed: %s", e)
            self._last_error = _E_PUBLISH
            return {"ok": False, "error": _E_PUBLISH}

    def _post_info(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        opts = prepared.get("options") or {}
        info: Dict[str, Any] = {
            "title": str(prepared.get("caption") or prepared.get("body") or ""),
            "privacy_level": str(opts.get("privacy_level") or _SELF_ONLY),
            "disable_duet": bool(opts.get("disable_duet")),
            "disable_comment": bool(opts.get("disable_comment")),
            "disable_stitch": bool(opts.get("disable_stitch")),
        }
        if opts.get("video_cover_timestamp_ms") is not None:
            try:
                info["video_cover_timestamp_ms"] = int(opts["video_cover_timestamp_ms"])
            except (TypeError, ValueError):
                pass
        # commercial-content flags travel through options (§4.12)
        for flag in ("brand_content_toggle", "brand_organic_toggle"):
            if flag in opts:
                info[flag] = bool(opts.get(flag))
        return info

    def _publish_video(self, bearer: str, prepared: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "post_info": self._post_info(prepared),
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": int(prepared["video_size"]),
                "chunk_size": int(prepared["chunk_size"]),
                "total_chunk_count": int(prepared["total_chunk_count"]),
            },
        }
        data = self._api(bearer, _VIDEO_INIT_URL, payload)
        if data is None:
            self._last_error = _E_API
            return {"ok": False, "error": _E_API}
        publish_id = str(data.get("publish_id") or "")
        upload_url = str(data.get("upload_url") or "")
        if not publish_id or not upload_url:
            self._last_error = _E_API
            return {"ok": False, "error": _E_API}
        if not self._upload_chunks(upload_url, prepared):
            self._last_error = _E_UPLOAD
            return {"ok": False, "error": _E_UPLOAD,
                    "raw": {"publish_id": publish_id}}
        return self._await_publish(bearer, publish_id, prepared)

    def _publish_photo(self, bearer: str, prepared: Dict[str, Any]) -> Dict[str, Any]:
        opts = prepared.get("options") or {}
        try:
            cover = int(opts.get("photo_cover_index", 0) or 0)
        except (TypeError, ValueError):
            cover = 0
        payload = {
            "post_info": {
                "title": str(prepared.get("title")
                             or prepared.get("caption") or "")[:90],
                "description": str(prepared.get("caption") or "")[:4000],
                "privacy_level": str(opts.get("privacy_level") or _SELF_ONLY),
                "disable_comment": bool(opts.get("disable_comment")),
                "auto_add_music": bool(opts.get("auto_add_music")),
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "photo_images": list(prepared.get("photo_urls") or []),
                "photo_cover_index": cover,
            },
            "post_mode": "DIRECT_POST",
            "media_type": "PHOTO",
        }
        data = self._api(bearer, _CONTENT_INIT_URL, payload)
        if data is None:
            self._last_error = _E_API
            return {"ok": False, "error": _E_API}
        publish_id = str(data.get("publish_id") or "")
        if not publish_id:
            self._last_error = _E_API
            return {"ok": False, "error": _E_API}
        return self._await_publish(bearer, publish_id, prepared)

    def _upload_chunks(self, upload_url: str, prepared: Dict[str, Any]) -> bool:
        """Sequential Content-Range PUTs; the final chunk absorbs the
        remainder. False on any short read or transport/API failure."""
        total = int(prepared["video_size"])
        chunk = int(prepared["chunk_size"])
        count = int(prepared["total_chunk_count"])
        mime = str(prepared.get("options", {}).get("video_mime") or "video/mp4")
        try:
            with open(prepared["video_path"], "rb") as fh:
                for i in range(count):
                    start = i * chunk
                    end = total - 1 if i == count - 1 else start + chunk - 1
                    blob = fh.read(end - start + 1)
                    if len(blob) != end - start + 1:
                        return False
                    resp = _http("PUT", upload_url, headers={
                        "Content-Type": mime,
                        "Content-Length": str(len(blob)),
                        "Content-Range": f"bytes {start}-{end}/{total}",
                    }, body=blob, timeout=300)
                    if resp.get("error"):
                        return False
        except OSError:
            return False
        return True

    def _await_publish(self, bearer: str, publish_id: str,
                       prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Poll status/fetch until a terminal state. SEND_TO_USER_INBOX is a
        *successful* outcome — the honest private-draft rung (§4.12)."""
        try:
            interval = float(self._config.get("status_poll_interval_s", 2.0) or 0)
        except (TypeError, ValueError):
            interval = 2.0
        try:
            max_polls = max(1, int(self._config.get("status_poll_max", 30) or 30))
        except (TypeError, ValueError):
            max_polls = 30
        for attempt in range(max_polls):
            data = self._api(bearer, _STATUS_URL, {"publish_id": publish_id})
            if data is None:
                self._last_error = _E_API
                return {"ok": False, "error": _E_API,
                        "raw": {"publish_id": publish_id}}
            state = str(data.get("status") or "").upper()
            if state == "PUBLISH_COMPLETE":
                ids = data.get("publicaly_available_post_id")  # sic — API spelling
                vid = (str(ids[0]) if isinstance(ids, list) and ids
                       else str(publish_id))
                user = str(prepared.get("creator_username") or "")
                url = (f"https://www.tiktok.com/@{user}/video/{vid}" if user
                       else f"https://www.tiktok.com/video/{vid}")
                return {"ok": True, "post_url": url, "platform_post_id": vid,
                        "raw": {"publish_id": publish_id, "status": state}}
            if state == "SEND_TO_USER_INBOX":
                return {"ok": True, "post_url": _INBOX_URL,
                        "platform_post_id": publish_id, "inbox_draft": True,
                        "raw": {"publish_id": publish_id, "status": state}}
            if state == "FAILED":
                _LOG.debug("tiktok publish %s failed: %s", publish_id,
                           str(data.get("fail_reason"))[:200])
                self._last_error = _E_PUBLISH
                return {"ok": False, "error": _E_PUBLISH,
                        "raw": {"publish_id": publish_id, "status": state}}
            if attempt < max_polls - 1 and interval > 0:
                time.sleep(min(interval, 10.0))
        # ambiguous — the publisher's verify-before-retry probe owns this (§7.2)
        self._last_error = _E_TIMEOUT
        return {"ok": False, "error": _E_TIMEOUT,
                "raw": {"publish_id": publish_id}}

    # ── prepare internals ──────────────────────────────────────────────────────
    def _caption(self, prepared: Dict[str, Any], warnings: List[str]) -> str:
        """Body + any hashtags the composer left out of it, inside 2,200."""
        body = str(prepared.get("body") or "")
        tags = [str(t).strip().lstrip("#") for t in (prepared.get("hashtags") or [])]
        extra = [f"#{t}" for t in tags if t and f"#{t}".lower() not in body.lower()]
        caption = (body + ("\n" + " ".join(extra) if extra else "")).strip()
        if len(caption) > 2200:
            caption = caption[:2200]
            warnings.append("caption trimmed to 2,200 chars (hashtag overflow)")
        return caption

    def _photo_urls(self, prepared: Dict[str, Any]) -> List[str]:
        """Publicly reachable image URLs: explicit options.photo_urls first,
        then per-asset staged_url entries (§7.4 staging output)."""
        urls: List[str] = []
        for u in (prepared.get("options", {}).get("photo_urls") or []):
            if isinstance(u, str) and u.lower().startswith(("http://", "https://")):
                urls.append(u)
        for a in (prepared.get("assets") or []):
            if not isinstance(a, dict):
                continue
            u = a.get("staged_url")
            if isinstance(u, str) and u.lower().startswith(("http://", "https://")):
                urls.append(u)
        return urls

    def _video_file(self, prepared: Dict[str, Any]
                    ) -> Tuple[Optional[str], int, Optional[float]]:
        """First readable local video file among the adapted assets:
        (path, size_bytes, duration_s|None) — (None, 0, None) when absent."""
        for a in (prepared.get("assets") or []):
            if not isinstance(a, dict):
                continue
            src = a.get("source") if isinstance(a.get("source"), dict) else {}
            kind = a.get("kind") or src.get("kind")
            if kind and kind != "video":
                continue
            cand = (a.get("out_path") or a.get("path")
                    or a.get("filename") or src.get("filename"))
            if not cand:
                continue
            try:
                p = Path(str(cand))
                if p.is_file():
                    dur = a.get("duration_s") or src.get("duration_s")
                    dur = float(dur) if isinstance(dur, (int, float)) else None
                    return str(p), int(p.stat().st_size), dur
            except OSError:
                continue
        return None, 0, None

    # ── analytics path (Display API — §8.2 TikTok column) ────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        try:
            bearer = self._bearer()
            if not bearer or not platform_post_id:
                return None
            url = (_VIDEO_QUERY_URL + "?" + urlencode(
                {"fields": "id,like_count,comment_count,share_count,view_count"}))
            resp = _http("POST", url, headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json; charset=UTF-8",
            }, body=json.dumps(
                {"filters": {"video_ids": [str(platform_post_id)]}}).encode("utf-8"))
            data = _tt_data(resp)
            if not data:
                return None
            videos = data.get("videos")
            if not isinstance(videos, list) or not videos:
                return None
            v = videos[0] if isinstance(videos[0], dict) else {}
            out: Dict[str, Any] = {}
            # missing ≠ zero (§8.2): only map what the platform reported
            if v.get("view_count") is not None:
                out["impressions"] = int(v["view_count"])
                out["video_views"] = int(v["view_count"])
            for src_key, dst in (("like_count", "likes"),
                                 ("comment_count", "comments"),
                                 ("share_count", "shares")):
                if v.get(src_key) is not None:
                    out[dst] = int(v[src_key])
            return out or None
        except Exception:
            return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        try:
            bearer = self._bearer()
            if not bearer:
                return None
            user = self._user_info(
                bearer, "open_id,display_name,follower_count,likes_count,video_count")
            if not user:
                return None
            out: Dict[str, Any] = {}
            for src_key, dst in (("follower_count", "followers"),
                                 ("likes_count", "likes"),
                                 ("video_count", "posts")):
                if user.get(src_key) is not None:
                    out[dst] = int(user[src_key])
            return out or None
        except Exception:
            return None


ADAPTER = TikTokAdapter
