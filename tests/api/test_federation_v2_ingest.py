"""POST /api/federation/v2/events: signed peer events are authenticated and recorded as
evidence, and nothing more (Federation / Economy spec, roadmap F6 start).

The route is held by the invited_jobs switch and the kill switch (F2), trusts only peers the
owner has set to 'allow', answers with the spec's dispositions (202 recorded, 200 a known
retry, 409 equivocation, 413 oversized, 422 malformed/unsupported/untrusted, 429 over budget),
and never raises an approval card, moves credits or runs a job: it acknowledges bytes. The
bundle's public wire vectors drive it. Offline; fictitious public test keys only."""
import json
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import federation as fed

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "wire-v2.json").read_text(encoding="utf-8"))
KEYS = sorted(set(VECTORS["peer_keys"].values()))
URL = "/api/federation/v2/events"
ALLOW = {"federation": True, "fed_invited_jobs": True}


@pytest.fixture
def client():
    import agent_friday.server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from agent_friday.routes import federation as routes
    from agent_friday.services import approvals, peer_wire_v2
    monkeypatch.setattr(routes, "_V2_STORE", peer_wire_v2.PeerEventStore(tmp_path / "v2.db"))
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": dict(ALLOW)})
    monkeypatch.setattr(fed, "get_peers", lambda: [{"agent_id": k, "fed_pref": "allow"} for k in KEYS])


def _raw(case):
    from agent_friday.services import peer_wire_v2 as wire
    return bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else wire.canonical(case["envelope"])


def _post(client, raw):
    return client.post(URL, data=raw, content_type="application/json")


def test_the_route_exists_and_the_first_signed_event_is_recorded(client):
    r = _post(client, _raw(VECTORS["cases"][0]))
    assert r.status_code == 202, r.get_data(as_text=True)
    body = r.get_json()
    assert body["disposition"] == "enqueued" and body["event_id"] == VECTORS["cases"][0]["event_id"]


def test_a_retry_is_200_and_every_vector_gets_the_spec_status(client):
    expected = {"verified_bytes": 202, "replay": 200, "equivocation": 409}
    for case in VECTORS["cases"]:
        r = _post(client, _raw(case))
        want = expected.get(case["expect"], 422)
        assert r.status_code == want, (case["name"], case["expect"], r.status_code, r.get_json())
        if want == 422:
            assert r.get_json()["error"] == case["expect"], case["name"]


def test_only_peers_the_owner_allowed_are_trusted(client, monkeypatch):
    monkeypatch.setattr(fed, "get_peers", lambda: [{"agent_id": k, "fed_pref": "ask"} for k in KEYS]
                        + [{"agent_id": KEYS[0], "fed_pref": "block"}])
    r = _post(client, _raw(VECTORS["cases"][0]))
    assert r.status_code == 422 and r.get_json()["error"] == "untrusted_key"


def test_it_is_held_until_invited_jobs_is_switched_on_and_the_kill_switch_stops_it(client, monkeypatch):
    raw = _raw(VECTORS["cases"][0])
    for held in ({"federation": True}, {"federation": False, "fed_invited_jobs": True}):
        monkeypatch.setattr(core, "_load_settings", lambda h=held: {"held_features": dict(h)})
        r = _post(client, raw)
        assert r.status_code == 404 and r.get_json()["error"] == "not_enabled"
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"held_features": dict(ALLOW, fed_kill_switch=True)})
    r = _post(client, raw)
    assert r.status_code == 404 and r.get_json()["feature"] == "fed_kill_switch"


def test_oversized_bodies_are_413_before_parsing(client):
    assert _post(client, b"x" * 65537).status_code == 413


def test_over_budget_is_429(client, monkeypatch):
    from agent_friday.services import federation_transport as transport
    monkeypatch.setattr(transport, "check_rate_limit", lambda key, *a, **k: False)
    assert _post(client, _raw(VECTORS["cases"][0])).status_code == 429


def test_recording_an_event_authorizes_and_runs_nothing(client):
    from agent_friday.services import approvals
    for case in VECTORS["cases"]:
        _post(client, _raw(case))
    assert approvals.list_approvals(status="pending") == [], "an event raised an approval card"
    assert approvals.list_approvals(status="approved") == []
