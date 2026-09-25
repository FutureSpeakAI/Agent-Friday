"""Every search backend hands back the same row shape.

Backends each use their own field name for the same string. Brave, DuckDuckGo
and Firecrawl say `snippet`; wigolo, at the front of the chain, says
`description`. A renderer that reads a hard `r['snippet']` turns every wigolo
search into `KeyError: 'snippet'`, and the model is handed
"Tool error (search_web): 'snippet'" -- search is dead on the one backend that
cannot fail for a billing reason.

A backend contract that only holds for the backends someone happened to write
a fixture for is not a contract, so these assert measured shapes for every
backend rather than a `snippet`-shaped fixture.
"""
from __future__ import annotations

import pytest

from agent_friday.services import web_search as W

CONTRACT = ("title", "url", "snippet")


def test_a_wigolo_shaped_row_comes_out_with_a_snippet():
    """THE CRUX. `description` in, `snippet` out."""
    rows = W._normalise_rows([
        {"title": "T", "url": "https://example.com", "description": "D"}])
    assert rows == [{"title": "T", "url": "https://example.com", "snippet": "D"}]


def test_a_snippet_shaped_row_is_unchanged():
    rows = W._normalise_rows([
        {"title": "T", "url": "https://example.com", "snippet": "S"}])
    assert rows[0]["snippet"] == "S"


@pytest.mark.parametrize("row", [
    {"title": "T", "url": "https://e.com", "snippet": "S"},       # brave, ddg
    {"title": "T", "url": "https://e.com", "description": "S"},   # wigolo
    {"title": "T", "url": "https://e.com", "abstract": "S"},      # hypothetical
    {"title": "T", "url": "https://e.com", "text": "S"},          # hypothetical
    {"url": "https://e.com"},                                     # nothing but a link
])
def test_every_shape_satisfies_the_contract(row):
    """The point is the CLASS of bug, not the one field name. A fifth backend
    with a sixth word for the same string must cost nothing."""
    out = W._normalise_rows([row])
    assert len(out) == 1
    for k in CONTRACT:
        assert k in out[0], "%s missing from a normalised row" % k
        assert isinstance(out[0][k], str)


def test_a_row_with_no_url_is_dropped():
    """The stated contract of this module is a real fetchable href. A row
    without one is not a result, it is a thing to click that goes nowhere."""
    assert W._normalise_rows([{"title": "T", "snippet": "S"}]) == []
    assert W._normalise_rows([{"title": "T", "url": "  ", "snippet": "S"}]) == []


def test_junk_in_the_list_does_not_take_the_search_down():
    rows = W._normalise_rows(
        [None, "nope", 42, {"url": "https://e.com", "description": "ok"}])
    assert len(rows) == 1 and rows[0]["snippet"] == "ok"


def test_a_missing_title_falls_back_to_the_url():
    rows = W._normalise_rows([{"url": "https://e.com", "description": "d"}])
    assert rows[0]["title"] == "https://e.com"


def test_the_search_tool_renders_a_wigolo_row_without_erroring(monkeypatch):
    """END TO END through the function the model actually calls.

    The unit above proves the normaliser; this proves the renderer is fed by
    it - which is where the KeyError lands.
    """
    from agent_friday.services import agent as A

    monkeypatch.setattr(W, "_wigolo_ready", lambda: True)
    monkeypatch.setattr(W, "_wigolo_search", lambda q, n: {
        "status": W.SearchStatus.OK,
        # The row shape wigolo returns.
        "results": [{"title": "Claude", "url": "https://anthropic.com",
                     "description": "A model."}],
        "detail": "local, keyless",
    })
    out = A._tool_search_web({"query": "anything", "count": 3})
    assert "Tool error" not in str(out)
    assert "A model." in str(out)
    assert "https://anthropic.com" in str(out)


def test_the_renderer_survives_a_row_that_slipped_past_the_normaliser(monkeypatch):
    """Belt and braces. The normaliser is the fix; this asserts the renderer
    degrades to a blank line rather than taking the whole tool down if some
    future path ever hands it an unnormalised row."""
    from agent_friday.services import agent as A

    # `web_search` is imported inside the tool function, so the patch has to
    # land on the module rather than on a name bound in agent's namespace.
    monkeypatch.setattr(W, "search", lambda q, count=10: {
        "status": W.SearchStatus.OK, "backend": "test", "detail": "",
        "results": [{"url": "https://e.com"}],          # no title, no snippet
    })
    out = A._tool_search_web({"query": "anything", "count": 1})
    assert "Tool error" not in str(out)
    assert "https://e.com" in str(out)
