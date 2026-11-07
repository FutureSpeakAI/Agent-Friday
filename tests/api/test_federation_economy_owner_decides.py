"""The old /api/economy and federation routes move value, publish and change settings
only after the owner decides on an approval card (Federation / Economy spec, NS-40.2-16).

Before this, POST /api/economy/transfer, /earn and /wallet/genesis changed balances for
whatever the caller named, POST /api/marketplace/listings published an asset, and a peer's
SETTINGS_SYNC message rewrote this device's settings, all with no card and no ledger
decision. They sat behind the federation switch only. Now each one takes the same
checkpoint a peer's compute job takes (governance.action_gate.authorize_external):
HELD (403) while the card waits, the action runs once it is approved and then not again,
a refusal is DECLINED, and the decision is closed with what happened.
Real cards in a temp approvals file; the economy and listing stores are real test stores.
"""
import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("federation_on")


@pytest.fixture(autouse=True)
def _cards(tmp_path, monkeypatch):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)


@pytest.fixture(autouse=True, scope="session")
def _schemas():
    from agent_friday.services import economy, federation
    federation._ensure_schema()
    economy._ensure_schema()


@pytest.fixture
def client():
    import agent_friday.server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def _cards_waiting():
    from agent_friday.services import approvals
    return approvals.list_approvals(status="pending", kind="governed_action")


def _approve_the_card():
    from agent_friday.services import approvals
    (card,) = _cards_waiting()
    approvals.decide(card["approval_id"], "approve", decided_by="owner")


def _balance(agent_id):
    from agent_friday.services import economy
    return (economy.get_wallet(agent_id) or {}).get("psi_balance", 0)


def test_earn_waits_for_the_owner_then_runs_once(client):
    agent = "econ-test-" + uuid.uuid4().hex[:8]
    body = {"agent_id": agent, "amount_mpsi": 1000, "reason": "owner-decides"}
    held = _post(client, "/api/economy/earn", body)
    assert held.status_code == 403 and held.get_json()["status"] == "HELD"
    assert _balance(agent) == 0, "it credited before the owner decided"
    _approve_the_card()
    done = _post(client, "/api/economy/earn", body)
    assert done.status_code == 200 and done.get_json()["ok"] is True
    assert _balance(agent) == 1000
    again = _post(client, "/api/economy/earn", body)
    assert again.status_code == 403, "one approval must not cover a second credit"
    assert _balance(agent) == 1000


def test_earn_still_rejects_a_bad_amount_before_raising_a_card(client):
    r = _post(client, "/api/economy/earn", {"amount_mpsi": 0})
    assert r.status_code == 400 and not _cards_waiting()


def test_transfer_waits_for_the_owner(client):
    sender = "econ-from-" + uuid.uuid4().hex[:8]
    from agent_friday.services import economy
    economy.earn(sender, 5000, "seed")
    body = {"from_agent": sender, "to_agent": "econ-to-" + uuid.uuid4().hex[:8],
            "amount_mpsi": 700, "reason": "owner-decides"}
    held = _post(client, "/api/economy/transfer", body)
    assert held.status_code == 403 and held.get_json()["status"] == "HELD"
    assert _balance(sender) == 5000, "it moved credits before the owner decided"
    _approve_the_card()
    assert _post(client, "/api/economy/transfer", body).status_code == 200
    assert _balance(sender) == 4300


def test_a_declined_transfer_does_not_move_anything(client):
    from agent_friday.services import approvals, economy
    sender = "econ-no-" + uuid.uuid4().hex[:8]
    economy.earn(sender, 900, "seed")
    body = {"from_agent": sender, "to_agent": "econ-other", "amount_mpsi": 100}
    assert _post(client, "/api/economy/transfer", body).status_code == 403
    (card,) = _cards_waiting()
    approvals.decide(card["approval_id"], "deny", decided_by="owner")
    r = _post(client, "/api/economy/transfer", body)
    assert r.status_code == 403 and r.get_json()["status"] == "DECLINED"
    assert _balance(sender) == 900


def test_genesis_waits_for_the_owner(client):
    held = _post(client, "/api/economy/wallet/genesis", {})
    assert held.status_code in (403, 503)
    if held.status_code == 403:
        assert held.get_json()["status"] == "HELD" and len(_cards_waiting()) == 1


def test_a_listing_waits_for_the_owner(client):
    body = {"asset_id": "owner-decides-" + uuid.uuid4().hex[:8], "price_mpsi": 0, "title": "t"}
    held = _post(client, "/api/marketplace/listings", body)
    assert held.status_code == 403 and held.get_json()["status"] == "HELD"
    mine = client.get("/api/marketplace/listings/mine").get_json()
    assert not [x for x in (mine.get("listings") or []) if x.get("asset_id") == body["asset_id"]]
    _approve_the_card()
    ok = _post(client, "/api/marketplace/listings", body)
    assert ok.status_code == 200 and ok.get_json()["ok"] is True


def test_a_peers_settings_push_changes_nothing_until_the_owner_approves(monkeypatch):
    from agent_friday.routes import federation as routes
    saved = []
    import agent_friday.core as core
    monkeypatch.setattr(core, "_save_settings", lambda d: saved.append(dict(d)))
    peer = "ab" * 32
    payload = {"settings": {"theme": "dark", "model_routing": {"mode": "cloud"}, "api_key": "x"}}
    held = routes._handle_federation_message("SETTINGS_SYNC", payload, peer)
    assert held["status"] == "HELD" and not saved, "a peer rewrote settings with no card"
    _approve_the_card()
    done = routes._handle_federation_message("SETTINGS_SYNC", payload, peer)
    assert sorted(done["applied"]) == ["model_routing", "theme"] and len(saved) == 1
    assert "api_key" not in saved[0]
