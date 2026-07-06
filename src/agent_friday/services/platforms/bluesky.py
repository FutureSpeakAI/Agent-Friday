"""
Bluesky / AT Protocol platform adapter (docs/CONTENT_PIPELINE_SPEC.md §4.7).

XRPC against the user's PDS (default ``bsky.social``). Auth is the simplest
open-protocol path: **app password** → ``com.atproto.server.createSession``
(access/refresh JWTs), i.e. token-paste mode — ``connect_url()`` is None.
ATProto OAuth is the future upgrade path; the §4.1 loopback redirect
(``http://localhost:3000/api/content/platforms/bluesky/callback``) is reserved
for it. The app password rides ``set_provider_key("platform_bluesky", …)``;
the session (JWTs + did + handle) is the encrypted ``bluesky.cred`` blob.

The sharp constraints this adapter owns:
  * **300 graphemes** — the limit is grapheme *clusters*, not characters or
    bytes (``grapheme_len``: real segmentation lib when available, honest
    ZWJ/VS/RI-aware fallback otherwise).
  * **Richtext facets** — links, mentions, and hashtags are **UTF-8
    byte-range** facets the client must compute (``detect_facets``); the
    classic off-by-one source is emoji-adjacent links, so offsets are
    computed on the encoded prefix, never on char indices. Mentions resolve
    handle→DID at publish; an unresolvable mention degrades to plain text
    (the post ships, the tap-target doesn't — honest, not silent failure).
  * **uploadBlob ≤ ~1 MB** per image — larger stills are recompressed via
    PIL (lazy import); when recompression cannot get under the cap the
    prepare fails loudly (``media_too_large``), never silently drops media.
  * **Threads** — reply chains with root/parent ``{uri, cid}`` refs, one
    segment at a time with jitter; confirmed segments persist so a retry
    resumes instead of double-posting (same pattern as x_twitter).
  * **Video is feature-detected** (``app.bsky.video.getUploadLimits``) —
    never advertised in ``capabilities()`` until detection (or config)
    says so; a video target on an account that can't upload fails with a
    fixed ``video_not_available`` string (§4.14: surface, don't fall).
  * **Analytics: counts** — ``app.bsky.feed.getPosts`` like/repost/reply/
    quote counts mapped to the §8.2 unified keys; no impressions on
    Bluesky, so the key is *absent*, never zero.

Rate limits are generous (≈5,000 action points/hour → effectively
unconstrained for a human account); the local budget stays conservative
anyway (§4.1 — never trust ourselves to remember).

Transport: every HTTP byte goes through the module-level ``_http()``
chokepoint so tests stub it — the adapter itself never opens a socket.
Adapter errors are externalized as fixed content-free strings (§12.5);
full detail goes to the local debug logger only, never to envelopes or
``status()``. Nothing tokenish is ever logged.

Import-safe under FRIDAY_TESTING: stdlib only at import time, no threads
started, no network; credential_store and PIL are lazy-imported.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from urllib.parse import quote

from agent_friday.services.platforms import base as pbase
from agent_friday.services.platforms.base import PlatformAdapter

_log = logging.getLogger("friday.platforms.bluesky")

# ── endpoints (design-time snapshot, mid-2026 — §4.2 volatility warning) ─────
DEFAULT_PDS = "https://bsky.social"
APP_URL = "https://bsky.app"
# Reserved for the ATProto OAuth upgrade path (§4.1 loopback convention).
DEFAULT_REDIRECT_URI = "http://localhost:3000/api/content/platforms/bluesky/callback"

POST_COLLECTION = "app.bsky.feed.post"
FACET_LINK = "app.bsky.richtext.facet#link"
FACET_MENTION = "app.bsky.richtext.facet#mention"
FACET_TAG = "app.bsky.richtext.facet#tag"

GRAPHEME_LIMIT = 300                    # graphemes, not chars/bytes (§4.7)
IMAGE_MAX_BYTES = 1_000_000             # uploadBlob cap per image (~1 MB)
VIDEO_MAX_BYTES = 50 * 1024 * 1024      # PDS blob ceiling snapshot
VIDEO_MAX_S = 180                       # ~3 min via the video service
_SESSION_TTL_S = 90 * 60                # access JWTs are short-lived (~2 h)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_MENTION_RE = re.compile(
    r"(?<![\w@.])@([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+)")
_TAG_RE = re.compile(r"(?<![\w#/&])#([A-Za-z][\w-]*)")
_URL_TRAIL = ".,;:!?\"')]}"

_IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "webp": "image/webp", "gif": "image/gif"}
_VIDEO_MIME = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}

# ── fixed content-free error strings (§12.5) ─────────────────────────────────
E_NOT_CONNECTED = "not_connected"
E_AUTH = "auth_error"
E_FORBIDDEN = "forbidden"
E_RATE = "rate_limited"
E_HTTP = "platform_http_error"
E_NETWORK = "network_error"
E_BUDGET = "rate_budget_exhausted"
E_MEDIA = "media_upload_failed"
E_MEDIA_SIZE = "media_too_large"
E_VIDEO = "video_not_available"
E_CONFIG = "missing_identifier"
E_INTERNAL = "adapter_error"


# ── transport chokepoint — tests monkeypatch THIS ────────────────────────────
def _http(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
          body: Optional[bytes] = None, timeout: int = 20) -> Dict[str, Any]:
    """The single wire for every XRPC call. Returns
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


# ── grapheme counting (the 300 limit is clusters, not code points) ───────────
_GRAPHEME_IMPL = None


def _grapheme_len_fallback(s: str) -> int:
    """ZWJ/variation-selector/regional-indicator/combining-mark aware
    approximation of extended grapheme clusters — used only when neither
    ``grapheme`` nor ``regex`` is importable. Errs on the honest side for
    the emoji families that actually show up in posts."""
    count = 0
    prev_zwj = False
    prev_ri = False
    for ch in s:
        cp = ord(ch)
        if unicodedata.category(ch) in ("Mn", "Mc", "Me"):
            prev_ri = False
            continue                       # combining marks extend the cluster
        if cp == 0x200D:                   # zero-width joiner
            prev_zwj = True
            prev_ri = False
            continue
        if cp in (0xFE0E, 0xFE0F):         # variation selectors
            prev_ri = False
            continue
        if 0x1F3FB <= cp <= 0x1F3FF:       # skin-tone modifiers
            prev_ri = False
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:       # regional indicators pair up (flags)
            if prev_ri:
                prev_ri = False
                continue
            prev_ri = True
            prev_zwj = False
            count += 1
            continue
        prev_ri = False
        if prev_zwj:                       # joined into the previous cluster
            prev_zwj = False
            continue
        count += 1
    return count


def _grapheme_impl():
    """Best available grapheme-cluster counter, resolved once (lazy imports —
    import-safe under FRIDAY_TESTING, no hard dependency)."""
    global _GRAPHEME_IMPL
    if _GRAPHEME_IMPL is None:
        try:
            import grapheme as _g                      # type: ignore
            _GRAPHEME_IMPL = _g.length
        except Exception:
            try:
                import regex as _rx                    # type: ignore
                pat = _rx.compile(r"\X")
                _GRAPHEME_IMPL = lambda s: len(pat.findall(s))  # noqa: E731
            except Exception:
                _GRAPHEME_IMPL = _grapheme_len_fallback
    return _GRAPHEME_IMPL


def grapheme_len(text: Any) -> int:
    """Grapheme-cluster count of ``text`` (NFC-normalized first — the same
    normalization the record text ships with)."""
    s = unicodedata.normalize("NFC", str(text or ""))
    try:
        return int(_grapheme_impl()(s))
    except Exception:
        return _grapheme_len_fallback(s)


# ── richtext facets: UTF-8 BYTE offsets (the §4.7 off-by-one minefield) ──────
def _byte_offset(s: str, char_idx: int) -> int:
    """Byte offset of ``char_idx`` in UTF-8 — emoji are 3–4 bytes each, so
    this is never the char index for real-world post text."""
    return len(s[:char_idx].encode("utf-8"))


def detect_facets(text: Any) -> List[Dict[str, Any]]:
    """Pure facet detection (no network): links, mentions, hashtags with
    UTF-8 byte ranges. Mention features carry ``{"handle": …}`` pending
    DID resolution at publish time; link/tag features are final."""
    s = str(text or "")
    facets: List[Dict[str, Any]] = []
    url_spans: List[Tuple[int, int]] = []

    for m in _URL_RE.finditer(s):
        url = m.group(0)
        # trim trailing punctuation, but keep a ')' that closes one in the URL
        while url and url[-1] in _URL_TRAIL:
            if url[-1] == ")" and "(" in url:
                break
            url = url[:-1]
        if len(url) <= len("https://"):
            continue
        start, end = m.start(), m.start() + len(url)
        url_spans.append((start, end))
        facets.append({
            "index": {"byteStart": _byte_offset(s, start),
                      "byteEnd": _byte_offset(s, end)},
            "features": [{"$type": FACET_LINK, "uri": url}],
        })

    def _clear(a: int, b: int) -> bool:
        return all(b <= s0 or a >= e0 for s0, e0 in url_spans)

    for m in _MENTION_RE.finditer(s):
        if not _clear(m.start(), m.end()):
            continue
        facets.append({
            "index": {"byteStart": _byte_offset(s, m.start()),
                      "byteEnd": _byte_offset(s, m.end())},
            "features": [{"$type": FACET_MENTION, "handle": m.group(1)}],
        })

    for m in _TAG_RE.finditer(s):
        if not _clear(m.start(), m.end()):
            continue
        facets.append({
            "index": {"byteStart": _byte_offset(s, m.start()),
                      "byteEnd": _byte_offset(s, m.end())},
            "features": [{"$type": FACET_TAG, "tag": m.group(1)}],
        })

    facets.sort(key=lambda f: f["index"]["byteStart"])
    return facets


# ── image recompress (uploadBlob cap ≈ 1 MB) — module-level so tests stub ────
def _image_dims(data: bytes) -> Optional[Tuple[int, int]]:
    """Best-effort (width, height) via PIL — None when PIL is unavailable or
    the bytes aren't a readable image. Never raises."""
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            return int(img.size[0]), int(img.size[1])
    except Exception:
        return None


def _recompress_image(data: bytes,
                      max_bytes: int) -> Optional[Tuple[bytes, str, Tuple[int, int]]]:
    """Recompress an oversized still under ``max_bytes`` (progressive JPEG
    quality, then downscale). Returns ``(bytes, mime, (w, h))`` or None when
    it cannot be done (PIL missing / unreadable / stubbornly large)."""
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as src:
            img = src.convert("RGB")
        for scale in (1.0, 0.75, 0.5, 0.25):
            w = max(1, int(img.size[0] * scale))
            h = max(1, int(img.size[1] * scale))
            cur = img if scale == 1.0 else img.resize((w, h))
            for q in (85, 70, 55, 40):
                buf = io.BytesIO()
                cur.save(buf, format="JPEG", quality=q)
                if buf.tell() <= max_bytes:
                    return buf.getvalue(), "image/jpeg", (cur.size[0], cur.size[1])
    except Exception:
        _log.debug("image recompress failed", exc_info=True)
    return None


# ── thread progress persistence (per-segment resume, §4.7 / §7.2) ────────────
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_MAX_ENTRIES = 64


def _progress_path() -> Path:
    return pbase.PLATFORMS_DIR / "bluesky_thread_progress.json"


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


def _append_hashtag_block(text: str, hashtags: Any) -> str:
    """§5.3: the composer suggests the tags, the outbound payload carries them
    (facet detection picks them up from the text) — tags already present in
    the text are never duplicated."""
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


def _parse_at_uri(uri: Any) -> Optional[Tuple[str, str, str]]:
    """``at://<repo>/<collection>/<rkey>`` → (repo, collection, rkey)."""
    s = str(uri or "")
    if not s.startswith("at://"):
        return None
    parts = s[5:].split("/")
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


class BlueskyAdapter(PlatformAdapter):
    name = "bluesky"
    label = "Bluesky"
    auth_mode = "app_password"
    automation_tier = "api"          # open protocol, no review, no cost
    # Platform budget is effectively unconstrained (~35k/day); the local
    # budget stays conservative anyway — §4.1 "never trust ourselves".
    default_daily_limit = 100

    def __init__(self) -> None:
        super().__init__()
        self._video_ok: Optional[bool] = None      # feature-detect cache
        self._handle_cache: Dict[str, str] = {}    # handle → did (per process)

    # ── config helpers ────────────────────────────────────────────────────────
    def _pds(self) -> str:
        v = self._config.get("pds") or self._config.get("service")
        return str(v).rstrip("/") if v else DEFAULT_PDS

    def _identifier(self) -> str:
        v = self._config.get("identifier") or self._config.get("handle")
        return str(v).strip() if v else ""

    # ── capability declaration (§4.2 / §4.7) ─────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({
            "formats": ["post", "thread", "image_post"],
            "char_limit": GRAPHEME_LIMIT,
            "title_limit": None,
            "thread": True,
            "native_schedule": False,
            "native_delete": True,
            "analytics": "counts",      # getPosts counts; no impressions API
            "hashtags_max": None,       # no API cap; norms (0–3) are composer's
            "notes": [
                "300-grapheme limit — grapheme clusters, not characters or bytes",
                f"images ≤{IMAGE_MAX_BYTES // 1000} KB each (uploadBlob cap) — "
                "larger stills are recompressed",
                "links/mentions/hashtags are UTF-8 byte-offset richtext facets",
                "video is feature-detected per account "
                "(app.bsky.video.getUploadLimits) — not advertised until known",
                "no impressions in the API — analytics are per-post counts",
                f"PDS (instance) is configuration — default {DEFAULT_PDS}",
            ],
        })
        caps["media"] = {
            "images": {"max": 4, "formats": ["png", "jpeg", "webp"],
                       "max_bytes": IMAGE_MAX_BYTES, "aspect": (0.1, 10.0)},
            "video": ({"max_s": VIDEO_MAX_S, "formats": ["mp4", "webm", "mov"],
                       "max_bytes": VIDEO_MAX_BYTES}
                      if self._video_enabled() else None),
            "alt_text": True,
        }
        return caps

    def _video_enabled(self) -> bool:
        """Honest video declaration: config override, else the cached probe.
        Unknown = not advertised (§4.2: honest, not optimistic)."""
        v = self._config.get("video")
        if v is not None:
            return bool(v)
        return self._video_ok is True

    # ── auth lifecycle (app-password token-paste mode) ───────────────────────
    def connect_url(self, state: str) -> Optional[str]:
        """App-password mode has no browser flow — the Accounts tab shows the
        token-paste descriptor and posts to handle_callback (§11 connect)."""
        return None

    def _create_session(self, identifier: str,
                        secret_value: str) -> Optional[Dict[str, Any]]:
        """com.atproto.server.createSession → the encrypted blob shape, or
        None (``_last_error`` set to a fixed string)."""
        pds = self._pds()
        r = _http("POST", f"{pds}/xrpc/com.atproto.server.createSession",
                  headers={"Content-Type": "application/json"},
                  body=json.dumps({"identifier": identifier,
                                   "password": secret_value}  # pragma: allowlist secret
                                  ).encode("utf-8"))
        j = r.get("json") or {}
        if r.get("status") != 200 or not j.get("accessJwt"):
            self._classify(r)
            if r.get("status") not in (0,):
                self._last_error = E_AUTH
            return None
        now = pbase._now_utc()
        handle = str(j.get("handle") or identifier)
        return {
            "did": str(j.get("did") or ""),
            "handle": handle,
            "account": handle,
            "identifier": str(identifier),
            "access_jwt": j.get("accessJwt"),
            "refresh_jwt": j.get("refreshJwt"),
            "expires_at": (now + timedelta(seconds=_SESSION_TTL_S)
                           ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pds": pds,
            "scopes": ["app_password"],
            "connected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def handle_callback(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Token-paste connect: ``{identifier, app_password}`` → createSession.
        Falls back to the stored provider-key secret + configured identifier
        so re-connect after a restart is one click."""
        try:
            params = dict(params or {})
            identifier = str(params.get("identifier") or params.get("handle")
                             or self._identifier() or "").strip()
            supplied = params.get("app_password") or params.get("password")  # pragma: allowlist secret
            secret_value = str(supplied or self.simple_secret() or "")
            if not identifier or not secret_value:
                self._last_error = E_CONFIG
                self._audit("callback_missing_config")
                return self.status()
            blob = self._create_session(identifier, secret_value)
            if blob is None:
                self._audit("callback_session_failed")
                return self.status()
            self.save_credentials(blob)
            if supplied:                    # keep for silent session re-login
                self.set_simple_secret(str(supplied))
            self._last_error = None
            self._audit("connected", account_resolved=bool(blob.get("handle")))
            return self.status()
        except Exception:
            _log.debug("handle_callback failed", exc_info=True)
            self._last_error = E_INTERNAL
            return self.status()

    def _relogin(self, identifier: Optional[str] = None) -> bool:
        """Recreate the session from the stored app password (refresh JWT dead
        or cold start). True = a fresh session was stored."""
        secret_value = self.simple_secret()
        ident = str(identifier or self._identifier() or "").strip()
        if not secret_value or not ident:
            return False
        blob = self._create_session(ident, str(secret_value))
        if blob is None:
            return False
        self.save_credentials(blob)
        self._audit("session_recreated")
        return True

    def refresh(self) -> bool:
        try:
            creds = self.load_credentials()
            if not isinstance(creds, dict) or not creds.get("access_jwt"):
                return self._relogin()      # cold start from app password
            exp = pbase._parse_iso(creds.get("expires_at"))
            if exp is not None and (exp - pbase._now_utc()).total_seconds() > 120:
                return True                 # not expiring — usable as-is
            rj = creds.get("refresh_jwt")
            pds = str(creds.get("pds") or self._pds())
            if rj:
                r = _http("POST", f"{pds}/xrpc/com.atproto.server.refreshSession",
                          headers={"Authorization": f"Bearer {rj}"})
                j = r.get("json") or {}
                if r.get("status") == 200 and j.get("accessJwt"):
                    now = pbase._now_utc()
                    creds["access_jwt"] = j.get("accessJwt")
                    if j.get("refreshJwt"):
                        creds["refresh_jwt"] = j.get("refreshJwt")
                    if j.get("did"):
                        creds["did"] = str(j["did"])
                    if j.get("handle"):
                        creds["handle"] = str(j["handle"])
                        creds["account"] = str(j["handle"])
                    creds["expires_at"] = (now + timedelta(seconds=_SESSION_TTL_S)
                                           ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    self.save_credentials(creds)
                    self._audit("refreshed")
                    return True
                self._classify(r)
            # refresh JWT dead → re-login from the stored app password
            return self._relogin(identifier=str(creds.get("identifier")
                                                or creds.get("handle") or ""))
        except Exception:
            _log.debug("refresh failed", exc_info=True)
            self._last_error = E_INTERNAL
            return False

    def revoke(self) -> bool:
        try:
            creds = self.load_credentials() or {}
            platform_side = False
            rj = creds.get("refresh_jwt")
            if rj:
                pds = str(creds.get("pds") or self._pds())
                r = _http("POST", f"{pds}/xrpc/com.atproto.server.deleteSession",
                          headers={"Authorization": f"Bearer {rj}"})
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
            # a stored app password alone is NOT a live session
            st["connected"] = bool(creds.get("access_jwt"))
            st["account"] = creds.get("handle") or creds.get("account")
            st["pds"] = str(creds.get("pds") or self._pds())
            st["did"] = creds.get("did")
        except Exception:
            pass
        return st

    def _session(self) -> Optional[Tuple[str, str, str, str]]:
        """Live ``(access_jwt, did, handle, pds)`` — refreshes when expiring.
        None = not usable (never raises).

        Cold start: when no session blob exists but an app password + handle
        ARE stored (the token-paste connect path stores the secret without
        necessarily having run createSession), attempt _relogin once — the
        pasted credential must be enough for publish/fetch_metrics to work
        without an incidental refresh() call establishing the session first."""
        creds = self.load_credentials()
        if not isinstance(creds, dict) or not creds.get("access_jwt"):
            if not self._relogin():
                return None
            creds = self.load_credentials()
            if not isinstance(creds, dict) or not creds.get("access_jwt"):
                return None
        exp = pbase._parse_iso(creds.get("expires_at"))
        if exp is not None and (exp - pbase._now_utc()).total_seconds() <= 60:
            if not self.refresh():
                return None
            creds = self.load_credentials() or {}
            if not creds.get("access_jwt"):
                return None
        return (str(creds["access_jwt"]), str(creds.get("did") or ""),
                str(creds.get("handle") or creds.get("account") or ""),
                str(creds.get("pds") or self._pds()))

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
        _log.debug("bluesky XRPC %s: %s", status, (r.get("text") or "")[:500])
        return out

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Hard validation (grapheme limit, media caps) + blob uploads with
        ≤1 MB recompress + video feature-detect. {ok, prepared, warnings} —
        never raises; text-only targets never touch transport."""
        try:
            target = dict(target or {})
            post = dict(post or {})
            caps = self.capabilities()
            warnings: List[str] = []

            body = unicodedata.normalize(
                "NFC", str(target.get("adapted_body") or post.get("body") or ""))
            segments = [unicodedata.normalize("NFC", s)
                        for s in (target.get("segments") or [])
                        if isinstance(s, str)]
            # §5.3: the suggested hashtag block ships with the payload — on the
            # closing segment for threads — BEFORE the grapheme check so
            # validation covers exactly what publishes (the composer reserved
            # room for the block when it trimmed).
            hashtags = list(target.get("hashtags") or [])
            if hashtags:
                if segments:
                    segments = list(segments)
                    segments[-1] = _append_hashtag_block(segments[-1], hashtags)
                else:
                    body = _append_hashtag_block(body, hashtags)
            limit = target.get("char_limit") or caps.get("char_limit") or GRAPHEME_LIMIT
            if isinstance(limit, int) and limit > 0:
                if segments:
                    over = [i for i, s in enumerate(segments)
                            if grapheme_len(s) > limit]
                    if over:
                        return {"ok": False, "warnings": warnings,
                                "error": f"segment(s) {over} exceed char_limit "
                                         f"({limit} graphemes)"}
                elif grapheme_len(body) > limit:
                    return {"ok": False, "warnings": warnings,
                            "error": f"body exceeds char_limit "
                                     f"({grapheme_len(body)}/{limit} graphemes)"}

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
            for v in videos:
                dur = v.get("duration_s") or (v.get("source") or {}).get("duration_s")
                if dur and float(dur) > VIDEO_MAX_S:
                    return {"ok": False, "warnings": warnings,
                            "error": f"video too long ({float(dur):.0f}s/"
                                     f"{VIDEO_MAX_S}s)"}
            missing_alt = [a for a in images
                           if not (a.get("alt_text")
                                   or (a.get("source") or {}).get("alt_text"))]
            if missing_alt:
                warnings.append(f"{len(missing_alt)} image(s) missing alt text")

            # blob uploads — transport only when there are local files to send
            up_imgs = [a for a in images if a.get("out_path") or a.get("path")]
            up_vids = [a for a in videos if a.get("out_path") or a.get("path")]
            embed_images: List[Dict[str, Any]] = []
            embed_video: Optional[Dict[str, Any]] = None
            if up_imgs or up_vids:
                sess = self._session()
                if not sess:
                    self._last_error = E_NOT_CONNECTED
                    return {"ok": False, "warnings": warnings,
                            "error": E_NOT_CONNECTED}
                access, _did, _handle, pds = sess
                if up_vids and not self._video_available(access, pds):
                    # §4.14: surface the constraint, never silently drop media
                    return {"ok": False, "warnings": warnings, "error": E_VIDEO}
                for a in up_imgs:
                    up = self._upload_image(access, pds, a)
                    if not up.get("ok"):
                        return {"ok": False, "warnings": warnings,
                                "error": up.get("error") or E_MEDIA}
                    embed_images.append(up["embed"])
                for a in up_vids:
                    up = self._upload_video(access, pds, a)
                    if not up.get("ok"):
                        return {"ok": False, "warnings": warnings,
                                "error": up.get("error") or E_MEDIA}
                    embed_video = up["embed"]
            wanted = len(images) + len(videos)
            if wanted > len(up_imgs) + len(up_vids):
                warnings.append(f"{wanted - len(up_imgs) - len(up_vids)} asset(s) "
                                "have no local file yet — not uploaded")

            prepared = {
                "platform": self.name,
                "target_id": target.get("id"),
                "post_id": post.get("id"),
                "format": target.get("format") or "post",
                "title": "",                        # Bluesky has no title field
                "body": body,
                "segments": segments,
                "assets": assets,
                "embed_images": embed_images,
                "embed_video": embed_video,
                "hashtags": list(target.get("hashtags") or []),
                "mentions": list(target.get("mentions") or []),
                "options": dict(target.get("options") or {}),
            }
            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception:
            _log.debug("prepare failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL, "warnings": []}

    # ── media helpers ─────────────────────────────────────────────────────────
    def _upload_blob(self, access: str, pds: str, data: bytes,
                     mime: str) -> Optional[Dict[str, Any]]:
        r = _http("POST", f"{pds}/xrpc/com.atproto.repo.uploadBlob",
                  headers={"Authorization": f"Bearer {access}",
                           "Content-Type": mime},
                  body=data, timeout=60)
        if r.get("status") != 200:
            self._classify(r)
            return None
        blob = (r.get("json") or {}).get("blob")
        return blob if isinstance(blob, dict) else None

    def _upload_image(self, access: str, pds: str,
                      asset: Dict[str, Any]) -> Dict[str, Any]:
        """uploadBlob one still; recompress when over the ~1 MB cap. Fixed
        error strings out; alt text + aspect-ratio hint attached."""
        try:
            p = Path(str(asset.get("out_path") or asset.get("path")))
            try:
                data = p.read_bytes()
            except Exception:
                return {"ok": False, "error": E_MEDIA}
            mime = _IMAGE_MIME.get(p.suffix.lower().lstrip("."),
                                   "application/octet-stream")
            dims = _image_dims(data)
            if len(data) > IMAGE_MAX_BYTES:
                rec = _recompress_image(data, IMAGE_MAX_BYTES)
                if rec is None:
                    self._last_error = E_MEDIA_SIZE
                    return {"ok": False, "error": E_MEDIA_SIZE}
                data, mime, dims = rec
            blob = self._upload_blob(access, pds, data, mime)
            if blob is None:
                return {"ok": False, "error": E_MEDIA}
            alt = (asset.get("alt_text")
                   or (asset.get("source") or {}).get("alt_text") or "")
            embed: Dict[str, Any] = {"image": blob, "alt": str(alt)[:2000]}
            if dims:
                embed["aspectRatio"] = {"width": int(dims[0]),
                                        "height": int(dims[1])}
            return {"ok": True, "embed": embed}
        except Exception:
            _log.debug("image upload failed", exc_info=True)
            return {"ok": False, "error": E_MEDIA}

    def _video_available(self, access: str, pds: str) -> bool:
        """Feature-detect video for this account (config override wins,
        result cached per process). §4.7: the adapter feature-detects."""
        v = self._config.get("video")
        if v is not None:
            return bool(v)
        if self._video_ok is not None:
            return self._video_ok
        r = _http("GET", f"{pds}/xrpc/app.bsky.video.getUploadLimits",
                  headers={"Authorization": f"Bearer {access}"})
        if r.get("status") == 0:
            return False                    # network blip — don't cache
        ok = (r.get("status") == 200
              and bool((r.get("json") or {}).get("canUpload")))
        self._video_ok = ok
        return ok

    def _upload_video(self, access: str, pds: str,
                      asset: Dict[str, Any]) -> Dict[str, Any]:
        """uploadBlob the clip (transcode/downsize is the composer's transform
        plan job, §5.2 — the adapter never silently re-encodes video)."""
        try:
            p = Path(str(asset.get("out_path") or asset.get("path")))
            try:
                data = p.read_bytes()
            except Exception:
                return {"ok": False, "error": E_MEDIA}
            if len(data) > VIDEO_MAX_BYTES:
                self._last_error = E_MEDIA_SIZE
                return {"ok": False, "error": E_MEDIA_SIZE}
            mime = _VIDEO_MIME.get(p.suffix.lower().lstrip("."), "video/mp4")
            blob = self._upload_blob(access, pds, data, mime)
            if blob is None:
                return {"ok": False, "error": E_MEDIA}
            embed: Dict[str, Any] = {"video": blob}
            ar = (asset.get("aspect_ratio")
                  or (asset.get("source") or {}).get("aspect_ratio"))
            if isinstance(ar, dict) and ar.get("width") and ar.get("height"):
                embed["aspectRatio"] = {"width": int(ar["width"]),
                                        "height": int(ar["height"])}
            return {"ok": True, "embed": embed}
        except Exception:
            _log.debug("video upload failed", exc_info=True)
            return {"ok": False, "error": E_MEDIA}

    # ── facet resolution (mentions need a live DID) ──────────────────────────
    def _resolve_handle(self, access: str, pds: str,
                        handle: str) -> Optional[str]:
        h = str(handle or "").strip().lstrip("@")
        if not h:
            return None
        cached = self._handle_cache.get(h)
        if cached:
            return cached
        r = _http("GET",
                  f"{pds}/xrpc/com.atproto.identity.resolveHandle"
                  f"?handle={quote(h)}",
                  headers={"Authorization": f"Bearer {access}"})
        did = str(((r.get("json") or {}).get("did")) or "")
        if r.get("status") == 200 and did:
            self._handle_cache[h] = did
            return did
        return None

    def _facets(self, access: str, pds: str, text: str) -> List[Dict[str, Any]]:
        """detect_facets + mention DID resolution. An unresolvable mention is
        dropped (text keeps the @handle, just unlinked) — degrade loudly in
        the record, never fail the whole post over a dead handle."""
        out: List[Dict[str, Any]] = []
        for f in detect_facets(text):
            feats: List[Dict[str, Any]] = []
            for ft in f.get("features", []):
                if ft.get("$type") == FACET_MENTION:
                    did = self._resolve_handle(access, pds, ft.get("handle", ""))
                    if not did:
                        continue
                    feats.append({"$type": FACET_MENTION, "did": did})
                else:
                    feats.append(ft)
            if feats:
                out.append({"index": f["index"], "features": feats})
        return out

    def _build_embed(self, prepared: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        imgs = [i for i in (prepared.get("embed_images") or [])
                if isinstance(i, dict) and i.get("image")]
        if imgs:
            return {"$type": "app.bsky.embed.images", "images": imgs[:4]}
        vid = prepared.get("embed_video")
        if isinstance(vid, dict) and vid.get("video"):
            out: Dict[str, Any] = {"$type": "app.bsky.embed.video",
                                   "video": vid["video"]}
            if vid.get("aspectRatio"):
                out["aspectRatio"] = vid["aspectRatio"]
            return out
        card = (prepared.get("options") or {}).get("link_card")
        if isinstance(card, dict) and card.get("uri"):
            return {"$type": "app.bsky.embed.external",
                    "external": {"uri": str(card["uri"])[:2048],
                                 "title": str(card.get("title") or "")[:300],
                                 "description":
                                     str(card.get("description") or "")[:1000]}}
        return None

    # ── jitter between thread segments (§12.4 API-citizen posting) ───────────
    def _jitter_bounds(self) -> Tuple[float, float]:
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
    def _progress_get(self, tid: str, fp: str) -> List[Dict[str, str]]:
        with _PROGRESS_LOCK:
            entry = _read_progress().get(tid)
            if isinstance(entry, dict) and entry.get("fp") == fp:
                out = []
                for x in entry.get("posts") or []:
                    if isinstance(x, dict) and x.get("uri"):
                        out.append({"uri": str(x["uri"]),
                                    "cid": str(x.get("cid") or "")})
                return out
        return []

    def _progress_put(self, tid: str, fp: str,
                      posts: List[Dict[str, str]]) -> None:
        with _PROGRESS_LOCK:
            state = _read_progress()
            state[tid] = {"fp": fp, "posts": list(posts),
                          "updated_at": pbase._now_utc().strftime(
                              "%Y-%m-%dT%H:%M:%SZ")}
            _write_progress(state)

    def _progress_clear(self, tid: str) -> None:
        with _PROGRESS_LOCK:
            state = _read_progress()
            if tid in state:
                del state[tid]
                _write_progress(state)

    @staticmethod
    def _post_url(uri: str, handle: str) -> str:
        rkey = str(uri).rstrip("/").rsplit("/", 1)[-1]
        actor = handle
        if not actor:
            parsed = _parse_at_uri(uri)
            actor = parsed[0] if parsed else ""
        return f"{APP_URL}/profile/{actor}/post/{rkey}"

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Single post or reply-chain thread (root/parent {uri, cid} refs).
        Threads publish one segment at a time with jitter; every confirmed
        segment ref persists so a retry resumes, never double-posts."""
        try:
            prepared = dict(prepared or {})
            sess = self._session()
            if not sess:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            access, did, handle, pds = sess
            segments = [s for s in (prepared.get("segments") or [])
                        if isinstance(s, str)]
            texts = segments if segments else [str(prepared.get("body") or "")]
            texts = [unicodedata.normalize("NFC", t) for t in texts]
            has_media = bool(prepared.get("embed_images")
                             or prepared.get("embed_video"))
            if not texts[0].strip() and not has_media:
                self._last_error = "empty_post"
                return {"ok": False, "error": "empty_post"}

            n = len(texts)
            tid = str(prepared.get("target_id") or "")
            fp = hashlib.sha256("\x1f".join(texts).encode("utf-8")).hexdigest()
            done = self._progress_get(tid, fp) if (n > 1 and tid) else []
            if done and len(done) >= n:      # crash landed after the last segment
                self._progress_clear(tid)
                root = done[0]
                return {"ok": True,
                        "post_url": self._post_url(root["uri"], handle),
                        "platform_post_id": root["uri"],
                        "raw": {"uri": root["uri"], "cid": root["cid"],
                                "segments": list(done), "resumed_from": n}}

            needed = n - len(done)
            if self.budget_would_exceed(needed):
                b = self.rate_budget()
                self._last_error = E_BUDGET
                return {"ok": False, "error": E_BUDGET,
                        "defer_until": b.get("reset_at")}

            langs = [str(x) for x in
                     ((prepared.get("options") or {}).get("langs") or []) if x]
            root_ref = dict(done[0]) if done else None
            parent_ref = dict(done[-1]) if done else None
            resumed_from = len(done)
            for i in range(resumed_from, n):
                if i > 0:
                    self._thread_pause()
                record: Dict[str, Any] = {
                    "$type": POST_COLLECTION,
                    "text": texts[i],
                    "createdAt": pbase._now_utc().strftime(
                        "%Y-%m-%dT%H:%M:%SZ"),
                }
                facets = self._facets(access, pds, texts[i])
                if facets:
                    record["facets"] = facets
                if langs:
                    record["langs"] = langs[:3]
                if i == 0:
                    embed = self._build_embed(prepared)
                    if embed:
                        record["embed"] = embed
                if parent_ref:
                    record["reply"] = {"root": root_ref, "parent": parent_ref}
                r = _http("POST", f"{pds}/xrpc/com.atproto.repo.createRecord",
                          headers={"Authorization": f"Bearer {access}",
                                   "Content-Type": "application/json"},
                          body=json.dumps({"repo": did or handle,
                                           "collection": POST_COLLECTION,
                                           "record": record}).encode("utf-8"))
                j = r.get("json") or {}
                uri = str(j.get("uri") or "")
                cid = str(j.get("cid") or "")
                if r.get("status") in (200, 201) and uri:
                    ref = {"uri": uri, "cid": cid}
                    done.append(ref)
                    if root_ref is None:
                        root_ref = dict(ref)
                    parent_ref = dict(ref)
                    self.consume_budget(1)
                    if n > 1 and tid:
                        self._progress_put(tid, fp, done)
                    continue
                # honest failure: fixed string out, resume state preserved
                env = self._classify(r)
                if done and n > 1:
                    env["resume_index"] = len(done)
                    env["segments"] = [d["uri"] for d in done]
                return env

            if tid:
                self._progress_clear(tid)
            root = done[0]
            return {"ok": True,
                    "post_url": self._post_url(root["uri"], handle),
                    "platform_post_id": root["uri"],
                    "raw": {"uri": root["uri"], "cid": root["cid"],
                            "segments": list(done),
                            "resumed_from": resumed_from}}
        except Exception:
            _log.debug("publish failed", exc_info=True)
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        try:
            sess = self._session()
            if not sess:
                self._last_error = E_NOT_CONNECTED
                return {"ok": False, "error": E_NOT_CONNECTED}
            access, did, _handle, pds = sess
            parsed = _parse_at_uri(platform_post_id)
            if parsed is None:
                return {"ok": False, "error": "invalid_post_id"}
            repo, collection, rkey = parsed
            r = _http("POST", f"{pds}/xrpc/com.atproto.repo.deleteRecord",
                      headers={"Authorization": f"Bearer {access}",
                               "Content-Type": "application/json"},
                      body=json.dumps({"repo": repo or did,
                                       "collection": collection,
                                       "rkey": rkey}).encode("utf-8"))
            if r.get("status") in (200, 204):
                self._audit("delete", deleted=True)
                return {"ok": True, "deleted": True}
            return self._classify(r)
        except Exception:
            self._last_error = E_INTERNAL
            return {"ok": False, "error": E_INTERNAL}

    # ── analytics path (§8.2 unified keys; missing ≠ zero, no impressions) ───
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        try:
            pid = str(platform_post_id or "").strip()
            sess = self._session()
            if not sess or not pid.startswith("at://"):
                return None
            access, _did, _handle, pds = sess
            r = _http("GET",
                      f"{pds}/xrpc/app.bsky.feed.getPosts"
                      f"?uris={quote(pid, safe='')}",
                      headers={"Authorization": f"Bearer {access}"})
            if r.get("status") != 200:
                self._classify(r)
                return None
            posts = (r.get("json") or {}).get("posts") or []
            if not (isinstance(posts, list) and posts):
                return None
            p = posts[0] if isinstance(posts[0], dict) else {}

            def _num(k: str) -> Optional[int]:
                v = p.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return None
                return int(v)

            out: Dict[str, Any] = {}
            v = _num("likeCount")
            if v is not None:
                out["likes"] = v
            v = _num("replyCount")
            if v is not None:
                out["comments"] = v
            rp, qt = _num("repostCount"), _num("quoteCount")
            if rp is not None or qt is not None:
                out["shares"] = (rp or 0) + (qt or 0)   # repost + quote (§8.2)
            # no impressions/saves/clicks on Bluesky — absent, never zero
            return out or None
        except Exception:
            return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        try:
            sess = self._session()
            if not sess:
                return None
            access, did, handle, pds = sess
            actor = did or handle
            if not actor:
                return None
            r = _http("GET",
                      f"{pds}/xrpc/app.bsky.actor.getProfile"
                      f"?actor={quote(actor, safe='')}",
                      headers={"Authorization": f"Bearer {access}"})
            if r.get("status") != 200:
                return None
            j = r.get("json") or {}
            out: Dict[str, Any] = {}
            if isinstance(j.get("followersCount"), int):
                out["followers"] = j["followersCount"]
            if isinstance(j.get("postsCount"), int):
                out["posts"] = j["postsCount"]
            return out or None
        except Exception:
            return None


ADAPTER = BlueskyAdapter
