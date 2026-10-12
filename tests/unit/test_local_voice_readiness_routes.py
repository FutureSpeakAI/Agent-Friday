"""LV-7 routes: session-info says why Friday is on cloud voice; the panel reads one status;
Measure now queues (never measures inline); the offer answer is the only thing that moves the engine.

A bare Flask app with the voice blueprint on loopback (trusted by login_required); no server
import, no models, no network.
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent_friday.privacy import cloud_consent as cc
from agent_friday.services import local_scheduler as ls
from agent_friday.services import local_voice_readiness as lvr

UNKNOWN_SNAP = {"capable": False, "state": "unknown", "assessed_at": "2026-10-10T00:00:00+00:00",
                "roles": {"voice": {"ok": False, "state": "unknown", "why": "not measured on this machine yet"}}}
CAPABLE_SNAP = {"capable": True, "state": "capable", "assessed_at": "2026-10-10T00:00:00+00:00",
                "roles": {"voice": {"ok": True, "state": "capable", "why": None}}}
NUMBERS = {"measured": True,
           "stt": {"model_id": "stt-x", "host_ram_mib": 480.0, "load_s": 2.1, "measured_at": "2026-10-10"},
           "tts": {"model_id": "tts-x", "host_ram_mib": 120.0, "load_s": 1.4, "measured_at": "2026-10-10"}}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(lvr, "_state_path", lambda: tmp_path / "local_voice_readiness.json")
    ls.reset_for_tests()
    lvr.reset_for_tests()
    yield
    ls.reset_for_tests()
    lvr.reset_for_tests()


class _Manifest:
    def refresh_selection(self):
        pass

    def snapshot(self):
        return {"mode": "gemini", "stages": {}}


def _client(monkeypatch, *, engine="gemini", settings=None, snap=UNKNOWN_SNAP):
    import agent_friday.core as core
    import agent_friday.routes.voice as rv
    data = dict({"voice_engine": engine, lvr.OFFER_KEY: ""}, **(settings or {}))
    writes = []

    def _save(patch, **kw):
        writes.append(dict(patch))
        data.update(patch)
        return dict(data)
    monkeypatch.setattr(core, "_save_settings", _save)
    monkeypatch.setattr(lvr, "_settings", lambda: dict(data))
    monkeypatch.setattr(cc, "stored_snapshot", lambda: snap)
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: NUMBERS)
    monkeypatch.setattr(rv, "_resolve_voice_engine",
                        lambda settings=None: {"engine": data["voice_engine"] if data["voice_engine"] == "gemini" else "local",
                                               "ws_url": "/ws/live", "label": "x", "models_ready": True})
    # The three readiness routes read stored settings only: resolving the engine probes the network
    # and the key, which a polled route must not do.
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: (_ for _ in ()).throw(AssertionError("probed the voice engine")))
    monkeypatch.setattr(rv._vm, "get_manifest", lambda: _Manifest())
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session on loopback
    app.register_blueprint(rv.voice_bp)
    return app.test_client(), data, writes


def test_session_info_says_why_the_voice_is_on_the_cloud_not_measured(monkeypatch):
    client, _, _ = _client(monkeypatch)
    body = client.get("/api/voice/session-info").get_json()
    why = body["why_cloud"]
    assert why["on_cloud"] is True and why["reason"] == "local_not_measured"
    assert why["action"] == {"kind": "measure", "label": "Measure now"}


def test_session_info_says_your_choice_when_local_would_run(monkeypatch):
    client, _, _ = _client(monkeypatch, snap=CAPABLE_SNAP)
    assert client.get("/api/voice/session-info").get_json()["why_cloud"]["reason"] == "your_choice"


def test_session_info_says_nothing_about_cloud_when_the_voice_is_local(monkeypatch):
    client, _, _ = _client(monkeypatch, engine="local")
    assert client.get("/api/voice/session-info").get_json()["why_cloud"]["on_cloud"] is False


def test_the_panel_status_route_is_one_read_only_object(monkeypatch):
    client, _, writes = _client(monkeypatch, settings={lvr.OFFER_KEY: "pending"}, snap=CAPABLE_SNAP)
    body = client.get("/api/voice/local-readiness").get_json()
    assert body["status"] == "ok" and body["voice_state"] == "capable"
    assert [c["id"] for c in body["offer"]["choices"]] == ["switch", "stay"]
    assert body["job"]["state"] == "idle"
    assert writes == [], "reading changes nothing"


def test_measure_now_queues_the_job_and_measures_nothing_inline(monkeypatch):
    client, _, writes = _client(monkeypatch)
    calls = []
    monkeypatch.setattr(lvr, "_measure_voice", lambda: calls.append(1))
    body = client.post("/api/voice/local-readiness/measure").get_json()
    assert body["status"] == "ok" and body["job"]["wanted"] is True and body["job"]["measure"] is True
    assert body["job"]["state"] == "waiting"
    assert calls == [], "the measuring happens in the idle job, never in the request"
    assert writes == []


def test_answering_switch_moves_the_engine_and_stay_does_not(monkeypatch):
    client, data, _ = _client(monkeypatch, settings={lvr.OFFER_KEY: "pending"}, snap=CAPABLE_SNAP)
    body = client.post("/api/voice/local-readiness/offer", json={"choice": "stay"}).get_json()
    assert body["status"] == "ok" and data["voice_engine"] == "gemini" and data[lvr.OFFER_KEY] == "declined"
    again = client.post("/api/voice/local-readiness/offer", json={"choice": "switch"})
    assert again.status_code == 409, "declined is final: a later switch is refused, not honoured"
    assert data["voice_engine"] == "gemini"


def test_answering_switch_on_a_pending_offer_sets_local(monkeypatch):
    client, data, _ = _client(monkeypatch, settings={lvr.OFFER_KEY: "pending"}, snap=CAPABLE_SNAP)
    body = client.post("/api/voice/local-readiness/offer", json={"choice": "switch"}).get_json()
    assert body["status"] == "ok" and data["voice_engine"] == "local" and data[lvr.OFFER_KEY] == "accepted"


def test_an_answer_with_nothing_pending_is_a_409(monkeypatch):
    client, data, writes = _client(monkeypatch)
    r = client.post("/api/voice/local-readiness/offer", json={"choice": "switch"})
    assert r.status_code == 409 and writes == [] and data["voice_engine"] == "gemini"
