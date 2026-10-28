"""Egress when the classifier fails or is still loading (NS-34.6-7), and every cloud provider
Friday can talk to reached only through the gate (NS-34.6-14). F5.

- An owner's never-send list that exists but cannot be read is an unknown floor, never an empty
  one: every cloud send stops, in words, until it can be read. (It used to be read as empty.)
- A send refused because the gate failed its startup self-test is a "blocked" row in the egress
  ledger, not only a log line.
- Background work that sent while the full privacy check was starting is told the truth: the
  parts not yet checked were left out and the rest went. (It used to say nothing was sent.)
- The Brave news query is gated like a web search query (it used to be recorded only).
- Every cloud provider in the registry is sealed by the gate before its request is posted; a new
  provider added without the gate fails here. Network stubbed; synthetic content only.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import egress_gate as eg

pytestmark = pytest.mark.real_provider_paths


# ── a never-send list that cannot be read ──────────────────────────────────

@pytest.fixture
def shield(monkeypatch):
    from agent_friday import core
    from agent_friday.services import judgment_gate as jg
    path = core.FRIDAY_DIR / "privacy_shield.json"
    before = path.read_bytes() if path.exists() else None
    monkeypatch.setitem(jg._NEVER_CACHE, "mtime", -1.0)
    yield path, jg
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(before)
    jg._NEVER_CACHE.update({"mtime": -1.0, "broken": False})


def test_an_unreadable_never_send_list_stops_every_cloud_send(shield):
    path, jg = shield
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(jg.NeverSendListUnreadable) as err:
        jg.never_send_hits("what is the weather like today")
    assert "could not be read" in str(err.value) and "nothing is sent" in str(err.value).lower()


def test_once_it_can_be_read_again_sends_go_on(shield):
    path, jg = shield
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(jg.NeverSendListUnreadable):
        jg.never_send_hits("hello")
    path.write_text(json.dumps({"never_send": ["SYNTHETIC-NEVER-TOKEN"]}), encoding="utf-8")
    assert jg.never_send_hits("hello") == []
    assert jg.never_send_hits("it says SYNTHETIC-NEVER-TOKEN here")


# ── a self-test failure is a blocked send in the ledger ─────────────────────

def test_a_send_refused_by_a_failed_self_test_is_a_blocked_row(tmp_path, monkeypatch):
    from agent_friday.services import model_router as smr
    log = tmp_path / "egress-log.jsonl"
    monkeypatch.setattr(eg, "_DEFAULT_LOG", log)
    monkeypatch.setattr(eg, "gate_operational", lambda: False)
    with pytest.raises(RuntimeError):
        smr._seal_or_block({"messages": [{"role": "user", "content": "hi"}]}, "openrouter", model="m")
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    calls = [r for r in rows if r.get("kind") == "call"]
    assert calls and calls[-1]["result"] == "blocked" and calls[-1]["provider"] == "openrouter"
    assert "self-test" in (calls[-1].get("note") or "")


# ── the background notice says what happened ────────────────────────────────

def test_background_work_is_told_what_was_sent_while_the_check_was_starting(monkeypatch):
    from agent_friday.services import notifications, sensitivity_classifier as sc
    pushed = []

    class _Engine:
        def push(self, **kw):
            pushed.append(kw)
    monkeypatch.setattr(notifications, "_notif_engine", _Engine(), raising=False)
    monkeypatch.setitem(sc._BG_NOTIFIED_AT, 0, 0.0)
    sc._record_layer3_hold("synthetic text")
    assert pushed, "a hold outside a turn is never silent"
    body = pushed[-1]["body"]
    assert "left out" in body and "the rest was sent" in body
    assert "nothing was sent" not in body.lower(), "the redacted send did go out"


# ── the news query is gated ─────────────────────────────────────────────────

def test_a_news_query_holding_private_words_is_not_sent(monkeypatch):
    import requests
    from agent_friday.services import news_engine as ne, web_search as ws
    sent = []
    monkeypatch.setattr(ws, "brave_key", lambda: "synthetic-not-real")
    monkeypatch.setattr(ws, "_gate_search_query", lambda q, p: ("", "it stays on this device"))
    monkeypatch.setattr(requests, "get", lambda *a, **k: sent.append(a) or pytest.fail("sent"))
    assert ne._brave_results("my diagnosis at the clinic", limit=3) == []
    assert sent == []


def test_a_plain_news_query_goes_through_the_gate(monkeypatch):
    import requests
    from agent_friday.services import news_engine as ne, web_search as ws
    gated, got = [], []
    monkeypatch.setattr(ws, "brave_key", lambda: "synthetic-not-real")
    monkeypatch.setattr(ws, "_gate_search_query", lambda q, p: gated.append((q, p)) or (q, ""))

    class _R:
        status_code = 200

        def json(self):
            return {"results": []}
    monkeypatch.setattr(requests, "get", lambda url, params=None, **k: got.append(params) or _R())
    ne._brave_results("solar eclipse", limit=3)
    assert gated == [("solar eclipse", "brave-news")] and got and got[0]["q"] == "solar eclipse"


# ── every cloud provider is sealed before it is posted ──────────────────────

def _cloud_providers():
    from agent_friday.routing import provider_descriptors as pd
    out = {d["name"]: d for d in pd.BUILTIN_EXTRA_PROVIDERS}
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        for d in get_provider_registry().list_providers():
            if isinstance(d, dict) and d.get("name"):
                out.setdefault(d["name"], d)
    except Exception:
        pass
    return [d for n, d in sorted(out.items())
            if eg._is_cloud(n) and (d.get("adapter") or d.get("type")) == pd.ADAPTER_OPENAI_COMPAT]


CLOUD = _cloud_providers()


def test_the_registry_lists_the_cloud_providers_this_test_covers():
    names = {d["name"] for d in CLOUD}
    for expected in ("openrouter", "groq", "together", "fireworks", "mistral", "deepseek", "xai",
                     "perplexity", "cohere", "huggingface"):
        assert expected in names, expected


@pytest.mark.parametrize("desc", CLOUD, ids=[d["name"] for d in CLOUD])
def test_every_cloud_provider_is_sealed_by_the_gate_before_its_request_is_posted(monkeypatch, desc):
    import requests
    from agent_friday.services import model_router as smr
    auth = desc.get("auth") or {}
    for env in [auth.get("key")] + list(auth.get("key_aliases") or []):
        if env:
            monkeypatch.setenv(env, "synthetic-not-real")
    sealed, posted = [], []

    def spy(payload, provider, model=""):
        sealed.append(provider)
        return payload
    monkeypatch.setattr(smr, "_seal_or_block", spy)

    class _R:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def post(url, headers=None, json=None, timeout=None, **kw):
        posted.append({"url": url, "sealed_before": list(sealed)})
        return _R()
    monkeypatch.setattr(requests, "post", post)
    smr._call_openai([{"role": "user", "content": "hello"}], model="synthetic-model", tools=None,
                     provider=desc["name"])
    assert posted, "%s was never posted (the dispatch failed)" % desc["name"]
    assert posted[0]["sealed_before"] == [desc["name"]], \
        "%s must pass the egress gate before anything is posted" % desc["name"]
