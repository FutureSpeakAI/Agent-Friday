"""
YouTube platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.6).

YouTube Data API v3 over Friday's existing Google OAuth plumbing
(``services/google_accounts.py``) — same encrypted token store, three extra
scopes (``youtube.upload``, ``youtube``, ``yt-analytics.readonly``). The
adapter never stores Google tokens itself: it stores a small *link blob*
(account id + granted scopes) via the base credential convention and pulls a
live credential from google_accounts at call time.

Highlights (§4.6):
  * ``videos.insert`` resumable upload — title ≤100 chars (no angle brackets),
    description ≤5,000 BYTES, tags ≤500 chars total, categoryId, madeForKids,
    privacyStatus.
  * Shorts are ordinary uploads (vertical, ≤3 min, ``#shorts``) — the "short"
    format only changes validation + tagging, not the endpoint.
  * Custom thumbnails via ``thumbnails.set`` (≤2 MB; phone-verified account).
  * NATIVE SCHEDULING: ``status.publishAt`` + ``privacyStatus: private`` when
    the slot is ≥10 min out; ``native_schedule: true`` is advertised and a
    scheduled publish can be cancelled (`cancel_scheduled`).
  * Quota trap: default project quota 10,000 units/day, one upload costs
    1,600 → 6 uploads/day local budget, surfaced in ``status()``.
  * Audit-state honesty: unaudited projects may have uploads locked private —
    ``status()`` says so instead of pretending.

ALL transport goes through the module-level ``_request`` seam and all Google
credential access through the module-level ``_google_credentials`` seam —
tests stub those two names; nothing else talks to the network.

Import-safe under FRIDAY_TESTING: stdlib only at import, no threads, no
network; google_accounts / credential_store are lazy-imported inside methods.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import PlatformAdapter, _now_utc, _parse_iso

_log = logging.getLogger("friday.platforms.youtube")

# ── scopes (added on top of google_accounts.GOOGLE_MULTI_SCOPES) ─────────────
YT_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YT_FULL_SCOPE = "https://www.googleapis.com/auth/youtube"
YT_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
YOUTUBE_SCOPES = [YT_UPLOAD_SCOPE, YT_FULL_SCOPE, YT_ANALYTICS_SCOPE]

# ── endpoints (design-time snapshot, §4.2 volatility warning) ─────────────────
_DATA_API = "https://www.googleapis.com/youtube/v3"
_UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2"

# ── hard platform limits (§4.6) ───────────────────────────────────────────────
TITLE_LIMIT = 100                    # chars, no angle brackets
DESC_LIMIT_BYTES = 5000              # BYTES, not chars
TAGS_LIMIT_CHARS = 500               # total across all tags
THUMB_MAX_BYTES = 2 * 1024 * 1024    # thumbnails.set cap
SHORT_MAX_S = 180                    # Shorts: ≤3 min vertical
NATIVE_SCHEDULE_MIN_LEAD_S = 600     # §6.5: publishAt only when ≥10 min out

# ── the quota trap (§4.6) ─────────────────────────────────────────────────────
DEFAULT_DAILY_QUOTA_UNITS = 10_000
QUOTA_UNITS_PER_UPLOAD = 1_600       # → 6 uploads/day out of the box

# ── resumable upload geometry (§4.6) ─────────────────────────────────────────
# Chunks stream from disk — the file is NEVER read whole into RAM (caps
# advertise 128 GB). Must stay a multiple of 256 KiB per the resumable
# protocol; 8 MiB keeps each PUT well inside its timeout on slow links.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
_RESUME_PROBE_MAX = 3                # session status queries per dropped chunk
_CHUNK_TIMEOUT_S = 600.0

_PRIVACY_STATUSES = ("public", "unlisted", "private")
_AUDIT_STATES = ("audited", "unaudited", "unknown")

# ── fixed content-free error strings (§12.5) ─────────────────────────────────
E_NOT_CONNECTED = "not_connected"
E_AUTH_EXPIRED = "auth_expired"
E_OAUTH_FAILED = "oauth_exchange_failed"
E_TITLE_MISSING = "title_missing"
E_TITLE_TOO_LONG = "title_too_long"
E_DESC_TOO_LONG = "description_too_long"
E_BAD_PRIVACY = "invalid_privacy_status"
E_VIDEO_MISSING = "video_asset_missing"
E_SHORT_TOO_LONG = "short_too_long"
E_UPLOAD_FAILED = "upload_failed"
E_PUBLISH_FAILED = "publish_failed"
E_DELETE_FAILED = "delete_failed"
E_UPDATE_FAILED = "update_failed"
E_QUOTA_DEFERRED = "quota_budget_exhausted"
E_AUTH_ERROR = "auth_error"
E_PERMISSION = "permission_denied"
E_RATE_LIMITED = "rate_limited"
E_SERVER_ERROR = "server_error"
E_NETWORK_ERROR = "network_error"

_VIDEO_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime",
               ".webm": "video/webm", ".avi": "video/x-msvideo",
               ".mkv": "video/x-matroska"}
_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


# ── module-level transport seam (tests stub THIS — nothing else talks HTTP) ──
def _request(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
             data: Optional[bytes] = None, timeout: float = 120.0) -> Dict[str, Any]:
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
        _log.warning("youtube transport error: %s: %s", type(e).__name__, e)
        return {"status": 0, "headers": {}, "body": b""}


# ── module-level Google credential seam (tests stub THIS) ────────────────────
def _google_credentials(account_id: Optional[str]):
    """A live (refreshed) Google credentials object for the linked account, or
    None. Delegates to google_accounts.credentials_for — the single encrypted
    token store. Never raises."""
    if not account_id:
        return None
    try:
        from agent_friday.services import google_accounts
        return google_accounts.credentials_for(account_id)
    except Exception as e:
        _log.warning("youtube credential fetch failed: %s", type(e).__name__)
        return None


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


def _committed_end(resp: Dict[str, Any]) -> int:
    """Last byte the resumable session confirms received, from a 308's
    ``Range: bytes=0-N`` header; -1 when the header is absent/unparseable
    (the protocol's way of saying 'nothing received — start at 0')."""
    rng = str(_header(resp, "Range") or "")
    if "-" not in rng:
        return -1
    try:
        return int(rng.rsplit("-", 1)[1].strip())
    except (TypeError, ValueError):
        return -1


def _iso_z(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _asset_source(asset: Dict[str, Any]) -> Dict[str, Any]:
    src = asset.get("source")
    return src if isinstance(src, dict) else {}


def _asset_kind(asset: Dict[str, Any]) -> str:
    return str(asset.get("kind") or _asset_source(asset).get("kind") or "image")


def _asset_path(asset: Dict[str, Any]) -> Optional[Path]:
    for k in ("out_path", "path", "filename"):
        v = asset.get(k) or _asset_source(asset).get(k)
        if not v:
            continue
        try:
            p = Path(str(v))
            if p.is_file():
                return p
        except Exception:
            continue
    return None


def _asset_meta(asset: Dict[str, Any], key: str):
    return asset.get(key) if asset.get(key) is not None else _asset_source(asset).get(key)


class YouTubeAdapter(PlatformAdapter):
    name = "youtube"
    label = "YouTube"
    auth_mode = "oauth2"
    automation_tier = "api"          # §4.14 declared rung (API, quota-bounded)
    default_daily_limit = DEFAULT_DAILY_QUOTA_UNITS // QUOTA_UNITS_PER_UPLOAD  # 6

    # ── capability declaration (§4.2 / §4.6) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({
            "formats": ["video_upload", "short"],
            "char_limit": DESC_LIMIT_BYTES,   # description; enforced in BYTES
            "title_limit": TITLE_LIMIT,
            "thread": False,
            "native_schedule": True,          # status.publishAt (§4.6 / §6.5)
            "native_delete": True,            # videos.delete
            "analytics": "full",              # Data API stats + Analytics API
            "hashtags_max": 15,
            "notes": [
                "description limit is 5,000 BYTES (utf-8), not characters",
                "title <=100 chars and may not contain angle brackets",
                "tags capped at 500 characters total",
                "Shorts are ordinary uploads: vertical, <=3 min, #shorts",
                "thumbnails need a phone-verified account and <=2 MB image",
                "quota trap: one upload costs 1,600 of 10,000 daily units -> "
                "6 uploads/day until a quota raise is granted",
                "unaudited API projects may have uploads locked private",
                "native scheduling via status.publishAt when >=10 min out; "
                "cancellable while still private",
            ],
        })
        caps["media"] = {
            # a single image asset is used as the custom thumbnail
            "images": {"max": 1, "formats": ["jpeg", "png"],
                       "max_bytes": THUMB_MAX_BYTES, "aspect": (1.7, 1.8)},
            "video": {"min_s": 1, "max_s": 12 * 3600, "formats": ["mp4", "mov", "webm"],
                      "max_bytes": 128 * 1024 * 1024 * 1024},
            "alt_text": False,
        }
        return caps

    # ── local quota budget (§4.6 — never trust ourselves to remember) ────────
    def _budget_limit(self) -> int:
        try:
            return int(self._config.get("daily_post_limit"))
        except (TypeError, ValueError):
            pass
        try:
            units = int(self._config.get("daily_quota_units"))
            if units > 0:
                return max(1, units // QUOTA_UNITS_PER_UPLOAD)
        except (TypeError, ValueError):
            pass
        return int(self.default_daily_limit)

    # ── auth lifecycle: link to a google_accounts identity ───────────────────
    def _extra_scopes(self) -> List[str]:
        return list(YOUTUBE_SCOPES)

    def connect_url(self, state: str) -> Optional[str]:
        """Google consent URL covering the standard multi-account scopes PLUS
        the YouTube trio. Best-effort: None when no OAuth client is configured
        (the Accounts tab then offers linking an existing Google account)."""
        try:
            from agent_friday.services import google_accounts as ga
            flow, _redirect, _kind = ga.build_auth_flow(state=state)
            merged = list(dict.fromkeys(
                list(getattr(ga, "GOOGLE_MULTI_SCOPES", [])) + self._extra_scopes()))
            try:
                flow.oauth2session.scope = merged
            except Exception:
                pass
            url, _ = flow.authorization_url(
                access_type="offline", include_granted_scopes="true", prompt="consent")
            self._audit("connect_url_issued")
            return url
        except Exception as e:
            _log.warning("youtube connect_url unavailable: %s", type(e).__name__)
            self._last_error = E_OAUTH_FAILED
            return None

    def _link_account(self, account_id: str, email: str,
                      scopes: List[str]) -> Dict[str, Any]:
        blob = {
            "account_id": account_id,
            "account": email or account_id,
            "scopes": list(scopes or []),
            "expires_at": None,           # google_accounts refreshes on demand
            "linked_at": _iso_z(_now_utc()),
        }
        res = self.save_credentials(blob)
        if res.get("ok"):
            self._last_error = None
            self._audit("account_linked", account_id=account_id)
        return res

    def handle_callback(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Two paths: link an EXISTING google_accounts identity
        (``{"account_id": ...}``) or finish a fresh OAuth code exchange
        (``{"code": ..., "state": ...}``) — the token lands in google_accounts'
        encrypted store either way; we keep only the link blob."""
        params = dict(params or {})
        try:
            from agent_friday.services import google_accounts as ga
            account_id = str(params.get("account_id") or "").strip()
            if account_id:
                rec = ga.get_account(account_id)
                if not rec:
                    self._last_error = E_NOT_CONNECTED
                    return self.status()
                self._link_account(account_id, rec.get("email") or "",
                                   list(rec.get("scopes") or []))
                return self.status()
            code = str(params.get("code") or "").strip()
            if not code:
                self._last_error = E_OAUTH_FAILED
                return self.status()
            flow, _redirect, _kind = ga.build_auth_flow(state=params.get("state"))
            merged = list(dict.fromkeys(
                list(getattr(ga, "GOOGLE_MULTI_SCOPES", [])) + self._extra_scopes()))
            try:
                flow.oauth2session.scope = merged
            except Exception:
                pass
            flow.fetch_token(code=code)
            creds = flow.credentials
            rec = ga.upsert_account(creds)
            self._link_account(rec.get("id") or "", rec.get("email") or "",
                               list(getattr(creds, "scopes", None) or merged))
            return self.status()
        except Exception as e:
            _log.warning("youtube callback failed: %s", type(e).__name__)
            self._last_error = E_OAUTH_FAILED
            return self.status()

    def refresh(self) -> bool:
        """True when the linked Google account can mint a live token now.
        Refresh itself is google_accounts' job (credentials_for)."""
        blob = self.load_credentials()
        if not blob:
            return False
        creds = _google_credentials(blob.get("account_id"))
        ok = bool(creds is not None and getattr(creds, "token", None))
        if not ok:
            self._last_error = E_AUTH_EXPIRED
        return ok

    def revoke(self) -> bool:
        """Unlink ONLY — the Google account is shared with Gmail/Calendar/Drive,
        so we never revoke it platform-side from here."""
        self.clear_credentials()
        self._audit("revoke", platform_side=False, note="unlink_only")
        return True

    def status(self) -> Dict[str, Any]:
        """Base facts + the quota budget + audit-state honesty (§4.6)."""
        out = super().status()
        try:
            b = self.rate_budget()
            limit = int(b.get("limit") or 0)
            used = int(b.get("used") or 0)
            out["quota"] = {
                "daily_quota_units": int(self._config.get("daily_quota_units")
                                         or DEFAULT_DAILY_QUOTA_UNITS),
                "units_per_upload": QUOTA_UNITS_PER_UPLOAD,
                "uploads_per_day": limit,
                "uploads_used_today": used,
                "uploads_remaining_today": max(0, limit - used),
                "reset_at": b.get("reset_at"),
            }
            audit_state = str(self._config.get("project_audit_state") or "unknown")
            if audit_state not in _AUDIT_STATES:
                audit_state = "unknown"
            out["audit_state"] = audit_state
            if audit_state != "audited":
                out["audit_note"] = ("YouTube API project not verified as audited: "
                                     "uploads may be locked private until the "
                                     "audit/verification form is completed")
            blob = self.load_credentials() if out.get("connected") else None
            if isinstance(blob, dict):
                scopes = list(blob.get("scopes") or [])
                if scopes and YT_UPLOAD_SCOPE not in scopes:
                    out["scope_warning"] = ("linked Google account was not granted "
                                            "youtube.upload — reconnect with the "
                                            "YouTube scopes")
        except Exception as e:
            out["last_error"] = str(e)
        return out

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Transport-free validation + snippet/status construction (§4.6)."""
        try:
            target = dict(target or {})
            post = dict(post or {})
            options = dict(target.get("options") or {})
            warnings: List[str] = []

            fmt = str(target.get("format") or "video_upload")
            if fmt not in ("video_upload", "short"):
                warnings.append(f"format '{fmt}' treated as video_upload")
                fmt = "video_upload"

            # title: <=100 chars, no angle brackets
            title = str(target.get("adapted_title") or post.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": E_TITLE_MISSING, "warnings": warnings}
            if "<" in title or ">" in title:
                title = title.replace("<", "").replace(">", "")
                warnings.append("angle brackets removed from title (YouTube forbids them)")
            if len(title) > TITLE_LIMIT:
                return {"ok": False, "error": E_TITLE_TOO_LONG, "warnings": warnings,
                        "detail": f"{len(title)}/{TITLE_LIMIT} chars"}

            # description: <=5000 BYTES (utf-8)
            description = str(target.get("adapted_body") or post.get("body") or "")

            # Shorts: ordinary vertical upload that carries #shorts
            if fmt == "short":
                blob = (title + "\n" + description).lower()
                if "#shorts" not in blob:
                    description = (description.rstrip() + "\n\n#shorts").strip()
                    warnings.append("#shorts appended to description")
            if len(description.encode("utf-8")) > DESC_LIMIT_BYTES:
                return {"ok": False, "error": E_DESC_TOO_LONG, "warnings": warnings,
                        "detail": f"{len(description.encode('utf-8'))}/"
                                  f"{DESC_LIMIT_BYTES} bytes"}

            # tags: from hashtags/options, total <=500 chars
            raw_tags = list(target.get("hashtags") or []) + list(options.get("tags") or [])
            tags: List[str] = []
            total = 0
            for t in raw_tags:
                t = str(t or "").strip().lstrip("#")
                if not t or t.lower() in (x.lower() for x in tags):
                    continue
                cost = len(t) + (2 if " " in t else 0)   # spaced tags count quotes
                if total + cost > TAGS_LIMIT_CHARS:
                    warnings.append("tags truncated at 500 total characters")
                    break
                tags.append(t)
                total += cost

            # privacy / kids / category
            privacy = str(options.get("privacy_status")
                          or self._config.get("default_privacy") or "public")
            if privacy not in _PRIVACY_STATUSES:
                return {"ok": False, "error": E_BAD_PRIVACY, "warnings": warnings}
            made_for_kids = bool(options.get("made_for_kids",
                                             self._config.get("default_made_for_kids",
                                                              False)))
            category_id = str(options.get("category_id")
                              or self._config.get("default_category_id") or "22")

            # native scheduling (§4.6 / §6.5): publishAt when >=10 min out
            publish_at_raw = (options.get("publish_at") or target.get("publish_at")
                              or target.get("scheduled_at"))
            publish_at = _parse_iso(publish_at_raw) if publish_at_raw else None
            native_scheduled = False
            status_obj: Dict[str, Any] = {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": made_for_kids,
            }
            if publish_at_raw and publish_at is None:
                warnings.append("publish_at unparsable — ignored")
            if publish_at is not None:
                lead = (publish_at - _now_utc()).total_seconds()
                if lead >= NATIVE_SCHEDULE_MIN_LEAD_S:
                    native_scheduled = True
                    status_obj["privacyStatus"] = "private"   # required with publishAt
                    status_obj["publishAt"] = _iso_z(publish_at)
                    if privacy != "public":
                        warnings.append("publishAt makes the video public at the "
                                        "scheduled instant")
                else:
                    warnings.append("publish_at under 10 min out — publisher "
                                    "handles timing locally (no native schedule)")

            if (str(self._config.get("project_audit_state") or "unknown") != "audited"
                    and status_obj["privacyStatus"] != "private"):
                warnings.append("API project not audited — YouTube may force this "
                                "upload private")

            # assets: exactly one video required; first image = thumbnail
            assets = [a for a in (target.get("adapted_assets") or post.get("assets") or [])
                      if isinstance(a, dict)]
            video = next((a for a in assets if _asset_kind(a) == "video"), None)
            if video is None or _asset_path(video) is None:
                return {"ok": False, "error": E_VIDEO_MISSING, "warnings": warnings}

            duration = _asset_meta(video, "duration_s")
            if fmt == "short":
                try:
                    if duration is not None and float(duration) > SHORT_MAX_S:
                        return {"ok": False, "error": E_SHORT_TOO_LONG,
                                "warnings": warnings}
                except (TypeError, ValueError):
                    pass
                w, h = _asset_meta(video, "width"), _asset_meta(video, "height")
                try:
                    if w is not None and h is not None and int(w) > int(h):
                        warnings.append("short is not vertical — use the "
                                        "mp4-vertical-9x16 transform")
                except (TypeError, ValueError):
                    pass

            thumbnail = next((a for a in assets if _asset_kind(a) == "image"), None)
            if thumbnail is not None:
                tp = _asset_path(thumbnail)
                if tp is None:
                    warnings.append("thumbnail file missing — skipped")
                    thumbnail = None
                elif tp.suffix.lower() not in _IMAGE_MIME:
                    warnings.append("thumbnail must be jpeg/png — skipped")
                    thumbnail = None
                elif tp.stat().st_size > THUMB_MAX_BYTES:
                    warnings.append("thumbnail over 2 MB — skipped")
                    thumbnail = None

            prepared = {
                "platform": self.name,
                "target_id": target.get("id"),
                "post_id": post.get("id"),
                "format": fmt,
                "title": title,
                "body": description,
                "segments": [],
                "assets": assets,
                "hashtags": tags,
                "mentions": [],
                "options": options,
                "video_asset": video,
                "thumbnail_asset": thumbnail,
                "snippet": {"title": title, "description": description,
                            "tags": tags, "categoryId": category_id},
                "status": status_obj,
                "native_scheduled": native_scheduled,
            }
            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e), "warnings": []}

    # ── shared HTTP plumbing ──────────────────────────────────────────────────
    def _auth_token(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """(bearer_token, link_blob) or None; fixed-string reason in _last_error."""
        blob = self.load_credentials()
        if not blob or not blob.get("account_id"):
            self._last_error = E_NOT_CONNECTED
            return None
        creds = _google_credentials(blob.get("account_id"))
        token = getattr(creds, "token", None) if creds is not None else None  # pragma: allowlist secret
        if not token:
            self._last_error = E_AUTH_EXPIRED
            return None
        return str(token), blob

    def _upload_video_chunks(self, session_url: str, vpath: Path, size: int,
                             mime: str, token: str) -> Dict[str, Any]:
        """The actual §4.6 resumable upload: the file is STREAMED from disk in
        ``UPLOAD_CHUNK_BYTES`` slices (never read whole into RAM — caps allow
        128 GB) with ``Content-Range`` headers. A 308 advances the offset to
        the server's committed byte; a dropped chunk (status 0) queries the
        session (``bytes */total``) and resumes from the committed offset
        instead of restarting at byte 0.

        Returns the final transport response. A synthetic ``{"status": 0,
        "_ambiguous": True}`` means the FINAL bytes left but no definitive
        answer came back — the upload may have completed server-side, so the
        caller must flag §7.2 ambiguity (verify-before-retry, never a blind
        re-send)."""
        offset = 0
        final_sent = False
        stalls = 0
        try:
            fh = vpath.open("rb")
        except Exception:
            return {"status": 0, "headers": {}, "body": b""}

        def _bail() -> Dict[str, Any]:
            out: Dict[str, Any] = {"status": 0, "headers": {}, "body": b""}
            if final_sent:
                out["_ambiguous"] = True
            return out

        with fh:
            while offset < size:
                try:
                    fh.seek(offset)
                    chunk = fh.read(UPLOAD_CHUNK_BYTES)
                except Exception:
                    return _bail()
                if not chunk:
                    return _bail()      # file shrank under us — no clean answer
                end = offset + len(chunk) - 1
                if end + 1 >= size:
                    final_sent = True
                resp = _request(
                    "PUT", session_url,
                    headers={"Authorization": "Bearer " + token,
                             "Content-Type": mime,
                             "Content-Length": str(len(chunk)),
                             "Content-Range": f"bytes {offset}-{end}/{size}"},
                    data=chunk, timeout=_CHUNK_TIMEOUT_S)
                st = int(resp.get("status") or 0)
                if st in (200, 201):
                    return resp
                if st == 308:                      # incomplete — keep going
                    nxt = _committed_end(resp) + 1
                    stalls = stalls + 1 if nxt <= offset else 0
                    if stalls > _RESUME_PROBE_MAX:
                        return _bail()             # session refuses to advance
                    offset = max(0, nxt)
                    continue
                if st == 0:
                    # transport died mid-chunk — ask the session where it
                    # stands rather than re-uploading from byte 0.
                    probed = False
                    for _ in range(_RESUME_PROBE_MAX):
                        q = _request(
                            "PUT", session_url,
                            headers={"Authorization": "Bearer " + token,
                                     "Content-Length": "0",
                                     "Content-Range": f"bytes */{size}"},
                            data=b"")
                        qst = int(q.get("status") or 0)
                        if qst in (200, 201):
                            return q               # it DID land server-side
                        if qst == 308:             # definitive: not complete
                            offset = max(0, _committed_end(q) + 1)
                            probed = True
                            break
                        if qst != 0:
                            return q               # real platform error
                    if probed:
                        final_sent = False         # provably incomplete
                        continue
                    return _bail()                 # no answer at all
                return resp                        # 4xx/5xx → caller classifies
        return _bail()

    def verify_recent_post(self, prepared: Dict[str, Any],
                           window_s: float = 6 * 3600) -> Optional[Dict[str, Any]]:
        """§7.2 verify-before-retry probe: search MY recent uploads for the
        prepared title. A title match uploaded inside the window → its ids
        (the publisher confirms without re-sending); None → nothing found /
        cannot tell. Never raises; costs search quota (~100 units) but never
        an upload slot."""
        try:
            prepared = dict(prepared or {})
            title = str(((prepared.get("snippet") or {}).get("title"))
                        or prepared.get("title")
                        or prepared.get("adapted_title") or "").strip()
            if not title:
                return None
            auth = self._auth_token()
            if auth is None:
                return None
            token, _blob = auth
            from urllib.parse import quote
            resp = _request(
                "GET",
                _DATA_API + "/search?part=snippet&forMine=true&type=video"
                            "&order=date&maxResults=10&q=" + quote(title),
                headers={"Authorization": "Bearer " + token})
            if int(resp.get("status") or 0) != 200:
                return None
            import html as _html
            want = _html.unescape(title).strip().lower()
            now = _now_utc()
            for item in _json_body(resp).get("items") or []:
                vid = str(((item or {}).get("id") or {}).get("videoId") or "")
                snip = (item or {}).get("snippet") or {}
                got = _html.unescape(str(snip.get("title") or "")).strip().lower()
                if not vid or got != want:
                    continue
                dt = _parse_iso(snip.get("publishedAt"))
                if dt is None:
                    continue
                age = (now - dt).total_seconds()
                if age > float(window_s) or age < -600:
                    continue           # an OLD same-titled video must not confirm
                self._audit("verify_recent_post", video_id=vid)
                return {"platform_post_id": vid,
                        "post_url": "https://www.youtube.com/watch?v=" + vid}
            return None
        except Exception as e:
            _log.warning("youtube verify_recent_post failed: %s",
                         type(e).__name__)
            return None

    def _error_envelope(self, resp: Dict[str, Any], fallback: str) -> Dict[str, Any]:
        """HTTP status → fixed content-free string (§12.5); platform bodies
        never leave the local log."""
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
        _log.warning("youtube http %s -> %s", st, err)
        return {"ok": False, "error": err, "status_code": st}

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Resumable ``videos.insert`` (+ best-effort ``thumbnails.set``).

        Order: local quota budget (defer, don't burn) → auth → upload."""
        try:
            prepared = dict(prepared or {})

            # §4.6 quota budget: the publisher defers, we never burn an attempt
            if self.budget_would_exceed(1):
                b = self.rate_budget()
                self._last_error = E_QUOTA_DEFERRED
                return {"ok": False, "error": E_QUOTA_DEFERRED, "deferred": True,
                        "retry_at": b.get("reset_at"), "budget": b}

            auth = self._auth_token()
            if auth is None:
                return {"ok": False, "error": self._last_error or E_NOT_CONNECTED}
            token, _blob = auth

            snippet = dict(prepared.get("snippet") or {})
            if not snippet:
                snippet = {"title": str(prepared.get("title") or "")[:TITLE_LIMIT],
                           "description": str(prepared.get("body") or ""),
                           "tags": list(prepared.get("hashtags") or []),
                           "categoryId": "22"}
            status_obj = dict(prepared.get("status") or {"privacyStatus": "public",
                                                         "selfDeclaredMadeForKids": False})

            video = prepared.get("video_asset")
            vpath = _asset_path(video) if isinstance(video, dict) else None
            if vpath is None:
                self._last_error = E_VIDEO_MISSING
                return {"ok": False, "error": E_VIDEO_MISSING}
            try:
                size = int(vpath.stat().st_size)
            except Exception:
                size = 0
            if size <= 0:
                self._last_error = E_VIDEO_MISSING
                return {"ok": False, "error": E_VIDEO_MISSING}
            mime = _VIDEO_MIME.get(vpath.suffix.lower(), "video/mp4")

            # 1) initiate the resumable session
            meta = json.dumps({"snippet": snippet, "status": status_obj}).encode("utf-8")
            init = _request(
                "POST",
                _UPLOAD_API + "/videos?uploadType=resumable&part=snippet,status",
                headers={"Authorization": "Bearer " + token,
                         "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Type": mime,
                         "X-Upload-Content-Length": str(size)},
                data=meta)
            if int(init.get("status") or 0) != 200:
                return self._error_envelope(init, E_UPLOAD_FAILED)
            session_url = _header(init, "Location")
            if not session_url:
                self._last_error = E_UPLOAD_FAILED
                return {"ok": False, "error": E_UPLOAD_FAILED}

            # 2) stream the bytes in chunks with 308 resume handling (§4.6)
            up = self._upload_video_chunks(session_url, vpath, size, mime, token)
            if int(up.get("status") or 0) not in (200, 201):
                env = self._error_envelope(up, E_UPLOAD_FAILED)
                if up.get("_ambiguous"):
                    # The final bytes left and the outcome is unknowable —
                    # §7.2: the publisher must verify-before-retry, never
                    # blind-resend (a second session = a second public video).
                    env["ambiguous"] = True
                return env
            resource = _json_body(up)
            video_id = str(resource.get("id") or "")
            if not video_id:
                self._last_error = E_PUBLISH_FAILED
                return {"ok": False, "error": E_PUBLISH_FAILED}

            warnings: List[str] = []

            # 3) best-effort custom thumbnail (never fails the publish)
            thumb = prepared.get("thumbnail_asset")
            tpath = _asset_path(thumb) if isinstance(thumb, dict) else None
            if tpath is not None:
                try:
                    tbytes = tpath.read_bytes()
                    tmime = _IMAGE_MIME.get(tpath.suffix.lower(), "image/jpeg")
                    tresp = _request(
                        "POST",
                        _UPLOAD_API + "/thumbnails/set?videoId=" + video_id,
                        headers={"Authorization": "Bearer " + token,
                                 "Content-Type": tmime},
                        data=tbytes)
                    if int(tresp.get("status") or 0) not in (200, 201):
                        warnings.append("thumbnail_set_failed")
                        _log.warning("youtube thumbnails.set http %s",
                                     tresp.get("status"))
                except Exception:
                    warnings.append("thumbnail_set_failed")

            self.consume_budget(1)
            self._last_error = None
            self._audit("publish", video_id=video_id,
                        native_scheduled=bool(status_obj.get("publishAt")))
            out = {
                "ok": True,
                "post_url": "https://www.youtube.com/watch?v=" + video_id,
                "platform_post_id": video_id,
                "raw": {"id": video_id, "status": resource.get("status") or status_obj},
                "native_scheduled": bool(status_obj.get("publishAt")),
                "warnings": warnings,
            }
            if status_obj.get("publishAt"):
                out["publish_at"] = status_obj["publishAt"]
            return out
        except Exception as e:
            self._last_error = str(e)
            _log.warning("youtube publish failed: %s: %s", type(e).__name__, e)
            return {"ok": False, "error": E_PUBLISH_FAILED}

    def cancel_scheduled(self, platform_post_id: str) -> Dict[str, Any]:
        """Cancel a native ``publishAt`` while the video is still private:
        ``videos.update`` pins it back to plain private with no publishAt."""
        try:
            auth = self._auth_token()
            if auth is None:
                return {"ok": False, "error": self._last_error or E_NOT_CONNECTED}
            token, _blob = auth
            body = json.dumps({"id": str(platform_post_id),
                               "status": {"privacyStatus": "private"}}).encode("utf-8")
            resp = _request("PUT", _DATA_API + "/videos?part=status",
                            headers={"Authorization": "Bearer " + token,
                                     "Content-Type": "application/json; charset=UTF-8"},
                            data=body)
            if int(resp.get("status") or 0) not in (200, 204):
                return self._error_envelope(resp, E_UPDATE_FAILED)
            self._audit("cancel_scheduled", video_id=str(platform_post_id))
            return {"ok": True, "platform_post_id": str(platform_post_id),
                    "privacy_status": "private", "native_scheduled": False}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": E_UPDATE_FAILED}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        """videos.delete — best-effort takedown."""
        try:
            auth = self._auth_token()
            if auth is None:
                return {"ok": False, "error": self._last_error or E_NOT_CONNECTED}
            token, _blob = auth
            resp = _request("DELETE",
                            _DATA_API + "/videos?id=" + str(platform_post_id),
                            headers={"Authorization": "Bearer " + token})
            if int(resp.get("status") or 0) in (200, 204):
                self._audit("delete", video_id=str(platform_post_id))
                return {"ok": True, "deleted": True}
            return self._error_envelope(resp, E_DELETE_FAILED)
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": E_DELETE_FAILED}

    # ── analytics path (§4.6 — best in class) ─────────────────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        """Data API ``statistics`` snapshot + best-effort Analytics API series,
        mapped to the unified keys. NOTE: YouTube exposes *views*, not true
        impressions — ``impressions`` mirrors views and is flagged as such."""
        try:
            auth = self._auth_token()
            if auth is None:
                return None
            token, _blob = auth
            vid = str(platform_post_id)
            resp = _request("GET",
                            _DATA_API + "/videos?part=statistics&id=" + vid,
                            headers={"Authorization": "Bearer " + token})
            if int(resp.get("status") or 0) != 200:
                self._error_envelope(resp, E_NETWORK_ERROR)
                return None
            items = _json_body(resp).get("items") or []
            if not items:
                return None
            stats = (items[0] or {}).get("statistics") or {}

            def _n(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 0

            views = _n(stats.get("viewCount"))
            metrics: Dict[str, Any] = {
                "views": views,
                "impressions": views,           # † views proxy — see flag below
                "impressions_are_views": True,  # honesty flag for the UI
                "likes": _n(stats.get("likeCount")),
                "comments": _n(stats.get("commentCount")),
                "fetched_at": _iso_z(_now_utc()),
                "source": "youtube_data",
            }

            # Analytics API (watch time, avg duration, subs gained) — best effort
            try:
                today = _iso_z(_now_utc())[:10]
                q = ("/reports?ids=channel%3D%3DMINE&startDate=2000-01-01"
                     "&endDate=" + today +
                     "&metrics=views,estimatedMinutesWatched,averageViewDuration,"
                     "subscribersGained&filters=video%3D%3D" + vid)
                aresp = _request("GET", _ANALYTICS_API + q,
                                 headers={"Authorization": "Bearer " + token})
                if int(aresp.get("status") or 0) == 200:
                    data = _json_body(aresp)
                    cols = [str((c or {}).get("name") or "")
                            for c in data.get("columnHeaders") or []]
                    rows = data.get("rows") or []
                    if cols and rows and isinstance(rows[0], list):
                        row = dict(zip(cols, rows[0]))
                        if "estimatedMinutesWatched" in row:
                            metrics["watch_time_s"] = int(
                                float(row["estimatedMinutesWatched"]) * 60)
                        if "averageViewDuration" in row:
                            metrics["avg_view_duration_s"] = int(
                                float(row["averageViewDuration"]))
                        if "subscribersGained" in row:
                            metrics["follows_gained"] = int(
                                float(row["subscribersGained"]))
                        metrics["source"] = "youtube_data+analytics"
            except Exception:
                _log.debug("youtube analytics fetch skipped", exc_info=True)

            return metrics
        except Exception as e:
            self._last_error = str(e)
            return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        """Channel-level counters: subscribers → followers."""
        try:
            auth = self._auth_token()
            if auth is None:
                return None
            token, _blob = auth
            resp = _request("GET",
                            _DATA_API + "/channels?part=statistics&mine=true",
                            headers={"Authorization": "Bearer " + token})
            if int(resp.get("status") or 0) != 200:
                self._error_envelope(resp, E_NETWORK_ERROR)
                return None
            items = _json_body(resp).get("items") or []
            if not items:
                return None
            stats = (items[0] or {}).get("statistics") or {}

            def _n(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 0

            return {"followers": _n(stats.get("subscriberCount")),
                    "views": _n(stats.get("viewCount")),
                    "posts": _n(stats.get("videoCount")),
                    "fetched_at": _iso_z(_now_utc())}
        except Exception as e:
            self._last_error = str(e)
            return None


ADAPTER = YouTubeAdapter
