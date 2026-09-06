"""Gauntlet finding Q7 part (c): Firecrawl/Brave paid API calls
(web_search.py, web_fetch.py -> firecrawl.py) were completely unmetered
despite firecrawl.py's own docstring stating credits must be treated as a
budget (docs/history/audits/gauntlet-2026-09-03/findings.jsonl, Q7).

Both `firecrawl.search()`/`firecrawl.scrape()` and `web_search._brave()`
are plain module-level functions, so these are real behavioral tests:
monkeypatch the HTTP layer to scripted 2xx responses, call the real
functions, and assert `cost_meter.record()` was invoked -- not a
source-text pin. Firecrawl bills in CREDITS (no single public $/credit
rate across its plan tiers), so this fix records a conservative,
worst-case-tier USD estimate rather than a fabricated precise figure --
these tests assert that estimate is actually being computed and recorded,
not that it is authoritative.
"""
from __future__ import annotations

import agent_friday.services.cost_meter as cost_meter
import agent_friday.services.firecrawl as fc
import agent_friday.services.web_search as ws


def _recording_record(monkeypatch, module):
    recorded = []

    def _fake_record(provider, model, **kw):
        recorded.append((provider, model, kw))
        return 0.0

    monkeypatch.setattr(module, "record", _fake_record)
    return recorded


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class TestFirecrawlCostMetering:
    def test_search_records_credits_used(self, monkeypatch):
        monkeypatch.setattr(fc, "configured", lambda: True)
        monkeypatch.setattr(fc, "_headers", lambda: {})
        monkeypatch.setattr(
            "requests.post",
            lambda *a, **k: _FakeResponse(200, {
                "success": True,
                "data": [{"url": "https://example.com", "title": "t", "description": "d"}],
            }))
        recorded = _recording_record(monkeypatch, cost_meter)

        out = fc.search("a harmless test query", count=10)

        assert out["ok"] is True
        assert len(recorded) == 1, (
            "cost_meter.record() was not called exactly once for a real "
            "Firecrawl search call"
        )
        provider, model, kw = recorded[0]
        assert provider == "firecrawl"
        assert model == "search"
        assert kw.get("cost_usd", 0) > 0, (
            "recorded $0 for a real, credit-consuming Firecrawl search call"
        )
        assert kw.get("kind") == "tool"

    def test_scrape_records_one_credit(self, monkeypatch):
        monkeypatch.setattr(fc, "configured", lambda: True)
        monkeypatch.setattr(fc, "_headers", lambda: {})
        monkeypatch.setattr(
            "requests.post",
            lambda *a, **k: _FakeResponse(200, {
                "success": True,
                "data": {"markdown": "# hello world", "metadata": {"title": "t",
                                                                    "sourceURL": "https://example.com",
                                                                    "statusCode": 200}},
            }))
        recorded = _recording_record(monkeypatch, cost_meter)

        out = fc.scrape("https://example.com")

        assert out["ok"] is True
        assert len(recorded) == 1, (
            "cost_meter.record() was not called exactly once for a real "
            "Firecrawl scrape call"
        )
        provider, model, kw = recorded[0]
        assert provider == "firecrawl"
        assert model == "scrape"
        expected = round(fc._FIRECRAWL_SCRAPE_CREDITS * fc._FIRECRAWL_USD_PER_CREDIT, 6)
        assert kw.get("cost_usd") == expected

    def test_a_failed_meter_call_never_breaks_the_fetch(self, monkeypatch):
        monkeypatch.setattr(fc, "configured", lambda: True)
        monkeypatch.setattr(fc, "_headers", lambda: {})
        monkeypatch.setattr(
            "requests.post",
            lambda *a, **k: _FakeResponse(200, {
                "success": True,
                "data": {"markdown": "content", "metadata": {}},
            }))

        def _boom(*a, **k):
            raise RuntimeError("cost_meter is down")

        monkeypatch.setattr(cost_meter, "record", _boom)

        out = fc.scrape("https://example.com")
        assert out["ok"] is True, f"a cost_meter failure broke the fetch: {out}"


class TestBraveCostMetering:
    def test_brave_search_records_a_flat_per_query_cost(self, monkeypatch):
        monkeypatch.setattr(ws, "brave_key", lambda: "fake-brave-key")
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _FakeResponse(200, {
                "web": {"results": [{"title": "t", "url": "https://example.com",
                                     "description": "d"}]},
            }))
        recorded = _recording_record(monkeypatch, cost_meter)

        out = ws._brave("a harmless test query", 10)

        assert out["status"] == ws.SearchStatus.OK
        assert len(recorded) == 1, (
            "cost_meter.record() was not called exactly once for a real "
            "Brave search call"
        )
        provider, model, kw = recorded[0]
        assert provider == "brave"
        assert kw.get("cost_usd") == ws._BRAVE_USD_PER_QUERY
        assert kw.get("kind") == "tool"

    def test_a_failed_meter_call_never_breaks_brave_search(self, monkeypatch):
        monkeypatch.setattr(ws, "brave_key", lambda: "fake-brave-key")
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _FakeResponse(200, {"web": {"results": []}}))

        def _boom(*a, **k):
            raise RuntimeError("cost_meter is down")

        monkeypatch.setattr(cost_meter, "record", _boom)

        out = ws._brave("a harmless test query", 10)
        assert out["status"] == ws.SearchStatus.EMPTY
