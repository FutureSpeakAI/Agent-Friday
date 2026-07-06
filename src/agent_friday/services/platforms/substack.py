"""
Substack adapter — assisted handoff (docs/CONTENT_PIPELINE_SPEC.md §4.10).

Substack has **no official API** (unofficial endpoints are cookie-authed and
ToS-gray — not a foundation), so this adapter never pretends to automate:

  * ``publish()`` produces an **export package** — title, subtitle, markdown +
    HTML body, and images copied into a local folder — plus an open-editor
    descriptor. One click copies the body and opens the editor; the user
    pastes and hits publish (or uses Substack's own in-editor scheduling,
    which is why the capability matrix marks native_schedule "in-editor").
  * The target stays **SENT** until a canonical URL is attached
    (``attach_url``) — either pasted back by the user or auto-confirmed by
    matching the publication's RSS feed (``poll_rss``). SENT → CONFIRMED on
    URL attach keeps the queue truthful (§4.10).
  * **Headless mode is a STUB** behind ``headless_enabled`` (default OFF).
    Playwright driving is intentionally NOT implemented; enabling the flag
    surfaces a §4.14 choice envelope instead of silently falling a rung.

Auth is ``manual`` — no tokens exist for a platform with no API. The base
credential-blob machinery stays available (a future opt-in headless session
would live in ``~/.friday/platforms/substack.cred``), and ``connected`` in
``status()`` means "publication URL configured".

All transport (RSS poll only — publishing itself never touches the network)
goes through the module-level ``_http_get`` indirection so tests stub it.
Errors are externalized as fixed content-free strings (§12.5); RSS payloads
are untrusted data — size-capped, defensively parsed, never instructions
(§8.6/§12.5).

Config keys (``~/.friday/platforms.json`` → "substack"):
  publication_url: str   — e.g. "https://mynewsletter.substack.com"
  export_dir: str        — override the package folder (default
                           ``~/.friday/content/substack_exports``)
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
from urllib.parse import urlsplit

from agent_friday.services.platforms import base as _base
from agent_friday.services.platforms.base import PlatformAdapter

# ── fixed content-free error strings (§12.5) ─────────────────────────────────
ERR_PACKAGE_WRITE = "package_write_failed"
ERR_HEADLESS_UNAVAILABLE = "headless_mode_not_implemented"
ERR_NOT_CONFIGURED = "publication_url_not_configured"
ERR_RSS_UNAVAILABLE = "rss_fetch_failed"
ERR_RSS_PARSE = "rss_parse_failed"
ERR_UNKNOWN_HANDOFF = "unknown_handoff_id"
ERR_INVALID_URL = "invalid_url"
ERR_NOT_SUPPORTED = "not_supported"

# ~100 KB — Substack's email-clip threshold is a composer warning (§4.10).
EMAIL_CLIP_BYTES = 100_000

_URL_RE = re.compile(r"^https?://\S+$")
_RSS_MAX_BYTES = 1_048_576        # untrusted input: cap the feed read (§8.6)
_RSS_MAX_ITEMS = 50

_HANDOFFS_LOCK = threading.Lock()

_HANDOFF_INSTRUCTIONS = (
    "Open the editor, paste post.md (Substack's editor accepts markdown), "
    "attach images from the package folder, then publish or use Substack's "
    "in-editor scheduler. Paste the published URL back to confirm."
)


def _http_get(url: str, timeout: float = 15.0) -> bytes:
    """Module-level transport indirection — the ONLY network path in this
    adapter (RSS confirmation poll). Tests monkeypatch this symbol."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Friday-Content/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(_RSS_MAX_BYTES)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_title(text: str) -> str:
    """Whitespace-collapsed casefold — RSS titles vs handoff titles (§4.10)."""
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _inline_md(text: str) -> str:
    """Escape-then-tag inline markdown (bold/italic/links)."""
    import html as _html
    t = _html.escape(text)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def _md_to_html(md: str) -> str:
    """Tiny dependency-free markdown → HTML for the package's convenience
    copy (Substack's editor pastes markdown natively; HTML is a bonus)."""
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


def _parse_rss(raw: bytes) -> Optional[List[Dict[str, str]]]:
    """Defensive RSS/Atom item extraction — untrusted data, never
    instructions (§8.6). Returns [{title, link}] or None on parse failure."""
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw[:_RSS_MAX_BYTES])
    except Exception:
        return None
    items: List[Dict[str, str]] = []
    try:
        nodes = root.findall(".//item")
        if not nodes:   # Atom fallback
            nodes = [n for n in root.iter() if n.tag.endswith("entry")]
        for node in nodes[:_RSS_MAX_ITEMS]:
            title, link = "", ""
            for child in node:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "title":
                    title = (child.text or "")[:500]
                elif tag == "link":
                    link = (child.text or child.get("href") or "")[:2048]
            if title and _URL_RE.match(link or ""):
                items.append({"title": title, "link": link})
    except Exception:
        return None
    return items


class SubstackAdapter(PlatformAdapter):
    name = "substack"
    label = "Substack"
    auth_mode = "manual"                  # §4.2: auth "none" — no API, no tokens
    automation_tier = "assisted_handoff"  # §4.14 declared rung
    default_daily_limit = 5               # conservative: it's a newsletter

    # ── configuration ────────────────────────────────────────────────────────
    def _publication_url(self) -> str:
        u = str(self._config.get("publication_url") or "").strip().rstrip("/")
        return u if _URL_RE.match(u) else ""

    def _headless_flag(self) -> bool:
        return bool(self._config.get("headless_enabled", False))

    def _export_root(self) -> Path:
        d = self._config.get("export_dir")
        if d:
            return Path(str(d))
        # Derive from the live base constant (tests monkeypatch PLATFORMS_DIR):
        # ~/.friday/platforms → ~/.friday/content/substack_exports
        return Path(_base.PLATFORMS_DIR).parent / "content" / "substack_exports"

    def _handoffs_path(self) -> Path:
        return self._export_root() / "handoffs.json"

    def _editor_url(self) -> Optional[str]:
        pub = self._publication_url()
        return f"{pub}/publish/post" if pub else None

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

    # ── capability declaration (§4.2 row, honest) ────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({
            "formats": ["article", "post"],
            "char_limit": None,           # long-form; no hard platform limit
            "title_limit": None,
            "thread": False,
            # §4.2 "✅ (in-editor)": the handoff package is produced at arm
            # time; the *user* schedules inside Substack's editor. No API.
            "native_schedule": True,
            "native_delete": False,       # no takedown API
            "analytics": "none",          # §4.10 — rendered "manual/partial"
            "hashtags_max": 0,            # Substack has no hashtags
            "notes": [
                "no official API — assisted handoff: Friday builds the export "
                "package, you paste and publish",
                "native scheduling means Substack's own in-editor scheduler "
                "(a human action, not an API call)",
                "bodies over ~100 KB will be clipped in the email version",
                "analytics are manual entry only (no API)",
                "headless automation is an opt-in stub — not implemented",
            ],
        })
        media = caps.get("media") or {}
        media["images"] = {"max": 50, "formats": ["png", "jpeg", "gif", "webp"],
                           "max_bytes": 20 * 1024 * 1024, "aspect": (0.1, 10.0)}
        media["video"] = {"max_s": 3600, "formats": ["mp4", "mov"],
                          "max_bytes": 2 * 1024 * 1024 * 1024}
        media["alt_text"] = True
        caps["media"] = media
        return caps

    # ── auth lifecycle (manual mode) ──────────────────────────────────────────
    def connect_url(self, state: str) -> Optional[str]:
        return None                       # no OAuth — nothing to connect to

    def refresh(self) -> bool:
        # Handoff packages can always be produced — the adapter is usable
        # regardless of stored credentials (there are none in manual mode).
        return True

    def status(self) -> Dict[str, Any]:
        st = super().status()
        pub = self._publication_url()
        if pub:
            st["connected"] = True        # manual mode: configured == connected
            try:
                st["account"] = st.get("account") or urlsplit(pub).netloc
            except Exception:
                pass
        st["publication_url"] = pub or None
        st["headless_enabled"] = self._headless_flag()
        return st

    # ── publish path — the export package (§4.10 mode 1) ─────────────────────
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
        opts = prepared.get("options") or {}
        prepared["subtitle"] = str(opts.get("subtitle") or "")
        try:
            if len(str(prepared.get("body") or "").encode("utf-8")) > EMAIL_CLIP_BYTES:
                warnings.append(
                    "body exceeds ~100 KB — the email version will be clipped")
        except Exception:
            pass
        return res

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Build the export package + open-editor descriptor. The result is a
        HANDOFF: the pipeline records the target SENT, and it only becomes
        CONFIRMED when a URL is attached (§4.10)."""
        if self._headless_flag():
            # STUB (§4.10 mode 2): Playwright driving is intentionally not
            # implemented. Never silently fall a rung (§4.14) — surface it.
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
        try:
            prepared = dict(prepared or {})
            handoff_id = "sub_" + uuid.uuid4().hex[:10]
            pkg = self._export_root() / handoff_id
            (pkg / "images").mkdir(parents=True, exist_ok=True)

            title = str(prepared.get("title") or "")
            subtitle = str(prepared.get("subtitle")
                           or (prepared.get("options") or {}).get("subtitle") or "")
            body_md = str(prepared.get("body") or "")
            warnings: List[str] = []

            # Copy local assets into the package (missing files warn, never fail).
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
                     f"subtitle: {json.dumps(subtitle)}\n"
                     f"handoff_id: {handoff_id}\n"
                     "---\n\n")
            (pkg / "post.md").write_text(front + body_md, encoding="utf-8")
            (pkg / "post.html").write_text(_md_to_html(body_md), encoding="utf-8")

            editor_url = self._editor_url()
            descriptor = {
                "action": "open_editor",
                "editor_url": editor_url,
                "package_dir": str(pkg),
                "copy_file": str(pkg / "post.md"),
                "instructions": _HANDOFF_INSTRUCTIONS,
            }
            meta = {
                "handoff_id": handoff_id,
                "title": title,
                "subtitle": subtitle,
                "format": prepared.get("format") or "article",
                "target_id": prepared.get("target_id"),
                "post_id": prepared.get("post_id"),
                "created_at": _now_iso(),
                "editor_url": editor_url,
                "status": "SENT",
                "images": copied,
            }
            (pkg / "meta.json").write_text(json.dumps(meta, indent=2),
                                           encoding="utf-8")

            with _HANDOFFS_LOCK:
                state = self._read_handoffs()
                state[handoff_id] = {
                    "title": title,
                    "norm_title": _norm_title(title),
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
                "post_url": editor_url or pkg.resolve().as_uri(),
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
        """Retract a PENDING handoff (remove the package before paste). A
        confirmed post lives on Substack — there is no takedown API."""
        hid = str(platform_post_id or "")
        try:
            with _HANDOFFS_LOCK:
                state = self._read_handoffs()
                entry = state.get(hid)
                if not isinstance(entry, dict):
                    return {"ok": True, "deleted": False}
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

    # ── SENT → CONFIRMED (§4.10) ──────────────────────────────────────────────
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

    def poll_rss(self) -> Dict[str, Any]:
        """RSS auto-confirmation (§4.10): match pending handoffs against the
        publication's feed by normalized title; attach matched URLs. The feed
        is untrusted data — capped, defensively parsed, never instructions."""
        pub = self._publication_url()
        if not pub:
            return {"ok": False, "error": ERR_NOT_CONFIGURED}
        with _HANDOFFS_LOCK:
            state = self._read_handoffs()
        pending = {hid: e for hid, e in state.items()
                   if isinstance(e, dict) and not e.get("url")}
        if not pending:
            return {"ok": True, "checked": 0, "confirmed": [], "pending": []}
        try:
            raw = _http_get(pub + "/feed")
        except Exception:
            self._last_error = ERR_RSS_UNAVAILABLE
            return {"ok": False, "error": ERR_RSS_UNAVAILABLE}
        items = _parse_rss(raw)
        if items is None:
            self._last_error = ERR_RSS_PARSE
            return {"ok": False, "error": ERR_RSS_PARSE}
        by_title = {_norm_title(i["title"]): i["link"] for i in items}
        confirmed: List[Dict[str, str]] = []
        for hid, entry in pending.items():
            link = by_title.get(str(entry.get("norm_title") or ""))
            if link and self.attach_url(hid, link).get("ok"):
                confirmed.append({"handoff_id": hid, "post_url": link})
        still = [hid for hid in pending
                 if hid not in {c["handoff_id"] for c in confirmed}]
        return {"ok": True, "checked": len(items),
                "confirmed": confirmed, "pending": still}

    # ── analytics path (§4.10: none via API — honest) ─────────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        return None                       # manual entry only; never fabricate

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        return None


ADAPTER = SubstackAdapter
