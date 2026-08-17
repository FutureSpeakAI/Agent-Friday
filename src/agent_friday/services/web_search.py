"""
web_search — structured web search with URLs you can actually fetch.

Three defects this replaces (deep-research.md P1, plus one the design did not
know about because it was found live on 2026-08-17):

  1. The old scraper returned `.result__url`'s DISPLAY TEXT as the url — a
     truncated, scheme-less domain string. search_web found a page and handed
     browse_web something unfetchable. The real href is on the `.result__a`
     anchor; that is what this module returns.

  2. LIVE FINDING, worse than the design assumed: html.duckduckgo.com now
     answers a plain GET with HTTP 202 and an anti-bot challenge page. Zero
     `.result` blocks parse, and the old code's fallback branch then returned
     the CHALLENGE PAGE's text under the heading "Search results for '<q>'".
     Friday's web search was not degraded, it was dead, and it reported its
     own deadness as content. A POST with a browser User-Agent gets HTTP 200
     and real results; that is the request this module makes.

  3. Zero results was ambiguous between "nothing is published about this" and
     "our scraper broke" (§7.2). Every response from this module carries a
     `status` that distinguishes them, and `canary()` settles it by asking a
     question with a known stable answer.

Backends, in order: Brave Web Search when BRAVE_SEARCH_API_KEY is set (the
paid general-search key Q1 approved), DuckDuckGo HTML otherwise. The DDG path
is a scrape and is labelled as one — when it breaks again, the caller finds
out rather than receiving an error page dressed as research.
"""
from __future__ import annotations

import os
import time
from typing import Any

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# A query whose answer is stable and heavily indexed. Used only to tell a
# broken tool from an empty web (§7.2) — never surfaced as a finding.
_CANARY_QUERY = "wikipedia"
_CANARY_TTL_S = 300.0
_canary_cache: dict[str, Any] = {"ts": 0.0, "ok": None, "detail": ""}


class SearchStatus:
    OK = "ok"                      # results returned
    EMPTY = "empty"                # backend answered, genuinely nothing
    BACKEND_BROKEN = "backend_broken"   # challenge page, parse failure, HTTP error
    NO_BACKEND = "no_backend"      # nothing configured or importable


def brave_key() -> str:
    """The Brave subscription token, from wherever Stephen put it.

    Environment first (start.bat, which is how every other key here is set),
    then the encrypted provider store, then settings. Checking all three
    matters because the alternative is a key that is present, paid for, and
    silently ignored because it went in the "wrong" place — and the symptom
    would be search quietly staying on the DuckDuckGo scrape with nothing
    saying why.

    ONE key covers both endpoints: the news engine's /res/v1/news/search and
    this module's /res/v1/web/search use the same X-Subscription-Token.
    """
    env = (os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()
    if env:
        return env
    try:
        from agent_friday.services import credential_store
        k = credential_store.get_provider_key("brave")
        if k and k.strip():
            return k.strip()
    except Exception:
        pass
    try:
        from agent_friday.core import _load_settings
        s = _load_settings() or {}
        for holder in (s, s.get("api_keys") or {}, s.get("search") or {}):
            if isinstance(holder, dict):
                v = holder.get("brave_search_api_key") or holder.get("brave_api_key")
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return ""


# ── Three states, never two (the false-green this surface exists to kill) ─────
#
# ABSENT           no key anywhere
# PRESENT_FAILING  a key is stored and Brave refuses it — the dangerous state,
#                  because search silently falls back to the scrape and a
#                  two-state report ("configured: true") would call it working
# WORKING          proven against a named endpoint, with the proof recorded
#
# The middle state is the whole point. A key that exists but does not work must
# never render as "Brave is primary" — that would hide a broken search
# permanently, which is worse than having no key at all, because nobody looks.
ABSENT = "absent"
PRESENT_FAILING = "present_but_failing"
WORKING = "working"
UNVERIFIED = "present_unverified"

_HEALTH: dict = {"state": UNVERIFIED, "proven_on": None, "detail": "",
                 "checked_at": 0.0}
_FC_HEALTH: dict = {"state": UNVERIFIED, "proven_on": None, "detail": "",
                    "checked_at": 0.0}


def health_state() -> dict:
    """Brave's three-state health, plus what it was proven against.

    Never makes a network call — search() records the truth as it goes, and
    verify_key() records it deliberately. A status endpoint that reached out
    on every page load would be its own problem.
    """
    if not brave_key():
        return {"state": ABSENT, "proven_on": None,
                "detail": "No Brave key is configured.", "checked_at": 0.0}
    return dict(_HEALTH)


def firecrawl_health() -> dict:
    """Same three states for Firecrawl. `working` only after a real query
    returned real results — never on the strength of a stored string."""
    if not _firecrawl_ready():
        return {"state": ABSENT, "proven_on": None,
                "detail": "No Firecrawl key is configured.", "checked_at": 0.0}
    return dict(_FC_HEALTH)


def _record_health(state: str, *, proven_on: str | None = None,
                   detail: str = "") -> None:
    import time as _t
    _HEALTH.update({"state": state, "proven_on": proven_on, "detail": detail,
                    "checked_at": _t.time()})


def _record_fc_health(state: str, *, proven_on: str | None = None,
                      detail: str = "") -> None:
    import time as _t
    _FC_HEALTH.update({"state": state, "proven_on": proven_on, "detail": detail,
                       "checked_at": _t.time()})


def verify_key(key: str | None = None) -> dict:
    """Ask Brave what this token is actually entitled to. One live call each.

    Exists because entitlement is not something to reason about. A token can be
    absent, malformed, unrecognized, valid-but-news-only, or valid-for-
    everything, and the only authority on which is Brave. Reasoning about it
    from the plan name on a pricing page is how you end up telling someone
    their key is "news-only" when the API has never seen it.

    Returns which endpoints answered, the error code for those that did not,
    and any rate-limit/quota headers the API volunteered.
    """
    import requests
    k = (key or brave_key() or "").strip()
    if not k:
        return {"configured": False, "valid": None,
                "summary": "No Brave key is configured."}

    endpoints = {
        "web": "https://api.search.brave.com/res/v1/web/search",
        "news": "https://api.search.brave.com/res/v1/news/search",
    }
    out: dict = {"configured": True, "endpoints": {}, "limits": {}}
    for name, url in endpoints.items():
        try:
            r = requests.get(url, params={"q": "test", "count": 1},
                             headers={"Accept": "application/json",
                                      "X-Subscription-Token": k}, timeout=20)
        except Exception as e:
            out["endpoints"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            continue
        entry = {"ok": r.status_code == 200, "http": r.status_code}
        if r.status_code != 200:
            try:
                entry["code"] = (r.json().get("error") or {}).get("code", "")
                entry["detail"] = (r.json().get("error") or {}).get("detail", "")
            except Exception:
                entry["detail"] = r.text[:200]
        for hk, hv in r.headers.items():
            if "ratelimit" in hk.lower() or "quota" in hk.lower():
                out["limits"][hk] = hv
        out["endpoints"][name] = entry

    web_ok = out["endpoints"].get("web", {}).get("ok")
    news_ok = out["endpoints"].get("news", {}).get("ok")
    out["valid"] = bool(web_ok or news_ok)
    out["web_search"] = bool(web_ok)
    if web_ok and news_ok:
        out["summary"] = "Valid for both web search and news."
        _record_health(WORKING, proven_on="/res/v1/web/search + /res/v1/news/search",
                       detail="200 OK on both endpoints")
    elif news_ok and not web_ok:
        out["summary"] = ("Valid for NEWS ONLY — web search is refused. The "
                          "research pipeline needs web search; this needs a "
                          "plan that includes it.")
        _record_health(PRESENT_FAILING, proven_on="/res/v1/news/search",
                       detail="news endpoint works, web search refused")
    elif web_ok:
        out["summary"] = "Valid for web search."
        _record_health(WORKING, proven_on="/res/v1/web/search",
                       detail="200 OK on web search")
    else:
        code = (out["endpoints"].get("web", {}).get("code")
                or out["endpoints"].get("news", {}).get("code") or "")
        out["summary"] = (f"Brave does not recognize this token ({code}). It is "
                          f"not a tier limitation — the API rejects it on every "
                          f"endpoint, the same way it rejects a made-up token.")
        _record_health(PRESENT_FAILING, proven_on=None,
                       detail=f"both endpoints refused ({code})")
    return out


def key_status() -> dict:
    """Where the key is (or is not), so 'why is search still scraping?' is
    answerable without reading source."""
    import os as _os
    sources = {
        "environment": bool((_os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()),
        "encrypted_store": False,
        "settings": False,
    }
    try:
        from agent_friday.services import credential_store
        sources["encrypted_store"] = bool(credential_store.get_provider_key("brave"))
    except Exception:
        pass
    try:
        from agent_friday.core import _load_settings
        s = _load_settings() or {}
        sources["settings"] = any(
            isinstance(h, dict) and (h.get("brave_search_api_key")
                                     or h.get("brave_api_key"))
            for h in (s, s.get("api_keys") or {}, s.get("search") or {}))
    except Exception:
        pass
    have = bool(brave_key())
    return {
        "configured": have,
        "state": health_state(),
        "found_in": [k for k, v in sources.items() if v],
        "backend": active_backend(),
        # A key being PRESENT is not a key being VALID. Saying "Brave is
        # primary" on the strength of a stored string is the same false-green
        # reporting this whole surface exists to remove — an invalid token
        # would leave every search silently falling back to the scrape while
        # the status page claimed otherwise. Presence and validity are reported
        # as different facts; verify_key() settles the second one.
        "note": ("A Brave key is configured and will be tried first. Whether "
                 "it AUTHENTICATES is a separate question — call this endpoint "
                 "with ?verify=1 to ask Brave directly." if have else
                 "No Brave key found in the environment, the encrypted "
                 "provider store, or settings — search is on the DuckDuckGo "
                 "scrape, which is fragile and has broken before. This also "
                 "means the news engine's Brave path is inert and news is "
                 "running on RSS alone."),
    }


def active_backend() -> str:
    """The backend that will be TRIED first.

    Chain: firecrawl -> brave -> duckduckgo-scrape. Firecrawl leads because it
    is the only one of the three that is not a scrape and that also returns
    page content; Brave stays in place (demoted, not deleted) in case that
    subscription gets sorted; the DDG scrape is last resort and is the one that
    has already broken once.

    "Tried first" is not "working" — see health_state().
    """
    try:
        from agent_friday.services import firecrawl
        if firecrawl.configured():
            return "firecrawl"
    except Exception:
        pass
    return "brave" if brave_key() else "duckduckgo-scrape"


def _firecrawl_search(query: str, count: int) -> dict:
    from agent_friday.services import firecrawl
    out = firecrawl.search(query, count)
    if not out["ok"]:
        return {"status": SearchStatus.BACKEND_BROKEN, "detail": out["error"]}
    res = [{"title": r["title"], "url": r["url"], "snippet": r["snippet"]}
           for r in out["results"]]
    return {"status": SearchStatus.OK if res else SearchStatus.EMPTY,
            "results": res, "detail": ""}


def _brave(query: str, count: int) -> dict:
    import requests
    key = brave_key()
    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(max(count, 1), 20)},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        timeout=20,
    )
    if r.status_code == 401:
        return {"status": SearchStatus.BACKEND_BROKEN,
                "detail": "Brave rejected the API key (HTTP 401)"}
    if r.status_code == 429:
        return {"status": SearchStatus.BACKEND_BROKEN,
                "detail": "Brave rate limit reached (HTTP 429)"}
    if r.status_code >= 400:
        return {"status": SearchStatus.BACKEND_BROKEN,
                "detail": f"Brave returned HTTP {r.status_code}"}
    data = r.json()
    items = ((data.get("web") or {}).get("results")) or []
    results = [{
        "title": (it.get("title") or "").strip(),
        "url": (it.get("url") or "").strip(),
        "snippet": (it.get("description") or "").strip(),
    } for it in items if (it.get("url") or "").startswith(("http://", "https://"))]
    return {"status": SearchStatus.OK if results else SearchStatus.EMPTY,
            "results": results, "detail": ""}


def _unwrap_ddg(href: str) -> str:
    """DDG sometimes wraps the destination in its own redirector
    (//duckduckgo.com/l/?uddg=<encoded>). Return the real target."""
    from urllib.parse import parse_qs, unquote, urlparse
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    p = urlparse(href)
    if "duckduckgo.com" in (p.netloc or "") and p.path.startswith("/l/"):
        target = (parse_qs(p.query).get("uddg") or [""])[0]
        if target:
            return unquote(target)
    return href


def _duckduckgo(query: str, count: int) -> dict:
    import requests
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"status": SearchStatus.NO_BACKEND,
                "detail": "beautifulsoup4 is not installed"}
    # POST, not GET: a GET now answers 202 with an anti-bot challenge.
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                      timeout=20, headers={"User-Agent": _UA})
    if r.status_code != 200:
        return {"status": SearchStatus.BACKEND_BROKEN,
                "detail": f"DuckDuckGo returned HTTP {r.status_code} "
                          f"(202 means its anti-bot challenge)"}
    body_l = r.text.lower()
    soup = BeautifulSoup(r.text, "html.parser")
    blocks = soup.select(".result")
    if not blocks:
        # Do NOT fall back to dumping page text as "results" — that is the
        # defect this module exists to remove.
        challenged = "anomaly" in body_l or "challenge" in body_l
        return {"status": SearchStatus.BACKEND_BROKEN,
                "detail": ("DuckDuckGo served an anti-bot challenge instead of "
                           "results" if challenged else
                           "DuckDuckGo's result markup did not parse — the "
                           "page layout likely changed")}
    results = []
    for b in blocks:
        a = b.select_one(".result__a")
        if not a:
            continue
        url = _unwrap_ddg((a.get("href") or "").strip())
        if not url.startswith(("http://", "https://")):
            continue
        snip = b.select_one(".result__snippet")
        results.append({
            "title": a.get_text(strip=True),
            "url": url,                                  # a real href, P1's fix
            "snippet": snip.get_text(strip=True) if snip else "",
        })
        if len(results) >= count:
            break
    return {"status": SearchStatus.OK if results else SearchStatus.EMPTY,
            "results": results, "detail": ""}


def search(query: str, count: int = 10) -> dict:
    """Search the web. Returns {status, results[], backend, detail, query}.

    `results[i]["url"]` is always a real, fetchable, clickable href — that is
    this function's contract and the thing the old one got wrong.
    """
    q = (query or "").strip()
    if not q:
        return {"status": SearchStatus.NO_BACKEND, "results": [], "query": q,
                "backend": "none", "detail": "empty query"}
    # The chain, in order, skipping backends that are not configured. Each
    # attempt records what it learned, so health cannot drift from reality and
    # the caller is always told which backend ACTUALLY answered — a result
    # labelled with the backend we hoped for would be its own small lie.
    runners = [("firecrawl", _firecrawl_search)] if _firecrawl_ready() else []
    if brave_key():
        runners.append(("brave", _brave))
    runners.append(("duckduckgo-scrape", _duckduckgo))

    tried: list[str] = []
    last: dict = {}
    for name, fn in runners:
        try:
            out = fn(q, count)
        except Exception as e:
            out = {"status": SearchStatus.BACKEND_BROKEN,
                   "detail": f"{type(e).__name__}: {e}"}
        _note_backend_health(name, out)
        if out.get("status") == SearchStatus.OK:
            out.setdefault("results", [])
            out["query"] = q
            out["backend"] = name
            if tried:
                out["detail"] = (f"{', '.join(tried)} failed; {name} answered. "
                                 + (out.get("detail") or "")).strip()
            return out
        tried.append(f"{name} ({out.get('detail') or out.get('status')})")
        last = out
    last.setdefault("results", [])
    last["query"] = q
    last["backend"] = runners[-1][0] if runners else "none"
    last["detail"] = "; ".join(tried)
    return last


def _firecrawl_ready() -> bool:
    try:
        from agent_friday.services import firecrawl
        return firecrawl.configured()
    except Exception:
        return False


def _note_backend_health(name: str, out: dict) -> None:
    """Every real search is evidence about a key, whether anyone asked or not."""
    st = out.get("status")
    if name == "brave":
        if st == SearchStatus.OK:
            _record_health(WORKING, proven_on="/res/v1/web/search",
                           detail="a live search returned results")
        elif st == SearchStatus.BACKEND_BROKEN:
            _record_health(PRESENT_FAILING, proven_on=None,
                           detail=str(out.get("detail"))[:160])
    elif name == "firecrawl":
        if st == SearchStatus.OK:
            _record_fc_health(WORKING, proven_on="/v1/search",
                              detail="a live search returned results")
        elif st == SearchStatus.BACKEND_BROKEN:
            _record_fc_health(PRESENT_FAILING, proven_on=None,
                              detail=str(out.get("detail"))[:160])


def canary(force: bool = False) -> dict:
    """Is the search tool working at all? (§7.2)

    Cheap and cached: a commission that finds nothing across ten sub-questions
    calls this once to decide whether it discovered an absence or hit a broken
    scraper. Reporting "there is nothing published" when the tool is down is a
    fabricated empirical result, which is worse than reporting nothing.
    """
    now = time.time()
    if not force and _canary_cache["ok"] is not None and \
            (now - _canary_cache["ts"]) < _CANARY_TTL_S:
        return {"ok": _canary_cache["ok"], "detail": _canary_cache["detail"],
                "cached": True}
    out = search(_CANARY_QUERY, count=3)
    ok = out.get("status") == SearchStatus.OK and bool(out.get("results"))
    detail = out.get("detail") or ("search is answering normally" if ok else
                                   f"status={out.get('status')}")
    _canary_cache.update({"ts": now, "ok": ok, "detail": detail})
    return {"ok": ok, "detail": detail, "backend": out.get("backend"),
            "cached": False}


def status_note(out: dict) -> str:
    """The sentence a tool result should carry when a search returns nothing —
    written so a model cannot mistake a broken tool for an empty web."""
    st = out.get("status")
    if st == SearchStatus.OK:
        return ""
    if st == SearchStatus.EMPTY:
        c = canary()
        if c.get("ok"):
            return ("No results. The search tool is verified working (canary "
                    "passed), so this is a genuine absence for this query.")
        return ("No results, AND the search tool failed its canary check "
                f"({c.get('detail')}). Treat this as a BROKEN TOOL, not as "
                f"evidence that nothing is published. Do not report an absence.")
    if st == SearchStatus.NO_BACKEND:
        return f"Search is not available: {out.get('detail')}"
    return (f"The search backend failed: {out.get('detail')}. This is a tool "
            f"failure, NOT evidence that nothing is published.")
