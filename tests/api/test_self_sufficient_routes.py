"""API tests for Part A/B/D routes: hooks, schedules, costs."""


# ── Part B: hooks ─────────────────────────────────────────────────────────────
def test_list_hooks(client):
    r = client.get('/api/hooks')
    assert r.status_code == 200
    data = r.get_json()
    names = {h["name"] for h in data["hooks"]}
    # The eight built-ins are registered when services.agent imports.
    assert {"governance_rings", "audit_log", "pii_scrub", "rate_limiter"} <= names
    gov = next(h for h in data["hooks"] if h["name"] == "governance_rings")
    assert gov["critical"] is True


def test_cannot_disable_critical_hook(client):
    r = client.post('/api/hooks/governance_rings', json={"enabled": False})
    assert r.status_code == 400


def test_toggle_noncritical_hook(client):
    r = client.post('/api/hooks/rate_limiter', json={"enabled": False})
    assert r.status_code == 200
    assert r.get_json()["enabled"] is False
    # restore
    client.post('/api/hooks/rate_limiter', json={"enabled": True})


# ── Part A: schedules ─────────────────────────────────────────────────────────
def test_schedules_crud_and_run_now(client):
    # Create
    r = client.post('/api/schedules', json={
        "name": "API test job", "trigger": "interval",
        "spec": {"every_minutes": 120},
        "task": {"kind": "agent_prompt", "prompt": "noop"},
        "enabled": False,
    })
    assert r.status_code == 200
    sid = r.get_json()["schedule"]["id"]

    # List includes it with a computed next_run field.
    r = client.get('/api/schedules')
    assert r.status_code == 200
    rows = {s["id"]: s for s in r.get_json()["schedules"]}
    assert sid in rows
    assert "next_run" in rows[sid]

    # Patch
    r = client.post(f'/api/schedules/{sid}', json={"name": "Renamed"})
    assert r.get_json()["schedule"]["name"] == "Renamed"

    # History (empty but well-formed)
    r = client.get(f'/api/schedules/{sid}/history')
    assert r.status_code == 200
    assert r.get_json()["history"] == []

    # Delete
    r = client.delete(f'/api/schedules/{sid}')
    assert r.status_code == 200


def test_create_schedule_requires_task_kind(client):
    r = client.post('/api/schedules', json={"name": "bad", "trigger": "daily"})
    assert r.status_code == 400


def test_run_now_missing_schedule(client):
    r = client.post('/api/schedules/sch_does_not_exist/run-now')
    assert r.status_code == 404


# ── Part D: costs ─────────────────────────────────────────────────────────────
def test_costs_summary_shape(client):
    r = client.get('/api/costs/summary?range=today')
    assert r.status_code == 200
    summ = r.get_json()["summary"]
    for key in ("total_usd", "total_calls", "by_provider", "by_workspace",
                "by_model", "by_kind"):
        assert key in summ


def test_costs_timeseries_and_scheduled(client):
    assert client.get('/api/costs/timeseries?range=month').status_code == 200
    assert client.get('/api/costs/scheduled?range=month').status_code == 200


def test_costs_budget_roundtrip(client):
    r = client.post('/api/costs/budget', json={"monthly": 42.0, "monthly_enabled": True})
    assert r.status_code == 200
    assert r.get_json()["budget"]["monthly"] == 42.0
    r = client.get('/api/costs/budget')
    assert r.get_json()["budget"]["monthly"] == 42.0
