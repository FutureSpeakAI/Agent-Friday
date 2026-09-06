"""services/firecrawl.py was the eleventh ungated egress path from
security-boundary.md §19 (a36ae73 closed the other ten). Its search()
query was gated only because web_search.search() gates before calling in;
scrape(url) from web_fetch.py had no gate anywhere on its path. The gate
now lives inside the module, on both caller-supplied values, and fails
closed when the gate itself is unreachable -- the same contract as
web_search._gate_search_query.
"""
from __future__ import annotations

import pytest

from agent_friday.services import firecrawl as fc
from agent_friday.services import egress_gate

TIER3 = "my SSN is 123-45-6789, is it associated with any breaches"  # pragma: allowlist secret


@pytest.fixture
def wire(monkeypatch):
    """Pretend a key is configured and capture every outbound POST."""
    import requests
    sent = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"success": True,
                    "data": [{"url": "https://example.com/a", "title": "t",
                              "description": "d", "markdown": "# page\nbody"}]
                    if "/search" in sent[-1]["url"] else
                    {"markdown": "# page\nbody",
                     "metadata": {"title": "t", "sourceURL": "https://example.com/", "statusCode": 200}}}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "body": json})
        return _Resp()

    monkeypatch.setattr(fc, "api_key", lambda: "fake-firecrawl-key")  # pragma: allowlist secret
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(fc, "_meter", lambda *a, **k: None)
    return sent


def test_tier3_query_never_reaches_firecrawl(wire):
    out = fc.search(TIER3)
    assert wire == [], "a TIER_3 query must never reach Firecrawl"
    assert out["ok"] is False and out.get("withheld") is True
    assert "123-45-6789" not in out["error"]  # pragma: allowlist secret


def test_tier3_url_never_reaches_firecrawl(wire):
    out = fc.scrape("https://example.com/lookup?ssn=123-45-6789")  # pragma: allowlist secret
    assert wire == [], "a URL carrying TIER_3 content must never reach Firecrawl"
    assert out["ok"] is False and out.get("withheld") is True


def test_benign_query_and_url_still_go_out(wire):
    s = fc.search("what's the weather in Denver", count=1)
    assert s["ok"] is True and wire[-1]["body"]["query"] == "what's the weather in Denver"
    p = fc.scrape("https://example.com/")
    assert p["ok"] is True and wire[-1]["body"]["url"] == "https://example.com/"


def test_gate_failure_fails_closed(wire, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gate exploded")
    monkeypatch.setattr(egress_gate, "_gate_text", boom)
    assert fc.search("anything")["ok"] is False
    assert fc.scrape("https://example.com/")["ok"] is False
    assert wire == []
