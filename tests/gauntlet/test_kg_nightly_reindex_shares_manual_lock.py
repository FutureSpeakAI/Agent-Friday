"""Gauntlet finding Q24 (part B): the scheduler's own nightly
'knowledge_graph_reindex' builtin task called
``wiki_graph.rebuild_tier_a()`` / ``indexer.reindex_tier_b()`` DIRECTLY,
acquiring neither the ``_rebuild_lock`` nor checking ``_TIER_B_STATE`` that
``routes/knowledge_graph.py``'s manual ``/api/knowledge-graph/reindex``
route uses -- so a user clicking "Reindex now" in the Knowledge Graph panel
while the 03:30 nightly schedule (or its own earlier Run Now) was mid-run
got two fully concurrent, uncoordinated rebuilds of the same on-disk KG
store.

This probe must be RED before the fix (``run_nightly_reindex()`` never
touches ``_rebuild_lock``/``_TIER_B_STATE`` at all -- Tier A runs without
the lock held, and Tier B runs a second time even while the manual route's
own Tier B run is already in flight) and GREEN after (it goes through the
exact same guard objects the manual route uses).
"""
from __future__ import annotations

import threading
import time

import pytest

import agent_friday.services.knowledge_graph.indexer as idx
import agent_friday.services.knowledge_graph.wiki_graph as wg
from agent_friday.routes import knowledge_graph as kgr
from agent_friday.services.knowledge_graph import integration as kgi


@pytest.fixture(autouse=True)
def _kg_nightly_enabled(monkeypatch):
    monkeypatch.setattr(kgi, "kg_settings",
                        lambda: {"enabled": True, "nightly_reindex": True})
    monkeypatch.setattr(kgi, "mark_wiki_dirty", lambda *a, **k: None)
    yield


@pytest.fixture(autouse=True)
def _restore_tier_b_state():
    prev = dict(kgr._TIER_B_STATE)
    yield
    kgr._TIER_B_STATE.clear()
    kgr._TIER_B_STATE.update(prev)


class TestNightlyReindexSharesManualLock:
    def test_tier_a_rebuild_holds_the_same_lock_the_manual_route_uses(
            self, monkeypatch):
        held_during_call = {}

        def _fake_rebuild_tier_a(*a, **k):
            held_during_call["locked"] = kgr._rebuild_lock.locked()
            return {"ok": True}

        monkeypatch.setattr(wg, "rebuild_tier_a", _fake_rebuild_tier_a)
        monkeypatch.setattr(idx, "reindex_tier_b", lambda *a, **k: {"n": 0})

        result = kgi.run_nightly_reindex()

        assert held_during_call.get("locked") is True, (
            "run_nightly_reindex() called wiki_graph.rebuild_tier_a() "
            "without holding routes/knowledge_graph.py's _rebuild_lock -- a "
            "concurrent manual Tier A reindex (which DOES hold that lock) "
            "can race with it"
        )
        assert not kgr._rebuild_lock.locked(), "the lock must be released after"
        assert result["tier_a"] == {"ok": True}

    def test_tier_a_rebuild_actually_serializes_against_a_concurrent_holder(
            self, monkeypatch):
        """Stronger than checking .locked() from inside the call: prove a
        concurrent holder of the SAME lock the manual route uses genuinely
        blocks the nightly job's Tier A rebuild until released."""
        order = []

        def _fake_rebuild_tier_a(*a, **k):
            order.append("tier_a_ran")
            return {"ok": True}

        monkeypatch.setattr(wg, "rebuild_tier_a", _fake_rebuild_tier_a)
        monkeypatch.setattr(idx, "reindex_tier_b", lambda *a, **k: {"n": 0})

        release = threading.Event()

        def _hold_lock_like_manual_route():
            with kgr._rebuild_lock:
                order.append("manual_holder_acquired")
                release.wait(timeout=5)
            order.append("manual_holder_released")

        holder = threading.Thread(target=_hold_lock_like_manual_route)
        holder.start()
        # Give the holder thread a moment to actually acquire the lock
        # before the nightly job tries.
        for _ in range(50):
            if kgr._rebuild_lock.locked():
                break
            time.sleep(0.02)
        assert kgr._rebuild_lock.locked()

        def _run_nightly():
            kgi.run_nightly_reindex()

        nightly_thread = threading.Thread(target=_run_nightly)
        nightly_thread.start()
        time.sleep(0.2)   # nightly should still be blocked on the lock
        assert "tier_a_ran" not in order, (
            "the nightly job's Tier A rebuild ran WHILE a concurrent holder "
            "of the same _rebuild_lock the manual route uses was still "
            "active -- it never actually serialized against that lock"
        )

        release.set()
        holder.join(timeout=5)
        nightly_thread.join(timeout=5)
        assert order.index("manual_holder_released") < order.index("tier_a_ran"), (
            "the nightly Tier A rebuild started before the concurrent "
            "manual-route-style lock holder released it"
        )

    def test_tier_b_skips_when_manual_route_already_has_one_running(
            self, monkeypatch):
        monkeypatch.setattr(wg, "rebuild_tier_a", lambda *a, **k: {})
        called = {"n": 0}

        def _fake_tier_b(*a, **k):
            called["n"] += 1
            return {"entities": 1}

        monkeypatch.setattr(idx, "reindex_tier_b", _fake_tier_b)

        # Simulate the manual route's Tier B run already in flight -- the
        # SAME state dict routes/knowledge_graph.py's manual reindex route
        # sets running=True on.
        kgr._TIER_B_STATE["running"] = True

        result = kgi.run_nightly_reindex()

        assert called["n"] == 0, (
            "the nightly job ran a second, fully concurrent Tier B reindex "
            "while the manual route's own Tier B run was already in "
            "progress -- exactly the uncoordinated concurrent rebuild Q24 "
            "describes"
        )
        assert result["tier_b"] == {"skipped": "tier_b_already_running"}

    def test_tier_b_runs_normally_and_updates_shared_state_when_idle(
            self, monkeypatch):
        monkeypatch.setattr(wg, "rebuild_tier_a", lambda *a, **k: {})
        monkeypatch.setattr(idx, "reindex_tier_b",
                            lambda *a, **k: {"entities": 3})
        kgr._TIER_B_STATE.clear()
        kgr._TIER_B_STATE.update(
            {"running": False, "last": None, "started_at": None})

        result = kgi.run_nightly_reindex()

        assert result["tier_b"] == {"entities": 3}
        assert kgr._TIER_B_STATE["running"] is False, (
            "the nightly job must reset the SAME running flag it set, so "
            "it doesn't permanently wedge the manual route's own guard"
        )
        assert kgr._TIER_B_STATE["last"] == {"entities": 3}

    def test_disabled_setting_skips_without_touching_either_guard(
            self, monkeypatch):
        monkeypatch.setattr(kgi, "kg_settings",
                            lambda: {"enabled": True, "nightly_reindex": False})
        was_locked = {"v": None}

        def _fake_rebuild_tier_a(*a, **k):
            was_locked["v"] = True
            return {}

        monkeypatch.setattr(wg, "rebuild_tier_a", _fake_rebuild_tier_a)
        result = kgi.run_nightly_reindex()
        assert result == {"skipped": True}
        assert was_locked["v"] is None
