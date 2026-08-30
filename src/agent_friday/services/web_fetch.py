"""
web_fetch — guarded page fetching and the SourceRecord store.

Two jobs, deliberately together (deep-research.md P2/P3):

  1. Fetch a page with the SSRF guard applied to EVERY redirect hop, not just
     the URL the caller handed us. Automatic redirect following was the actual
     hole: a public URL is allowed to 302 to http://127.0.0.1:3000 and a
     front-door check never sees the second request. So redirects are followed
     manually, one hop at a time, each validated.

  2. Be the fetch cache — and the cache IS the provenance record. A research
     finding's receipt has to be checkable against the bytes the finding was
     born from, not against a re-fetch that may have changed underneath it
     (RS12). So every fetch writes the extracted text verbatim to disk, and
     verification later reads that file.

Not a research-only module: browse_web routes through here too, so the guard
covers ordinary chat browsing as well as the research harness.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from agent_friday.services.web_safety import MAX_REDIRECT_HOPS, UnsafeURLError, check_url

_log = logging.getLogger("friday.web_fetch")

# Extraction cap. Generous — the 12b's 131k window can hold a large page — but
# not unbounded, because one pathological page should not eat a commission's
# whole budget.
MAX_EXTRACT_CHARS = 200_000
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Paragraph-sized spans, shaped to the egress registry's existing 2,000-char
# bound on purpose (egress_gate.register_public_text drops anything longer).
SPAN_MAX_CHARS = 2000


def _store_root() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "research" / "sources"


def _url_key(url: str) -> str:
    return hashlib.blake2b(url.strip().encode("utf-8"), digest_size=12).hexdigest()


def _html_to_text(html: str) -> str:
    """Strip markup to readable text. Mirrors agent._html_to_text so browse_web
    behaviour does not change shape when it routes through here."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", text)
    except ImportError:
        text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html,
                      flags=re.I | re.S)
        text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text,
                      flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()


def _title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:300]


def _spans(text: str) -> list[dict]:
    """Split extracted text into paragraph-sized spans for provenance
    registration. Oversized paragraphs are hard-split rather than dropped —
    a 3,000-char paragraph should still yield quotable spans."""
    out: list[dict] = []
    for para in re.split(r"\n{2,}", text):
        p = para.strip()
        if not p:
            continue
        while p:
            chunk, p = p[:SPAN_MAX_CHARS], p[SPAN_MAX_CHARS:]
            out.append({"span_id": f"s{len(out)}", "text": chunk,
                        "para_index": len(out)})
    return out


class FetchResult(dict):
    """A SourceRecord. dict-shaped so it serializes without ceremony."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


def _fail(url: str, reason: str, kind: str) -> FetchResult:
    return FetchResult({"ok": False, "url": url, "error": reason,
                        "error_kind": kind})


def _firecrawl_enabled() -> bool:
    try:
        from agent_friday.services import firecrawl
        return firecrawl.configured()
    except Exception:
        return False


def _fetch_via_firecrawl(url, key, root, meta_path, text_path,
                         register_provenance) -> FetchResult | None:
    """Fetch through Firecrawl. Returns None to mean "fall back to direct".

    None rather than a failure result, because Firecrawl being down is our
    problem to route around, not a fact about the source. A genuine "this page
    cannot be read" still has to come from actually trying.
    """
    from agent_friday.services import firecrawl
    out = firecrawl.scrape(url)
    if not out.get("ok"):
        _log.info("firecrawl could not fetch %s (%s) — trying direct",
                  url, out.get("error"))
        return None
    text = out.get("markdown") or ""
    truncated = len(text) > MAX_EXTRACT_CHARS
    if truncated:
        text = text[:MAX_EXTRACT_CHARS]
    rec = FetchResult({
        "ok": True, "id": key, "url": url,
        "final_url": out.get("final_url") or url,
        "redirect_chain": [], "title": out.get("title") or "",
        "http_status": out.get("status") or 200,
        "content_type": "text/markdown",
        "fetched_at": time.time(), "chars": len(text), "truncated": truncated,
        "provenance": "fetched-by-friday-research", "via": "firecrawl",
        "from_cache": False,
    })
    spans = _spans(text)
    rec["spans"] = spans
    try:
        root.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        meta_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        rec["extracted_path"] = str(text_path)
    except Exception as e:
        rec["cache_error"] = str(e)
    if register_provenance:
        _register_spans(rec, spans)
    return rec


def fetch(url: str, *, timeout: int = 20, use_cache: bool = True,
          register_provenance: bool = True) -> FetchResult:
    """Fetch one URL safely. Returns a FetchResult / SourceRecord.

    Never raises for an unsafe or unreachable URL — the refusal is data, so a
    caller (and the report) can say WHICH source could not be read and why,
    rather than a source silently vanishing from the trail (§7.6).
    """
    import requests

    url = (url or "").strip()
    key = _url_key(url)
    root = _store_root()
    meta_path = root / f"{key}.json"
    text_path = root / f"{key}.txt"

    if use_cache and meta_path.exists() and text_path.exists():
        try:
            rec = json.loads(meta_path.read_text(encoding="utf-8"))
            rec["extracted_path"] = str(text_path)
            rec["from_cache"] = True
            return FetchResult(rec)
        except Exception:
            pass  # unreadable cache entry → re-fetch

    # ── SSRF check FIRST, before any backend is chosen ──
    # Deliberately ahead of the Firecrawl branch. Routing through Firecrawl
    # does mean THEIR infrastructure cannot reach this machine's localhost, so
    # that hole is closed for anything they fetch — but Friday still falls back
    # to a direct fetch when Firecrawl is unavailable, and a guard that lived
    # inside the direct branch would be skipped on the way in and applied only
    # on the way back. Checking here means there is no arrangement of failures
    # in which a URL reaches the network unchecked.
    ok, why = check_url(url)
    if not ok:
        return _fail(url, why, "refused_unsafe")

    if _firecrawl_enabled():
        rec = _fetch_via_firecrawl(url, key, root, meta_path, text_path,
                                   register_provenance)
        if rec is not None:
            return rec
        # Fall through to the direct path — which re-checks nothing, because
        # the check above already ran and the URL has not changed.

    current = url
    seen: list[str] = []
    resp = None
    try:
        for _hop in range(MAX_REDIRECT_HOPS + 1):
            seen.append(current)
            resp = requests.get(
                current, timeout=timeout, headers={"User-Agent": _UA},
                allow_redirects=False,      # THE point — see module docstring
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location") or ""
                if not loc:
                    return _fail(url, f"redirect with no destination at {current}",
                                 "bad_redirect")
                nxt = urljoin(current, loc)
                ok, why = check_url(nxt)
                if not ok:
                    # The interesting case: a public URL trying to bounce us
                    # somewhere internal. Name it precisely.
                    return _fail(url, f"redirect to {nxt!r} refused — {why}",
                                 "refused_unsafe")
                current = nxt
                continue
            break
        else:
            return _fail(url, f"too many redirects (>{MAX_REDIRECT_HOPS})",
                         "too_many_redirects")
    except UnsafeURLError as e:
        return _fail(url, str(e), "refused_unsafe")
    except Exception as e:
        return _fail(url, f"{type(e).__name__}: {e}", "network")

    if resp is None:
        return _fail(url, "no response", "network")
    if resp.status_code >= 400:
        return _fail(url, f"HTTP {resp.status_code}", "http_error")

    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and not (ctype.startswith("text/") or ctype in (
            "application/xhtml+xml", "application/xml", "application/json")):
        # §7.6: a limit the tools genuinely have is DISCLOSED, never quietly
        # swapped for a different source.
        return _fail(url, f"content type {ctype} cannot be read as text "
                          f"(this source exists but is not readable by Friday)",
                     "unreadable_type")

    html = resp.text
    text = _html_to_text(html)
    truncated = len(text) > MAX_EXTRACT_CHARS
    if truncated:
        text = text[:MAX_EXTRACT_CHARS]

    rec = FetchResult({
        "ok": True,
        "id": key,
        "url": url,
        "final_url": current,
        "redirect_chain": seen if len(seen) > 1 else [],
        "title": _title_of(html),
        "http_status": resp.status_code,
        "content_type": ctype,
        "fetched_at": time.time(),
        "chars": len(text),
        "truncated": truncated,
        "provenance": "fetched-by-friday-research",
        "via": "direct",
        "from_cache": False,
    })
    spans = _spans(text)
    rec["spans"] = spans

    try:
        root.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        meta_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        rec["extracted_path"] = str(text_path)
    except Exception as e:
        rec["cache_error"] = str(e)

    if register_provenance:
        _register_spans(rec, spans)
    return rec


def _register_spans(rec: dict, spans: list[dict]) -> None:
    """Register this page's spans as third-party published text (§5.7).

    Ingest-side only, exactly as the news exemption established: this is the
    fetch path, the text is on the list because Friday retrieved it from a
    public URL, and no send-time API exists to claim the exemption by asserting
    it. Friday's own analysis ABOUT the page still classifies normally.
    """
    try:
        from agent_friday.services import egress_gate
        origin = rec.get("final_url") or rec.get("url") or ""
        for sp in spans:
            egress_gate.register_public_text(sp.get("text", ""), origin=origin)
    except Exception:
        pass  # registration is an optimization; losing it costs recall, not safety


def load_extraction(source_id: str) -> str:
    """The verbatim text a finding was born from. Verification reads this,
    never a re-fetch (RS12)."""
    p = _store_root() / f"{source_id}.txt"
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def cache_stats() -> dict[str, Any]:
    root = _store_root()
    try:
        return {"entries": len(list(root.glob("*.json"))), "path": str(root)}
    except Exception:
        return {"entries": 0, "path": str(root)}
