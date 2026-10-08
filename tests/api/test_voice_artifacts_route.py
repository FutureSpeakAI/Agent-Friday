"""The local voice models are listed, with sizes and pins, in every voice mode,
and a download starts only from an explicit, confirmed click.

Before this route the local cascade (ear, fast reply model, voice helpers) had
no Settings control at all, and an owner on cloud voice saw no download step.
No network and no real download: the installer's start() is a recording stub.
"""
import agent_friday.routes.voice as vr
from agent_friday.services import voice_installer as vi

CASCADE = ("voice-ear-streaming", "sherpa-onnx", "voice-front-4b",
           "voice-front-1.7b", "misaki")


def _rows(client, monkeypatch, engine):
    monkeypatch.setattr(vr, "_resolve_voice_engine", lambda *a, **k: {"engine": engine})
    monkeypatch.setattr(vi, "artifact_installed", lambda aid: False)
    r = client.get("/api/voice/artifacts")
    assert r.status_code == 200
    return {a["id"]: a for a in r.get_json()["artifacts"]}


def test_cascade_is_listed_with_sizes_in_gemini_mode(client, monkeypatch):
    rows = _rows(client, monkeypatch, "gemini")
    for aid in CASCADE:
        row = rows[aid]
        assert row["size_mb"] > 0 and row["size_bytes"] > 0, aid
        assert row["purpose"], aid
        assert row["pinned"] is True and row["installable"] is True, aid
        assert row["installed"] is False and row["job"] is None, aid
    assert rows["voice-ear-streaming"]["requires"] == ["sherpa-onnx"]


def test_listing_does_not_depend_on_the_voice_engine(client, monkeypatch):
    a = _rows(client, monkeypatch, "gemini")
    b = _rows(client, monkeypatch, "local")
    assert set(a) == set(b)


def test_an_unpinned_artifact_is_listed_with_its_reason(client, monkeypatch):
    rows = _rows(client, monkeypatch, "gemini")
    turbo = rows["voice-ear-turbo"]
    assert turbo["pinned"] is False and turbo["installable"] is False
    assert turbo["why_not"]


def test_a_download_without_consent_never_starts(client, monkeypatch):
    started = []
    monkeypatch.setattr(vi, "start", lambda t: started.append(t) or {"state": "running"})
    r = client.post("/api/voice/setup/install", json={"target": "voice-front-1.7b"})
    assert r.status_code == 400
    assert "confirmation" in r.get_json()["error"]
    r = client.post("/api/voice/setup/install",
                    json={"target": "voice-front-1.7b", "consent": "yes"})
    assert r.status_code == 400
    assert started == []


def test_a_confirmed_download_starts(client, monkeypatch):
    started = []
    monkeypatch.setattr(vi, "start", lambda t: started.append(t) or {"state": "running"})
    r = client.post("/api/voice/setup/install",
                    json={"target": "voice-front-1.7b", "consent": True})
    assert r.status_code == 200 and started == ["voice-front-1.7b"]


def test_a_non_artifact_target_keeps_its_old_contract(client, monkeypatch):
    started = []
    monkeypatch.setattr(vi, "start", lambda t: started.append(t) or {"state": "running"})
    r = client.post("/api/voice/setup/install", json={"target": "tier1-models"})
    assert r.status_code == 200 and started == ["tier1-models"]
