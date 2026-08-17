"""
firecrawl — search and page-fetch in one service, returning rendered content.

WHY THIS IS THE RIGHT BACKEND FOR FRIDAY SPECIFICALLY. Both of the tools this
work started from were broken in ways Firecrawl addresses directly, and it is
worth being precise about which, because "it's a better API" is not a reason:

  * search_web returned link TEXT instead of hrefs, and the DuckDuckGo scrape
    it depended on now answers with an anti-bot challenge. Firecrawl's /search
    returns real URLs from a maintained index — no scraping, nothing to break
    when a results page changes its markup.
  * browse_web extracted text with BeautifulSoup, which sees almost nothing on
    a client-rendered page. VERIFIED 2026-08-17: react.dev, an SPA, comes back
    from Firecrawl as 16,060 chars of markdown.
  * PDFs were a DISCLOSED limitation (§7.6 — "this source exists but I cannot
    read it"). VERIFIED: the STORM paper at arxiv.org/pdf/2402.14207 returns
    88,736 chars of markdown. That limitation is gone.

And the structural win for the research grind: **search can return page content
inline**. One /search call with scrapeOptions returned 3 results carrying
14k-56k chars of markdown each, in 6.1s total. The pipeline's
search -> select -> fetch -> extract sequence collapses into a single round
trip, which is the dominant cost in the 495s figure measured earlier.

Credits are consumed per page, so the harness treats them as a budget, not as
free.

WHAT THIS DOES NOT CHANGE: the SSRF guard. Firecrawl fetching server-side means
THEIR infrastructure cannot reach Stephen's localhost, which removes that hole
for anything routed through them — but Friday still has a direct-fetch path for
when Firecrawl is unavailable, and that path keeps its guard. The URL check
runs BEFORE the backend is chosen (see web_fetch.fetch) precisely so there is
no arrangement of failures in which a URL skips Firecrawl and goes direct
unchecked.
"""
from __future__ import annotations

import logging
import time
from typing import Any

_log = logging.getLogger("friday.firecrawl")

BASE = "https://api.firecrawl.dev"
API_VERSION = "v2"          # the documented base; v1 also answers, v2 is current
DEFAULT_TIMEOUT_S = 120
SEARCH_TIMEOUT_S = 180

# ── The rest of the surface, deliberately NOT built ───────────────────────────
#
# Relayed from Stephen's onboarding doc and NOT verified here, except where
# noted. Recorded so the next person does not rediscover it, and left unbuilt
# so this change stays about search and fetch:
#
#   POST /v2/parse        upload a LOCAL document (PDF/DOCX/XLSX/HTML, <=50 MB)
#                         as multipart, get markdown. Distinct from /scrape,
#                         which takes a URL. Would let Friday read a file
#                         Stephen drops in rather than one she can reach.
#   POST /v2/interact     browser actions on live pages — clicks, forms,
#                         navigation. Would reach content behind an
#                         interaction, which no fetch can.
#   POST /v2/monitor      recurring checks on pages/crawls/search results,
#                         DIFFED against the last snapshot, with a
#                         plain-language goal to filter noise. This is a
#                         candidate answer to the stale news-import problem —
#                         it is change detection with a relevance judgment
#                         attached, which is exactly what a news feed that
#                         re-reports the same story lacks. FLAGGED, NOT BUILT.
#   GET  /v2/search/research/papers
#                         scientific paper index with metadata, full-text
#                         passages and citation expansion. VERIFIED to exist
#                         (returns a schema error on a bad param rather than
#                         404), so the endpoint is real; its parameters were
#                         not explored.
#   POST /v2/support/ask  diagnose a failing call from its jobId, returning
#                         prose plus machine-readable retry parameters.
#
# NOT INSTALLED, deliberately: their doc offers `npx firecrawl-cli init --all
# --browser`, a CLI-plus-skills install. That is for an agent driving its own
# terminal. Friday IS the product, so this is the integrate-into-app-code case:
# plain REST from Friday's own process, key from the encrypted store. No global
# toolchain was added to Stephen's machine.


def api_key() -> str:
    """The Firecrawl key, from wherever it was put — same order as Brave's."""
    import os
    env = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    if env:
        return env
    try:
        from agent_friday.services import credential_store
        k = credential_store.get_provider_key("firecrawl")
        if k and k.strip():
            return k.strip()
    except Exception:
        pass
    try:
        from agent_friday.core import _load_settings
        s = _load_settings() or {}
        for holder in (s, s.get("api_keys") or {}, s.get("search") or {}):
            if isinstance(holder, dict):
                v = holder.get("firecrawl_api_key")
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return ""


def configured() -> bool:
    return bool(api_key())


def _headers() -> dict:
    return {"Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json"}


def _post(path: str, body: dict, timeout: int) -> tuple[dict | None, str]:
    """POST and return (json, error_detail). Never raises."""
    import requests
    if not configured():
        return None, "no Firecrawl API key configured"
    try:
        r = requests.post(f"{BASE}{path}", headers=_headers(), json=body,
                          timeout=timeout)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if r.status_code == 401:
        return None, "Firecrawl rejected the API key (HTTP 401)"
    if r.status_code == 402:
        return None, "Firecrawl account is out of credits (HTTP 402)"
    if r.status_code == 429:
        return None, "Firecrawl rate limit reached (HTTP 429)"
    if r.status_code >= 400:
        return None, f"Firecrawl returned HTTP {r.status_code}: {r.text[:200]}"
    try:
        return r.json(), ""
    except Exception as e:
        return None, f"Firecrawl returned unparseable JSON: {e}"


def _results_of(payload: dict) -> list:
    """Firecrawl has shipped `data` as both a list and a {web: [...]} dict.

    Handling both is not defensive clutter — a shape change here would look
    exactly like "the web returned nothing", which §7.2 exists to prevent
    anyone from ever reporting as an absence.
    """
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("web", "results", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def search(query: str, count: int = 10, *, with_content: bool = False,
           timeout: int | None = None) -> dict:
    """Search. With `with_content`, each result carries the page's markdown.

    Returns {ok, results[], error}. results[i] = {title, url, snippet, markdown}
    where markdown is "" unless with_content was asked for.
    """
    body: dict[str, Any] = {"query": query, "limit": max(1, min(count, 20))}
    if with_content:
        body["scrapeOptions"] = {"formats": ["markdown"], "onlyMainContent": True}
    payload, err = _post(f"/{API_VERSION}/search", body,
                         timeout or (SEARCH_TIMEOUT_S if with_content else 60))
    if payload is None:
        return {"ok": False, "results": [], "error": err}
    if not payload.get("success", True):
        return {"ok": False, "results": [],
                "error": str(payload.get("error") or "Firecrawl reported failure")}
    out = []
    for it in _results_of(payload):
        if not isinstance(it, dict):
            continue
        url = (it.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        out.append({
            "title": (it.get("title") or "").strip(),
            "url": url,
            "snippet": (it.get("description") or it.get("snippet") or "").strip(),
            "markdown": it.get("markdown") or "",
        })
    return {"ok": True, "results": out, "error": ""}


def scrape(url: str, *, timeout: int | None = None,
           main_only: bool = True) -> dict:
    """Fetch one page as markdown. Returns {ok, markdown, title, final_url,
    status, error}."""
    payload, err = _post(f"/{API_VERSION}/scrape",
                         {"url": url, "formats": ["markdown"],
                          "onlyMainContent": main_only},
                         timeout or DEFAULT_TIMEOUT_S)
    if payload is None:
        return {"ok": False, "error": err}
    if not payload.get("success", True):
        return {"ok": False,
                "error": str(payload.get("error") or "Firecrawl reported failure")}
    data = payload.get("data") or {}
    meta = data.get("metadata") or {}
    md = data.get("markdown") or ""
    if not md.strip():
        # A 200 with no content is not a success. Reporting it as one would
        # hand the pipeline an empty page and let it conclude the source said
        # nothing — the exact shape of a green job producing nothing.
        return {"ok": False,
                "error": ("Firecrawl returned no content for this URL "
                          f"(source status {meta.get('statusCode')})"),
                "status": meta.get("statusCode")}
    return {"ok": True, "markdown": md,
            "title": (meta.get("title") or "").strip(),
            "final_url": meta.get("sourceURL") or url,
            "status": meta.get("statusCode"), "error": ""}


def credits() -> dict:
    """Remaining credits and the billing window, straight from the API."""
    import requests
    if not configured():
        return {"ok": False, "error": "no key"}
    try:
        r = requests.get(f"{BASE}/{API_VERSION}/team/credit-usage",
                         headers={"Authorization": f"Bearer {api_key()}"},
                         timeout=30)
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        d = (r.json() or {}).get("data") or {}
        return {"ok": True, **d}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def verify() -> dict:
    """Ask Firecrawl what this key can actually do. One live call per endpoint.

    Same principle as the Brave check: entitlement is not something to reason
    about from a pricing page.
    """
    if not configured():
        return {"configured": False, "valid": None,
                "summary": "No Firecrawl key is configured."}
    out: dict = {"configured": True, "endpoints": {}}
    t0 = time.time()
    s = search("test", count=1)
    out["endpoints"]["search"] = {"ok": s["ok"], "error": s.get("error", ""),
                                  "results": len(s.get("results") or [])}
    sc = scrape("https://example.com")
    out["endpoints"]["scrape"] = {"ok": sc["ok"], "error": sc.get("error", ""),
                                  "chars": len(sc.get("markdown") or "")}
    out["credits"] = credits()
    out["elapsed_s"] = round(time.time() - t0, 1)
    search_ok = out["endpoints"]["search"]["ok"]
    scrape_ok = out["endpoints"]["scrape"]["ok"]
    out["valid"] = bool(search_ok or scrape_ok)
    out["search"] = bool(search_ok)
    out["scrape"] = bool(scrape_ok)
    if search_ok and scrape_ok:
        c = out["credits"]
        out["summary"] = (f"Valid for both search and page fetching."
                          + (f" {c.get('remaining_credits')} credits remaining "
                             f"until {str(c.get('billing_period_end'))[:10]}."
                             if c.get("ok") else ""))
    elif search_ok:
        out["summary"] = "Valid for search; page fetching refused."
    elif scrape_ok:
        out["summary"] = "Valid for page fetching; search refused."
    else:
        out["summary"] = ("Firecrawl refused this key on both endpoints: "
                          + (out["endpoints"]["search"]["error"] or "?"))
    return out
