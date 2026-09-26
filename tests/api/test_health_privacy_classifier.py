"""/api/health says whether the privacy classifier is actually running.

Layer 3 of the egress classifier can be installed and down (a boot-time
import race), and while it is, cloud egress fails closed. Health must say
which, instead of reporting the layer active because its package resolves.
"""
from agent_friday.services import sensitivity_classifier as sc


def _privacy(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    return resp.get_json()["privacy_classifier"]


def test_health_reports_a_failed_layer3_as_degraded(client, monkeypatch):
    monkeypatch.setattr(sc, "_EMBEDDER", None)
    monkeypatch.setattr(sc, "_EMBEDDER_ERROR", "KeyError: 'transformers.utils.import_utils'")
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True)
    p = _privacy(client)
    assert p["degraded"] is True
    assert p["layer3"]["state"] == "failed, retrying"
    assert "import_utils" in p["layer3"]["error"]
    assert "DEGRADED" in p["summary"] and "embedding" in p["summary"]
    assert "ml_preload" in p


def test_health_reports_a_running_layer3_as_healthy(client, monkeypatch):
    monkeypatch.setattr(sc, "_EMBEDDER", object())
    p = _privacy(client)
    assert p["degraded"] is False and p["layer3"]["state"] == "ready"
