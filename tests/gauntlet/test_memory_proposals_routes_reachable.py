"""Gauntlet finding F53 (2026-09-04): services/memory_proposals.py was
fully built and correct -- propose()/pending()/approve()/reject()/state() --
but nothing in the running app could ever call it. No route, no CLI command,
nothing. routes/memory_proposals.py is the manual door its own docstring
says should exist ("propose() is something the user RUNS, and its output is
shown to him before any of it becomes durable").

All offline: no LLM seat is assigned in test settings, so propose() takes
its real "no seat assigned" early-return path rather than calling a model.

Uses a locally-defined `client` fixture rather than tests/api/conftest.py's:
that one is scoped to tests/api/ only, and the standing rule for this audit
is new probes go only in tests/gauntlet/, never editing or importing from an
existing test's conftest (mirrors test_content_pause_kill_switch_route.py's
same accommodation for the same reason).
"""
from __future__ import annotations

import pytest

import agent_friday.server as friday_server


@pytest.fixture
def client():
    friday_server.app.config.update(TESTING=True)
    return friday_server.app.test_client()


class TestMemoryProposalsRoutesAreReachable:
    def test_state_route_responds(self, client):
        resp = client.get("/api/memory/proposals/state")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "pending" in data
        assert "seat_usable" in data
        assert data["scheduled"] is False

    def test_pending_route_responds_with_a_list(self, client):
        resp = client.get("/api/memory/proposals/pending")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["facts"], list)

    def test_propose_route_is_reachable_and_reports_no_seat_rather_than_crashing(self, client):
        # No memory_manager seat is assigned under test settings, so this
        # exercises the real early-return path in propose() -- proving the
        # route is wired, not that a model was actually called.
        resp = client.post("/api/memory/proposals/propose", json={"day": "2026-01-01"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "seat" in data

    def test_approve_route_with_no_ids_is_a_reachable_no_op(self, client):
        resp = client.post("/api/memory/proposals/approve", json={"fact_ids": []})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["approved"] == 0

    def test_reject_route_with_no_ids_is_a_reachable_no_op(self, client):
        resp = client.post("/api/memory/proposals/reject", json={"fact_ids": []})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["rejected"] == 0

    def test_memory_proposals_blueprint_is_actually_registered(self):
        rules = {r.rule for r in friday_server.app.url_map.iter_rules()}
        assert "/api/memory/proposals/state" in rules, (
            "the memory_proposals blueprint did not register -- check it's a "
            "top-level module-level Blueprint in routes/memory_proposals.py "
            "and listed in server.py's ROUTE_MODULES fallback manifest"
        )
