"""Accepting an issuer is a press, withdrawing is not, and an event for an unaccepted issuer is declined
before its content is read (NS-40.4-1, design A-5, A-6, EC-1, PR 4). Test credits only."""
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import agent_friday.core as core
from agent_friday.services import accepted_issuers as acc
from agent_friday.services import federation as fed
from agent_friday.services import issuer_registry as reg
from agent_friday.services import peer_wire_v2 as wire

URL = "/api/federation/accepted-issuers"
EVENTS = "/api/federation/v2/events"
VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "wire-v2.json").read_text(encoding="utf-8"))
KEYS = sorted(set(VECTORS["peer_keys"].values()))
OWNER = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC = OWNER.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
CHARTER = reg.IssuerCharter(issuer_id="test-issuer", unit_code="TESTP", precision=0, genesis_supply=1000,
                            treasury_account="treasury", cash_claim=False, genesis_authority="ab" * 32,
                            created="2026-11-16T00:00:00Z")


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
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": {"federation": True, "fed_invited_jobs": True}})
    monkeypatch.setattr(fed, "get_peers", lambda: [{"agent_id": k, "fed_pref": "allow"} for k in KEYS])
    accepted = acc.AcceptedIssuers(tmp_path / "accepted.json", lambda data: OWNER.sign(data).hex(), PUBLIC)
    monkeypatch.setattr(routes, "_ACCEPTED", accepted)
    registry = reg.IssuerRegistry(tmp_path / "federation")
    monkeypatch.setattr(routes, "_issuer_registry", lambda: registry)
    registry.register(CHARTER)
    return accepted


def _press():
    from agent_friday.services import desk_press
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(), "X-Friday-Desk": desk_press._KEY}


def _case(name):
    case = next(c for c in VECTORS["cases"] if c.get("name") == name) if "name" in VECTORS["cases"][0] else None
    return case


def _offer():
    for case in VECTORS["cases"]:
        raw = bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else wire.canonical(case["envelope"])
        if acc.named_issuer(raw):
            return raw
    raise AssertionError("no job offer in the vectors")


def test_a_press_on_this_pc_accepts_a_known_issuer(client, _isolated):
    r = client.post(URL, json={"issuer_id": "test-issuer"}, headers=_press())
    assert r.status_code == 201 and r.get_json() == {"ok": True, "outcome": "accepted"}
    assert _isolated.is_accepted_id("test-issuer")
    assert client.post(URL, json={"issuer_id": "test-issuer"}, headers=_press()).status_code == 200
    listing = client.get(URL, headers=_press()).get_json()
    assert listing["issuers"] == [{"issuer_id": "test-issuer", "unit_code": "TESTP"}]


def test_accepting_without_a_press_or_from_another_machine_is_refused_and_writes_nothing(client, _isolated):
    assert client.post(URL, json={"issuer_id": "test-issuer"}).status_code in (401, 403)
    assert client.post(URL, json={"issuer_id": "test-issuer"}, headers=_press(),
                       environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code in (401, 403)
    assert _isolated.refs() == []


def test_an_unknown_or_malformed_issuer_is_refused(client, _isolated):
    assert client.post(URL, json={"issuer_id": "nobody"}, headers=_press()).status_code == 404
    for bad in ("../x", "", None, 5, "A b"):
        assert client.post(URL, json={"issuer_id": bad}, headers=_press()).status_code == 400
    assert _isolated.refs() == []


def test_withdrawing_needs_no_press(client, _isolated):
    client.post(URL, json={"issuer_id": "test-issuer"}, headers=_press())
    plain = {"X-Friday-Token": core._current_api_token()}
    r = client.delete(URL + "/test-issuer", headers=plain)
    assert r.status_code == 200 and r.get_json()["withdrawn"] is True and _isolated.refs() == []
    assert client.delete(URL + "/test-issuer", headers=plain).status_code == 404


def test_an_event_for_an_unaccepted_issuer_is_declined_and_nothing_else_is_parsed(client, monkeypatch):
    from agent_friday.services import peer_wire_v2 as module
    raw = _offer()
    touched = []
    for name in ("verify_event", "schema_matches", "receive", "event_schema"):
        real = getattr(module, name)
        monkeypatch.setattr(module, name, lambda *a, _n=name, **k: touched.append(_n) or (_ for _ in ()).throw(
            AssertionError(_n)))
    r = client.post(EVENTS, data=raw, content_type="application/json")
    assert r.status_code == 422 and r.get_json() == {"ok": False, "error": "issuer_not_accepted"}
    assert touched == [], "no schema, signature or store work happened"


def test_the_same_event_is_recorded_once_its_issuer_is_accepted(client, _isolated):
    raw = _offer()
    assert client.post(EVENTS, data=raw, content_type="application/json").status_code == 422
    named = acc.named_issuer(raw)
    _isolated.accept(reg.IssuerRef(named, "d" * 64, "TESTP"), acc.Press(), 1)
    assert client.post(EVENTS, data=raw, content_type="application/json").status_code in (200, 202)


def test_an_event_that_names_no_issuer_is_not_stopped_by_this_check(client):
    for case in VECTORS["cases"]:
        raw = bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else wire.canonical(case["envelope"])
        if acc.named_issuer(raw) is None:
            assert client.post(EVENTS, data=raw, content_type="application/json").get_json().get("error") != \
                "issuer_not_accepted"
            return
    pytest.skip("every vector names an issuer")


def test_a_list_that_cannot_be_signed_answers_a_named_503_not_a_500(client, _isolated, monkeypatch):
    from agent_friday.routes import federation as routes
    cannot = acc.AcceptedIssuers(_isolated._path, lambda data: "", PUBLIC)
    monkeypatch.setattr(routes, "_ACCEPTED", cannot)
    r = client.post(URL, json={"issuer_id": "test-issuer"}, headers=_press())
    assert r.status_code == 503 and r.get_json() == {"ok": False, "outcome": "cannot-sign"}
