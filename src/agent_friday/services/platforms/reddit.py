"""
Reddit platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.9).

Reddit Data API, OAuth2 code flow with a script-app credential pair
(client id in config, client secret via the provider-key convention),
loopback redirect ``http://localhost:3000/api/content/platforms/reddit/callback``.
Reddit's script-type apps do not support PKCE — state binding + a single-use
pending flow stand in (§12.2 "PKCE where supported"). A mandatory descriptive
``User-Agent`` rides every call.

The sharp constraints this adapter owns:
  * **Per-subreddit jurisdictions** — every subreddit is its own rulebook.
    ``get_subreddit_requirements()`` fetches ``/r/{sr}/about`` +
    ``/about/rules`` (+ best-effort ``post_requirements``) so the composer and
    routes can surface flair-required / link-only / 18+ warnings at compose
    time, not as publish-time failures. Results are TTL-cached; one
    ``PlatformTarget`` per subreddit is the pipeline convention (§4.9).
  * **Submit flows** — ``POST /api/submit`` for ``self`` (markdown ≤40k) and
    ``link`` posts; images/videos ride the media-asset **lease flow**
    (``/api/media/asset.json`` → S3 multipart upload → submit); multi-image
    posts use ``/api/submit_gallery_post.json``. Flair / ``nsfw`` /
    ``spoiler`` / ``sendreplies`` map from target options and post tags.
  * **Async media confirmation** — media submissions may return only a
    websocket handle instead of a post id. The adapter probes the account's
    recent submissions (the §7.2 verify-probe pattern); an unresolved submit
    is surfaced honestly as ``submit_unconfirmed`` — never silently retried.
  * **Invisible velocity heuristics** — beyond the ~100 q/min OAuth ceiling,
    Reddit's spam filter watches per-account posting velocity, so the local
    budget defaults conservative: **≤10 posts/day** (§4.9).

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
import copy
import json
import logging
import re
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from agent_friday.services.platforms import base as pbase
from agent_friday.services.platforms.base import PlatformAdapter

_log = logging.getLogger("friday.platforms.reddit")

# ── endpoints (design-time snapshot, mid-2026 — §4.2 volatility warning) ─────
AUTH_URL = "https://www.reddit.com/api/v1/authorize"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"   # pragma: allowlist secret
REVOKE_URL = "https://www.reddit.com/api/v1/revoke_token"
OAUTH_API = "https://oauth.reddit.com"
SUBMIT_URL = f"{OAUTH_API}/api/submit"
GALLERY_URL = f"{OAUTH_API}/api/submit_gallery_post.json"
MEDIA_ASSET_URL = f"{OAUTH_API}/api/media/asset.json"
DEFAULT_REDIRECT_URI = "http://localhost:3000/api/content/platforms/reddit/callback"
SCOPES = "identity submit flair read"
DEFAULT_USER_AGENT = "desktop:ai.futurespeak.friday.content:v1.0 (sovereign personal agent)"

TITLE_LIMIT = 300                   # §4.9: title ≤300 chars
BODY_LIMIT = 40000                  # §4.9: self markdown body ≤40k
GALLERY_MAX_IMAGES = 20
_STATE_TTL_S = 600                  # pending OAuth state is single-use + 10 min
_REQ_CACHE_TTL_S = 300              # subreddit-requirements cache window
_MAX_RULES = 50                     # §8.6: size-cap untrusted platform strings
_SR_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_]{1,20}$")

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
E_NO_SUBREDDIT = "missing_subreddit"
E_BAD_SUBREDDIT = "invalid_subreddit"
E_TITLE = "missing_title"
E_LINK = "missing_link_url"
E_SUBMIT = "submit_rejected"
E_UNCONFIRMED = "submit_unconfirmed"


# ── transport chokepoint — tests monkeypatch THIS ────────────────────────────
def _http(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
          body: Optional[bytes] = None, timeout: int = 20) -> Dict[str, Any]:
    """The single wire for every Reddit API call. Returns
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


def _clean_sr(value: Any) -> str:
    """Normalize 'r/foo' / '/r/foo' / 'foo' → 'foo'; '' when invalid."""
    s = str(value or "").strip().lstrip("/")
    if s.lower().startswith("r/"):
        s = s[2:]
    return s if _SR_RE.match(s) else ""


def _multipart(fields: List[Dict[str, Any]], filename: str, blob: bytes,
               mime: str) -> tuple:
    """Build the S3 lease-upload multipart body: every lease field first, the
    file part last (S3 POST-policy requirement). → (body_bytes, content_type)."""
    boundary = "----FridayForm" + secrets.token_hex(8)
    parts: List[bytes] = []
    for f in fields:
        name = str((f or {}).get("name") or "")
        value = str((f or {}).get("value") or "")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n{value}\r\n".encode("utf-8"))
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode("utf-8"))
    parts.append(blob)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class RedditAdapter(PlatformAdapter):
    name = "reddit"
    label = "Reddit"
    auth_mode = "oauth2"             # script-app code flow; no PKCE on Reddit
    automation_tier = "api"
    default_daily_limit = 10         # §4.9 conservative local budget

    def __init__(self) -> None:
        super().__init__()
        self._req_lock = threading.Lock()
        self._req_cache: Dict[str, Any] = {}   # sr -> (fetched_ts, envelope)

    # ── config helpers ────────────────────────────────────────────────────────
    def _client_id(self) -> Optional[str]:
        v = self._config.get("client_id")
        return str(v) if v else None

    def _redirect_uri(self) -> str:
        v = self._config.get("redirect_uri")
        return str(v) if v else DEFAULT_REDIRECT_URI

    def _ua(self) -> str:
        v = self._config.get("user_agent")
        return str(v) if v else DEFAULT_USER_AGENT

    def _headers(self, bearer: str, ctype: Optional[str] = None) -> Dict[str, str]:
        h = {"Authorization": f"Bearer {bearer}", "User-Agent": self._ua()}
        if ctype:
            h["Content-Type"] = ctype
        return h

    # ── capability declaration (§4.2 / §4.9) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({
            "formats": ["post", "link_post", "image_post", "video_upload"],
            "char_limit": BODY_LIMIT,
            "title_limit": TITLE_LIMIT,
            "thread": False,
            "native_schedule": False,
            "native_delete": True,
            "analytics": "counts",    # t3 info: score / upvote_ratio / comments
            "hashtags_max": 0,        # hashtags are not a Reddit norm (§5.2)
            "notes": [
                "one target per subreddit — cross-posting to N subs = N targets",
                f"conservative local budget {self._budget_limit()}/day "
                "(invisible per-account velocity heuristics)",
                "subreddit rules fetched at compose time via "
                "get_subreddit_requirements() — flair/self-promo/karma warnings",
                "flair_id / nsfw / spoiler / sendreplies via target options",
                "descriptive User-Agent required on every call",
                "media posts may confirm asynchronously — adapter probes "
                "recent submissions before reporting an id",
            ],
        })
        caps["media"] = {
            "images": {"max": GALLERY_MAX_IMAGES,
                       "formats": ["png", "jpeg", "gif", "webp"],
                       "max_bytes": 20 * 1024 * 1024, "aspect": (0.1, 10.0)},
            "video": {"max_s": 900, "formats": ["mp4", "mov"],
                      "max_bytes": 1024 * 1024 * 1024},
            "alt_text": False,        # gallery captions exist; alt text does not
        }
        return caps

    # ── auth lifecycle (OAuth2 code flow, loopback redirect, state bound) ────
    def connect_url(self, state: str) -> Optional[str]:
        try:
            cid = self._client_id()
            if not cid:
                self._last_error = E_CONFIG
                return None
            if not state:
                self._last_error = E_STATE
                return None
            # pending state rides the encrypted blob — single-use, 10 min TTL
            blob = self.load_credentials() or {}
            blob["pending_auth"] = {
                "state": str(state),
                "created_at": pbase._now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            self.save_credentials(blob)
            q = urlencode({
                "client_id": cid, "response_type": "code", "state": str(state),
                "redirect_uri": self._redirect_uri(),
                "duration": "permanent",          # refresh token, please
                "scope": SCOPES,
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
        """Reddit's token endpoints want HTTP Basic (client id : client secret)
        on every call; script apps always have a secret, installed apps use an
        empty one. The secret rides the provider-key convention."""
        cid = self._client_id() or ""
        confidential = self.simple_secret() or ""   # script-app client secret
        pair = base64.b64encode(
            f"{cid}:{confidential}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {pair}",
                   "User-Agent": self._ua(),
                   "Content-Type": "application/x-www-form-urlencoded"}
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
            if not code or not self._client_id():
                self._last_error = E_CONFIG
                return self.status()
            r = self._token_request({
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self._redirect_uri(),
            })
            j = r.get("json") or {}
            granted = j.get("access_token")
            if r.get("status") != 200 or not granted:
                self._classify(r)
                self._last_error = E_AUTH
                self._audit("callback_exchange_failed")
                return self.status()
            now = pbase._now_utc()
            ttl = int(j.get("expires_in") or 3600)
            creds: Dict[str, Any] = {
                "access_token": granted,
                "refresh_token": j.get("refresh_token"),
                "expires_at": (now + timedelta(seconds=ttl)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scopes": str(j.get("scope") or SCOPES).split(),
                "account": None,
                "connected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            me = _http("GET", f"{OAUTH_API}/api/v1/me",
                       headers=self._headers(str(granted)))
            if me.get("status") == 200:
                creds["account"] = (me.get("json") or {}).get("name")
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
            if not self._client_id():
                self._last_error = E_CONFIG
                return False
            r = self._token_request({"grant_type": "refresh_token",
                                     "refresh_token": rot})
            j = r.get("json") or {}
            fresh = j.get("access_token")
            if r.get("status") == 200 and fresh:
                creds["access_token"] = fresh
                if j.get("refresh_token"):        # Reddit may rotate; keep ours otherwise
                    creds["refresh_token"] = j.get("refresh_token")
                ttl = int(j.get("expires_in") or 3600)
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
            # revoking the refresh grant kills the whole family (RFC 7009)
            rot = creds.get("refresh_token") or creds.get("access_token")
            hint = ("refresh_token" if creds.get("refresh_token")
                    else "access_token")
            if rot and self._client_id():
                r = self._token_request({"token": str(rot),
                                         "token_type_hint": hint},
                                        url=REVOKE_URL)
                platform_side = r.get("status") in (200, 204)
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
            st["client_configured"] = bool(self._client_id())
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
        _log.debug("reddit API %s: %s", status, (r.get("text") or "")[:500])
        return out

    # ── subreddit requirements (§4.9 — the real complexity) ──────────────────
    def get_subreddit_requirements(self, subreddit: str) -> Dict[str, Any]:
        """Fetch ``/r/{sr}/about`` + ``/about/rules`` (+ best-effort
        ``post_requirements``) and derive composer warnings — flair mandatory?
        link-only? 18+? — *before* scheduling, never as publish-time failures.

        Returns {ok, subreddit, about, rules, post_requirements, warnings}.
        TTL-cached (5 min); platform strings are truncated per §8.6. Never
        raises.
        """
        try:
            sr = _clean_sr(subreddit)
            if not sr:
                return {"ok": False, "error": E_BAD_SUBREDDIT}
            now = time.time()
            with self._req_lock:
                hit = self._req_cache.get(sr.lower())
                if hit and now - hit[0] < _REQ_CACHE_TTL_S:
                    return copy.deepcopy(hit[1])
            bearer = self._bearer()
            if not bearer:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            hdrs = self._headers(bearer)
            r = _http("GET", f"{OAUTH_API}/r/{quote(sr)}/about", headers=hdrs)
            if r.get("status") != 200:
                env = self._classify(r)
                return {"ok": False, "error": env.get("error") or E_HTTP}
            d = ((r.get("json") or {}).get("data")
                 if isinstance((r.get("json") or {}).get("data"), dict) else {})
            about: Dict[str, Any] = {}
            for k in ("subreddit_type", "submission_type", "over18",
                      "allow_images", "allow_videos", "allow_galleries",
                      "spoilers_enabled", "link_flair_enabled", "subscribers"):
                v = d.get(k)
                if isinstance(v, bool) or isinstance(v, int):
                    about[k] = v
                elif isinstance(v, str):
                    about[k] = v[:64]

            rules: List[Dict[str, Any]] = []
            rr = _http("GET", f"{OAUTH_API}/r/{quote(sr)}/about/rules",
                       headers=hdrs)
            if rr.get("status") == 200:
                for rule in ((rr.get("json") or {}).get("rules") or [])[:_MAX_RULES]:
                    if isinstance(rule, dict):
                        rules.append({
                            "short_name": str(rule.get("short_name") or "")[:120],
                            "kind": str(rule.get("kind") or "")[:32],
                            "description": str(rule.get("description") or "")[:500],
                        })

            reqs: Optional[Dict[str, Any]] = None
            pr = _http("GET", f"{OAUTH_API}/api/v1/{quote(sr)}/post_requirements",
                       headers=hdrs)
            if pr.get("status") == 200 and isinstance(pr.get("json"), dict):
                pj = pr["json"]
                reqs = {
                    "is_flair_required": bool(pj.get("is_flair_required")),
                    "title_text_max_length": (
                        int(pj["title_text_max_length"])
                        if isinstance(pj.get("title_text_max_length"), int)
                        else None),
                    "title_required_strings": [
                        str(s)[:64] for s in
                        (pj.get("title_required_strings") or [])[:10]],
                    "body_restriction_policy": str(
                        pj.get("body_restriction_policy") or "")[:32],
                }

            warnings: List[str] = []
            sub_type = str(about.get("subreddit_type") or "")
            if sub_type and sub_type != "public":
                warnings.append(f"r/{sr} is {sub_type} — posting may be restricted")
            submission = str(about.get("submission_type") or "")
            if submission == "link":
                warnings.append(f"r/{sr} accepts link posts only")
            elif submission == "self":
                warnings.append(f"r/{sr} accepts self (text) posts only")
            if about.get("over18"):
                warnings.append(f"r/{sr} is 18+ — the nsfw flag will apply")
            if about.get("allow_images") is False:
                warnings.append(f"images are not allowed in r/{sr}")
            if about.get("allow_videos") is False:
                warnings.append(f"videos are not allowed in r/{sr}")
            if reqs:
                if reqs.get("is_flair_required"):
                    warnings.append(f"post flair is required in r/{sr}")
                tmax = reqs.get("title_text_max_length")
                if isinstance(tmax, int) and 0 < tmax < TITLE_LIMIT:
                    warnings.append(f"titles capped at {tmax} chars in r/{sr}")
                if reqs.get("title_required_strings"):
                    warnings.append(f"r/{sr} requires specific text in titles "
                                    "(see subreddit rules)")
                if reqs.get("body_restriction_policy") == "notAllowed":
                    warnings.append(f"self-text bodies are not allowed in r/{sr}")
            if rules:
                warnings.append(f"{len(rules)} community rule(s) in r/{sr} — "
                                "review before posting")

            out = {"ok": True, "subreddit": sr, "about": about, "rules": rules,
                   "post_requirements": reqs, "warnings": warnings,
                   "fetched_at": pbase._now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")}
            with self._req_lock:
                self._req_cache[sr.lower()] = (now, copy.deepcopy(out))
            return out
        except Exception:
            _log.debug("get_subreddit_requirements failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Hard validation (title/body limits, subreddit target, media caps) +
        the media-asset lease upload. Subreddit requirement warnings are merged
        in when connected (cached). {ok, prepared, warnings} — never raises."""
        try:
            target = dict(target or {})
            post = dict(post or {})
            caps = self.capabilities()
            warnings: List[str] = []
            options = dict(target.get("options") or {})

            sr = _clean_sr(options.get("subreddit") or options.get("sr"))
            if not sr:
                return {"ok": False, "warnings": warnings, "error": E_NO_SUBREDDIT}

            body = str(target.get("adapted_body") or post.get("body") or "")
            segments = [s for s in (target.get("segments") or [])
                        if isinstance(s, str)]
            if segments:
                body = body or "\n\n".join(segments)
                warnings.append("segments merged into one body "
                                "(Reddit has no threads)")
            title = str(target.get("adapted_title") or post.get("title") or "").strip()
            if not title:
                first = next((ln.strip() for ln in body.splitlines()
                              if ln.strip()), "")
                title = first[:TITLE_LIMIT]
                if title:
                    warnings.append("title derived from the first line of the body")
            if not title:
                return {"ok": False, "warnings": warnings, "error": E_TITLE}
            if len(title) > TITLE_LIMIT:
                return {"ok": False, "warnings": warnings,
                        "error": f"title exceeds title_limit "
                                 f"({len(title)}/{TITLE_LIMIT})"}
            limit = caps.get("char_limit") or BODY_LIMIT
            if len(body) > limit:
                return {"ok": False, "warnings": warnings,
                        "error": f"body exceeds char_limit ({len(body)}/{limit})"}
            if target.get("hashtags"):
                warnings.append("hashtags dropped — not a Reddit norm")

            tags = [str(t).lower() for t in (post.get("tags") or [])]
            nsfw = bool(options.get("nsfw")) or "nsfw" in tags
            spoiler = bool(options.get("spoiler")) or "spoiler" in tags

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
            img_max = ((caps.get("media") or {}).get("images") or {}).get("max") \
                or GALLERY_MAX_IMAGES
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

            # media-asset lease upload — transport only for local files
            wanted = images or videos
            uploadable = [a for a in wanted if a.get("out_path") or a.get("path")]
            media: List[Dict[str, Any]] = []
            if uploadable:
                bearer = self._bearer()
                if not bearer:
                    self._last_error = E_NOT_CONNECTED
                    return {"ok": False, "warnings": warnings,
                            "error": E_NOT_CONNECTED}
                for a in uploadable:
                    up = self._upload_media_asset(bearer, a, caps)
                    if not up.get("ok"):
                        return {"ok": False, "warnings": warnings,
                                "error": up.get("error") or E_MEDIA}
                    media.append({
                        "kind": _kind(a),
                        "asset_url": up["asset_url"],
                        "asset_id": up["asset_id"],
                        "caption": str(a.get("alt_text")
                                       or (a.get("source") or {}).get("alt_text")
                                       or "")[:180],
                    })
            if len(wanted) > len(uploadable):
                warnings.append(f"{len(wanted) - len(uploadable)} asset(s) have "
                                "no local file yet — not uploaded")

            # compose-time rule surfacing (§4.9) — best-effort, cached
            if self.has_credentials():
                req = self.get_subreddit_requirements(sr)
                if req.get("ok"):
                    warnings.extend(req.get("warnings") or [])

            prepared = {
                "platform": self.name,
                "target_id": target.get("id"),
                "post_id": post.get("id"),
                "format": target.get("format") or "post",
                "subreddit": sr,
                "title": title,
                "body": body,
                "segments": [],                   # no threads on Reddit
                "assets": assets,
                "media": media,
                "link_url": str(options.get("url") or "").strip(),
                "nsfw": nsfw,
                "spoiler": spoiler,
                "sendreplies": bool(options.get("sendreplies", True)),
                "flair_id": str(options.get("flair_id") or "") or None,
                "flair_text": str(options.get("flair_text") or "") or None,
                "hashtags": [],                   # zero hashtags on Reddit (§5.2)
                "mentions": list(target.get("mentions") or []),
                "options": options,
            }
            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception:
            _log.debug("prepare failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL, "warnings": []}

    def _upload_media_asset(self, bearer: str, asset: Dict[str, Any],
                            caps: Dict[str, Any]) -> Dict[str, Any]:
        """§4.9 media-asset lease flow: ``/api/media/asset.json`` grants an S3
        POST-policy lease → multipart upload to the leased action URL. Fixed
        error strings out."""
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
                    "gif": "image/gif", "webp": "image/webp", "mp4": "video/mp4",
                    "mov": "video/quicktime"}.get(suffix, "application/octet-stream")
            mcaps = caps.get("media") or {}
            max_bytes = ((mcaps.get("video") or {}) if kind == "video"
                         else (mcaps.get("images") or {})).get("max_bytes")
            if isinstance(max_bytes, int) and 0 < max_bytes < len(data):
                return {"ok": False, "error": "media_too_large"}

            r = _http("POST", MEDIA_ASSET_URL,
                      headers=self._headers(
                          bearer, "application/x-www-form-urlencoded"),
                      body=urlencode({"filepath": p.name,
                                      "mimetype": mime}).encode("utf-8"))
            j = r.get("json") or {}
            args = j.get("args") if isinstance(j.get("args"), dict) else {}
            action = str(args.get("action") or "")
            fields = [f for f in (args.get("fields") or []) if isinstance(f, dict)]
            asset_id = str(((j.get("asset") or {}) if isinstance(j.get("asset"), dict)
                            else {}).get("asset_id") or "")
            if r.get("status") not in (200, 201) or not action or not fields:
                self._classify(r)
                return {"ok": False, "error": E_MEDIA}
            if action.startswith("//"):
                action = "https:" + action

            body, ctype = _multipart(fields, p.name, data, mime)
            up = _http("POST", action,
                       headers={"Content-Type": ctype, "User-Agent": self._ua()},
                       body=body)
            if up.get("status") not in (200, 201, 204):
                self._classify(up)
                return {"ok": False, "error": E_MEDIA}
            key = next((str(f.get("value")) for f in fields
                        if f.get("name") == "key"), "")
            asset_url = ((up.get("headers") or {}).get("location")
                         or (f"{action.rstrip('/')}/{key}" if key else action))
            return {"ok": True, "asset_url": str(asset_url), "asset_id": asset_id}
        except Exception:
            _log.debug("media lease upload failed", exc_info=True)
            return {"ok": False, "error": E_MEDIA}

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """``POST /api/submit`` (self / link / image / video) or the gallery
        endpoint for multi-image posts. One subreddit per target (§4.9)."""
        try:
            prepared = dict(prepared or {})
            bearer = self._bearer()
            if not bearer:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            options = dict(prepared.get("options") or {})
            sr = _clean_sr(prepared.get("subreddit")
                           or options.get("subreddit") or options.get("sr"))
            if not sr:
                self._last_error = E_NO_SUBREDDIT
                return {"ok": False, "error": E_NO_SUBREDDIT}
            title = str(prepared.get("title") or "").strip()[:TITLE_LIMIT]
            if not title:
                self._last_error = E_TITLE
                return {"ok": False, "error": E_TITLE}
            if self.budget_would_exceed(1):
                b = self.rate_budget()
                self._last_error = E_BUDGET
                return {"ok": False, "error": E_BUDGET,
                        "defer_until": b.get("reset_at")}

            media = [m for m in (prepared.get("media") or [])
                     if isinstance(m, dict) and m.get("asset_url")]
            images = [m for m in media if m.get("kind") == "image"]
            videos = [m for m in media if m.get("kind") == "video"]
            link_url = str(prepared.get("link_url") or "").strip()

            common: Dict[str, Any] = {
                "api_type": "json", "sr": sr, "title": title,
                "nsfw": "true" if prepared.get("nsfw") else "false",
                "spoiler": "true" if prepared.get("spoiler") else "false",
                "sendreplies": ("true" if prepared.get("sendreplies", True)
                                else "false"),
            }
            if prepared.get("flair_id"):
                common["flair_id"] = str(prepared["flair_id"])
            if prepared.get("flair_text"):
                common["flair_text"] = str(prepared["flair_text"])[:64]

            if len(images) > 1:                   # gallery endpoint, JSON body
                payload = dict(common)
                payload["nsfw"] = bool(prepared.get("nsfw"))
                payload["spoiler"] = bool(prepared.get("spoiler"))
                payload["sendreplies"] = bool(prepared.get("sendreplies", True))
                payload["items"] = [
                    {"media_id": str(m.get("asset_id") or ""),
                     "caption": str(m.get("caption") or ""),
                     "outbound_url": ""}
                    for m in images]
                r = _http("POST", GALLERY_URL,
                          headers=self._headers(bearer, "application/json"),
                          body=json.dumps(payload).encode("utf-8"))
                return self._handle_submit_response(r, sr=sr, kind="gallery",
                                                    title=title)

            form = dict(common)
            if videos:
                form["kind"] = "video"
                form["url"] = str(videos[0].get("asset_url"))
                poster = (options.get("poster_url")
                          or (images[0].get("asset_url") if images else None))
                if poster:
                    form["video_poster_url"] = str(poster)
            elif images:
                form["kind"] = "image"
                form["url"] = str(images[0].get("asset_url"))
            elif link_url or prepared.get("format") == "link_post":
                if not link_url:
                    self._last_error = E_LINK
                    return {"ok": False, "error": E_LINK}
                form["kind"] = "link"
                form["url"] = link_url
            else:
                body = str(prepared.get("body") or "")
                if not body.strip():
                    self._last_error = "empty_post"
                    return {"ok": False, "error": "empty_post"}
                form["kind"] = "self"
                form["text"] = body

            r = _http("POST", SUBMIT_URL,
                      headers=self._headers(
                          bearer, "application/x-www-form-urlencoded"),
                      body=urlencode(form).encode("utf-8"))
            return self._handle_submit_response(r, sr=sr, kind=form["kind"],
                                                title=title)
        except Exception:
            _log.debug("publish failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    def _handle_submit_response(self, r: Dict[str, Any], *, sr: str,
                                kind: str, title: str) -> Dict[str, Any]:
        """Parse the api_type=json envelope. In-body error triples map to fixed
        strings (RATELIMIT → rate_limited); enum codes travel, messages never
        do (§12.5). Media submits that only return a websocket handle are
        resolved via a recent-submissions probe — honest ``submit_unconfirmed``
        when the probe misses (§7.2 verify-before-retry owns the retry call)."""
        if r.get("status") != 200:
            return self._classify(r)
        j = r.get("json") or {}
        jj = j.get("json") if isinstance(j.get("json"), dict) else {}
        errors = jj.get("errors") if isinstance(jj.get("errors"), list) else []
        if errors:
            codes: List[str] = []
            for e in errors:
                if isinstance(e, (list, tuple)) and e:
                    code = re.sub(r"[^A-Z0-9_]", "", str(e[0]).upper())[:64]
                    if code:
                        codes.append(code)
            err = E_RATE if "RATELIMIT" in codes else E_SUBMIT
            self._last_error = err
            _log.debug("reddit submit rejected: %s", (r.get("text") or "")[:500])
            out: Dict[str, Any] = {"ok": False, "error": err}
            if codes:
                out["codes"] = codes
            return out
        data = jj.get("data") if isinstance(jj.get("data"), dict) else {}
        name = str(data.get("name") or "")
        id36 = str(data.get("id") or "")
        if not name and id36:
            name = f"t3_{id36}"
        if name:
            if not id36 and "_" in name:
                id36 = name.split("_", 1)[1]
            url = str(data.get("url") or "") \
                or f"https://www.reddit.com/comments/{id36}"
            self.consume_budget(1)
            self._audit("publish", subreddit=sr, kind=kind)
            return {"ok": True, "post_url": url, "platform_post_id": name,
                    "raw": {"name": name, "id": id36, "url": url}}
        # media submit accepted but confirmed asynchronously (websocket) —
        # budget is spent (it DID land); probe recent submissions for the id.
        self.consume_budget(1)
        found = self._find_recent_submission(sr, title)
        if found:
            self._audit("publish", subreddit=sr, kind=kind, probed=True)
            return {"ok": True, "post_url": found["url"],
                    "platform_post_id": found["name"],
                    "raw": {"name": found["name"], "probed": True}}
        self._last_error = E_UNCONFIRMED
        return {"ok": False, "error": E_UNCONFIRMED, "unconfirmed": True}

    def _find_recent_submission(self, sr: str,
                                title: str) -> Optional[Dict[str, str]]:
        """Verify-probe (§7.2): match the payload fingerprint (subreddit +
        title) against the account's newest submissions."""
        try:
            bearer = self._bearer()
            creds = self.load_credentials() or {}
            account = creds.get("account")
            if not bearer or not account:
                return None
            r = _http("GET",
                      f"{OAUTH_API}/user/{quote(str(account))}/submitted"
                      "?limit=10&sort=new",
                      headers=self._headers(bearer))
            if r.get("status") != 200:
                return None
            children = (((r.get("json") or {}).get("data") or {})
                        .get("children") or [])
            for ch in children:
                d = (ch or {}).get("data") if isinstance(ch, dict) else None
                if not isinstance(d, dict):
                    continue
                if (str(d.get("subreddit") or "").lower() == sr.lower()
                        and str(d.get("title") or "") == title):
                    name = str(d.get("name") or "")
                    if not name:
                        continue
                    perm = str(d.get("permalink") or "")
                    url = (f"https://www.reddit.com{perm}" if perm
                           else f"https://www.reddit.com/comments/{name.split('_', 1)[-1]}")
                    return {"name": name, "url": url}
            return None
        except Exception:
            return None

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        try:
            bearer = self._bearer()
            if not bearer:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            pid = str(platform_post_id or "").strip()
            if not pid:
                return {"ok": False, "error": E_HTTP}
            if not pid.startswith("t3_"):
                pid = f"t3_{pid}"
            r = _http("POST", f"{OAUTH_API}/api/del",
                      headers=self._headers(
                          bearer, "application/x-www-form-urlencoded"),
                      body=urlencode({"id": pid}).encode("utf-8"))
            if r.get("status") == 200:
                self._audit("delete", deleted=True)
                return {"ok": True, "deleted": True}
            return self._classify(r)
        except Exception:
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    # ── analytics path (§8.2 unified keys; missing ≠ zero) ───────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        """t3 info counts: score → likes (upvotes−downvotes), num_comments →
        comments, num_crossposts → shares. No impressions on Reddit."""
        try:
            bearer = self._bearer()
            pid = str(platform_post_id or "").strip()
            if not bearer or not pid:
                return None
            if not pid.startswith("t3_"):
                pid = f"t3_{pid}"
            r = _http("GET", f"{OAUTH_API}/api/info?id={quote(pid)}",
                      headers=self._headers(bearer))
            if r.get("status") != 200:
                self._classify(r)
                return None
            children = (((r.get("json") or {}).get("data") or {})
                        .get("children") or [])
            if not (isinstance(children, list) and children):
                return None
            d = (children[0] or {}).get("data") or {}
            if not isinstance(d, dict) or not d:
                return None

            def _num(k: str) -> Optional[int]:
                v = d.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return None
                return int(v)

            out: Dict[str, Any] = {}
            for src, dst in (("score", "likes"),          # upvotes − downvotes
                             ("num_comments", "comments"),
                             ("num_crossposts", "shares")):
                v = _num(src)
                if v is not None:
                    out[dst] = v
            return out or None
        except Exception:
            return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        try:
            bearer = self._bearer()
            if not bearer:
                return None
            r = _http("GET", f"{OAUTH_API}/api/v1/me",
                      headers=self._headers(bearer))
            if r.get("status") != 200:
                return None
            j = r.get("json") or {}
            out: Dict[str, Any] = {}
            if isinstance(j.get("total_karma"), int):
                out["karma"] = j["total_karma"]
            profile = j.get("subreddit") if isinstance(j.get("subreddit"), dict) else {}
            if isinstance(profile.get("subscribers"), int):
                out["followers"] = profile["subscribers"]
            return out or None
        except Exception:
            return None


ADAPTER = RedditAdapter
