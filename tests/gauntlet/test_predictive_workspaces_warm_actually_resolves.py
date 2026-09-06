"""Gauntlet finding F58 (claim-corpus sweep, 2026-09-04):
predictive_workspaces.py's module docstring promises boot/hourly
pre-warming that "proactively touch[es] the caches a predicted workspace
depends on... so it renders instantly." _warm_workspace() resolved its
target function via `_resolve_warmer()`, which looked candidate names up
in `globals()` -- THIS module's own namespace. Nothing ever imported the
real target functions into that namespace, so every lookup returned None
and every single warm attempt silently returned False, forever, for
every workspace, since the function was written.

This probe proves the fix for the three branches with a real, importable
warmer (messages, wiki, contacts/trust), and proves "news" and "calendar"
are now honest, documented no-ops rather than dead references to
functions (_load_front_page, _get_cached_news, _collect_calendar_events,
_get_calendar_events) that do not exist anywhere in the codebase.
"""
from __future__ import annotations

import agent_friday.services.predictive_workspaces as pw


class TestWarmWorkspaceActuallyResolvesRealFunctions:
    def test_messages_warm_calls_the_real_cache_trigger_and_reports_true(self, monkeypatch):
        calls = []
        import agent_friday.services.notifications as notifications
        monkeypatch.setattr(notifications, "_trigger_message_cache",
                             lambda: calls.append("called"))

        result = pw._warm_workspace("messages")

        assert result is True, (
            "messages warm reported False even though a real warmer function "
            "exists and is importable -- the resolver is still broken (F58 regressed)"
        )
        assert calls == ["called"]

    def test_wiki_warm_calls_the_real_index_generator_and_reports_true(self, monkeypatch):
        calls = []
        import agent_friday.services.model_router as model_router
        monkeypatch.setattr(model_router, "_generate_wiki_indexes",
                             lambda: calls.append("called"))

        result = pw._warm_workspace("wiki")

        assert result is True
        assert calls == ["called"]

    def test_contacts_warm_calls_the_real_trust_graph_loader_and_reports_true(self, monkeypatch):
        calls = []
        import agent_friday.services.misc_engine as misc_engine
        monkeypatch.setattr(misc_engine, "_load_trust_graph",
                             lambda: calls.append("called"))

        result = pw._warm_workspace("contacts")

        assert result is True
        assert calls == ["called"]

    def test_news_warm_is_an_honest_no_op_not_a_dead_reference(self):
        # No cache exists for news to warm (routes/news.py reads the on-disk
        # front-page cache directly); this must report False, not crash, and
        # must not reference a function that doesn't exist anywhere.
        assert pw._warm_workspace("news") is False

    def test_calendar_warm_is_an_honest_no_op_not_a_dead_reference(self):
        # The calendar workspace has no cache either -- _events_for_day()
        # calls Google's API live on every read.
        assert pw._warm_workspace("calendar") is False

    def test_resolve_warmer_returns_none_for_a_genuinely_missing_function(self):
        fn = pw._resolve_warmer(
            ("agent_friday.services.notifications", "_this_function_does_not_exist"),
        )
        assert fn is None

    def test_resolve_warmer_survives_a_bad_module_path(self):
        fn = pw._resolve_warmer(
            ("agent_friday.services.not_a_real_module", "whatever"),
        )
        assert fn is None
