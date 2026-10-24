"""Shared by the test files that start a Tier B knowledge-graph index.

An index a test started on the background thread (the route's default) must
finish before the next test: otherwise that test's own reindex meets "already
running" (409) whenever the earlier thread is slow. The nightly pass
(knowledge_graph.integration) sets the same running flag on whatever thread
calls it, so the flag itself is waited on, not only the route's own thread.
"""
import sys
import threading
import time

import pytest


def settle():
    for t in [t for t in threading.enumerate() if t.name == "kg-tier-b-index"]:
        t.join(timeout=120)
    mod = sys.modules.get("agent_friday.routes.knowledge_graph")
    if mod is None:
        return
    deadline = time.monotonic() + 120
    while mod._TIER_B_STATE.get("running") and time.monotonic() < deadline:
        time.sleep(0.05)
    mod._TIER_B_STATE["running"] = False


@pytest.fixture(autouse=True)
def _no_tier_b_index_outlives_its_test():
    settle()
    yield
    settle()
