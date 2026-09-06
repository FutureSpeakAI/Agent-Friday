"""services/kie_generate.py — dispatch for kie.ai-seated creative models.

kie.ai (https://docs.kie.ai) is a pay-per-use marketplace that resells API
access to third-party creative models (Kling, Flux-2, Nano Banana Pro, GPT
Image 2, Qwen, Seedream, Hailuo/MiniMax, ElevenLabs, ...) at 30-50% below the
vendors' own prices. Registered in services/provider_registry (provider "kie")
with role "creative" so its models appear in the image/video pickers exactly
like Higgsfield's.

The shape here deliberately mirrors services/higgsfield_generate.py — same
submit -> poll -> pull-to-disk -> record lifecycle, same return envelope — but
the transport is a REAL DIFFERENCE, not a stylistic one:

  * Higgsfield is called over an MCP connector Friday already runs. kie.ai is
    a plain HTTPS API: this module is the direct HTTP client, gated through
    routing/provider_descriptors for the encrypted-store-then-env API key
    (Settings -> Providers -> kie.ai, same flow as every other provider) and
    through services/egress_gate for the prompt.
  * kie.ai's task model is submit -> POLL (services/kie_generate._poll) or a
    webhook callback. This module polls only — a webhook needs a publicly
    reachable callback URL, which is an exposure decision for Stephen, not
    this integration (see the provider_registry.py comment on the "kie"
    entry).
  * kie.ai enforces "up to 20 new generation requests per 10 seconds" with a
    hard 429 on the 21st — no queuing on their side. `_RateLimiter` below is
    the client-side half of respecting that: it blocks the caller rather than
    firing the 21st request and discovering the limit as a production error.
  * kie.ai deletes generated media after 14 days and task logs after 2
    months (docs.kie.ai, retrieved 2026-09-04 — Higgsfield's floor is 7 days,
    kie.ai's is double that, but the obligation is the same): a completed
    task is not "done" until the bytes are verified on this machine, which is
    exactly what services/creative_store.download_output enforces. Every
    kie.ai job ends up there.
  * kie.ai reports `creditsConsumed` on the completed task — real per-task
    spend, unlike Higgsfield which required a separate cost-preflight call.
    This module feeds that straight into services/cost_meter.record() so
    kie.ai spend actually lands in the cost ledger, which — as of 2026-09-04
    — NOTHING in services/creative_engine.py did for any creative provider,
    Higgsfield included. That gap is still open for Higgsfield; this module
    closes it for kie.ai only.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

_log = logging.getLogger("friday.kie_generate")

PROVIDER = "kie"
BASE_URL = "https://api.kie.ai/api/v1"
CREATE_TASK_URL = f"{BASE_URL}/jobs/createTask"
RECORD_INFO_URL = f"{BASE_URL}/jobs/recordInfo"
CREDIT_URL = f"{BASE_URL}/chat/credit"

#: kie.ai does not publish a fixed USD-per-credit rate; ~$0.005/credit is the
#: figure reported across third-party reviews as of 2026-09 (docs.kie.ai
#: itself only says "view consumption at kie.ai/logs"). Treat this as an
#: ESTIMATE for the cost ledger, not an invoice — kie.ai/logs is the
#: authoritative source Stephen asked to be able to trust.
CREDIT_USD_ESTIMATE = 0.005

#: Vendor limit: "up to 20 new generation requests per 10 seconds" per
#: account, HTTP 429 (not queued) past that. Client-side enforcement blocks
#: the caller instead of firing the 21st request and finding out in
#: production. A little under the wire (18/10s) so clock skew between this
#: process's sleep and the vendor's own window doesn't trip it anyway.
_RATE_LIMIT_N = 18
_RATE_LIMIT_WINDOW_S = 10.0

#: Terminal task states (docs.kie.ai/market/common/get-task-detail).
_TERMINAL = ("success", "fail")
#: Poll cadence + budget. Video renders can run minutes; images are seconds.
_POLL_INTERVAL_S = 3.0
_MAX_POLLS = 200  # ~10 minutes at the interval above

_TOOLS = {"image": "image", "video": "video", "audio": "audio"}


class _RateLimiter:
    """Blocking sliding-window limiter: at most N acquisitions per window.

    A 429 from kie.ai is not queued — it is simply rejected — so the only way
    to "respect" the limit rather than discover it in production is to never
    send the request that would trip it. `acquire()` sleeps as needed; it
    never raises.
    """

    def __init__(self, n: int, window_s: float):
        self._n = n
        self._window_s = window_s
        self._hits: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and (now - self._hits[0]) > self._window_s:
                    self._hits.popleft()
                if len(self._hits) < self._n:
                    self._hits.append(now)
                    return
                wait = self._window_s - (now - self._hits[0]) + 0.05
            time.sleep(max(0.0, wait))


_LIMITER = _RateLimiter(_RATE_LIMIT_N, _RATE_LIMIT_WINDOW_S)


# ── Auth + the single HTTP seam ──────────────────────────────────────────────

def _headers() -> dict:
    """Bearer auth from the SAME resolver every other provider uses — the
    saved-key-then-environment precedence from routing.provider_descriptors,
    never a raw env lookup. The key never appears in this module as a literal
    or a default."""
    from agent_friday.routing.provider_descriptors import (
        auth_headers, provider_api_key)
    from agent_friday.services.provider_registry import get_provider_registry
    prov = get_provider_registry().get_provider(PROVIDER) or {}
    headers = {"Content-Type": "application/json"}
    headers.update(auth_headers(prov, provider_api_key(prov)))
    return headers


def is_configured() -> bool:
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        return get_provider_registry().is_provider_available(PROVIDER)
    except Exception:
        return False


def check_credentials(name: str = PROVIDER) -> dict:
    """A real, free, authoritative round trip against kie.ai: GET the account
    credit balance. Requires a valid key, spends nothing, and creates no
    task — kie.ai has no /models endpoint to probe (services/provider_health
    used to fall back to one anyway and get a 404 on every check) and no
    chat-completions shape for a cheap 1-token generation, so this is the one
    check that actually proves the key works rather than merely existing.

    Returns the same shape services/provider_health._check() returns:
    {provider, status, detail, config, proved_inference[, credits]}.
    Never raises.
    """
    if not is_configured():
        return {"provider": name, "status": "missing", "detail": "no API key",
                "config": "missing", "proved_inference": False}
    try:
        import requests
        _LIMITER.acquire()
        resp = requests.get(CREDIT_URL, headers=_headers(), timeout=15)
    except Exception as e:
        return {"provider": name, "status": "down",
                "detail": f"{type(e).__name__}: {e}"[:160],
                "config": "ok", "proved_inference": False}
    if resp.status_code == 401:
        # docs.kie.ai's documented shape for this case:
        # {"code":401,"msg":"You do not have access permissions"}
        return {"provider": name, "status": "down",
                "detail": "kie.ai rejected this key (HTTP 401) — it may be "
                          "wrong, revoked, or need to be re-saved in Settings",
                "config": "ok", "proved_inference": True}
    if resp.status_code >= 400:
        return {"provider": name, "status": "error",
                "detail": f"HTTP {resp.status_code}",
                "config": "ok", "proved_inference": False}
    try:
        body = resp.json()
    except ValueError:
        return {"provider": name, "status": "error",
                "detail": "kie.ai returned a non-JSON response",
                "config": "ok", "proved_inference": False}
    if not isinstance(body, dict) or body.get("code") not in (200, None):
        detail = f"kie.ai error {body.get('code')}: {body.get('msg') or 'unknown'}" \
            if isinstance(body, dict) else "kie.ai returned an unexpected body"
        return {"provider": name, "status": "down", "detail": detail[:160],
                "config": "ok", "proved_inference": True}
    credits = body.get("data")
    detail = (f"key verified — {credits} credits available"
              if isinstance(credits, (int, float)) else "key verified")
    out = {"provider": name, "status": "ok", "detail": detail,
           "config": "ok", "proved_inference": True}
    if isinstance(credits, (int, float)):
        out["credits"] = credits
    return out


def _request(method: str, url: str, *, json_body: dict | None = None,
             params: dict | None = None, timeout: float = 60.0) -> dict:
    """One HTTP call, rate-limited and parsed. Raises on transport/HTTP/vendor
    error; callers convert that into a status, never a crash. Monkeypatch
    this in tests — nothing else here touches the wire directly."""
    import requests
    _LIMITER.acquire()
    resp = requests.request(method, url, headers=_headers(), json=json_body,
                            params=params, timeout=timeout)
    if resp.status_code == 429:
        raise RuntimeError("kie.ai rate limit hit (HTTP 429) despite the "
                           "client-side limiter — retry shortly")
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("kie.ai returned a non-object response")
    code = body.get("code")
    if code is not None and int(code) != 200:
        raise RuntimeError(f"kie.ai error {code}: {body.get('msg') or 'unknown'}")
    return body


# ── Catalogue membership ─────────────────────────────────────────────────────

def is_kie_model(model_id: str) -> bool:
    """True when this id is in kie.ai's hand-curated static catalogue
    (provider_registry.py — kie.ai has no live enumeration API to check
    against, unlike Higgsfield's models_explore)."""
    mid = str(model_id or "").strip()
    if not mid:
        return False
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        prov = get_provider_registry().get_provider(PROVIDER) or {}
        return mid in set(prov.get("models") or [])
    except Exception:
        return False


# ── Submit + poll ────────────────────────────────────────────────────────────

def _create_task(model: str, input_params: dict) -> str:
    body = {"model": model, "input": {k: v for k, v in input_params.items()
                                       if v is not None}}
    reply = _request("POST", CREATE_TASK_URL, json_body=body, timeout=60.0)
    task_id = (reply.get("data") or {}).get("taskId")
    if not task_id:
        raise ValueError(f"kie.ai createTask returned no taskId: {reply}")
    return str(task_id)


def _parse_result_urls(record: dict) -> list:
    """Every output URL in a completed task's `resultJson`.

    resultJson is a JSON-encoded STRING (docs.kie.ai), not a nested object —
    parsed defensively since the field is also documented to sometimes carry
    resultObject (non-media results) instead of resultUrls.
    """
    raw = record.get("resultJson")
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    urls = parsed.get("resultUrls")
    if isinstance(urls, list):
        return [u for u in urls if isinstance(u, str) and u.startswith("http")]
    single = parsed.get("resultUrl")
    if isinstance(single, str) and single.startswith("http"):
        return [single]
    return []


def _poll(task_id: str) -> dict:
    """Poll recordInfo until the task reaches a terminal state or the poll
    budget runs out. Returns the last `data` payload either way."""
    last: dict = {}
    for _ in range(_MAX_POLLS):
        reply = _request("GET", RECORD_INFO_URL,
                         params={"taskId": task_id}, timeout=30.0)
        last = reply.get("data") or {}
        if str(last.get("state") or "").lower() in _TERMINAL:
            return last
        time.sleep(_POLL_INTERVAL_S)
    return last


# ── Public entry point ───────────────────────────────────────────────────────

def generate(kind: str, prompt: str, *, model: str, aspect_ratio=None,
             n: int = 1, dest_dir=None, extra: dict | None = None) -> dict:
    """Run one kie.ai generation end to end and land the bytes on disk.

    Returns the same envelope shape as higgsfield_generate.generate():
      {status: 'ok', provider: 'kie', files: [...], model, api_model, prompt,
       credits, cost_usd, task_id}
    or {status: 'error'|'blocked'|'unavailable', reason, ...}. Never raises.
    """
    if kind not in _TOOLS:
        return {"status": "error", "reason": f"unsupported kind {kind!r}"}
    if not is_configured():
        return {"status": "unavailable", "provider": PROVIDER, "model": model,
                "reason": "kie.ai has no API key configured — add one in "
                          "Settings → Providers"}

    try:
        from agent_friday.services import egress_gate as _eg
        gated_prompt = _eg.gate_text(prompt or "", PROVIDER, f"{kind}.prompt")
    except Exception:
        gated_prompt = prompt
    if prompt and not gated_prompt:
        # gate_text returns "" for content classified SENSITIVE — dropped
        # rather than sent. Nothing was submitted, nothing was charged.
        return {"status": "blocked", "provider": PROVIDER, "model": model,
                "reason": "prompt blocked by the egress gate before submission"}

    params = {"prompt": gated_prompt} if gated_prompt else {}
    if aspect_ratio:
        params["aspect_ratio"] = aspect_ratio
    params.update({k: v for k, v in (extra or {}).items() if v is not None})

    try:
        task_id = _create_task(model, params)
    except Exception as e:
        return {"status": "unavailable", "provider": PROVIDER, "model": model,
                "prompt": prompt,
                "reason": f"kie.ai submit failed: {e}"[:300]}

    try:
        record = _poll(task_id)
    except Exception as e:
        return {"status": "error", "provider": PROVIDER, "model": model,
                "prompt": prompt, "task_id": task_id,
                "reason": f"kie.ai poll failed: {e}"[:300]}

    state = str(record.get("state") or "").lower()
    credits = record.get("creditsConsumed")
    cost_usd = None
    if isinstance(credits, (int, float)):
        cost_usd = round(float(credits) * CREDIT_USD_ESTIMATE, 6)
        try:
            from agent_friday.services import cost_meter
            cost_meter.record(PROVIDER, model, 0, 0, cost_usd=cost_usd,
                              kind="creative")
        except Exception as e:      # metering must never break generation
            _log.warning("kie.ai cost_meter.record failed: %s", e)

    if state != "success":
        return {"status": "error", "provider": PROVIDER, "model": model,
                "prompt": prompt, "task_id": task_id, "credits": credits,
                "cost_usd": cost_usd,
                "reason": record.get("failMsg")
                          or f"kie.ai task ended in state {state!r} "
                             f"(may still be running — task {task_id})"}

    urls = _parse_result_urls(record)
    if not urls:
        return {"status": "error", "provider": PROVIDER, "model": model,
                "prompt": prompt, "task_id": task_id, "credits": credits,
                "cost_usd": cost_usd,
                "reason": "kie.ai reported success with no output URL",
                "detail": str(record)[:300]}

    from agent_friday.services import creative_store
    job = {"provider": PROVIDER, "kind": kind, "request_id": task_id,
           "prompt": prompt, "model": model}
    files, failures = [], []
    if kind == "image":
        urls = urls[: max(1, min(int(n or 1), 4))]
    for url in urls:
        try:
            res = creative_store.download_output(url, job=job, dest_dir=dest_dir)
        except Exception as e:
            failures.append(f"{url}: {e}")
            continue
        if res.get("ok"):
            files.append({"path": res.get("path"), "url": url})
        else:
            failures.append(f"{url}: {res.get('error') or 'download failed'}")

    if not files:
        return {"status": "error", "provider": PROVIDER, "model": model,
                "prompt": prompt, "task_id": task_id, "credits": credits,
                "cost_usd": cost_usd, "output_urls": urls,
                "reason": "kie.ai finished but the output could not be "
                          "saved locally. Files are on kie.ai for up to 14 "
                          "days: " + "; ".join(urls[:3]),
                "detail": "; ".join(failures)[:300]}

    out = {"status": "ok", "provider": PROVIDER, "files": files,
           "model": model, "api_model": model, "prompt": prompt,
           "credits": credits, "cost_usd": cost_usd, "task_id": task_id}
    if failures:
        out["partial"] = failures
    return out
