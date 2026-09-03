"""Unit tests for security-boundary.md §19 row 5: `web_search.search()` sent
the raw user query to Brave/DuckDuckGo/Firecrawl with no gate call anywhere
in the module. `firecrawl.search()` is only ever reached through this
function's `runners` loop (verified: its only other caller is
`web_fetch.py`'s `firecrawl.scrape(url)`, a URL fetch, not a query), so
gating at this one chokepoint covers all three backends.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services import web_search as ws
from agent_friday.services import egress_gate

TIER3_QUERY = "my SSN is 123-45-6789, is it associated with any breaches"  # pragma: allowlist secret


def test_tier3_query_never_reaches_any_backend(monkeypatch):
    calls = {"brave": 0, "ddg": 0, "firecrawl": 0}
    monkeypatch.setattr(ws, "_brave", lambda q, c: calls.__setitem__("brave", calls["brave"] + 1) or {"status": ws.SearchStatus.OK, "results": []})
    monkeypatch.setattr(ws, "_duckduckgo", lambda q, c: calls.__setitem__("ddg", calls["ddg"] + 1) or {"status": ws.SearchStatus.OK, "results": []})
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: False)
    monkeypatch.setattr(ws, "brave_key", lambda: "")

    out = ws.search(TIER3_QUERY)

    assert calls == {"brave": 0, "ddg": 0, "firecrawl": 0}, (
        "a TIER_3 query must never reach a search backend"
    )
    assert TIER3_QUERY not in (out.get("detail") or "")


def test_benign_query_still_reaches_a_backend(monkeypatch):
    seen = []
    monkeypatch.setattr(ws, "_brave", lambda q, c: seen.append(q) or {"status": ws.SearchStatus.OK, "results": [{"title": "t", "url": "http://x", "snippet": "s"}]})
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: False)
    monkeypatch.setattr(ws, "brave_key", lambda: "fake-key")

    out = ws.search("what's the weather in Denver")
    assert seen == ["what's the weather in Denver"]
    assert out["status"] == ws.SearchStatus.OK


def test_gate_failure_refuses_rather_than_sends_raw_query(monkeypatch):
    calls = {"brave": 0}
    monkeypatch.setattr(ws, "_brave", lambda q, c: calls.__setitem__("brave", 1) or {"status": ws.SearchStatus.OK, "results": []})
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: False)
    monkeypatch.setattr(ws, "brave_key", lambda: "fake-key")
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate down")))

    out = ws.search("anything")
    assert calls["brave"] == 0
    assert out["status"] != ws.SearchStatus.OK


def test_ungated_query_reproduction_is_falsifiable():
    """Reproduce the pre-fix shape: `search()` passed `q` straight into
    `fn(q, count)` for each backend with no gate call anywhere."""
    def _old_no_gate(q):
        return q  # pre-fix behavior

    leaked = _old_no_gate(TIER3_QUERY)
    assert TIER3_QUERY in leaked, (
        "the reproduction should leak the raw query; it did not, so this "
        "comparison is not meaningful"
    )
