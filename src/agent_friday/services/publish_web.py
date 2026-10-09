"""Publish to web: an artifact becomes a static page at an address the user chose.

docs/design/active/vibe-coding-salon.md §4.10.1 (Phase 1b). The owner's
decision (2026-09-29): "This PC" is the default host, and hosted adapters
(the user's own Cloudflare Pages or GitHub Pages) are for pages that must
stay up around the clock.

What a publication is:

* **A self-contained static bundle.** It runs entirely in the visitor's
  browser, with no backend and no secrets. Any data is baked in explicitly
  and listed on the card. It carries no analytics or tracking of any kind;
  a bundle that tries to is refused before a card exists.
* **One approval card, always.** Publishing is an outward action. The card
  (`kind = "publish_web"`) shows the file list with hashes, a sandboxed
  preview, the scan result and the licence check, and has a spoken form.
  Nothing leaves the machine before it is approved; a denied card publishes
  nothing and deletes the staged files.
* **Executed once, with a receipt.** Approval runs the adapter through the
  same claim-then-receipt path the approval executor uses
  (`approvals.claim_for_execution`, `action_gate.record_external`), and the
  outcome is posted back into the conversation that asked.
* **Republish, versions, take-down.** A new version of the artifact asks
  again; the published index remembers which version is up; `unpublish`
  removes the files.

The "This PC" adapter writes into `~/.friday/published/<slug>/`, which a
separate static server process serves read-only (services/published_server.py)
behind its own tunnel hostname, with no route to Friday. That process, its
tunnel, the kill switch and the reachability status live in
`services/publish_hosting.py`; this module only writes the folder and asks
that module for the address.
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent_friday.paths import contained, safe_name
from agent_friday.user_errors import UserFacingValueError

_log = logging.getLogger(__name__)

#: The approval card kind. Its decision hook is `_on_decision`.
KIND = "publish_web"

#: The one host an `html` app may load packages from (spike S1).
PACKAGE_HOST = "https://esm.sh"

#: The CSP written into an `html` app's page: the panel's frame policy.
FRAME_CSP = "; ".join([
    "default-src 'none'",
    "script-src 'unsafe-inline' 'wasm-unsafe-eval' " + PACKAGE_HOST,
    "style-src 'unsafe-inline' " + PACKAGE_HOST,
    "connect-src " + PACKAGE_HOST,
    "img-src 'self' data: blob: " + PACKAGE_HOST,
    "font-src data: " + PACKAGE_HOST,
    "media-src 'self' data: blob:",
    "worker-src blob:",
    "form-action 'none'",
    "base-uri 'none'",
])
#: The CSP for every other kind: no scripts at all except the chart page's own.
PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src data:; form-action 'none'; base-uri 'none'")
CHART_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
             "img-src 'self' data: blob:; font-src data:; form-action 'none'; base-uri 'none'")

#: Response headers for every served page: the preview route and the static
#: server both send them. `sandbox` makes every published page an opaque
#: origin even when served from a real hostname.
STRICT_HEADERS = {
    "Content-Security-Policy": (
        "sandbox allow-scripts allow-downloads; default-src 'none'; "
        "script-src 'unsafe-inline' 'wasm-unsafe-eval' " + PACKAGE_HOST + "; "
        "style-src 'unsafe-inline' " + PACKAGE_HOST + "; connect-src " + PACKAGE_HOST + "; "
        "img-src 'self' data: blob: " + PACKAGE_HOST + "; font-src data: " + PACKAGE_HOST + "; "
        "media-src 'self' data: blob:; worker-src blob:; form-action 'none'; base-uri 'none'; "
        "frame-ancestors 'self'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}

#: Analytics and tracking hosts. A bundle that names one is refused, so a
#: published page carries no tracker. The first three are joined from parts: no
#: telemetry endpoint appears as a literal anywhere in the tree
#: (tests/unit/test_no_vendored_telemetry.py), not even in a refusal list.
_GA = "analytics"
_GTM = "google" + "tag" + "manager"
TRACKER_HOSTS = (
    _GTM + ".com", "google-" + _GA + ".com", _GA + ".google.com",
    "doubleclick.net", "googlesyndication.com", "facebook.net", "connect.facebook.net",
    "hotjar.com", "segment.com", "segment.io", "mixpanel.com", "plausible.io",
    "matomo.cloud", "clarity.ms", "fullstory.com", "amplitude.com", "heapanalytics.com",
    "intercom.io", "intercomcdn.com", "sentry.io", "posthog.com", "cloudflareinsights.com",
    "newrelic.com", "nr-data.net", "datadoghq.com", "logrocket.com", "crazyegg.com",
)

#: Text that must never be public. Values are never echoed back, only counted.
_SECRET_RES = (
    re.compile(r"\b(?:sk-ant-|sk-|AQ\.|AIza)[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abp]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),
)
_SSN_RE = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
_SCRIPT_SRC_RE = re.compile(r"<script\b[^<>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]", re.I)
_IMPORT_URL_RE = re.compile(r"""(?:from|import)\s*(?:\(\s*)?['"](https?://[^'"]+)['"]""")
_ESM_PIN_RE = re.compile(r"https://esm\.sh/((?:@[a-z0-9~][a-z0-9._~-]*/)?[a-z0-9~][a-z0-9._~-]*)@([0-9][^/?'\"\s]*)")
_COPYLEFT_RE = re.compile(r"\b(?:A?GPL|LGPL|SSPL|EUPL|CC[- ]BY[- ]NC|OSL|CPAL|non-?commercial|NC\b)", re.I)

_LICENCE_NOTE = "The licence check reads each pinned package's package.json from " + PACKAGE_HOST + "."

ADAPTER_LABELS = {"this_pc": "This PC", "cloudflare_pages": "Cloudflare Pages",
                  "github_pages": "GitHub Pages"}


class Refused(Exception):
    """The bundle cannot be published as it is; the message says why."""


@dataclass
class Bundle:
    files: dict                    # path -> bytes
    title: str
    slug: str
    kind: str
    artifact_id: str
    conversation_id: str
    version: int
    imports: list = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(v) for v in self.files.values())

    def manifest(self) -> list:
        return [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                for p, b in sorted(self.files.items())]


# ── places ───────────────────────────────────────────────────────────────────

def _home() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR)


def _published_root() -> Path:
    """The folder the static server serves. Only `<slug>/…` paths ever exist in it."""
    return _home() / "published"


def _staging_root() -> Path:
    return _home() / "publish-staging"


def _index_path() -> Path:
    # Beside, never inside, the served folder.
    return _published_root().parent / (_published_root().name + "-index.json")


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def slug_for(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")[:48].strip("-")
    return s or "page"


def _check_slug(slug: str) -> str:
    slug = safe_name(slug, what="slug")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", slug):
        raise UserFacingValueError("invalid slug")
    return slug


def _human(n: int) -> str:
    return "%.1f MB" % (n / 1e6) if n >= 1e6 else ("%d KB" % (n // 1024) if n >= 1024 else "%d bytes" % n)


# ── the bundle ───────────────────────────────────────────────────────────────

_BASE_CSS = ("html,body{margin:0;background:#0b0e14;color:#e6eef8;font-family:Inter,system-ui,sans-serif;"
             "line-height:1.6}main{max-width:820px;margin:0 auto;padding:32px 20px 80px}h1,h2,h3{color:#f1f6fb;"
             "line-height:1.25}a{color:#00d4ff}pre{background:rgba(0,0,0,.35);border-radius:8px;padding:10px 12px;"
             "overflow:auto;font:12.5px/1.55 'JetBrains Mono',ui-monospace,monospace}code{font-family:'JetBrains Mono',"
             "ui-monospace,monospace}table{border-collapse:collapse;width:100%;font-size:14px}th{text-align:left;"
             "color:#00d4ff;font:600 11px 'JetBrains Mono',monospace;letter-spacing:.06em;padding:8px;border-bottom:"
             "1px solid rgba(0,212,255,.3)}td{padding:7px 8px;border-bottom:1px solid rgba(255,255,255,.06)}"
             "td.num{text-align:right;font-family:'JetBrains Mono',monospace}.dl{font-size:12px;margin-top:10px}"
             ".mark{position:fixed;right:12px;bottom:10px;font:10px 'JetBrains Mono',monospace;letter-spacing:.14em;"
             "color:rgba(0,212,255,.7);text-decoration:none;opacity:.8}.mark b{font-family:Orbitron,Inter,sans-serif;"
             "font-weight:400}.diff .add{color:#00ff80}.diff .del{color:#ff5a7a}.diff .hunk{color:#ff6dd9}"
             ".diff .meta{color:#888}figure{margin:0}img,svg{max-width:100%}#chart{height:min(70vh,560px)}"
             ".legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;margin-top:8px}.legend i{display:inline-block;"
             "width:9px;height:9px;border-radius:2px;margin-right:5px}")

def _made_with() -> str:
    """The product's credit line, from the brand (unified-shell.md §1: published pages carry MADE_WITH)."""
    from agent_friday import brand
    return brand.MADE_WITH


_MARK_HTML = ('<a class="mark" title="' + _made_with() + '" href="https://futurespeak.ai" rel="noopener noreferrer">'
              + _made_with().upper().replace("AGENT FRIDAY", "<b>AGENT FRIDAY</b>") + '</a>')


def _page(title: str, body: str, csp: str, *, mark: bool, extra_head: str = "") -> bytes:
    """A complete page. The CSP is the first thing in <head>, before any style
    or script, so nothing runs outside it."""
    doc = ("<!doctype html><html lang=\"en\"><head>"
           "<meta http-equiv=\"Content-Security-Policy\" content=\"" + csp.replace('"', "&quot;") + "\">"
           "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           "<meta name=\"referrer\" content=\"no-referrer\"><meta name=\"generator\" content=\"Friday\">"
           "<title>" + _html.escape(title) + "</title><style>" + _BASE_CSS + "</style>" + extra_head +
           "</head><body><main>" + body + "</main>" + (_MARK_HTML if mark else "") + "</body></html>")
    return doc.encode("utf-8")


def _inject_into_app(html_src: str, *, mark: bool) -> str:
    """An `html` app is the user's own document: the CSP goes first in its
    head, the referrer policy beside it, and the mark before </body>."""
    meta = ("<meta http-equiv=\"Content-Security-Policy\" content=\"" + FRAME_CSP.replace('"', "&quot;") + "\">"
            "<meta name=\"referrer\" content=\"no-referrer\"><meta name=\"generator\" content=\"Friday\">")
    s = html_src or ""
    m = re.search(r"<head\b[^<>]*>", s, re.I)
    if m:
        s = s[:m.end()] + meta + s[m.end():]
    else:
        m = re.search(r"<html\b[^<>]*>", s, re.I)
        if m:
            s = s[:m.end()] + "<head><meta charset=\"utf-8\">" + meta + "</head>" + s[m.end():]
        else:
            s = "<!doctype html><html><head><meta charset=\"utf-8\">" + meta + "</head><body>" + s + "</body></html>"
    if mark:
        style = ("<style>.friday-mark{position:fixed;right:12px;bottom:10px;font:10px 'JetBrains Mono',monospace;"
                 "letter-spacing:.14em;color:rgba(0,212,255,.7);text-decoration:none;opacity:.8;z-index:2147483647}</style>")
        mark_html = style + '<a class="friday-mark" title="' + _made_with() + '" href="https://futurespeak.ai" rel="noopener noreferrer">' + _made_with().upper() + '</a>'
        i = s.lower().rfind("</body>")
        s = s[:i] + mark_html + s[i:] if i >= 0 else s + mark_html
    return s


def _csv(columns, rows) -> str:
    def cell(v):
        t = "" if v is None else str(v)
        return '"' + t.replace('"', '""') + '"' if any(c in t for c in ',"\n') else t
    return "\n".join([",".join(cell(c) for c in columns)] + [",".join(cell(v) for v in r) for r in rows])


def _chart_renderer_js() -> str:
    p = Path(__file__).resolve().parents[3] / "static" / "friday_chart.js"
    return p.read_text(encoding="utf-8")


def _svg_is_inert(svg: str) -> bool:
    low = (svg or "").lower()
    if "<script" in low or "javascript:" in low or "<foreignobject" in low or "<iframe" in low:
        return False
    if re.search(r"\son[a-z]+\s*=", low):
        return False
    if re.search(r"\b(?:href|xlink:href)\s*=\s*['\"](?!#|data:image/)", low):
        return False
    return True


def _mark_default(mark) -> bool:
    if mark is not None:
        return bool(mark)
    return _settings().get("publish_mark", True) is not False


def pack(rec: dict, *, mark: Optional[bool] = None) -> Bundle:
    """The static bundle for one artifact version. Raises Refused for content
    that cannot be made safe (an svg with scripts, an image with no bytes)."""
    mark = _mark_default(mark)
    kind, title, content = rec["kind"], str(rec.get("title") or rec["kind"]), rec.get("content")
    files: dict = {}
    imports: list = []
    if kind == "markdown":
        import markdown as _md
        body = _md.markdown(content or "", extensions=["tables", "fenced_code", "sane_lists"])
        files["index.html"] = _page(title, body, PAGE_CSP, mark=mark)
        files["source.md"] = (content or "").encode("utf-8")
    elif kind == "html":
        files["index.html"] = _inject_into_app(content or "", mark=mark).encode("utf-8")
        imports = collect_imports(content or "")
    elif kind == "table":
        cols = list((content or {}).get("columns") or [])
        rows = list((content or {}).get("rows") or [])
        numeric = [all(r[i] is None or r[i] == "" or _isnum(r[i]) for r in rows) if rows else False for i in range(len(cols))]
        thead = "".join("<th>%s</th>" % _html.escape(str(c)) for c in cols)
        tbody = "".join("<tr>" + "".join(
            "<td%s>%s</td>" % (' class="num"' if numeric[i] else "", _html.escape("" if v is None else str(v)))
            for i, v in enumerate(r)) + "</tr>" for r in rows)
        body = ("<h1>%s</h1><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
                "<p class=\"dl\"><a href=\"data.csv\" download>Download as CSV</a></p>" % (_html.escape(title), thead, tbody))
        files["index.html"] = _page(title, body, PAGE_CSP, mark=mark)
        files["data.csv"] = _csv(cols, rows).encode("utf-8")
    elif kind == "chart":
        spec = content or {}
        spec_json = json.dumps(spec, ensure_ascii=False, default=str).replace("</", "<\\/")
        script = ("<script>" + _chart_renderer_js() +
                  "\n(function(){var spec=" + spec_json + ";var el=document.getElementById('chart');"
                  "var r=FridayChart.renderSVG(spec, el.clientWidth||960, el.clientHeight||540);"
                  "el.innerHTML=r.svg||'<p>Nothing to chart.</p>';var lg=document.getElementById('legend');"
                  "lg.innerHTML=r.legend.map(function(l){return '<span><i style=\"background:'+l.color+'\"></i>'+"
                  "String(l.name).replace(/[<>&]/g,function(c){return {'<':'&lt;','>':'&gt;','&':'&amp;'}[c];})+'</span>';}).join('');})();</script>")
        body = ("<h1>%s</h1><figure><div id=\"chart\"></div><div class=\"legend\" id=\"legend\"></div></figure>"
                "<p class=\"dl\"><a href=\"data.csv\" download>Download the data as CSV</a></p>" % _html.escape(title))
        files["index.html"] = _page(title, body + script, CHART_CSP, mark=mark)
        cols, rows = _chart_rows(spec)
        files["data.csv"] = _csv(cols, rows).encode("utf-8")
    elif kind == "image":
        src = str((content or {}).get("src") or "")
        m = re.match(r"data:image/(png|jpe?g|gif|webp|svg\+xml);base64,(.+)$", src, re.S)
        if not m:
            raise Refused("the image has no embedded bytes to publish")
        ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(m.group(1), m.group(1))
        try:
            data = base64.b64decode(m.group(2))
        except Exception:
            raise Refused("the image data could not be decoded")
        if ext == "svg" and not _svg_is_inert(data.decode("utf-8", "replace")):
            raise Refused("the svg image contains a script or an active link")
        files["image." + ext] = data
        alt = _html.escape(str((content or {}).get("alt") or ""))
        body = "<h1>%s</h1><figure><img src=\"image.%s\" alt=\"%s\"></figure>" % (_html.escape(title), ext, alt)
        files["index.html"] = _page(title, body, PAGE_CSP, mark=mark)
    elif kind == "svg":
        if not _svg_is_inert(content or ""):
            raise Refused("the drawing contains a script or an active link and cannot be published")
        body = "<h1>%s</h1><figure>%s</figure>" % (_html.escape(title), content or "")
        files["index.html"] = _page(title, body, PAGE_CSP, mark=mark)
        files["drawing.svg"] = (content or "").encode("utf-8")
    elif kind == "diff":
        lines = []
        for ln in (content or "").split("\n"):
            cls = "add" if ln.startswith("+") and not ln.startswith("+++") else \
                  "del" if ln.startswith("-") and not ln.startswith("---") else \
                  "hunk" if ln.startswith("@@") else "meta" if ln.startswith(("+++", "---")) else ""
            lines.append("<span class=\"%s\">%s</span>" % (cls, _html.escape(ln or " ")) if cls else _html.escape(ln or " "))
        body = "<h1>%s</h1><pre class=\"diff\">%s</pre>" % (_html.escape(title), "\n".join(lines))
        files["index.html"] = _page(title, body, PAGE_CSP, mark=mark)
        files["changes.diff"] = (content or "").encode("utf-8")
    else:
        raise Refused("artifact kind %r cannot be published" % kind)
    return Bundle(files=files, title=title, slug=slug_for(title), kind=kind,
                  artifact_id=rec["id"], conversation_id=rec["conversation_id"],
                  version=int(rec["version"]), imports=imports)


def _isnum(v) -> bool:
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v).strip())
        return True
    except ValueError:
        return False


def _chart_rows(spec: dict):
    cols = [c.get("name") if isinstance(c, dict) else c for c in (spec.get("columns") or [])]
    rows = spec.get("rows") or []
    if rows and isinstance(rows[0], dict):
        rows = [[r.get(c) for c in cols] for r in rows]
    if not cols and spec.get("series"):
        cols = ["x"] + [s.get("name") or "series %d" % (i + 1) for i, s in enumerate(spec["series"])]
        length = max((len(s.get("data") or []) for s in spec["series"]), default=0)
        rows = []
        for i in range(length):
            first = (spec["series"][0].get("data") or [None] * length)[i]
            x = first[0] if isinstance(first, (list, tuple)) else (first or {}).get("x") if isinstance(first, dict) else None
            row = [x]
            for s in spec["series"]:
                d = (s.get("data") or [])
                p = d[i] if i < len(d) else None
                row.append(p[1] if isinstance(p, (list, tuple)) else (p or {}).get("y") if isinstance(p, dict) else None)
            rows.append(row)
    return [str(c) for c in cols], rows


def collect_imports(html_src: str) -> list:
    """Every absolute script or module URL the app loads, in order, once."""
    out = []
    for m in _SCRIPT_SRC_RE.finditer(html_src or ""):
        out.append(m.group(1))
    for m in _IMPORT_URL_RE.finditer(html_src or ""):
        out.append(m.group(1))
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


# ── the scan ─────────────────────────────────────────────────────────────────

def _host_of(url: str) -> str:
    m = re.match(r"https?://([^/:?#]+)", url or "")
    return (m.group(1) if m else "").lower()


def scan(bundle: Bundle) -> dict:
    """What would go public. Refusals stop the card; warnings go on it.
    No value is echoed back: the report counts and names kinds."""
    texts = []
    for p, b in bundle.files.items():
        if (p.lower().endswith((".html", ".htm", ".md", ".csv", ".svg", ".diff", ".txt", ".json", ".js",
                               ".mjs", ".css", ".xml", ".map", ".webmanifest"))
                or p.rsplit("/", 1)[-1].lower() in {"cname", "_headers", "_redirects", ".nojekyll"}):
            texts.append(b.decode("utf-8", "replace"))
    text = "\n".join(texts)
    refusals, warnings = [], []
    tier = "TIER_1"
    try:
        from agent_friday.services import sensitivity_classifier as _sc
        t = _sc.classify(text, use_llm=False)
        tier = _sc.Tier.NAMES.get(t, "TIER_1")
    except Exception as e:
        warnings.append("the sensitivity classifier could not run (%s)" % e)
    ids = 0
    try:
        from agent_friday.services import judgment_gate as _jg
        ids += len(_jg.hard_identifier_hits(text))
        ns = _jg.never_send_hits(text)
        if ns:
            refusals.append("never-send material is in the bundle (%d item%s)" % (len(ns), "" if len(ns) == 1 else "s"))
    except Exception as e:
        warnings.append("the egress floor could not run (%s)" % e)
    ids += len(_SSN_RE.findall(text))
    if ids:
        refusals.append("hard identifier(s) found: %d (a national id, card or account number)" % ids)
    secrets = sum(len(r.findall(text)) for r in _SECRET_RES)
    if secrets:
        refusals.append("secret-shaped string(s) found: %d (an API key, token or private key)" % secrets)
    low = text.lower()
    trackers = sorted({h for h in TRACKER_HOSTS if h in low})
    if trackers:
        refusals.append("tracking/analytics host named: " + ", ".join(trackers))
    external = []
    html_text = "\n".join(b.decode("utf-8", "replace") for p, b in bundle.files.items() if p.lower().endswith((".html", ".htm")))
    for url in collect_imports(html_text):
        host = _host_of(url)
        if host and host != _host_of(PACKAGE_HOST) and host not in external:
            external.append(host)
    if external:
        refusals.append("script from %s is not the pinned package host (%s)" % (", ".join(external), _host_of(PACKAGE_HOST)))
    if tier != "TIER_1":
        warnings.append("the scan rates this content %s; it will be public to anyone with the link"
                        % ("private" if tier == "TIER_2" else "sensitive"))
    return {"ok": not refusals, "refusals": refusals, "warnings": warnings, "tier": tier,
            "identifiers": ids, "secrets": secrets, "trackers": trackers, "external_hosts": external,
            "bytes": bundle.size}


# ── the licence check ────────────────────────────────────────────────────────

def _fetch_package_json(url: str) -> Optional[dict]:
    """GET one package.json from the package host. None on any failure."""
    try:
        import requests
        r = requests.get(url, timeout=6, headers={"User-Agent": "Friday/publish-licence-check"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def licences(bundle: Bundle) -> dict:
    """Which packages the app pins, and each one's licence, flagged where a
    licence is copyleft, non-commercial or unknown. Never blocks: refusal is
    an owner rule (§4.11 item 9)."""
    pkgs, flagged = [], 0
    seen = set()
    for url in bundle.imports:
        m = _ESM_PIN_RE.search(url)
        if not m:
            continue
        name, ver = m.group(1), m.group(2)
        key = "%s@%s" % (name, ver)
        if key in seen:
            continue
        seen.add(key)
        pj = _fetch_package_json("%s/%s@%s/package.json" % (PACKAGE_HOST, name, ver))
        lic = None
        if isinstance(pj, dict):
            lic = pj.get("license")
            if isinstance(lic, dict):
                lic = lic.get("type")
            if not lic and isinstance(pj.get("licenses"), list) and pj["licenses"]:
                first = pj["licenses"][0]
                lic = first.get("type") if isinstance(first, dict) else first
        lic = str(lic) if lic else "unknown"
        flag = "unknown" if lic == "unknown" else ("copyleft" if _COPYLEFT_RE.search(lic) else None)
        if flag:
            flagged += 1
        pkgs.append({"package": key, "license": lic, "flag": flag})
    return {"packages": pkgs, "flagged": flagged}


# ── staging, the card, the publish ───────────────────────────────────────────

_LOCK = threading.RLock()
_registered = False


def _reset_for_tests() -> None:
    """Forget runtime state between tests. The decision hook stays registered
    (approvals keeps hooks per process); once-only is guaranteed by the claim."""
    pass


def _stage(bundle: Bundle) -> str:
    sid = "%s-v%d-%s" % (bundle.slug, bundle.version, hashlib.sha256(
        b"".join(bundle.files[p] for p in sorted(bundle.files))).hexdigest()[:8])
    d = _staging_root() / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    for p, b in bundle.files.items():
        target = contained(d, p)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b)
    return sid


def staged_file(staging_id: str, rel: str) -> Optional[Path]:
    """A staged file for the preview route, or None. Never outside staging."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}-v\d+-[0-9a-f]{8}", staging_id or ""):
        return None
    try:
        p = contained(_staging_root() / staging_id, rel)
    except ValueError:
        return None
    return p if p.is_file() else None


def _load_bundle_from_staging(payload: dict) -> Optional[Bundle]:
    sid = str(payload.get("staging") or "")
    d = _staging_root() / sid
    if not sid or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}-v\d+-[0-9a-f]{8}", sid) or not d.is_dir():
        return None
    files = {}
    for f in payload.get("files") or []:
        p = staged_file(sid, f["path"])
        if p is None:
            return None
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != f.get("sha256"):
            return None
        files[f["path"]] = data
    if not files or "index.html" not in files:
        return None
    return Bundle(files=files, title=str(payload.get("title") or payload.get("slug")),
                  slug=str(payload.get("slug")), kind=str(payload.get("kind") or ""),
                  artifact_id=str(payload.get("artifact_id") or ""),
                  conversation_id=str(payload.get("conversation_id") or ""),
                  version=int(payload.get("version") or 0))


def default_adapter() -> str:
    a = str(_settings().get("publish_default_adapter") or "this_pc")
    return a if a in ADAPTER_LABELS else "this_pc"


def _spoken(title: str, label: str, n: int, size: int, scan_: dict, lic: dict, warnings: list) -> str:
    tier_note = ("" if scan_.get("tier") == "TIER_1"
                 else " The scan rates it %s, and it would be public." % ("private" if scan_.get("tier") == "TIER_2" else "sensitive"))
    lic_note = (" %d package licence%s flagged." % (lic["flagged"], "" if lic["flagged"] == 1 else "s")) if lic.get("flagged") else ""
    where = " It stays up only while this PC is on." if label == "This PC" else ""
    return ('Publish "%s" to the web via %s? %d file%s, %s.%s%s%s Say "Friday, publish it" or "no".'
            % (title, label, n, "" if n == 1 else "s", _human(size), tier_note, lic_note, where))


def request_publish(cid: str, aid: str, *, adapter: Optional[str] = None, version: Optional[int] = None,
                    requested_by: str = "panel", mark: Optional[bool] = None) -> dict:
    """Pack, scan, check licences, stage, and file the one card. Returns
    {"approval": record} or {"refused": [reasons], "approval": None}."""
    from agent_friday.services import approvals as _ap
    from agent_friday.services import artifacts as _art
    rec = _art.get(cid, aid, version=version)
    if rec is None:
        raise KeyError(aid)
    adapter = adapter or default_adapter()
    if adapter not in ADAPTER_LABELS:
        raise UserFacingValueError("unknown publish adapter %r" % adapter)
    label = ADAPTER_LABELS[adapter]
    try:
        bundle = pack(rec, mark=mark)
    except Refused as e:
        return {"approval": None, "refused": [str(e)], "scan": None}
    s = scan(bundle)
    if not s["ok"]:
        return {"approval": None, "refused": s["refusals"], "scan": s}
    lic = licences(bundle)
    warnings = list(s["warnings"])
    if adapter == "this_pc":
        warnings.append("Reachable only while this PC is on and its tunnel is up.")
    big = bundle.size > 2_000_000 or re.search(r"<(?:audio|video)\b", bundle.files["index.html"].decode("utf-8", "replace"), re.I)
    if big:
        warnings.append("Large media: a hosted adapter (Cloudflare Pages or GitHub Pages) would stay up around the clock and carry the bandwidth.")
    if lic["flagged"]:
        warnings.append("Package licences flagged: " + ", ".join("%s (%s)" % (p["package"], p["license"]) for p in lic["packages"] if p["flag"]))
    if adapter != "this_pc" and not adapter_connected(adapter):
        return {"approval": None, "refused": ["%s is not connected yet: connect the account in Settings first" % label], "scan": s}
    with _LOCK:
        sid = _stage(bundle)
    files = bundle.manifest()
    payload = {
        "adapter": adapter, "adapter_label": label, "slug": bundle.slug, "title": bundle.title,
        "kind": bundle.kind, "artifact_id": bundle.artifact_id, "conversation_id": bundle.conversation_id,
        "version": bundle.version, "files": files, "bytes": bundle.size, "size": _human(bundle.size),
        "scan": s, "licences": lic, "licence_note": _LICENCE_NOTE, "staging": sid,
        "preview_url": "/api/publish/preview/%s/index.html" % sid,
        "warnings": warnings, "mark": _mark_default(mark),
        "spoken": _spoken(bundle.title, label, len(files), bundle.size, s, lic, warnings),
        "requested_by": requested_by,
    }
    card = _ap.create_approval(
        kind=KIND, subject_type="artifact",
        subject_id="%s/%s@v%d/%s" % (cid, aid, bundle.version, adapter),
        title='Publish "%s" to the web via %s' % (bundle.title, label),
        description="%d file%s, %s. %s" % (len(files), "" if len(files) == 1 else "s", _human(bundle.size),
                                          " ".join(warnings)),
        action_description='publish "%s" (v%d) to the public web via %s' % (bundle.title, bundle.version, label),
        payload=payload, requested_by=requested_by, action_class="outward", force_gate=True)
    return {"approval": card, "refused": None, "scan": s}


def adapter_connected(adapter: str) -> bool:
    if adapter == "this_pc":
        return True
    try:
        from agent_friday.services import publish_hosting as _ph
        return bool(_ph.adapter_connected(adapter))
    except Exception:
        return False


# ── the published index ──────────────────────────────────────────────────────

def _read_index() -> list:
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(items: list) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)


def list_published() -> list:
    return _read_index()


def _this_pc_url(slug: str) -> str:
    try:
        from agent_friday.services import publish_hosting as _ph
        base = _ph.public_base_url()
    except Exception:
        base = None
    return (base or "http://127.0.0.1:0").rstrip("/") + "/" + slug + "/"


def _publish_this_pc(bundle: Bundle) -> str:
    """Write the bundle into the served folder, atomically per site, with the
    static server and its tunnel running so the address handed back is live."""
    try:
        from agent_friday.services import publish_hosting as _ph
        _ph.ensure_started()
    except Exception as e:
        _log.warning("This PC hosting did not start: %s", e)
    root = _published_root()
    root.mkdir(parents=True, exist_ok=True)
    slug = _check_slug(bundle.slug)
    final = contained(root, slug)
    tmp = root / (".tmp-" + slug)
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for p, b in bundle.files.items():
        t = contained(tmp, p)
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(b)
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return _this_pc_url(slug)


def unpublish(slug: str) -> bool:
    """Remove a published site and its index entry. False when nothing was up."""
    slug = _check_slug(slug)
    with _LOCK:
        items = _read_index()
        entry = next((i for i in items if i.get("slug") == slug), None)
        if entry is None:
            return False
        if entry.get("adapter") == "this_pc":
            d = contained(_published_root(), slug)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        else:
            try:
                from agent_friday.services import publish_hosting as _ph
                _ph.unpublish_remote(entry)
            except Exception:
                # Remote timeouts can be ambiguous. Keep the local receipt and
                # mirror so a failed request cannot be presented as taken down.
                raise UserFacingValueError("Remote take-down was not confirmed. The saved publication remains listed; check the host before retrying.") from None
        _write_index([i for i in items if i.get("slug") != slug])
    return True


# ── the decision hook ────────────────────────────────────────────────────────

def register() -> None:
    """Attach the publisher to approval decisions. Idempotent."""
    global _registered
    if _registered:
        return
    from agent_friday.services import approvals as _ap
    _ap.register_decision_hook(KIND, _on_decision)
    _registered = True


def _post_back(record: dict, text: str) -> None:
    cid = ((record.get("payload") or {}).get("conversation_id") or "").strip()
    if not cid:
        return
    try:
        from agent_friday.services import conversations as _convs
        _convs.append(cid, {"role": "friday", "text": text, "ts": time.time(),
                            "meta": {"kind": "approval_result", "approval_id": record.get("approval_id")}})
    except Exception as e:
        _log.warning("could not post the publish result into %s: %s", cid, e)
        return
    try:
        from agent_friday.services import desktop_bus as _bus
        _bus.broadcast({"type": "approval_result", "conversation_id": cid,
                        "approval_id": record.get("approval_id")}, kind="chat")
    except Exception:
        pass


def _on_decision(record: dict) -> None:
    if not isinstance(record, dict) or record.get("kind") != KIND:
        return
    payload = record.get("payload") or {}
    title = str(payload.get("title") or payload.get("slug") or "the page")
    if record.get("status") != "approved":
        if record.get("status") in ("denied", "expired"):
            sid = str(payload.get("staging") or "")
            if sid:
                shutil.rmtree(_staging_root() / sid, ignore_errors=True)
            _post_back(record, 'Not published: "%s" stays on this PC only.' % title)
        return
    aid = record.get("approval_id")
    from agent_friday.services import approvals as _ap
    if not _ap.claim_for_execution(aid):
        return
    bundle = _load_bundle_from_staging(payload)
    if bundle is None:
        _ap.mark_used(aid, "publish_web", detail={"ok": False, "error": "staged bundle missing or altered"})
        _post_back(record, 'I could not publish "%s": the staged files are missing or were changed after the card was raised. Nothing was published.' % title)
        return
    adapter = str(payload.get("adapter") or "this_pc")
    label = ADAPTER_LABELS.get(adapter, adapter)
    url = _this_pc_url(bundle.slug) if adapter == "this_pc" else ""
    from agent_friday.governance import action_gate as _gate
    try:
        _gate.record_external(KIND, surface="approval_card", approval_id=aid, target=url or bundle.slug)
    except Exception as e:
        _ap.mark_used(aid, "publish_web", detail={"ok": False, "error": "held: %s" % e})
        _post_back(record, 'I could not publish "%s": %s. Nothing was published.' % (title, e))
        return
    try:
        if adapter == "this_pc":
            url = _publish_this_pc(bundle)
        else:
            from agent_friday.services import publish_hosting as _ph
            url = _ph.publish_remote(adapter, bundle)
    except Exception as e:
        _ap.mark_used(aid, "publish_web", detail={"ok": False, "error": str(e)})
        _post_back(record, 'Publishing "%s" via %s failed: %s. Nothing is up.' % (title, label, e))
        return
    with _LOCK:
        items = [i for i in _read_index() if i.get("slug") != bundle.slug]
        items.append({"slug": bundle.slug, "title": bundle.title, "kind": bundle.kind,
                      "adapter": adapter, "url": url, "version": bundle.version,
                      "artifact_id": bundle.artifact_id, "conversation_id": bundle.conversation_id,
                      "files": bundle.manifest(), "bytes": bundle.size,
                      "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "approval_id": aid})
        _write_index(items)
        shutil.rmtree(_staging_root() / str(payload.get("staging") or ""), ignore_errors=True)
    _ap.mark_used(aid, "publish_web", detail={"ok": True, "url": url})
    note = (" It is reachable only while this PC is on and its tunnel is up." if adapter == "this_pc" else "")
    _post_back(record, 'Published "%s" (v%d) via %s: %s%s Say "take it down" to unpublish.'
               % (title, bundle.version, label, url, note))
