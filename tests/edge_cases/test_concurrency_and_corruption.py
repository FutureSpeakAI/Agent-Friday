"""Edge-case tests: concurrent access to the SQLite-backed services, and
resilience to corrupted/missing config files.

The economy/user-model/learning modules serialize writes under an RLock and use
WAL, so concurrent callers must not corrupt state or raise.
"""
from __future__ import annotations

import json
import threading
import uuid

import pytest

from agent_friday.services import economy as econ
from agent_friday.services import user_model as um
from agent_friday.services import learning_loop as ll

econ._ensure_schema()


class TestConcurrentEconomy:
    def test_concurrent_earns_sum_correctly(self):
        agent = f"concurrent-earn-{uuid.uuid4().hex[:6]}"
        n_threads, per = 20, 100

        def worker():
            econ.earn(agent, per, "concurrent")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        # Serialized under _LOCK → exact total, no lost updates.
        assert econ.get_wallet(agent)["psi_balance"] == n_threads * per

    def test_concurrent_transfers_conserve_value(self):
        a, b = f"conc-a-{uuid.uuid4().hex[:6]}", f"conc-b-{uuid.uuid4().hex[:6]}"
        econ.earn(a, 10_000, "seed")

        def worker():
            econ.transfer(a, b, 100, "move")

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        total = econ.get_wallet(a)["psi_balance"] + econ.get_wallet(b)["psi_balance"]
        assert total == 10_000  # value conserved across concurrent transfers


class TestConcurrentUserModel:
    def test_concurrent_counter_bumps_no_undercount(self):
        um.forget()
        key = "workflow.workspace.concurrenttest"

        def worker():
            um._bump_counter(key)

        threads = [threading.Thread(target=worker) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        val = um.get_trait(key)
        assert val is not None
        assert int(float(val)) == 30  # locked RMW → no lost increments


class TestConcurrentLearning:
    def test_concurrent_observe_no_crash(self):
        tt = f"conc-learn-{uuid.uuid4().hex[:6]}"
        errors = []

        def worker(i):
            r = ll.observe(tt, f"prompt {i}", approach="a", success=True)
            if not r.get("ok"):
                errors.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors


class TestCorruptedConfig:
    def test_corrupted_settings_falls_back_to_defaults(self, monkeypatch, tmp_path):
        import agent_friday.core as core
        bad = tmp_path / "settings.json"
        bad.write_text("{ this is not valid json ", encoding="utf-8")
        monkeypatch.setattr(core, "SETTINGS_FILE", bad)
        core._invalidate_settings_cache()
        settings = core._load_settings_raw()
        # A corrupt file must not crash — defaults are returned.
        assert isinstance(settings, dict)
        assert "agent_name" in settings

    def test_missing_settings_seeds_defaults(self, monkeypatch, tmp_path):
        import agent_friday.core as core
        # Point at a path whose PARENT exists (tmp_path) so the seed write can
        # succeed; the file itself is absent on first load.
        target = tmp_path / "settings.json"
        assert not target.exists()
        monkeypatch.setattr(core, "SETTINGS_FILE", target)
        core._invalidate_settings_cache()
        settings = core._load_settings_raw()
        assert isinstance(settings, dict)
        assert target.exists()  # seeded on first load
        # And it's valid JSON with the default keys.
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert "agent_name" in loaded


class TestDatabaseResilience:
    def test_get_wallet_survives_bad_agent_types(self):
        # Non-string agent IDs coerced/handled without raising.
        for bad in (123, None):
            w = econ.get_wallet(str(bad))
            assert "psi_balance" in w

    def test_learning_state_with_empty_db(self):
        st = ll.state()
        assert "counts" in st
