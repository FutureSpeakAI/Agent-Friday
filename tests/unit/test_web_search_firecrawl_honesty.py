"""Firecrawl exists, is preferred, and a missing key is reported as exactly that.

With no FIRECRAWL_API_KEY anywhere, every query falls to the DuckDuckGo
scrape (HTTP 202 anti-bot walls), and a model that is not told otherwise says
"I don't have Firecrawl wired up as a tool right now". Firecrawl is the first
backend in `web_search.search()`'s chain; without a key it is unconfigured,
not absent. Three things that could fail:

  1. With a key, Firecrawl is tried first.
  2. Without a key, a failed search says Firecrawl is unconfigured and how
     to configure it -- and never that it does not exist.
  3. The tool description the model reads names Firecrawl as part of the tool.
"""
from __future__ import annotations

from agent_friday.services import web_search as ws


def _no_gate(monkeypatch):
    monkeypatch.setattr(ws, "_gate_search_query", lambda q, backend: (q, None))
    monkeypatch.setattr(ws, "_note_backend_health", lambda name, out: None)
    monkeypatch.setattr(ws, "brave_key", lambda: "")
    # wigolo runs on 127.0.0.1:3333 when installed, answers FIRST, and is
    # keyless — so on a machine where it is up these tests assert a failure
    # that never happens and fail for a reason that has nothing to do with
    # Firecrawl. A test whose result depends on whether a local service
    # happens to be running is measuring the afternoon, not the code.
    # Same fix as tests/unit/test_capability_state.py::_unkeyed.
    monkeypatch.setattr(ws, "_wigolo_ready", lambda: False)


def test_firecrawl_is_tried_first_when_a_key_is_present(monkeypatch):
    _no_gate(monkeypatch)
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: True)
    from agent_friday.services import firecrawl
    monkeypatch.setattr(firecrawl, "configured", lambda: True)   # active_backend() asks it directly
    order = []

    def fc(q, n):
        order.append("firecrawl")
        return {"status": ws.SearchStatus.OK,
                "results": [{"title": "t", "url": "https://x", "snippet": "s"}]}

    def ddg(q, n):
        order.append("duckduckgo-scrape")
        return {"status": ws.SearchStatus.BACKEND_BROKEN, "detail": "202"}
    monkeypatch.setattr(ws, "_firecrawl_search", fc)
    monkeypatch.setattr(ws, "_duckduckgo", ddg)
    out = ws.search("who is mahesh", count=5)
    assert order == ["firecrawl"]
    assert out["backend"] == "firecrawl" and out["status"] == ws.SearchStatus.OK
    assert ws.active_backend() == "firecrawl"


def test_without_a_key_the_failure_names_the_unconfigured_backend_and_the_fix(monkeypatch):
    _no_gate(monkeypatch)
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: False)
    monkeypatch.setattr(ws, "_duckduckgo", lambda q, n: {
        "status": ws.SearchStatus.BACKEND_BROKEN,
        "detail": "DuckDuckGo returned HTTP 202 (202 means its anti-bot challenge)"})
    out = ws.search("who is mahesh", count=5)
    assert out["status"] == ws.SearchStatus.BACKEND_BROKEN
    assert out["results"] == []
    d = out["detail"]
    assert "HTTP 202" in d                                   # the real error
    assert "Firecrawl" in d and "no API key" in d            # what was not tried, and why
    assert "FIRECRAWL_API_KEY" in d                          # where the key goes
    assert "not a tool" in d                                 # the lie it forbids
    assert out["firecrawl"] == {"configured": False, "how": ws.FIRECRAWL_KEY_HOWTO}


def test_the_search_web_tool_description_names_firecrawl():
    from agent_friday.services.agent import CLAUDE_TOOLS
    spec = next(t for t in CLAUDE_TOOLS if t.get("name") == "search_web")
    desc = spec["description"]
    assert "Firecrawl" in desc and "FIRECRAWL_API_KEY" in desc
    assert "never say it is not wired up" in desc
