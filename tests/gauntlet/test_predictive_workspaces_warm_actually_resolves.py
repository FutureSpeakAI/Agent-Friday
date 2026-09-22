"""Gauntlet finding F58 (claim-corpus sweep, 2026-09-04):
predictive_workspaces.py's module docstring promises boot/hourly
pre-warming that "proactively touch[es] the caches a predicted workspace
depends on... so it renders instantly." _warm_workspace() resolved its
target function via `_resolve_warmer()`, which looked candidate names up
in `globals()` -- THIS module's own namespace. Nothing ever imported the
real target functions into that namespace, so every lookup returned None
and every single warm attempt silently returned False, forever, for
every workspace, since the function was written.

This probe proves each branch resolves a real, importable warmer
(messages, wiki, contacts/trust), and that calendar, news and code warm the
stale-while-revalidate cache their routes read: a warmed workspace's next
read makes no live call.
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

    def test_news_warm_builds_the_clusters_the_route_serves(self, monkeypatch):
        import agent_friday.routes.news as news
        from agent_friday.services import swr_cache
        calls = []
        monkeypatch.setattr(news, "_compute_news_clusters",
                            lambda: calls.append(1) or [{"title": "t"}])

        assert pw._warm_workspace("news") is True
        assert calls == [1]
        assert swr_cache.peek("news.clusters")[0] == [{"title": "t"}]

    def test_calendar_warm_fills_the_cache_the_route_reads(self, monkeypatch):
        import agent_friday.services.calendar_engine as ce
        live = []
        monkeypatch.setattr(ce, "_fetch_calendar_range_live",
                            lambda start, end: live.append(start) or [])

        assert pw._warm_workspace("calendar") is True
        assert len(live) == 8                      # today + the next seven days
        ce._events_for_day(__import__("datetime").date.today())
        assert len(live) == 8                      # served from the warmed cache

    def test_code_warm_runs_the_repo_sweep_once(self, monkeypatch, tmp_path):
        import agent_friday.routes.code as code
        import agent_friday.services.code_engine as code_engine
        calls = []
        monkeypatch.setattr(code_engine, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(code, "_scan_repos", lambda root: calls.append(root) or [])

        assert pw._warm_workspace("code") is True
        assert pw._warm_workspace("code") is True
        assert calls == [tmp_path]

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
