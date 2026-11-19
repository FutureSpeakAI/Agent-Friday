"""The owner is told when an issuer was stopped for showing two histories (NS-49.1-2, design 10.2). Test credits only.

HeadPins already stops the issuer and keeps a report that survives a restart. This is the read-only route the Settings
panel shows it through: GET /api/federation/issuer-reports gives the reports (issuer id, sequence, why) and nothing else.
The limit, stated plainly: nothing on the receive path feeds HeadPins yet (peer wire v2 carries no head), so on a real
node the list stays empty until that message exists; the route and the panel are what is built here."""
import pytest

import agent_friday.core as core
from agent_friday.services import issuer_authority as auth
from agent_friday.services import issuer_registry as reg

URL = "/api/federation/issuer-reports"
KEY = auth.Ed25519Key(bytes(range(32)))
CHARTER = reg.IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=100,
                            treasury_account="treasury", cash_claim=False, genesis_authority=KEY.public_key,
                            created="2026-11-16T00:00:00Z")
DIGEST = reg.charter_digest(CHARTER)


@pytest.fixture
def client():
    import agent_friday.server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


@pytest.fixture
def pins(tmp_path, monkeypatch):
    from agent_friday.routes import federation as routes
    pins = auth.HeadPins(tmp_path / "pins.json")
    monkeypatch.setattr(routes, "_HEAD_PINS", pins)
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": {"federation": True}})
    return pins


def head(seq, state="aa", prev="0" * 64, receipt="bb"):
    h = auth.Head("test.one", DIGEST, 0, seq, state * 32, receipt * 32, prev)
    raw = h.canonical_bytes()
    return raw, KEY.sign(raw)


def test_no_report_by_default(client, pins):
    r = client.get(URL)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "reports": [], "stopped": []}


def test_two_histories_at_one_sequence_are_reported_with_issuer_sequence_and_reason(client, pins):
    first, other = head(1), head(1, state="cc")
    assert pins.accept(*first, KEY.public_key, DIGEST) is auth.PinOutcome.ACCEPTED
    assert pins.accept(*other, KEY.public_key, DIGEST) is auth.PinOutcome.EQUIVOCATION
    body = client.get(URL).get_json()
    assert body["stopped"] == ["test.one"]
    assert [(r["issuer_id"], r["seq"], r["why"]) for r in body["reports"]] == [("test.one", 1, "another head at this sequence")]


def test_a_head_that_does_not_link_is_reported_too(client, pins):
    pins.accept(*head(1), KEY.public_key, DIGEST)
    assert pins.accept(*head(2, prev="9" * 64), KEY.public_key, DIGEST) is auth.PinOutcome.EQUIVOCATION
    assert client.get(URL).get_json()["reports"][0]["why"] == "does not link to the pinned head below it"


def test_the_route_only_reads(client, pins):
    assert client.post(URL).status_code == 405 and client.delete(URL).status_code == 405


def test_the_report_survives_a_restart_of_the_pins(client, pins, tmp_path):
    pins.accept(*head(1), KEY.public_key, DIGEST)
    pins.accept(*head(1, state="cc"), KEY.public_key, DIGEST)
    reopened = auth.HeadPins(tmp_path / "pins.json")
    assert [r["issuer_id"] for r in reopened.reports] == ["test.one"] and reopened.stopped("test.one")
