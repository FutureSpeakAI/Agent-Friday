"""Tests for services/warm_cache.py.

The point of this module is that a slow read stops being felt. So the tests
are mostly about what happens when the underlying function is slow, broken, or
has never run - not about the happy path, which is trivial.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import warm_cache as wc


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(wc, "_ENTRIES", {})
    monkeypatch.setattr(wc, "_STARTED", False)
    monkeypatch.setattr(wc, "WARM_STAGGER_S", 0.01)
    yield


def test_a_cold_cache_does_not_block_by_default():
    """The default answer is 'nothing yet', instantly - not a wait."""
    wc.register("slow", lambda: time.sleep(5) or "late", ttl_s=60)
    t0 = time.time()
    out = wc.get("slow")
    assert time.time() - t0 < 0.5
    assert out["ready"] is False
    assert out["value"] is None


def test_compute_if_cold_pays_once_so_the_first_caller_gets_a_real_answer():
    calls = []
    wc.register("x", lambda: calls.append(1) or "built", ttl_s=60)
    assert wc.get("x", compute_if_cold=True)["value"] == "built"
    assert wc.get("x", compute_if_cold=True)["value"] == "built"
    assert len(calls) == 1


def test_it_survives_a_restart(tmp_path):
    """The property that makes a restart instant instead of costing 19 s."""
    wc.register("cat", lambda: {"models": [1, 2, 3]}, ttl_s=600)
    wc.refresh("cat", blocking=True)
    assert (tmp_path / "cat.json").exists()

    # A brand-new process: registry wiped, same disk.
    wc._ENTRIES.clear()
    calls = []
    wc.register("cat", lambda: calls.append(1) or {"models": []}, ttl_s=600)
    out = wc.get("cat")
    assert out["ready"] is True
    assert out["value"] == {"models": [1, 2, 3]}
    assert calls == [], "it recomputed instead of reading the disk copy"


def test_a_failing_refresh_never_clobbers_a_good_value():
    """Stale-while-revalidate: a bad refresh is less bad than no answer."""
    state = {"ok": True}

    def fn():
        if not state["ok"]:
            raise RuntimeError("provider down")
        return "good"

    wc.register("p", fn, ttl_s=60)
    wc.refresh("p", blocking=True)
    state["ok"] = False
    assert wc.refresh("p", blocking=True) is False
    out = wc.get("p")
    assert out["value"] == "good"
    assert "provider down" in out["error"]


def test_staleness_is_reported_not_hidden():
    """A cache that silently serves old data is worse than a slow endpoint.

    Read through the entry rather than get(), because get() on a stale value
    kicks a background refresh - and with a trivial fn that refresh finishes
    before the assertion runs, so the first version of this test raced itself
    and reported fresh. The refresh-on-read behaviour has its own test below.
    """
    wc.register("s", lambda: "v", ttl_s=0.05)
    wc.refresh("s", blocking=True)
    assert wc._ENTRIES["s"].is_stale is False
    time.sleep(0.08)
    assert wc._ENTRIES["s"].is_stale is True
    out = wc.get("s")
    assert out["value"] == "v"              # served regardless of staleness


def test_a_stale_read_triggers_a_background_refresh():
    calls = []
    wc.register("r", lambda: calls.append(1) or len(calls), ttl_s=0.05)
    wc.refresh("r", blocking=True)
    time.sleep(0.08)
    wc.get("r")                              # stale -> kicks a thread
    deadline = time.time() + 3
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(calls) >= 2


def test_start_warming_does_not_block_the_caller():
    """The literal ask: background, so it cannot stop the UI launching."""
    wc.register("slow", lambda: time.sleep(2) or "done", ttl_s=60)
    t0 = time.time()
    wc.start_warming()
    assert time.time() - t0 < 0.5


def test_start_warming_actually_warms():
    wc.register("w", lambda: "warmed", ttl_s=60)
    wc.start_warming()
    deadline = time.time() + 5
    while not wc.get("w")["ready"] and time.time() < deadline:
        time.sleep(0.05)
    assert wc.get("w")["value"] == "warmed"


def test_warming_skips_anything_already_fresh_from_disk(tmp_path):
    """Otherwise every restart re-pays the cost the disk copy just avoided."""
    (tmp_path / "d.json").write_text(
        json.dumps({"value": "from-disk", "fetched_at": time.time()}),
        encoding="utf-8")
    calls = []
    wc.register("d", lambda: calls.append(1) or "rebuilt", ttl_s=600)
    wc.start_warming()
    time.sleep(0.3)
    assert wc.get("d")["value"] == "from-disk"
    assert calls == []


def test_an_unknown_name_is_answered_not_raised():
    out = wc.get("never-registered")
    assert out["ready"] is False
    assert "no such cache" in out["error"]


def test_status_reports_every_entry():
    wc.register("a", lambda: 1, ttl_s=60)
    wc.register("b", lambda: 2, ttl_s=60)
    wc.refresh("a", blocking=True)
    s = wc.status()
    assert s["a"]["ready"] is True and s["b"]["ready"] is False
    assert sorted(wc.registered()) == ["a", "b"]


def test_register_is_idempotent_and_does_not_lose_the_value():
    wc.register("k", lambda: "first", ttl_s=60)
    wc.refresh("k", blocking=True)
    wc.register("k", lambda: "second", ttl_s=99)
    assert wc.get("k")["value"] == "first"   # not wiped by re-registration
    assert wc._ENTRIES["k"].ttl_s == 99      # but the new fn/ttl took effect
