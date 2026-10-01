"""The intelligence card is computed once for every caller polling at the same time.

The page polls /api/intelligence once a minute from every open tab, and the card
takes seconds to build (catalogue, cost ledger, residency). Callers that arrive
while a build runs share it; a card is reused for a few seconds; a settings save
drops it so a pick shows on the next read. A caller never waits without bound.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def slow_card(monkeypatch):
    """A card whose catalogue step is slow and counted; residency and ledgers are stubbed."""
    from agent_friday.services import model_catalog
    from agent_friday.services import machine_probe
    from agent_friday.routes import intelligence

    builds = []
    real = model_catalog.build_catalog

    def counted_build(*a, **k):
        builds.append(time.time())
        time.sleep(0.4)
        return real(*a, **k)

    monkeypatch.setattr(model_catalog, "build_catalog", counted_build)
    monkeypatch.setattr(machine_probe, "snapshot", lambda *a, **k: (None, 0.0, "unknown"))
    monkeypatch.setattr(intelligence, "_ollama_sizes", lambda: {})
    if hasattr(intelligence, "_reset_card_for_tests"):
        intelligence._reset_card_for_tests()
    yield builds
    if hasattr(intelligence, "_reset_card_for_tests"):
        intelligence._reset_card_for_tests()


def _poll_concurrently(app, n):
    results, errors = [], []
    start = threading.Barrier(n)

    def one():
        try:
            with app.test_client() as c:
                start.wait()
                r = c.get("/api/intelligence")
                results.append((r.status_code, (r.get_json() or {}).get("status")))
        except Exception as e:      # surfaced below
            errors.append(e)
    threads = [threading.Thread(target=one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    return results


def test_concurrent_polls_share_one_build(app, slow_card):
    results = _poll_concurrently(app, 6)
    assert results == [(200, "ok")] * 6
    assert len(slow_card) == 1, f"{len(slow_card)} builds for 6 concurrent polls"


def test_a_poll_just_after_a_build_reuses_it(client, slow_card):
    assert client.get("/api/intelligence").status_code == 200
    assert client.get("/api/intelligence").status_code == 200
    assert len(slow_card) == 1


def test_a_settings_save_drops_the_card(client, slow_card):
    assert client.get("/api/intelligence").status_code == 200
    r = client.post("/api/settings", json={"settings": {"capability_routing": {"subagent": {"model": "claude-opus-5-5"}}}})
    assert r.status_code == 200
    client.get("/api/intelligence")
    assert len(slow_card) == 2, "a settings save must not be hidden behind a cached card"


def test_repeated_polls_leave_file_handles_flat(client, slow_card):
    psutil = pytest.importorskip("psutil")
    me = psutil.Process()
    for _ in range(3):
        client.get("/api/intelligence")
    time.sleep(1)
    before = me.num_handles() if hasattr(me, "num_handles") else me.num_fds()
    for _ in range(20):
        client.get("/api/intelligence")
    time.sleep(1)
    after = me.num_handles() if hasattr(me, "num_handles") else me.num_fds()
    assert after - before <= 5, f"handles grew by {after - before} over 20 polls"
