"""Routes behind the held federation switch.

With `held_features.federation` off, the marketplace, economy, federation,
federated-compute (/api/compute/* and /api/federation/compute/*) and
defederation routes answer a plain "not enabled in this
release" result (never a 500). The identity and attestation routes stay
mounted and answer as they always have. With the switch on, every route
answers exactly as before. A purchase is refused either way.
"""
import base64

import pytest


def auth_headers():
    import agent_friday.core as core
    creds = base64.b64encode(
        f"{core.FRIDAY_USERNAME}:{core.FRIDAY_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


@pytest.fixture(autouse=True, scope="module")
def _schemas():
    from agent_friday.services import federation as _fed
    from agent_friday.services import economy as _econ
    _fed._ensure_schema()
    _econ._ensure_schema()


@pytest.fixture
def client():
    import agent_friday.server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


def _federation(monkeypatch, on):
    import agent_friday.core as core
    real = core._load_settings

    def _load():
        s = dict(real() or {})
        s["held_features"] = {"federation": bool(on)}
        return s
    monkeypatch.setattr(core, "_load_settings", _load)


HELD_READS = [
    "/api/marketplace/listings",
    "/api/marketplace/listings/mine",
    "/api/marketplace/policy",
    "/api/economy/wallet",
    "/api/economy/transactions",
    "/api/economy/leaderboard",
    "/api/federation/peers",
    "/api/federation/capabilities",
    "/api/defederation/assessments?assessor_pubkey=nobody",
    "/api/compute/sent",
]

HELD_WRITES = [
    "/api/marketplace/listings",
    "/api/economy/transfer",
    "/api/economy/wallet/genesis",
    "/api/federation/discover",
    "/api/federation/send",
    "/api/federation/compute/request",
    "/api/defederation/assess",
]

OPEN_READS = [
    "/api/federation/identity",
    "/.well-known/friday-agent.json",
    "/api/federation/attestations",
]


def _is_not_enabled(r):
    body = r.get_json(silent=True) or {}
    return (r.status_code == 404 and body.get("ok") is False
            and body.get("error") == "not_enabled"
            and body.get("message") == "Not enabled in this release.")


@pytest.mark.parametrize("url", HELD_READS)
def test_off_held_reads_answer_not_enabled(client, monkeypatch, url):
    _federation(monkeypatch, False)
    r = client.get(url, headers=auth_headers())
    assert _is_not_enabled(r), (url, r.status_code, r.get_data(as_text=True)[:200])


@pytest.mark.parametrize("url", HELD_WRITES)
def test_off_held_writes_answer_not_enabled(client, monkeypatch, url):
    _federation(monkeypatch, False)
    r = client.post(url, json={}, headers=auth_headers())
    assert _is_not_enabled(r), (url, r.status_code, r.get_data(as_text=True)[:200])


@pytest.mark.parametrize("url", OPEN_READS)
@pytest.mark.parametrize("on", [False, True], ids=["off", "on"])
def test_identity_and_attestation_routes_always_answer(client, monkeypatch, url, on):
    _federation(monkeypatch, on)
    r = client.get(url, headers=auth_headers())
    assert r.status_code == 200, (url, r.status_code)
    assert not _is_not_enabled(r)


@pytest.mark.parametrize("url", HELD_READS)
def test_on_held_reads_answer_as_before(client, monkeypatch, url):
    _federation(monkeypatch, True)
    r = client.get(url, headers=auth_headers())
    assert r.status_code == 200, (url, r.status_code, r.get_data(as_text=True)[:200])
    assert not _is_not_enabled(r)


@pytest.mark.parametrize("on", [False, True], ids=["off", "on"])
def test_purchase_is_refused_in_every_configuration(client, monkeypatch, on):
    _federation(monkeypatch, on)
    for body in ({"listing_id": "any"}, {"listing_id": "any", "confirm": True, "invoice_id": "x"}):
        r = client.post("/api/marketplace/purchase", json=body, headers=auth_headers())
        data = r.get_json(silent=True) or {}
        assert r.status_code == 403, (r.status_code, data)
        assert data.get("ok") is False
        assert data.get("error") == "not_in_this_release"
        assert data.get("message") == "Buying is not in this release."
