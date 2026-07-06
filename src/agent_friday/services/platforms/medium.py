"""
Medium adapter — legacy-token API or assisted handoff (docs/CONTENT_PIPELINE_SPEC.md §4.11).

Medium's official REST API v1 (``api.medium.com``) is **deprecated — new
integration tokens are no longer issued**. This adapter feature-detects:

  * **Legacy integration token stored** → full automation via the v1 API:
    ``GET /v1/me`` resolves (and caches) the author id, then
    ``POST /v1/users/{authorId}/posts`` publishes with title, ``contentFormat``
    (markdown|html), content, ≤5 tags, ``canonicalUrl``, ``publishStatus``
    (draft|public|unlisted), license, and the notify-followers flag. The
    ``canonicalUrl`` field matters (§4.11): repurposed content carries its
    original home automatically — SEO hygiene, never duplicate-content spam.
    Medium's ``title`` field is SEO/listing-only, so the visible title is
    prepended into the content when the body doesn't already carry one.
  * **No token** → the same assisted-handoff package pattern as Substack
    (§4.10 mode 1): title + markdown + HTML + images exported to a local
    folder, open-editor descriptor (Medium's editor accepts pasted markdown
    well), target SENT until a canonical URL is attached (``attach_url``) —
    SENT → CONFIRMED keeps the queue truthful.
  * **Headless mode is a STUB** behind ``headless_enabled`` (default OFF) —
    the opt-in last resort of §4.11 is intentionally NOT implemented; enabling
    the flag surfaces a §4.14 choice envelope instead of silently falling a
    rung. A stored token always outranks the flag.

Auth is ``token`` (token-paste in Accounts — there is no OAuth flow for a
deprecated API): the legacy integration token lives in the credential store as
provider key ``platform_medium``; verified account facts (author id, username
— never the secret) live in the encrypted ``~/.friday/platforms/medium.cred``
blob. ``automation_tier`` reflects the live feature detection so §4.14
declarations stay honest.

Analytics: none via API (partner dashboard only) — ``fetch_metrics`` returns
None and ``capabilities()`` says so; the dashboard renders "manual/none"
honestly rather than pretending (§4.11).

All transport goes through the module-level ``_http_json`` indirection so
tests stub it — the adapter itself never opens a socket in tests. Adapter
errors are externalized as fixed content-free strings (§12.5); API payloads
are untrusted data — size-capped, whitelisted, never instructions (§8.6).

Config keys (``~/.friday/platforms.json`` → "medium"):
  export_dir: str        — override the handoff package folder (default
                           ``~/.friday/content/medium_exports``)
  headless_enabled: bool — feature flag, default False (stub only)
  daily_post_limit: int  — local rate budget (base convention)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.services.platforms import base as _base
from agent_friday.services.platforms.base import PlatformAdapter

# ── fixed content-free error strings (§12.5) ─────────────────────────────────
ERR_PACKAGE_WRITE = "package_write_failed"
ERR_HEADLESS_UNAVAILABLE = "headless_mode_not_implemented"
ERR_NO_TOKEN = "no_integration_token"                  # pragma: allowlist secret
ERR_TOKEN_REJECTED = "integration_token_rejected"      # pragma: allowlist secret
ERR_API_UNAVAILABLE = "api_unreachable"
ERR_API_ERROR = "api_error"
ERR_UNKNOWN_HANDOFF = "unknown_handoff_id"
ERR_INVALID_URL = "invalid_url"
ERR_NOT_SUPPORTED = "not_supported"

# ── endpoints (design-time snapshot, mid-2026 — §4.2 volatility warning) ─────
_API_BASE = "https://api.medium.com/v1"
_EDITOR_URL = "https://medium.com/new-story"
_API_MAX_BYTES = 1_048_576            # untrusted response cap (§8.6)

# v1 API limits: title is SEO/listing-only and capped; tags ≤5, 25 chars each.
TITLE_LIMIT = 100
MAX_TAGS = 5
TAG_MAX_CHARS = 25
PUBLISH_STATUSES = ("public", "draft", "unlisted")
LICENSES = frozenset({
    "all-rights-reserved", "cc-40-by", "cc-40-by-sa", "cc-40-by-nd",
    "cc-40-by-nc", "cc-40-by-nc-nd", "cc-40-by-nc-sa", "cc-40-zero",
    "public-domain",
})

_URL_RE = re.compile(r"^https?://\S+$")
_HANDOFFS_LOCK = threading.Lock()

_HANDOFF_INSTRUCTIONS = (
    "Open medium.com/new-story, paste post.md (Medium's editor accepts pasted "
    "markdown), add images from the package folder, set the canonical link "
    "under Story settings → Advanced when one is listed, then publish. Paste "
    "the published URL back to confirm."
)


def _http_json(method: str, url: str, bearer: str,
               payload: Optional[Dict[str, Any]] = None,
               timeout: float = 15.0):
    """Module-level transport indirection — the ONLY network path in this
    adapter. Tests monkeypatch this symbol. Returns ``(status_code, dict)``;
    raises on transport failure (the caller maps that to a fixed string).
    The bearer secret rides the header only — never logged, never returned."""
    import urllib.error
    import urllib.request
    body = None
    headers = {
        "Accept": "application/json",
        "Accept-Charset": "utf-8",
        "User-Agent": "Friday-Content/1.0",
        "Authorization": "Bearer " + str(bearer),
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_API_MAX_BYTES)
            code = int(getattr(resp, "status", None) or resp.getcode() or 0)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()[:_API_MAX_BYTES]
        except Exception:
            raw = b""
        code = int(e.code)
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
        parsed = parsed if isinstance(parsed, dict) else {}
    except Exception:
        parsed = {}
    return code, parsed


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inline_md(text: str) -> str:
    """Escape-then-tag inline markdown (kept in step with substack.py's
    helper — duplicated on purpose so parallel adapter work stays decoupled)."""
    import html as _html
    t = _html.escape(text)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def _md_to_html(md: str) -> str:
    """Tiny dependency-free markdown → HTML for the package's convenience
    copy (Medium's editor pastes markdown natively; HTML is a bonus)."""
    import html as _html
    out: List[str] = []
    for block in re.split(r"\n\s*\n", str(md or "").strip()):
        b = block.strip()
        if not b:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", b)
        if m and "\n" not in b:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline_md(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)\s]+)\)$", b)
        if m:
            out.append('<img src="%s" alt="%s"/>' % (
                _html.escape(m.group(2), quote=True),
                _html.escape(m.group(1), quote=True)))
            continue
        out.append("<p>" + _inline_md(b).replace("\n", "<br/>") + "</p>")
    return "\n".join(out)


def _normalize_tags(tags) -> List[str]:
    """Medium tags: strip '#', dedupe (casefold), 25 chars each, 5 max."""
    out: List[str] = []
    seen = set()
    for t in tags or []:
        s = str(t).strip().lstrip("#").strip()[:TAG_MAX_CHARS]
        k = s.casefold()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
        if len(out) >= MAX_TAGS:
            break
    return out


def _canonical_url(opts: Dict[str, Any]) -> str:
    """The §4.11 canonicalUrl — validated or empty, never garbage outbound."""
    o = opts or {}
    u = str(o.get("canonical_url") or o.get("canonicalUrl") or "").strip()
    return u if len(u) <= 2048 and _URL_RE.match(u) else ""


def _publish_status(opts: Dict[str, Any]) -> str:
    o = opts or {}
    s = str(o.get("publish_status") or o.get("publishStatus") or "").strip().lower()
    return s if s in PUBLISH_STATUSES else "public"


def _content_with_title(title: str, body: str, fmt: str) -> str:
    """Medium's ``title`` field is SEO/listing-only — the visible title must
    live in the content itself (v1 API docs), so prepend one when absent."""
    if not title:
        return body
    if fmt == "html":
        if body.lstrip()[:200].lower().startswith("<h1"):
            return body
        import html as _html
        return f"<h1>{_html.escape(title)}</h1>\n{body}"
    if body.lstrip().startswith("#"):
        return body
    return f"# {title}\n\n{body}"


class MediumAdapter(PlatformAdapter):
    name = "medium"
    label = "Medium"
    auth_mode = "token"               # §4.2: legacy integration token only
    default_daily_limit = 5           # conservative: long-form articles

    # ── §4.11 feature detection drives the declared §4.14 rung ───────────────
    @property
    def automation_tier(self) -> str:
        """A stored legacy integration token means full v1-API publishing;
        without one the honest declared rung is the assisted handoff."""
        return "api" if self._api_available() else "assisted_handoff"

    def _api_secret(self) -> Optional[str]:
        return self.simple_secret()   # provider key "platform_medium"

    def _api_available(self) -> bool:
        return bool(self._api_secret())

    def _headless_flag(self) -> bool:
        return bool(self._config.get("headless_enabled", False))

    def _export_root(self) -> Path:
        d = self._config.get("export_dir")
        if d:
            return Path(str(d))
        # Derive from the live base constant (tests monkeypatch PLATFORMS_DIR):
        # ~/.friday/platforms → ~/.friday/content/medium_exports
        return Path(_base.PLATFORMS_DIR).parent / "content" / "medium_exports"

    def _handoffs_path(self) -> Path:
        return self._export_root() / "handoffs.json"

    # ── handoff ledger (pending SENT packages, survives restarts) ────────────
    def _read_handoffs(self) -> Dict[str, Any]:
        try:
            path = self._handoffs_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _write_handoffs(self, state: Dict[str, Any]) -> None:
        path = self._handoffs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ── capability declaration (§4.2 row, honest either way) ─────────────────
    def capabilities(self) -> Dict[str, Any]:
        api = self._api_available()
        caps = super().capabilities()
        if api:
            notes = [
                "legacy integration token detected — direct publishing via "
                "the deprecated v1 API",
                "canonicalUrl is set automatically for repurposed content "
                "(SEO hygiene, §4.11)",
                "analytics are not exposed by the API (partner dashboard "
                "only)",
            ]
        else:
            notes = [
                "Medium's v1 API is deprecated — new integration tokens are "
                "no longer issued",
                "assisted handoff: Friday builds the export package, you "
                "paste into the editor",
                "paste a legacy integration token in Accounts to enable "
                "direct API publishing",
                "analytics are not exposed by the API (partner dashboard "
                "only)",
                "headless automation is an opt-in stub — not implemented",
            ]
        caps.update({
            "formats": ["article", "post"],
            "char_limit": None,           # long-form; no hard platform limit
            "title_limit": TITLE_LIMIT,   # v1 API: title is SEO-only, ≤100
            "thread": False,
            "native_schedule": False,     # the v1 API cannot schedule
            "native_delete": False,       # …and has no delete endpoint
            "analytics": "none",          # §4.11 — never pretend
            "hashtags_max": MAX_TAGS,     # Medium "tags": ≤5, 25 chars each
            "notes": notes,
        })
        media = caps.get("media") or {}
        media["images"] = {"max": 50, "formats": ["png", "jpeg", "gif", "webp"],
                           "max_bytes": 25 * 1024 * 1024, "aspect": (0.1, 10.0)}
        media["video"] = None             # §4.2: video by embed link only
        media["alt_text"] = True          # markdown/HTML alt attributes
        caps["media"] = media
        return caps

    # ── auth lifecycle (token-paste — no OAuth for a deprecated API) ─────────
    def connect_url(self, state: str) -> Optional[str]:
        return None                       # token-paste mode (§4.11)

    def refresh(self) -> bool:
        # Legacy tokens have no refresh flow, and the assisted handoff needs
        # no credentials at all — the adapter is always usable.
        return True

    def verify_credentials(self) -> Dict[str, Any]:
        """Feature-detect + author lookup: ``GET /v1/me`` with the stored
        legacy token; caches account facts (never the secret) in the
        encrypted blob. → {ok, account, author_id} | {ok: False, error}."""
        tok = self._api_secret()
        if not tok:
            return {"ok": False, "error": ERR_NO_TOKEN}
        try:
            code, data = _http_json("GET", _API_BASE + "/me", tok)
        except Exception:
            self._last_error = ERR_API_UNAVAILABLE
            return {"ok": False, "error": ERR_API_UNAVAILABLE}
        if code == 401:
            self._last_error = ERR_TOKEN_REJECTED
            self._audit("verify_failed", reason="rejected")
            return {"ok": False, "error": ERR_TOKEN_REJECTED}
        d = data.get("data") if isinstance(data.get("data"), dict) else {}
        author_id = str(d.get("id") or "")[:128]
        if code != 200 or not author_id:
            self._last_error = ERR_API_ERROR
            return {"ok": False, "error": ERR_API_ERROR}
        account = str(d.get("username") or d.get("name") or "")[:128]
        acct_url = str(d.get("url") or "")[:2048]
        blob = {
            "account": account,
            "author_id": author_id,
            "account_url": acct_url if _URL_RE.match(acct_url) else None,
            "scopes": ["basicProfile", "publishPost"],
            "verified_at": _now_iso(),
        }
        self.save_credentials(blob)
        self._audit("verified", has_account=bool(account))
        return {"ok": True, "account": account, "author_id": author_id}

    def status(self) -> Dict[str, Any]:
        st = super().status()
        api = self._api_available()
        st["connected"] = api             # connected == token present (§4.11)
        st["mode"] = "api" if api else "assisted_handoff"
        st["headless_enabled"] = self._headless_flag()
        return st

    # ── prepare (validation + §4.11 canonicalUrl hygiene) ────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        res = super().prepare(target, post)
        if not res.get("ok"):
            return res
        prepared = res["prepared"]
        warnings = res.setdefault("warnings", [])
        # Articles want a title — fall back to the post's working title.
        if not prepared.get("title") and isinstance(post, dict):
            prepared["title"] = str(post.get("title") or "")
        if not prepared.get("title"):
            warnings.append("article has no title")
        elif len(prepared["title"]) > TITLE_LIMIT:
            warnings.append(
                f"title exceeds {TITLE_LIMIT} chars — will be truncated")
        opts = prepared.get("options") or {}
        raw_canonical = str(opts.get("canonical_url")
                            or opts.get("canonicalUrl") or "").strip()
        canonical = _canonical_url(opts)
        if raw_canonical and not canonical:
            warnings.append("canonical URL invalid — ignored")
        prepared["canonical_url"] = canonical
        ps = str(opts.get("publish_status")
                 or opts.get("publishStatus") or "").strip().lower()
        if ps and ps not in PUBLISH_STATUSES:
            warnings.append("publish_status invalid — defaulting to public")
        if len(prepared.get("hashtags") or []) > MAX_TAGS:
            warnings.append(
                f"Medium accepts at most {MAX_TAGS} tags — extra tags dropped")
        prepared["mode"] = "api" if self._api_available() else "assisted_handoff"
        return res

    # ── publish path — feature-detected (§4.11) ──────────────────────────────
    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Legacy token → full v1-API publish; otherwise the Substack-style
        assisted-handoff package. The result is honest about which rung it
        used — never a silent fall (§4.14). A stored token outranks the
        headless flag (headless is the last resort, and a stub)."""
        prepared = dict(prepared or {})
        tok = self._api_secret()
        if tok:
            return self._publish_api(prepared, tok)
        if self._headless_flag():
            # STUB (§4.11 last resort): Playwright driving is intentionally
            # not implemented. Never silently fall a rung — surface it.
            self._last_error = ERR_HEADLESS_UNAVAILABLE
            return {
                "ok": False,
                "error": ERR_HEADLESS_UNAVAILABLE,
                "degraded": True,
                "requires_user_choice": True,
                "declared_rung": self.automation_tier,
                "options": ["assisted_handoff", "clipboard"],
                "reason": "headless_stub",
            }
        return self._publish_handoff(prepared)

    def _api_payload(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        opts = prepared.get("options") or {}
        title = str(prepared.get("title") or "")[:TITLE_LIMIT]
        fmt = ("html" if str(opts.get("content_format") or "").strip().lower()
               == "html" else "markdown")
        payload: Dict[str, Any] = {
            "title": title,
            "contentFormat": fmt,
            "content": _content_with_title(
                title, str(prepared.get("body") or ""), fmt),
            "publishStatus": _publish_status(opts),
        }
        tags = _normalize_tags(prepared.get("hashtags") or [])
        if tags:
            payload["tags"] = tags
        canonical = (_canonical_url({"canonical_url": prepared.get("canonical_url")})
                     or _canonical_url(opts))
        if canonical:
            payload["canonicalUrl"] = canonical      # §4.11 — SEO hygiene
        lic = str(opts.get("license") or "").strip().lower()
        if lic in LICENSES:
            payload["license"] = lic
        if "notify_followers" in opts or "notifyFollowers" in opts:
            payload["notifyFollowers"] = bool(
                opts.get("notify_followers", opts.get("notifyFollowers")))
        return payload

    def _publish_api(self, prepared: Dict[str, Any], tok: str) -> Dict[str, Any]:
        """``POST /v1/users/{authorId}/posts`` — the full-automation rung."""
        author_id = ""
        creds = self.load_credentials()
        if isinstance(creds, dict):
            author_id = str(creds.get("author_id") or "")
        if not author_id:
            v = self.verify_credentials()
            if not v.get("ok"):
                return {"ok": False, "error": v.get("error") or ERR_API_ERROR}
            author_id = str(v.get("author_id") or "")
            if not author_id:
                self._last_error = ERR_API_ERROR
                return {"ok": False, "error": ERR_API_ERROR}
        try:
            code, data = _http_json(
                "POST", f"{_API_BASE}/users/{author_id}/posts", tok,
                self._api_payload(prepared))
        except Exception:
            self._last_error = ERR_API_UNAVAILABLE
            return {"ok": False, "error": ERR_API_UNAVAILABLE}
        if code == 401:
            # Declared rung is API — never silently fall to handoff (§4.14).
            self._last_error = ERR_TOKEN_REJECTED
            self._audit("publish_auth_failed")
            return {"ok": False, "error": ERR_TOKEN_REJECTED}
        d = data.get("data") if isinstance(data.get("data"), dict) else {}
        pid = str(d.get("id") or "")[:128]
        if code not in (200, 201) or not pid:
            self._last_error = ERR_API_ERROR
            return {"ok": False, "error": ERR_API_ERROR}
        url = str(d.get("url") or "")[:2048]
        if not _URL_RE.match(url):
            url = f"https://medium.com/p/{pid}"
        self.consume_budget()
        self._audit("publish", mode="api", post=pid)
        # Sanitized whitelist — platform payloads are untrusted data (§8.6).
        raw = {k: str(d.get(k))[:2048]
               for k in ("id", "url", "publishStatus", "canonicalUrl", "license")
               if d.get(k) is not None}
        return {"ok": True, "post_url": url, "platform_post_id": pid, "raw": raw}

    def _publish_handoff(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Build the export package + open-editor descriptor (§4.10 pattern).
        The result is a HANDOFF: the pipeline records the target SENT, and it
        only becomes CONFIRMED when a URL is attached."""
        try:
            handoff_id = "med_" + uuid.uuid4().hex[:10]
            pkg = self._export_root() / handoff_id
            (pkg / "images").mkdir(parents=True, exist_ok=True)

            title = str(prepared.get("title") or "")
            body_md = str(prepared.get("body") or "")
            canonical = (_canonical_url(
                {"canonical_url": prepared.get("canonical_url")})
                or _canonical_url(prepared.get("options") or {}))
            tags = _normalize_tags(prepared.get("hashtags") or [])
            warnings: List[str] = []

            # Copy local assets into the package (missing warn, never fail).
            copied: List[str] = []
            missing = 0
            for a in prepared.get("assets") or []:
                if not isinstance(a, dict):
                    continue
                src = a.get("out_path") or a.get("path") or a.get("filename") or ""
                try:
                    src_path = Path(str(src))
                    if src and src_path.is_file():
                        dest = pkg / "images" / src_path.name
                        shutil.copyfile(src_path, dest)
                        copied.append(src_path.name)
                    else:
                        missing += 1
                except Exception:
                    missing += 1
            if missing:
                warnings.append(f"{missing} asset(s) missing — not copied")

            front = ("---\n"
                     f"title: {json.dumps(title)}\n"
                     f"canonical_url: {json.dumps(canonical or None)}\n"
                     f"tags: {json.dumps(tags)}\n"
                     f"handoff_id: {handoff_id}\n"
                     "---\n\n")
            (pkg / "post.md").write_text(front + body_md, encoding="utf-8")
            (pkg / "post.html").write_text(_md_to_html(body_md), encoding="utf-8")

            descriptor = {
                "action": "open_editor",
                "editor_url": _EDITOR_URL,
                "package_dir": str(pkg),
                "copy_file": str(pkg / "post.md"),
                "canonical_url": canonical or None,
                "instructions": _HANDOFF_INSTRUCTIONS,
            }
            meta = {
                "handoff_id": handoff_id,
                "title": title,
                "canonical_url": canonical or None,
                "tags": tags,
                "format": prepared.get("format") or "article",
                "target_id": prepared.get("target_id"),
                "post_id": prepared.get("post_id"),
                "created_at": _now_iso(),
                "editor_url": _EDITOR_URL,
                "status": "SENT",
                "images": copied,
            }
            (pkg / "meta.json").write_text(json.dumps(meta, indent=2),
                                           encoding="utf-8")

            with _HANDOFFS_LOCK:
                state = self._read_handoffs()
                state[handoff_id] = {
                    "title": title,
                    "canonical_url": canonical or None,
                    "package_dir": str(pkg),
                    "target_id": prepared.get("target_id"),
                    "post_id": prepared.get("post_id"),
                    "created_at": meta["created_at"],
                    "url": None,
                    "confirmed_at": None,
                }
                self._write_handoffs(state)

            self.consume_budget()
            self._audit("publish", mode="assisted_handoff", handoff=handoff_id)
            return {
                "ok": True,
                "post_url": _EDITOR_URL,
                "platform_post_id": handoff_id,
                "handoff": True,
                "confirmed": False,
                "raw": {
                    "handoff": True,
                    "status": "SENT",
                    "descriptor": descriptor,
                    "package_dir": str(pkg),
                    "warnings": warnings,
                },
            }
        except Exception:
            # §12.5: externalized errors are fixed content-free strings.
            self._last_error = ERR_PACKAGE_WRITE
            return {"ok": False, "error": ERR_PACKAGE_WRITE}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        """Retract a pending handoff (remove the package before paste).
        Anything live on Medium stays — the v1 API has no delete endpoint."""
        hid = str(platform_post_id or "")
        try:
            with _HANDOFFS_LOCK:
                state = self._read_handoffs()
                entry = state.get(hid)
                if not isinstance(entry, dict):
                    if hid.startswith("med_"):
                        return {"ok": True, "deleted": False}   # idempotent
                    return {"ok": False, "error": ERR_NOT_SUPPORTED}
                if entry.get("url"):
                    return {"ok": False, "error": ERR_NOT_SUPPORTED}
                pkg = str(entry.get("package_dir") or "")
                state.pop(hid, None)
                self._write_handoffs(state)
            if pkg:
                shutil.rmtree(pkg, ignore_errors=True)
            self._audit("handoff_retracted", handoff=hid)
            return {"ok": True, "deleted": True}
        except Exception:
            self._last_error = ERR_PACKAGE_WRITE
            return {"ok": False, "error": ERR_PACKAGE_WRITE}

    # ── SENT → CONFIRMED (handoff mode, §4.10 pattern) ───────────────────────
    def attach_url(self, handoff_id: str, url: str) -> Dict[str, Any]:
        """Attach the published canonical URL to a pending handoff — the
        pipeline's SENT → CONFIRMED transition hook."""
        u = str(url or "").strip()
        if len(u) > 2048 or not _URL_RE.match(u):
            return {"ok": False, "error": ERR_INVALID_URL}
        try:
            with _HANDOFFS_LOCK:
                state = self._read_handoffs()
                entry = state.get(str(handoff_id))
                if not isinstance(entry, dict):
                    return {"ok": False, "error": ERR_UNKNOWN_HANDOFF}
                entry["url"] = u
                entry["confirmed_at"] = _now_iso()
                self._write_handoffs(state)
            self._audit("url_attached", handoff=str(handoff_id))
            return {"ok": True, "handoff_id": str(handoff_id),
                    "post_url": u, "confirmed": True}
        except Exception:
            self._last_error = ERR_PACKAGE_WRITE
            return {"ok": False, "error": ERR_PACKAGE_WRITE}

    def pending_handoffs(self) -> Dict[str, Any]:
        """Handoffs still awaiting a URL (queue truthfulness surface)."""
        try:
            state = self._read_handoffs()
            pending = [{"handoff_id": hid, "title": e.get("title"),
                        "package_dir": e.get("package_dir"),
                        "created_at": e.get("created_at")}
                       for hid, e in state.items()
                       if isinstance(e, dict) and not e.get("url")]
            return {"ok": True, "pending": pending}
        except Exception:
            return {"ok": True, "pending": []}

    # ── analytics path (§4.11: none via API — honest) ────────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        return None                       # partner dashboard only; never fabricate

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        return None


ADAPTER = MediumAdapter
