"""Every voice tool answers in conversational time, or says it could not.

An article deep-dive on a live call took one to two minutes, and its answer
landed after the conversation had moved on, so Friday answered the old question
as if it were the new one. The spoken deep-dive is now short and time-limited,
every voice tool has a hard limit, and a late result is labelled as late.
"""
import asyncio
import inspect
import json
import time

import pytest

import agent_friday.routes.voice as rv
import agent_friday.services.news_engine as ne
import agent_friday.services.voice_engine as ve

BODY = ("The city council approved the new transit plan on Tuesday. "
        "It adds three bus lines. Work starts in spring. Critics say it is late. "
        "Supporters call it overdue. ") * 30


@pytest.fixture
def article(monkeypatch, tmp_path):
    monkeypatch.setattr(ne, "DEEP_DIVE_DIR", tmp_path)
    monkeypatch.setattr(ne, "_extract_article_text", lambda url: ("Transit plan", BODY))
    monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda **k: "SYSTEM")
    monkeypatch.setattr(ne, "_predict_route_provider", lambda **k: "local")
    monkeypatch.setattr(ne, "_gated_vault_control", lambda: None)
    seen = {}

    def gen(messages, system=None, max_tokens=None, **k):
        seen["max_tokens"] = max_tokens
        time.sleep(seen.get("delay", 0))
        return json.dumps({"summary": "Council approved a transit plan.",
                           "implications": "Your commute may change.",
                           "key_quotes": ["overdue"]})
    monkeypatch.setattr(ne, "_generate_text", gen)
    return seen


def test_the_spoken_deep_dive_is_short(article):
    out = json.loads(ve._tool_get_article_deep_dive({"url": "https://example.test/a"}))
    assert out["summary"] == "Council approved a transit plan."
    assert article["max_tokens"] <= 500


def test_a_slow_deep_dive_answers_with_the_articles_opening(article, monkeypatch):
    monkeypatch.setattr(ne, "DEEP_DIVE_QUICK_BUDGET_S", 0.3, raising=False)
    article["delay"] = 1.5
    t0 = time.time()
    out = json.loads(ve._tool_get_article_deep_dive({"url": "https://example.test/b"}))
    assert time.time() - t0 < 1.2
    assert out["partial"] is True
    assert out["summary"].startswith("The city council approved the new transit plan")
    # The full answer still lands, for the next ask.
    time.sleep(1.6)
    again = json.loads(ve._tool_get_article_deep_dive({"url": "https://example.test/b"}))
    assert again["summary"] == "Council approved a transit plan."


def test_the_news_page_never_shows_the_short_read(article):
    ve._tool_get_article_deep_dive({"url": "https://example.test/c"})
    article["max_tokens"] = None
    result, status = ne._deep_dive_article("https://example.test/c")
    assert status == 200 and not result.get("quick")
    assert article["max_tokens"] == 2000


def test_every_voice_tool_has_a_hard_limit():
    def stuck(name, args, send, session=None):
        time.sleep(2)
        return "too late"
    async def call():
        # Timed inside the loop: asyncio.run's own shutdown waits for the
        # abandoned worker thread, which the live bridge's loop never does.
        t0 = time.time()
        out = await rv._voice_tool_with_limit("get_article_deep_dive", {}, None,
                                              limit=0.3, runner=stuck)
        return out, time.time() - t0
    out, took = asyncio.run(call())
    assert took < 1.0
    assert "did not finish" in out and "too late" not in out


def test_the_live_bridge_runs_tools_through_the_limit():
    src = inspect.getsource(rv)
    assert "await _voice_tool_with_limit(" in src
    assert "_voice_tool_run, fname, fargs, _safe_send)" not in src
    assert "_mark_if_stale(result, fname" in src
    assert rv.VOICE_TOOL_HARD_LIMIT_S <= 25


def test_a_late_result_is_labelled():
    t0 = 1000.0
    # Quick, and nobody spoke: untouched.
    assert rv._mark_if_stale("R", "search_news", t0, 0.0, t0 + 2) == "R"
    # The user said "ok" while a fast tool ran: still untouched.
    assert rv._mark_if_stale("R", "search_news", t0, t0 + 3, t0 + 4) == "R"
    # Slow, and the user has moved on.
    late = rv._mark_if_stale("R", "get_article_deep_dive", t0, t0 + 6, t0 + 9)
    assert late.startswith("[LATE RESULT") and late.endswith("R")
    # Very slow, even with no new words.
    assert rv._mark_if_stale("R", "x", t0, 0.0, t0 + 30).startswith("[LATE RESULT")
