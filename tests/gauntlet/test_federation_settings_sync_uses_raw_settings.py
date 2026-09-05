"""Gauntlet finding F22: a federation SETTINGS_SYNC push (send and receive)
built its settings delta from _load_settings() -- which applies
_apply_offline_routing_overlay(), a transient, device-local,
NEVER-PERSISTED view (see its docstring, core/__init__.py) that forces
model_routing.mode='local_only'/fallback_to_cloud=False while this device
is offline -- instead of _load_settings_raw(), the real persisted values.

Because model_routing is both in _SYNC_SAFE_KEYS and _DEEP_MERGED_BLOCKS
(routes/federation.py), this meant:
  * SEND side (POST /api/federation/settings/sync): a device that is
    WAN-offline but still LAN-reachable to a peer would broadcast its own
    transient offline-routing overlay to every peer as if it were a real,
    persisted setting.
  * RECEIVE side (_handle_federation_message's SETTINGS_SYNC branch): the
    RECEIVING device's own transient overlay (the overlay is evaluated
    locally, not carried in the envelope) got used as the merge base
    before _save_settings() persisted it -- so a receiver's own momentary
    offline state could get baked permanently into its real settings.json
    by any incoming sync, regardless of what the sender sent.

This probe proves both call sites read the real persisted settings
(_load_settings_raw()) rather than the overlaid view (_load_settings()) by
making the two functions return distinguishably-tagged dicts and checking
which tag survives to the outbound envelope / the _save_settings() call.

Must be RED before the fix (both sites use _load_settings()) and GREEN
after (both use _load_settings_raw()).
"""
from __future__ import annotations

import base64
import json

import pytest

from agent_friday.services import federation as fed

fed._ensure_schema()

RAW_MARKER = {"mode": "cloud_only", "fallback_to_cloud": True, "_tag": "RAW"}
OVERLAY_MARKER = {"mode": "local_only", "fallback_to_cloud": False, "_tag": "OVERLAY"}


def _auth_headers():
    import agent_friday.core as core
    creds = base64.b64encode(
        f"{core.FRIDAY_USERNAME}:{core.FRIDAY_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


@pytest.fixture
def client():
    import agent_friday.server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


class TestSettingsSyncSendUsesRawSettings:
    def test_send_delta_comes_from_raw_settings_not_overlay(self, client, monkeypatch):
        import agent_friday.core as core
        import agent_friday.services.federation_transport as transport

        monkeypatch.setattr(core, "_load_settings_raw",
                             lambda: {"model_routing": dict(RAW_MARKER)})
        monkeypatch.setattr(core, "_load_settings",
                             lambda: {"model_routing": dict(OVERLAY_MARKER)})
        monkeypatch.setattr(fed, "get_peers", lambda: [
            {"agent_id": "peer-1", "public_key_hex": "deadbeef",
             "endpoint": "http://peer.example", "fed_pref": "allow"},
        ])
        captured = []
        monkeypatch.setattr(
            transport, "build_message",
            lambda msg_type, payload_dict, recipient_pubkey_hex:
                captured.append(payload_dict) or {"envelope": True})
        monkeypatch.setattr(transport, "send_to_peer", lambda *a, **k: {"ok": True})

        r = client.post("/api/federation/settings/sync",
                         data=json.dumps({"keys": ["model_routing"]}),
                         headers=_auth_headers())

        assert r.status_code == 200
        assert captured, "build_message() was never called -- settings sync did not attempt to send"
        sent_mr = captured[0]["settings"]["model_routing"]
        assert sent_mr.get("_tag") == "RAW", (
            f"federation_settings_sync() sent {sent_mr!r} -- it used the "
            "transient offline-routing overlay (_load_settings()) instead "
            "of the real persisted settings (_load_settings_raw())"
        )

    def test_no_peers_no_overlay_marker_leaks_either(self, client, monkeypatch):
        """No-op-shaped sanity check: with zero known peers, the route must
        still short-circuit cleanly (sent: 0) rather than erroring -- the
        fix must not change this pre-existing early-exit behavior."""
        import agent_friday.core as core

        monkeypatch.setattr(core, "_load_settings_raw", lambda: {})
        monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": dict(OVERLAY_MARKER)})
        monkeypatch.setattr(fed, "get_peers", lambda: [])

        r = client.post("/api/federation/settings/sync",
                         data=json.dumps({}),
                         headers=_auth_headers())
        assert r.status_code == 200
        body = r.get_json()
        assert body["sent"] == 0


class TestSettingsSyncReceiveUsesRawSettings:
    def test_receive_merges_onto_raw_settings_not_overlay(self, monkeypatch):
        import agent_friday.core as core
        import agent_friday.routes.federation as fedroutes

        # Unknown sender -- irrelevant to F22, but must not trip the
        # separate F40 block-gate added alongside this fix.
        monkeypatch.setattr(fed, "get_peer", lambda agent_id: None)

        raw_settings = {"model_routing": dict(RAW_MARKER), "theme": "dark"}
        overlay_settings = {"model_routing": dict(OVERLAY_MARKER), "theme": "dark"}
        monkeypatch.setattr(core, "_load_settings_raw", lambda: dict(raw_settings))
        monkeypatch.setattr(core, "_load_settings", lambda: dict(overlay_settings))

        saved = {}
        monkeypatch.setattr(core, "_save_settings", lambda data: saved.update(data))

        # The incoming delta itself never touches model_routing -- proving
        # the RAW tag in the saved data came from the local merge base, not
        # from the payload.
        payload = {"settings": {"voice_engine": "piper"}}
        result = fedroutes._handle_federation_message(
            "SETTINGS_SYNC", payload, "some-sender-pubkey-hex")

        assert saved, "_save_settings() was never called"
        assert saved.get("model_routing", {}).get("_tag") == "RAW", (
            f"SETTINGS_SYNC receive handler merged onto "
            f"{saved.get('model_routing')!r} -- it used the receiving "
            "device's own transient offline-routing overlay "
            "(_load_settings()) as the merge base instead of the real "
            "persisted settings (_load_settings_raw())"
        )
        assert result["applied"] == ["voice_engine"]
        assert result["count"] == 1
