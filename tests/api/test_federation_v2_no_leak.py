"""Error feedback across the peer trust boundary discloses nothing (row NS-40.2-14).

Rule: an answer from POST /api/federation/v2/events is a status plus a code from a closed list
(or the sender's own event id back). It never echoes anything from the submitted document and
never names files, people, balances, pending jobs or approval ids, whatever the failure.
Offline; fictitious public test keys only."""
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
CODES = {"size", "encoding", "duplicate_key", "number", "string", "integer", "depth", "type",
         "noncanonical", "schema", "untrusted_key", "signature", "header", "terms_mismatch",
         "role", "split", "rate_limited"}
MARKER = "PLANTED-MARKER-7731"


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


def _hostile_bodies():
    """Documents that carry a marker in places a careless error message might echo."""
    first = VECTORS["cases"][0]
    envelope = json.loads(_raw(first))
    planted = json.loads(json.dumps(envelope))
    planted["body"]["payload"] = {"note": MARKER, "path": "/srv/data/" + MARKER}
    unknown_field = dict(envelope, **{MARKER: MARKER})
    return [
        ("not json", ("{ " + MARKER).encode()),
        ("marker as a key", json.dumps({MARKER: 1}).encode()),
        ("marker in a wrong-typed field", json.dumps({"body": MARKER, "signature": MARKER}).encode()),
        ("marker in the payload, signature now invalid", json.dumps(planted).encode()),
        ("marker as an extra top-level field", json.dumps(unknown_field).encode()),
        ("marker as invalid utf-8 neighbour", MARKER.encode() + b"\xff\xfe"),
        ("marker as a huge nested array", ("[" * 200 + '"' + MARKER + '"' + "]" * 200).encode()),
    ]


def test_no_failure_answer_echoes_anything_from_the_submitted_document(client):
    for name, raw in _hostile_bodies():
        r = _post(client, raw)
        assert r.status_code in (413, 422), (name, r.status_code)
        assert MARKER not in r.get_data(as_text=True), name


def test_a_failure_answer_is_exactly_ok_false_and_a_code_from_the_closed_list(client):
    seen = set()
    for name, raw in _hostile_bodies() + [(c["name"], _raw(c)) for c in VECTORS["cases"]]:
        r = _post(client, raw)
        if r.status_code in (200, 202, 409):
            continue
        body = r.get_json()
        assert set(body) == {"ok", "error"} and body["ok"] is False, (name, body)
        assert body["error"] in CODES, (name, body)
        seen.add(body["error"])
    assert {"schema", "untrusted_key", "signature"} <= seen


def test_a_success_or_conflict_answer_holds_only_the_senders_own_event_id(client):
    from agent_friday.services import peer_wire_v2 as wire
    answered = set()
    for case in VECTORS["cases"]:
        raw = _raw(case)
        r = _post(client, raw)
        if r.status_code in (200, 202, 409):
            body = r.get_json()
            assert set(body) == {"ok", "event_id", "disposition"}, case["name"]
            assert body["event_id"] == wire.verify_event(raw, KEYS).event_id, case["name"]
            answered.add(r.status_code)
    assert answered == {200, 202, 409}


def test_an_untrusted_sender_gets_only_the_code_untrusted_key(client, monkeypatch):
    raw = _raw(VECTORS["cases"][0])
    monkeypatch.setattr(fed, "get_peers", lambda: [])
    untrusted = _post(client, raw)
    assert untrusted.status_code == 422
    assert untrusted.get_json() == {"ok": False, "error": "untrusted_key"}


def test_the_answer_never_names_balances_peers_files_or_pending_approvals(client, monkeypatch):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "list_approvals", lambda **k: [{"id": MARKER, "title": MARKER}])
    for name, raw in _hostile_bodies():
        text = _post(client, raw).get_data(as_text=True).lower()
        for word in ("balance", "approval", "pending", "peer", "path", "file", "/srv/", MARKER.lower()):
            assert word not in text, (name, word)
