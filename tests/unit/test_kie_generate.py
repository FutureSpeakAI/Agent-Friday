"""kie.ai dispatch — the pay-per-use creative marketplace integration.

Mirrors tests/unit/test_higgsfield_generate.py: pins that a picked model
actually routes, that a paid-for generation is never silently lost, that
kie.ai's rate limit is respected client-side rather than discovered as a
429 in production, and that spend lands in cost_meter (the gap Higgsfield
itself still has).
"""
from __future__ import annotations

import pytest

from agent_friday.services import kie_generate as kg

TASK_ID = "task_abc123"
URL = "https://cdn.kie.ai/out/%s.png" % TASK_ID


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(kg.time, "sleep", lambda *_a: None)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(kg, "is_configured", lambda: True)


# ── Catalogue membership gates dispatch ──────────────────────────────────────

def test_only_curated_ids_are_claimed(monkeypatch):
    class _Registry:
        def get_provider(self, name):
            return {"models": ["nano-banana-pro", "kling-3.0/video"]}

    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry", lambda: _Registry())
    assert kg.is_kie_model("nano-banana-pro") is True
    assert kg.is_kie_model("gpt-image-2-text-to-image-typo") is False
    assert kg.is_kie_model("") is False
    assert kg.is_kie_model(None) is False


def test_membership_never_raises_without_a_registry(monkeypatch):
    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry",
                        lambda: (_ for _ in ()).throw(OSError("no registry")))
    assert kg.is_kie_model("nano-banana-pro") is False


# ── Rate limiter ─────────────────────────────────────────────────────────────

def test_rate_limiter_blocks_past_the_window(monkeypatch):
    lim = kg._RateLimiter(2, 10.0)
    slept = []
    monkeypatch.setattr(kg.time, "sleep", lambda s: slept.append(s))
    lim.acquire()
    lim.acquire()
    lim.acquire()  # third in the same instant must wait
    assert slept and slept[0] > 0


def test_rate_limiter_forgets_old_hits(monkeypatch):
    lim = kg._RateLimiter(1, 10.0)
    lim._hits.append(kg.time.monotonic() - 20.0)  # long expired
    slept = []
    monkeypatch.setattr(kg.time, "sleep", lambda s: slept.append(s))
    lim.acquire()
    assert not slept  # the expired hit must not count against the new one


# ── Result parsing ───────────────────────────────────────────────────────────

def test_result_urls_parsed_from_json_string():
    import json
    record = {"resultJson": json.dumps({"resultUrls": [URL, "not-a-url"]})}
    assert kg._parse_result_urls(record) == [URL]


def test_result_urls_absent_is_empty_not_an_error():
    assert kg._parse_result_urls({}) == []
    assert kg._parse_result_urls({"resultJson": "{not json"}) == []


# ── End to end ───────────────────────────────────────────────────────────────

def _wire(monkeypatch, *, state="success", credits=50, download_ok=True):
    import json as _json

    calls = {"create": 0, "poll": 0}

    def fake_request(method, url, *, json_body=None, params=None, timeout=60.0):
        if url == kg.CREATE_TASK_URL:
            calls["create"] += 1
            return {"code": 200, "msg": "success", "data": {"taskId": TASK_ID}}
        if url == kg.RECORD_INFO_URL:
            calls["poll"] += 1
            return {"code": 200, "msg": "success", "data": {
                "taskId": TASK_ID, "state": state, "creditsConsumed": credits,
                "failMsg": "content policy violation" if state == "fail" else "",
                "resultJson": _json.dumps({"resultUrls": [URL]}),
            }}
        raise AssertionError("unexpected URL %s" % url)

    monkeypatch.setattr(kg, "_request", fake_request)

    def fake_download(url, *, job, dest_dir=None):
        if download_ok:
            return {"ok": True, "path": "/tmp/out.png"}
        return {"ok": False, "error": "disk full"}

    from agent_friday.services import creative_store
    monkeypatch.setattr(creative_store, "download_output", fake_download)

    metered = []
    from agent_friday.services import cost_meter
    monkeypatch.setattr(cost_meter, "record",
                        lambda *a, **kw: metered.append((a, kw)))

    return calls, metered


def test_happy_path_downloads_and_meters_cost(monkeypatch):
    calls, metered = _wire(monkeypatch)
    monkeypatch.setattr(kg, "_headers", lambda: {})

    out = kg.generate("image", "a red fox", model="nano-banana-pro")

    assert out["status"] == "ok"
    assert out["files"] == [{"path": "/tmp/out.png", "url": URL}]
    assert out["credits"] == 50
    assert out["task_id"] == TASK_ID
    assert calls == {"create": 1, "poll": 1}
    # Spend actually reached the cost ledger — the gap this integration closes.
    assert len(metered) == 1
    (provider, model, in_tok, out_tok), kw = metered[0]
    assert provider == "kie" and model == "nano-banana-pro"
    assert kw["cost_usd"] == pytest.approx(50 * kg.CREDIT_USD_ESTIMATE)
    assert kw["kind"] == "creative"


def test_failed_task_reports_vendor_reason_and_still_meters(monkeypatch):
    calls, metered = _wire(monkeypatch, state="fail", credits=5)
    out = kg.generate("video", "a dragon", model="kling-3.0/video")
    assert out["status"] == "error"
    assert "content policy" in out["reason"]
    assert len(metered) == 1  # kie.ai charges even for a failed generation


def test_download_failure_is_reported_not_silently_dropped(monkeypatch):
    _wire(monkeypatch, download_ok=False)
    out = kg.generate("image", "a red fox", model="nano-banana-pro")
    assert out["status"] == "error"
    assert "could not be saved" in out["reason"]
    assert out["output_urls"] == [URL]  # rescuable by hand within the 14-day floor


def test_unconfigured_provider_reports_unavailable_not_error(monkeypatch):
    monkeypatch.setattr(kg, "is_configured", lambda: False)
    out = kg.generate("image", "a red fox", model="nano-banana-pro")
    assert out["status"] == "unavailable"


def test_sensitive_prompt_is_blocked_before_submission(monkeypatch):
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "gate_text", lambda text, provider, field: "")
    submitted = []
    monkeypatch.setattr(kg, "_create_task",
                        lambda *a, **k: submitted.append(1) or TASK_ID)
    out = kg.generate("image", "something sensitive", model="nano-banana-pro")
    assert out["status"] == "blocked"
    assert not submitted  # nothing left the machine, nothing was charged


# ── check_credentials() — the real, free, keyed round trip ──────────────────
#
# kie.ai has no /models endpoint and no chat-completions shape, so
# services/provider_health used to fall back to a generic GET {base_url}/models
# for it — and got a 404 every time, reporting a perfectly good key as down.
# Found live 2026-09-06 (the maintainer: "the API key is not working correctly").
# check_credentials() is the fix: GET /chat/credit, kie.ai's one free,
# authoritative, keyed endpoint.

class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def test_check_credentials_reports_ok_with_balance(monkeypatch):
    monkeypatch.setattr(kg, "_headers", lambda: {})
    import requests
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _Resp(200, {"code": 200, "data": 137}))
    out = kg.check_credentials()
    assert out["status"] == "ok"
    assert out["proved_inference"] is True
    assert out["credits"] == 137


def test_check_credentials_401_is_a_real_verdict_not_unknown(monkeypatch):
    """Documented kie.ai shape for an invalid key:
    {"code":401,"msg":"You do not have access permissions"} — this must
    report a definite `down`, not the generic key_verdict.UNKNOWN a provider
    with no probe_spec gets."""
    monkeypatch.setattr(kg, "_headers", lambda: {})
    import requests
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: _Resp(401, {"code": 401,
                                    "msg": "You do not have access permissions"}))
    out = kg.check_credentials()
    assert out["status"] == "down"
    assert out["proved_inference"] is True
    assert "401" in out["detail"]


def test_check_credentials_unconfigured_is_missing_not_down(monkeypatch):
    monkeypatch.setattr(kg, "is_configured", lambda: False)
    out = kg.check_credentials()
    assert out["status"] == "missing"


def test_check_credentials_never_raises_on_transport_failure(monkeypatch):
    import requests

    def boom(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    out = kg.check_credentials()
    assert out["status"] == "down"


# ── provider_health delegates to check_credentials for ptype "kie" ──────────

def test_provider_health_deep_check_uses_check_credentials(monkeypatch):
    from agent_friday.services import provider_health as ph

    class _Registry:
        def get_provider(self, name):
            return {"name": "kie", "type": "kie",
                    "auth": {"type": "env_var", "key": "KIE_API_KEY"}}

    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry", lambda: _Registry())
    monkeypatch.setenv("KIE_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(kg, "check_credentials",
                        lambda name="kie": {"provider": "kie", "status": "ok",
                                            "detail": "key verified — 9 credits",
                                            "credits": 9})
    result = ph._check("kie", deep=True)
    assert result["status"] == "ok"
    assert result["credits"] == 9
