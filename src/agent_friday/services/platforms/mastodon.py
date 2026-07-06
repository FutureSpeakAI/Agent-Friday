"""
Mastodon platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.8).

Plain REST per instance — the adapter is **instance-aware**: the base URL is
configuration (``instance``), and limits are discovered live from
``GET /api/v2/instance`` (``configuration.statuses.max_characters`` etc.)
instead of hard-coding 500. Auth is OAuth2 authorization-code against the
instance (app auto-registered via ``POST /api/v1/apps``, PKCE included —
harmless on older instances, honored on 4.3+) with loopback redirect
``http://localhost:3000/api/content/platforms/mastodon/callback``, or a pasted
personal access token (provider key ``platform_mastodon``) for the
paste-from-preferences path.

The sharp constraints this adapter owns:
  * **Async media** — ``POST /api/v2/media`` may answer 202; the attachment id
    is then polled at ``GET /api/v1/media/{id}`` (206 = still processing)
    until processed, bounded.
  * **Idempotency-Key** — every status create carries an ``Idempotency-Key``
    derived from the target id (+ content fingerprint, so an edited retry
    mints a new key) — free double-post insurance (§7.2).
  * **Native scheduling** — ``scheduled_at`` ≥5 min ahead → server-side
    scheduled status; the adapter advertises ``native_schedule: true``.
    Threads cannot be natively scheduled (replies need live parent ids) —
    that is surfaced honestly, never silently downgraded (§4.14).
  * **Sensitivity mapping** — the ``nsfw`` tag maps to the ``sensitive`` flag,
    ``spoiler_text`` carries the content warning; both apply to every thread
    segment. ``spoiler_text`` counts against the character limit.
  * **Counts analytics** — favourites/reblogs/replies from the status entity;
    no impressions, and missing ≠ zero (§8.2).

Transport: every HTTP byte goes through the module-level ``_http()``
chokepoint so tests stub it — the adapter itself never opens a socket.
Adapter errors are externalized as fixed content-free strings (§12.5);
full detail goes to the local debug logger only, never to envelopes or
``status()``. Nothing tokenish is ever logged.

Import-safe under FRIDAY_TESTING: stdlib only at import time, no threads
started, no network; credential_store is lazy-imported by the base class.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from agent_friday.services.platforms import base as pbase
from agent_friday.services.platforms.base import PlatformAdapter

_log = logging.getLogger("friday.platforms.mastodon")

DEFAULT_REDIRECT_URI = "http://localhost:3000/api/content/platforms/mastodon/callback"
SCOPES = "write:statuses write:media read:statuses"
APP_NAME = "Agent Friday"

# §4.8 conservative fallbacks — used ONLY until /api/v2/instance answers;
# the whole point of instance discovery is to never trust these as truth.
DEFAULT_CHAR_LIMIT = 500
DEFAULT_MAX_MEDIA = 4
DEFAULT_IMAGE_BYTES = 16 * 1024 * 1024
DEFAULT_VIDEO_BYTES = 99 * 1024 * 1024

VISIBILITIES = ("public", "unlisted", "private", "direct")

_STATE_TTL_S = 600                    # pending OAuth state is single-use + 10 min
_INSTANCE_TTL_S = 6 * 3600            # discovered instance config cache
_INSTANCE_FAIL_TTL_S = 300            # don't hammer a dead instance
_MEDIA_POLL_TRIES = 10                # bounded async-media poll (§4.8)
_NATIVE_SCHEDULE_MIN_LEAD_S = 300     # scheduled_at must be ≥5 min ahead (§4.8)

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
E_CONFIG = "missing_instance_config"
E_INTERNAL = "adapter_error"
E_SCHED_THREAD = "native_schedule_thread_unsupported"


# ── transport chokepoint — tests monkeypatch THIS ────────────────────────────
def _http(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
          body: Optional[bytes] = None, timeout: int = 20) -> Dict[str, Any]:
    """The single wire for every Mastodon API call. Returns
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


# ── multipart/form-data builder (media upload rides urllib, no requests) ─────
def _multipart(fields: Dict[str, str], filename: str, file_bytes: bytes,
               file_mime: str) -> Tuple[bytes, str]:
    boundary = "friday-" + secrets.token_hex(16)
    lines: List[bytes] = []
    for k, v in fields.items():
        lines += [f"--{boundary}".encode("ascii"),
                  f'Content-Disposition: form-data; name="{k}"'.encode("ascii"),
                  b"", str(v).encode("utf-8")]
    safe_name = str(filename).replace('"', "_") or "upload"
    lines += [f"--{boundary}".encode("ascii"),
              ('Content-Disposition: form-data; name="file"; '
               f'filename="{safe_name}"').encode("utf-8"),
              f"Content-Type: {file_mime}".encode("ascii"), b"", file_bytes]
    lines += [f"--{boundary}--".encode("ascii"), b""]
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"


# ── length metric: match the composer exactly (grapheme clusters) ────────────
def _m_len(text: Any) -> int:
    """One length metric across the pipeline: the composer's count_chars
    (grapheme clusters — family emoji = 1) is the source of truth, so a body
    the composer trimmed to the limit can never be rejected here over a
    counting disagreement. Falls back to code points if unimportable."""
    s = str(text or "")
    try:
        from agent_friday.services.content_composer import count_chars
        return int(count_chars(s, "mastodon"))
    except Exception:
        return len(s)


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


# ── thread progress persistence (per-segment resume, §7.2) ───────────────────
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_MAX_ENTRIES = 64


def _progress_path():
    return pbase.PLATFORMS_DIR / "mastodon_thread_progress.json"


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


class MastodonAdapter(PlatformAdapter):
    name = "mastodon"
    label = "Mastodon"
    auth_mode = "oauth2"             # token-paste alternative via provider key
    automation_tier = "api"          # open protocol — full API rung
    # default_daily_limit inherited (25/day): instance limits are generous
    # (~300 req/5 min), so the local budget is a courtesy cap, configurable.

    def __init__(self) -> None:
        super().__init__()
        self._instance_cache: Dict[str, Any] = {}

    # ── config helpers ────────────────────────────────────────────────────────
    def _base_url(self) -> Optional[str]:
        """Instance base URL — configuration first (§4.8), stored blob second
        (so a token-paste user configures it once and OAuth remembers it)."""
        v = self._config.get("instance") or self._config.get("base_url")
        if not v:
            try:
                creds = self.load_credentials() or {}
                v = creds.get("instance")
            except Exception:
                v = None
        s = str(v or "").strip().rstrip("/")
        if not s:
            return None
        if not s.startswith("http://") and not s.startswith("https://"):
            s = "https://" + s
        return s

    def _host(self) -> Optional[str]:
        base = self._base_url()
        if not base:
            return None
        return base.split("://", 1)[-1]

    def _redirect_uri(self) -> str:
        v = self._config.get("redirect_uri")
        return str(v) if v else DEFAULT_REDIRECT_URI

    def _bearer(self) -> Optional[str]:
        """Live access token: OAuth blob first, pasted personal token second.
        Mastodon tokens do not expire — no refresh dance."""
        try:
            creds = self.load_credentials()
            if isinstance(creds, dict) and creds.get("access_token"):
                return str(creds["access_token"])
        except Exception:
            pass
        val = self.simple_secret()
        return str(val) if val else None

    # ── instance discovery (§4.8 — never hard-code 500) ─────────────────────
    def _instance_info(self) -> Optional[Dict[str, Any]]:
        """Cached ``GET /api/v2/instance`` payload, or None (not configured /
        not reachable). No transport at all when no instance is configured."""
        base = self._base_url()
        if not base:
            return None
        cache = self._instance_cache
        now = time.time()
        if cache.get("base") == base:
            ttl = _INSTANCE_TTL_S if cache.get("data") is not None else _INSTANCE_FAIL_TTL_S
            if now - float(cache.get("at") or 0) < ttl:
                return cache.get("data")
        r = _http("GET", f"{base}/api/v2/instance")
        data = r.get("json") if r.get("status") == 200 else None
        self._instance_cache = {"base": base, "at": now,
                                "data": data if isinstance(data, dict) else None}
        return self._instance_cache["data"]

    def _instance_limits(self) -> Dict[str, Any]:
        """Discovered limits with honest fallbacks + a 'discovered' flag."""
        info = self._instance_info()
        conf = (info or {}).get("configuration") or {}
        statuses = conf.get("statuses") or {}
        media = conf.get("media_attachments") or {}

        def _pos_int(v, fallback):
            return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 \
                else fallback

        return {
            "discovered": info is not None,
            "char_limit": _pos_int(statuses.get("max_characters"), DEFAULT_CHAR_LIMIT),
            "max_media": _pos_int(statuses.get("max_media_attachments"), DEFAULT_MAX_MEDIA),
            "image_bytes": _pos_int(media.get("image_size_limit"), DEFAULT_IMAGE_BYTES),
            "video_bytes": _pos_int(media.get("video_size_limit"), DEFAULT_VIDEO_BYTES),
        }

    # ── capability declaration (§4.2 / §4.8) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        lim = self._instance_limits()
        host = self._host()
        if lim["discovered"]:
            source_note = f"limits discovered live from {host}/api/v2/instance"
        elif host:
            source_note = (f"instance {host} not reachable yet — conservative "
                           "defaults (500 chars, 4 media) until discovery")
        else:
            source_note = ("no instance configured — set 'instance' in platform "
                           "options; defaults shown are placeholders")
        caps.update({
            "formats": ["post", "thread", "image_post"],
            "char_limit": lim["char_limit"],
            "title_limit": None,
            "thread": True,
            "native_schedule": True,      # scheduled_at ≥5 min (§4.8)
            "native_delete": True,
            "analytics": "counts",        # favourites/reblogs/replies only
            "hashtags_max": None,         # no API cap; norms (0-3) are composer's
            "notes": [
                source_note,
                "native scheduled_at needs ≥5 min lead; threads cannot be "
                "natively scheduled",
                "'nsfw' tag maps to the sensitive flag; spoiler_text carries "
                "the content warning and counts against the character limit",
                "Idempotency-Key derived from the target id — double-post "
                "insurance on retries",
                "auth: OAuth (auto app registration) or paste a personal "
                "access token from instance preferences",
            ],
        })
        caps["media"] = {
            "images": {"max": lim["max_media"],
                       "formats": ["png", "jpeg", "gif", "webp"],
                       "max_bytes": lim["image_bytes"], "aspect": (0.2, 5.0)},
            "video": {"max_s": None, "formats": ["mp4", "webm", "mov"],
                      "max_bytes": lim["video_bytes"]},
            "alt_text": True,
        }
        return caps

    # ── auth lifecycle (OAuth2 code flow + PKCE, state bound) ────────────────
    def _ensure_app(self, base: str) -> Optional[Dict[str, Any]]:
        """App registration is per-instance (§4.8): reuse the stored client
        pair when it matches this instance, else POST /api/v1/apps once."""
        try:
            blob = self.load_credentials() or {}
            app = blob.get("app")
            if (isinstance(app, dict) and app.get("client_id")
                    and app.get("instance") == base):
                return app
            r = _http("POST", f"{base}/api/v1/apps",
                      headers={"Content-Type": "application/json"},
                      body=json.dumps({
                          "client_name": APP_NAME,
                          "redirect_uris": self._redirect_uri(),
                          "scopes": SCOPES,
                      }).encode("utf-8"))
            j = r.get("json") or {}
            if r.get("status") not in (200, 201) or not j.get("client_id"):
                self._classify(r)
                return None
            app = {"client_id": str(j.get("client_id")),
                   "client_secret": str(j.get("client_secret") or ""),
                   "instance": base}
            blob["app"] = app
            self.save_credentials(blob)
            self._audit("app_registered")
            return app
        except Exception:
            _log.debug("app registration failed", exc_info=True)
            self._last_error = E_INTERNAL
            return None

    def connect_url(self, state: str) -> Optional[str]:
        try:
            base = self._base_url()
            if not base:
                self._last_error = E_CONFIG
                return None
            if not state:
                self._last_error = E_STATE
                return None
            app = self._ensure_app(base)
            if not app:
                return None
            verifier = secrets.token_urlsafe(48)
            import base64 as _b64
            challenge = _b64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            ).decode("ascii").rstrip("=")
            # pending state rides the encrypted blob — single-use, 10 min TTL
            blob = self.load_credentials() or {}
            blob["pending_auth"] = {
                "state": str(state), "verifier": verifier, "instance": base,
                "created_at": pbase._now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            self.save_credentials(blob)
            q = urlencode({
                "response_type": "code", "client_id": app["client_id"],
                "redirect_uri": self._redirect_uri(), "scope": SCOPES,
                "state": str(state), "code_challenge": challenge,
                "code_challenge_method": "S256",
            })
            self._audit("connect_started")
            return f"{base}/oauth/authorize?{q}"
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
            base = str(pend.get("instance") or self._base_url() or "")
            app = (blob.get("app") if isinstance(blob.get("app"), dict) else {}) or {}
            code = str(params.get("code") or "")
            if not code or not base or not app.get("client_id"):
                self._last_error = E_CONFIG
                return self.status()
            r = _http("POST", f"{base}/oauth/token",
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      body=urlencode({
                          "grant_type": "authorization_code", "code": code,
                          "client_id": app.get("client_id"),
                          "client_secret": app.get("client_secret") or "",
                          "redirect_uri": self._redirect_uri(),
                          "code_verifier": str(pend.get("verifier") or ""),
                          "scope": SCOPES,
                      }).encode("utf-8"))
            j = r.get("json") or {}
            granted = j.get("access_token")
            if r.get("status") != 200 or not granted:
                self._classify(r)
                self._last_error = E_AUTH
                self._audit("callback_exchange_failed")
                return self.status()
            now = pbase._now_utc()
            creds: Dict[str, Any] = {
                "access_token": granted,
                "scopes": str(j.get("scope") or SCOPES).split(),
                "account": None,
                "instance": base,
                "app": app,
                "expires_at": None,          # Mastodon tokens do not expire
                "connected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            # account identity is best-effort — verify_credentials wants a
            # read:accounts/profile scope the spec's minimal set omits (§12.2)
            me = _http("GET", f"{base}/api/v1/accounts/verify_credentials",
                       headers={"Authorization": f"Bearer {granted}"})
            if me.get("status") == 200:
                mj = me.get("json") or {}
                acct = mj.get("acct") or mj.get("username")
                if acct:
                    creds["account"] = f"@{acct}@{base.split('://', 1)[-1]}" \
                        if "@" not in str(acct) else f"@{acct}"
            self.save_credentials(creds)
            self._last_error = None
            self._audit("connected", account_resolved=bool(creds["account"]))
            return self.status()
        except Exception:
            _log.debug("handle_callback failed", exc_info=True)
            self._last_error = E_INTERNAL
            return self.status()

    def refresh(self) -> bool:
        # Mastodon access tokens do not expire — usable iff one exists.
        return bool(self._bearer())

    def revoke(self) -> bool:
        try:
            creds = self.load_credentials() or {}
            platform_side = False
            bearer = creds.get("access_token")
            app = creds.get("app") if isinstance(creds.get("app"), dict) else {}
            base = str(creds.get("instance") or self._base_url() or "")
            if bearer and base and app.get("client_id"):
                r = _http("POST", f"{base}/oauth/revoke",
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          body=urlencode({
                              "client_id": app.get("client_id"),
                              "client_secret": app.get("client_secret") or "",
                              "token": str(bearer),
                          }).encode("utf-8"))
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
            oauth_connected = bool(creds.get("access_token"))
            pasted = bool(self.simple_secret())
            # a pending flow / registered app alone is NOT a connection
            st["connected"] = oauth_connected or pasted
            st["instance"] = self._host()
            st["auth_source"] = ("oauth" if oauth_connected
                                 else "token_paste" if pasted else None)
        except Exception:
            pass
        return st

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
        _log.debug("mastodon API %s: %s", status, (r.get("text") or "")[:500])
        return out

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Instance-aware hard validation (limits from discovery, spoiler_text
        counts against the limit), nsfw→sensitive/CW mapping, and async media
        upload. {ok, prepared, warnings} — never raises."""
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
            opts = dict(target.get("options") or {})

            # §4.8 sensitivity mapping: nsfw tag → sensitive flag; the CW rides
            # spoiler_text (composer decides its wording, adapter carries it).
            tags = {str(t).strip().lower() for t in (post.get("tags") or [])
                    if isinstance(t, str)}
            sensitive = bool(opts.get("sensitive")) or "nsfw" in tags
            spoiler = str(opts.get("spoiler_text") or "")
            visibility = str(opts.get("visibility") or "public").strip().lower()
            if visibility not in VISIBILITIES:
                warnings.append(f"unknown visibility '{visibility[:24]}' — "
                                "using 'public'")
                visibility = "public"

            # spoiler_text counts against max_characters on Mastodon; lengths
            # are grapheme clusters via _m_len — the composer's own metric.
            limit = target.get("char_limit") or caps.get("char_limit") or DEFAULT_CHAR_LIMIT
            if isinstance(limit, int) and limit > 0:
                spoiler_len = _m_len(spoiler)
                if segments:
                    over = [i for i, s in enumerate(segments)
                            if _m_len(s) + spoiler_len > limit]
                    if over:
                        return {"ok": False, "warnings": warnings,
                                "error": f"segment(s) {over} exceed char_limit "
                                         f"({limit}; spoiler_text counts)"}
                elif _m_len(body) + spoiler_len > limit:
                    return {"ok": False, "warnings": warnings,
                            "error": f"body exceeds char_limit "
                                     f"({_m_len(body) + spoiler_len}/{limit}; "
                                     "spoiler_text counts)"}

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
            max_media = ((caps.get("media") or {}).get("images") or {}).get("max") \
                or DEFAULT_MAX_MEDIA
            if videos and (len(videos) > 1 or images):
                return {"ok": False, "warnings": warnings,
                        "error": "video must be the only attachment"}
            if len(images) > max_media:
                return {"ok": False, "warnings": warnings,
                        "error": f"too many attachments ({len(images)}/{max_media})"}
            missing_alt = [a for a in images
                           if not (a.get("alt_text")
                                   or (a.get("source") or {}).get("alt_text"))]
            if missing_alt:
                warnings.append(f"{len(missing_alt)} image(s) missing alt text")

            # media upload — transport only when there are local files to send
            wanted = images + videos
            uploadable = [a for a in wanted if a.get("out_path") or a.get("path")]
            media_ids: List[str] = []
            if uploadable:
                bearer = self._bearer()
                base = self._base_url()
                if not bearer:
                    self._last_error = E_NOT_CONNECTED
                    return {"ok": False, "warnings": warnings,
                            "error": E_NOT_CONNECTED}
                if not base:
                    self._last_error = E_CONFIG
                    return {"ok": False, "warnings": warnings, "error": E_CONFIG}
                for a in uploadable:
                    up = self._upload_media(bearer, base, a, caps)
                    if not up.get("ok"):
                        return {"ok": False, "warnings": warnings,
                                "error": up.get("error") or E_MEDIA}
                    media_ids.append(up["media_id"])
            if len(wanted) > len(uploadable):
                warnings.append(f"{len(wanted) - len(uploadable)} asset(s) have "
                                "no local file yet — not uploaded")

            opts.update({"sensitive": sensitive, "spoiler_text": spoiler,
                         "visibility": visibility})
            prepared = {
                "platform": self.name,
                "target_id": target.get("id"),
                "post_id": post.get("id"),
                "format": target.get("format") or "post",
                "title": "",                       # statuses have no title field
                "body": body,
                "segments": segments,
                "assets": assets,
                "media_ids": media_ids,
                "hashtags": list(target.get("hashtags") or []),
                "mentions": list(target.get("mentions") or []),
                "options": opts,
            }
            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception:
            _log.debug("prepare failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL, "warnings": []}

    def _upload_media(self, bearer: str, base: str, asset: Dict[str, Any],
                      caps: Dict[str, Any]) -> Dict[str, Any]:
        """POST /api/v2/media (multipart, alt text as description) with the
        §4.8 async flow: 202 → poll GET /api/v1/media/{id} until processed
        (206 = still processing), bounded. Fixed error strings out."""
        try:
            from pathlib import Path
            p = Path(str(asset.get("out_path") or asset.get("path")))
            try:
                data = p.read_bytes()
            except Exception:
                return {"ok": False, "error": E_MEDIA}
            kind = str(asset.get("kind")
                       or (asset.get("source") or {}).get("kind") or "image")
            suffix = p.suffix.lower().lstrip(".")
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp", "mp4": "video/mp4",
                    "webm": "video/webm", "mov": "video/quicktime",
                    }.get(suffix, "application/octet-stream")
            mcaps = caps.get("media") or {}
            if kind == "video":
                max_bytes = (mcaps.get("video") or {}).get("max_bytes")
            else:
                max_bytes = (mcaps.get("images") or {}).get("max_bytes")
            if isinstance(max_bytes, int) and 0 < max_bytes < len(data):
                return {"ok": False, "error": "media_too_large"}

            fields: Dict[str, str] = {}
            alt = asset.get("alt_text") or (asset.get("source") or {}).get("alt_text")
            if alt:
                fields["description"] = str(alt)[:1500]
            focus = asset.get("focus") or ((asset.get("source") or {}).get("focus"))
            if isinstance(focus, (list, tuple)) and len(focus) == 2:
                try:                      # API wants "x,y" in [-1, 1]
                    fields["focus"] = f"{float(focus[0]):.2f},{float(focus[1]):.2f}"
                except (TypeError, ValueError):
                    pass
            elif focus:
                fields["focus"] = str(focus)[:32]
            payload, ctype = _multipart(fields, p.name, data, mime)
            r = _http("POST", f"{base}/api/v2/media",
                      headers={"Authorization": f"Bearer {bearer}",
                               "Content-Type": ctype},
                      body=payload, timeout=60)
            j = r.get("json") or {}
            mid = j.get("id")
            if r.get("status") not in (200, 202) or not mid:
                self._classify(r)
                return {"ok": False, "error": E_MEDIA}
            mid = str(mid)
            if r.get("status") == 202:            # async processing (§4.8)
                for _ in range(_MEDIA_POLL_TRIES):
                    time.sleep(1.0)
                    pr = _http("GET", f"{base}/api/v1/media/{quote(mid)}",
                               headers={"Authorization": f"Bearer {bearer}"})
                    if pr.get("status") == 200 and (pr.get("json") or {}).get("id"):
                        return {"ok": True, "media_id": mid}
                    if pr.get("status") == 206:   # still processing
                        continue
                    self._classify(pr)
                    return {"ok": False, "error": E_MEDIA}
                self._last_error = E_MEDIA        # poll budget exhausted
                return {"ok": False, "error": E_MEDIA}
            return {"ok": True, "media_id": mid}
        except Exception:
            _log.debug("media upload failed", exc_info=True)
            return {"ok": False, "error": E_MEDIA}

    # ── jitter between thread segments (§12.4 API-citizen posting) ───────────
    def _jitter_bounds(self) -> tuple:
        v = self._config.get("thread_jitter_s", (0.5, 2.0))
        try:
            if isinstance(v, (int, float)):
                lo = hi = float(v)
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                lo, hi = float(v[0]), float(v[1])
            else:
                lo, hi = 0.5, 2.0
        except (TypeError, ValueError):
            lo, hi = 0.5, 2.0
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

    @staticmethod
    def _idem_key(tid: str, fp: str, index: int) -> str:
        """Idempotency-Key derived from the target id (§4.8/§7.2) + content
        fingerprint + segment index — an identical retry reuses the key (the
        server returns the original status), an edited retry mints a new one."""
        return hashlib.sha256(f"{tid}|{fp}|{index}".encode("utf-8")).hexdigest()

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Single status, reply-chain thread, or native scheduled status.
        Threads publish one segment at a time with jitter; every confirmed id
        is persisted so a retry resumes from the last confirmed segment."""
        try:
            prepared = dict(prepared or {})
            bearer = self._bearer()
            if not bearer:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            base = self._base_url()
            if not base:
                self._last_error = E_CONFIG
                return {"ok": False, "error": E_CONFIG}

            opts = dict(prepared.get("options") or {})
            segments = [s for s in (prepared.get("segments") or [])
                        if isinstance(s, str)]
            texts = segments if segments else [str(prepared.get("body") or "")]
            media_ids = [str(m) for m in (prepared.get("media_ids") or []) if m]
            if not texts[0].strip() and not media_ids:
                self._last_error = "empty_post"
                return {"ok": False, "error": "empty_post"}

            visibility = str(opts.get("visibility") or "public").strip().lower()
            if visibility not in VISIBILITIES:
                visibility = "public"
            sensitive = bool(opts.get("sensitive"))
            spoiler = str(opts.get("spoiler_text") or "")
            language = str(opts.get("language") or "").strip() or None

            # §6.5 native scheduling: scheduled_at ≥5 min ahead → server-side.
            # Under 5 min the server rejects it, so post now instead (the slot
            # was due anyway); threads cannot be scheduled — surface, not skip.
            n = len(texts)
            sched_iso: Optional[str] = None
            sched_ignored = False
            sched_raw = opts.get("scheduled_at")
            if sched_raw:
                sched_dt = pbase._parse_iso(sched_raw)
                if sched_dt is not None:
                    if n > 1:
                        self._last_error = E_SCHED_THREAD
                        return {"ok": False, "error": E_SCHED_THREAD}
                    lead = (sched_dt - pbase._now_utc()).total_seconds()
                    if lead >= _NATIVE_SCHEDULE_MIN_LEAD_S:
                        sched_iso = sched_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    else:
                        sched_ignored = True

            tid = str(prepared.get("target_id") or "")
            fp = hashlib.sha256(
                ("\x1f".join(texts) + "\x1e" + ",".join(media_ids)).encode("utf-8")
            ).hexdigest()
            key_seed = tid or fp
            done: List[str] = self._progress_get(tid, fp) if (n > 1 and tid) else []
            if done and len(done) >= n:      # crash landed after the last segment
                self._progress_clear(tid)
                return {"ok": True, "post_url": None,
                        "platform_post_id": done[0],
                        "raw": {"segment_ids": list(done), "resumed_from": n}}

            needed = n - len(done)
            if self.budget_would_exceed(needed):
                b = self.rate_budget()
                self._last_error = E_BUDGET
                return {"ok": False, "error": E_BUDGET,
                        "defer_until": b.get("reset_at")}

            headers = {"Authorization": f"Bearer {bearer}",
                       "Content-Type": "application/json"}
            reply_to = done[-1] if done else None
            resumed_from = len(done)
            root_url: Optional[str] = None
            for i in range(resumed_from, n):
                if i > resumed_from or (i > 0 and resumed_from > 0 and i == resumed_from):
                    self._thread_pause()
                payload: Dict[str, Any] = {"status": texts[i],
                                           "visibility": visibility}
                if sensitive:
                    payload["sensitive"] = True
                if spoiler:                  # CW rides every thread segment
                    payload["spoiler_text"] = spoiler
                if language:
                    payload["language"] = language
                if i == 0 and media_ids:
                    payload["media_ids"] = media_ids
                if reply_to:
                    payload["in_reply_to_id"] = reply_to
                if sched_iso and i == 0:
                    payload["scheduled_at"] = sched_iso
                hdr = dict(headers)
                hdr["Idempotency-Key"] = self._idem_key(key_seed, fp, i)
                r = _http("POST", f"{base}/api/v1/statuses", headers=hdr,
                          body=json.dumps(payload).encode("utf-8"))
                j = r.get("json") or {}
                sid = str(j.get("id") or "")
                if r.get("status") in (200, 201, 202) and sid:
                    if i == 0:
                        root_url = j.get("url") or None
                    done.append(sid)
                    reply_to = sid
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
            raw: Dict[str, Any] = {"segment_ids": list(done),
                                   "resumed_from": resumed_from}
            if sched_iso:
                # ScheduledStatus entity — no public URL until it publishes
                raw["scheduled"] = True
                raw["scheduled_at"] = sched_iso
                self._audit("published", scheduled=True)
                return {"ok": True, "post_url": None,
                        "platform_post_id": root, "raw": raw}
            if sched_ignored:
                raw["scheduled_ignored"] = True   # <5 min lead → posted now
            self._audit("published", segments=len(done))
            return {"ok": True,
                    "post_url": root_url or f"{base}/web/statuses/{root}",
                    "platform_post_id": root, "raw": raw}
        except Exception:
            _log.debug("publish failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        """DELETE /api/v1/statuses/{id}; a 404 falls through to the
        scheduled-statuses cancel endpoint (native-schedule targets, §6.5)."""
        try:
            bearer = self._bearer()
            if not bearer:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            base = self._base_url()
            if not base:
                self._last_error = E_CONFIG
                return {"ok": False, "error": E_CONFIG}
            pid = quote(str(platform_post_id))
            r = _http("DELETE", f"{base}/api/v1/statuses/{pid}",
                      headers={"Authorization": f"Bearer {bearer}"})
            if r.get("status") == 200:
                self._audit("delete", deleted=True)
                return {"ok": True, "deleted": True}
            if r.get("status") == 404:
                rs = _http("DELETE", f"{base}/api/v1/scheduled_statuses/{pid}",
                           headers={"Authorization": f"Bearer {bearer}"})
                if rs.get("status") == 200:
                    self._audit("delete", deleted=True, scheduled=True)
                    return {"ok": True, "deleted": True, "scheduled": True}
                return {"ok": True, "deleted": False}
            return self._classify(r)
        except Exception:
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    # ── analytics path (§8.2 unified keys; counts only, missing ≠ zero) ──────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        try:
            bearer = self._bearer()
            base = self._base_url()
            pid = str(platform_post_id or "").strip()
            if not bearer or not base or not pid:
                return None
            r = _http("GET", f"{base}/api/v1/statuses/{quote(pid)}",
                      headers={"Authorization": f"Bearer {bearer}"})
            if r.get("status") != 200:
                self._classify(r)
                return None
            j = r.get("json") or {}

            def _num(k: str) -> Optional[int]:
                v = j.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return None
                return int(v)

            out: Dict[str, Any] = {}
            for src, dst in (("favourites_count", "likes"),
                             ("reblogs_count", "shares"),
                             ("replies_count", "comments")):
                v = _num(src)
                if v is not None:
                    out[dst] = v
            return out or None
        except Exception:
            return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        try:
            bearer = self._bearer()
            base = self._base_url()
            if not bearer or not base:
                return None
            r = _http("GET", f"{base}/api/v1/accounts/verify_credentials",
                      headers={"Authorization": f"Bearer {bearer}"})
            if r.get("status") != 200:
                return None
            j = r.get("json") or {}
            out: Dict[str, Any] = {}
            if isinstance(j.get("followers_count"), int):
                out["followers"] = j["followers_count"]
            if isinstance(j.get("statuses_count"), int):
                out["posts"] = j["statuses_count"]
            return out or None
        except Exception:
            return None


ADAPTER = MastodonAdapter
