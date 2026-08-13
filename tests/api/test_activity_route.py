"""API tests for GET /api/activity (B4 — global activity ledger route)."""

import pytest

from agent_friday.services import activity_ledger as al


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """Per-test ledger file so concurrently-running suites can't interleave."""
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity_ledger.jsonl")
    yield


def _seed():
    al.record("model_invocation", model="gemma4:latest", provider="ollama",
              seat="local", duration_ms=100, tokens_in=10, tokens_out=5,
              orb_id="local-1111", task_id="t-local")
    al.record("model_invocation", model="claude-sonnet-5", provider="anthropic",
              seat="cloud", duration_ms=2500, tokens_in=900, tokens_out=300,
              orb_id="agent-2222", task_id="t-cloud")
    al.record("tool_call", tool="search_web", ok=True, duration_ms=340,
              orb_id="agent-2222", task_id="t-cloud")
    al.record("subagent_spawn", task_id="t-spawn", description="deep research",
              model="claude-sonnet-5")


def test_activity_route_returns_events_newest_first(client):
    _seed()
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["count"] == 4
    kinds = [e["kind"] for e in data["events"]]
    assert kinds == ["subagent_spawn", "tool_call",
                     "model_invocation", "model_invocation"]


def test_activity_route_kind_filter(client):
    _seed()
    data = client.get("/api/activity?kind=model_invocation").get_json()
    assert data["count"] == 2
    assert all(e["kind"] == "model_invocation" for e in data["events"])


def test_activity_route_model_filter(client):
    _seed()
    data = client.get("/api/activity?model=gemma4:latest").get_json()
    assert data["count"] == 1
    assert data["events"][0]["seat"] == "local"


def test_activity_route_task_id_filter(client):
    _seed()
    data = client.get("/api/activity?task_id=t-cloud").get_json()
    assert data["count"] == 2
    assert {e["kind"] for e in data["events"]} == {"model_invocation", "tool_call"}


def test_activity_route_limit_and_since(client):
    _seed()
    data = client.get("/api/activity?limit=1").get_json()
    assert data["count"] == 1
    assert data["events"][0]["kind"] == "subagent_spawn"
    # since far in the future → nothing
    data = client.get("/api/activity?since=99999999999").get_json()
    assert data["count"] == 0
    assert data["events"] == []


def test_activity_route_bad_params_dont_500(client):
    _seed()
    data = client.get("/api/activity?limit=bogus&since=alsobogus").get_json()
    assert data["status"] == "ok"
    assert data["count"] == 4


def test_activity_route_empty_ledger(client):
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"status": "ok", "events": [], "count": 0}
