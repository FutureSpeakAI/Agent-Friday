"""Gauntlet finding F40: the Federation panel's per-peer ask/allow/block
control was completely non-functional in both directions.

Write side: the UI's setPref() (index.html FederationPanel, mirrored in
ui_parts/app.html) POSTs to /api/federation/peers/<agentId>/pref with body
{fed_pref: pref}. That route did not exist anywhere in routes/federation.py
-- only GET /api/federation/peers and GET .../<agent_id> were defined --
so every click 404'd, silently swallowed by the UI's own `catch(() => {})`.

Enforcement side: even though the `fed_pref` DB column already existed
(services/federation.py, default 'ask') and get_peers()/get_peer() already
read it back correctly (SELECT *) -- which is why the dropdown itself
rendered and looked alive -- nothing downstream ever consulted it:
  * _handle_federation_message() processed HANDSHAKE/HEARTBEAT/
    TRUST_ATTESTATION/LICENSE_QUERY/SETTINGS_SYNC from every peer
    identically regardless of fed_pref.
  * federation_settings_sync()'s outbound push iterated every peer with
    zero pref filtering.

This probe proves, against real DB-backed peer rows (no mocking of the
federation service layer itself for the write-path and enforcement
tests):
  1. POST .../pref actually persists to the fed_pref column (write path,
     previously a 404).
  2. A 'block'-prefed peer's messages are refused by
     _handle_federation_message() for representative message types.
  3. A non-blocked peer, and an entirely unknown sender, are unaffected
     (no-op-shaped checks) -- the gate must be specific to 'block' on a
     KNOWN peer, not a blanket refusal.
  4. The outbound settings-sync push skips a 'block'-prefed peer.

Must be RED before the fix (route 404s; 'block' has zero effect in either
direction) and GREEN after.
"""
from __future__ import annotations

import base64
import json
import uuid

import pytest

from agent_friday.services import federation as fed

fed._ensure_schema()


def _auth_headers():
    import agent_friday.core as core
    creds = base64.b64encode(
        f"{core.FRIDAY_USERNAME}:{core.FRIDAY_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def _insert_peer(agent_id: str, fed_pref: str = "ask") -> None:
    """Insert a minimal real peer row directly, bypassing the crypto/
    handshake machinery -- this probe only cares about fed_pref plumbing."""
    with fed._conn() as con:
        con.execute(
            "INSERT INTO peers (agent_id, label, fed_pref) VALUES (?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET fed_pref = excluded.fed_pref",
            (agent_id, "test-peer", fed_pref),
        )


def _uid(tag: str) -> str:
    return f"gauntlet-f40-{tag}-" + uuid.uuid4().hex[:8]


@pytest.fixture
def client():
    import agent_friday.server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


class TestPrefWriteRoute:
    def test_post_pref_persists_to_db(self, client):
        agent_id = _uid("write")
        _insert_peer(agent_id, fed_pref="ask")

        r = client.post(
            f"/api/federation/peers/{agent_id}/pref",
            data=json.dumps({"fed_pref": "block"}),
            headers=_auth_headers(),
        )
        assert r.status_code == 200, (
            f"POST /api/federation/peers/<id>/pref returned {r.status_code} "
            f"({r.get_data(as_text=True)}) -- this is the exact route the "
            "UI's setPref() calls, and it must exist"
        )
        peer = fed.get_peer(agent_id)
        assert peer is not None
        assert peer["fed_pref"] == "block", (
            "the route responded 200 but did not persist fed_pref to the "
            "same DB column get_peers()/get_peer() read back"
        )

    def test_post_pref_rejects_unknown_value(self, client):
        agent_id = _uid("badval")
        _insert_peer(agent_id, fed_pref="ask")
        r = client.post(
            f"/api/federation/peers/{agent_id}/pref",
            data=json.dumps({"fed_pref": "nonsense"}),
            headers=_auth_headers(),
        )
        assert r.status_code == 400
        assert fed.get_peer(agent_id)["fed_pref"] == "ask"

    def test_post_pref_unknown_peer_404s(self, client):
        r = client.post(
            "/api/federation/peers/totally-unknown-peer-id/pref",
            data=json.dumps({"fed_pref": "allow"}),
            headers=_auth_headers(),
        )
        assert r.status_code == 404


class TestHandleFederationMessageRespectsBlock:
    def test_blocked_peer_handshake_refused(self):
        import agent_friday.routes.federation as fedroutes
        agent_id = _uid("block-hs")
        _insert_peer(agent_id, fed_pref="block")

        result = fedroutes._handle_federation_message(
            "HANDSHAKE", {"manifest": {}, "peer_card": {"agent_id": agent_id}}, agent_id)

        assert result.get("blocked") is True
        assert result.get("ok") is False

    def test_blocked_peer_heartbeat_refused(self):
        import agent_friday.routes.federation as fedroutes
        agent_id = _uid("block-hb")
        _insert_peer(agent_id, fed_pref="block")

        result = fedroutes._handle_federation_message(
            "HEARTBEAT", {"timestamp": "now"}, agent_id)

        assert result.get("blocked") is True
        assert result.get("type") != "HEARTBEAT_ACK"

    def test_blocked_peer_trust_attestation_refused(self):
        import agent_friday.routes.federation as fedroutes
        agent_id = _uid("block-ta")
        _insert_peer(agent_id, fed_pref="block")

        result = fedroutes._handle_federation_message(
            "TRUST_ATTESTATION", {"observation": {"honesty": 0.1}}, agent_id)

        assert result.get("blocked") is True
        assert "accepted" not in result

    def test_blocked_peer_license_query_refused(self):
        import agent_friday.routes.federation as fedroutes
        agent_id = _uid("block-lq")
        _insert_peer(agent_id, fed_pref="block")

        result = fedroutes._handle_federation_message(
            "LICENSE_QUERY", {"asset_id": "some-asset"}, agent_id)

        assert result.get("blocked") is True
        assert "found" not in result

    def test_blocked_peer_settings_sync_refused(self):
        import agent_friday.routes.federation as fedroutes
        agent_id = _uid("block-ss")
        _insert_peer(agent_id, fed_pref="block")

        result = fedroutes._handle_federation_message(
            "SETTINGS_SYNC", {"settings": {"theme": "dark"}}, agent_id)

        assert result.get("blocked") is True
        assert "applied" not in result

    def test_non_blocked_peer_is_unaffected(self):
        """No-op-shaped sanity check: an 'allow'-prefed peer's HEARTBEAT must
        still be processed normally -- the gate must be specific to
        'block', not a blanket refusal of every known peer."""
        import agent_friday.routes.federation as fedroutes
        agent_id = _uid("allow")
        _insert_peer(agent_id, fed_pref="allow")

        result = fedroutes._handle_federation_message(
            "HEARTBEAT", {"timestamp": "now"}, agent_id)

        assert result.get("type") == "HEARTBEAT_ACK"

    def test_unknown_sender_is_unaffected(self):
        """A peer not yet in the DB at all (e.g. a first HANDSHAKE) must not
        be treated as blocked -- fed_pref only exists once a peer row does."""
        import agent_friday.routes.federation as fedroutes
        agent_id = _uid("unknown")

        result = fedroutes._handle_federation_message(
            "HEARTBEAT", {"timestamp": "now"}, agent_id)

        assert result.get("type") == "HEARTBEAT_ACK"


class TestSettingsSyncOutboundSkipsBlockedPeers:
    def test_blocked_peer_excluded_from_broadcast(self, client, monkeypatch):
        blocked_id = _uid("outblock")
        allowed_id = _uid("outallow")
        _insert_peer(blocked_id, fed_pref="block")
        _insert_peer(allowed_id, fed_pref="allow")

        r = client.post("/api/federation/settings/sync",
                         data=json.dumps({}),
                         headers=_auth_headers())
        assert r.status_code == 200
        body = r.get_json()
        attempted_ids = [res["peer"] for res in body["results"]]
        assert blocked_id not in attempted_ids, (
            "federation_settings_sync() attempted to send to a peer whose "
            "fed_pref is 'block' -- the outbound push must skip blocked "
            "peers entirely"
        )
        assert allowed_id in attempted_ids, (
            "no-op-shaped check: a non-blocked peer must still be attempted "
            "(this one has no real endpoint/pubkey so it will fail with "
            "'missing pubkey or endpoint', which is fine -- the point is it "
            "wasn't filtered out before the attempt)"
        )
