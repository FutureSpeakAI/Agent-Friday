"""/api/health says, in plain words, which privacy layers are actually running.

Layer 3 of the egress classifier (the semantic check) can be installed and
down (a boot-time import race); while it is, cloud-bound text with no other
privacy signal is held. The packaged build leaves it out on purpose; that is
not a fault and must not read as one. Health states each case plainly,
instead of reporting the layer active because its package resolves.
"""
from agent_friday.services import privacy_layers as pl
from agent_friday.services import sensitivity_classifier as sc


def _privacy(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    return resp.get_json()["privacy_classifier"]


def _layer(p, name):
    return next(l for l in p["layers"] if l["name"] == name)


def test_health_reports_a_failed_layer3_as_degraded(client, monkeypatch):
    monkeypatch.setattr(pl, "_module_available", lambda m: True)
    monkeypatch.setattr(sc, "_EMBEDDER", None)
    monkeypatch.setattr(sc, "_EMBEDDER_ERROR", "KeyError: 'transformers.utils.import_utils'")
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True)
    p = _privacy(client)
    assert p["degraded"] is True
    assert p["layer3"]["state"] == "failed, retrying"
    assert "import_utils" in p["layer3"]["error"]
    sem = _layer(p, "embedding")
    assert sem["label"] == "Semantic check" and sem["active"] is False
    assert sem["status"].startswith("starting") and "held" in sem["status"]
    assert "Semantic check (starting" in p["summary"]
    assert "ml_preload" in p


def test_health_reports_a_running_layer3_as_healthy(client, monkeypatch):
    monkeypatch.setattr(pl, "_module_available", lambda m: True)
    monkeypatch.setattr(sc, "_EMBEDDER", object())
    p = _privacy(client)
    assert p["degraded"] is False and p["layer3"]["state"] == "ready"
    assert _layer(p, "embedding")["status"] == "on"


def test_a_packaged_build_says_not_installed_and_is_not_degraded(client, monkeypatch):
    monkeypatch.setattr(pl, "_module_available",
                        lambda m: m != "sentence_transformers" and m != "presidio_analyzer")
    monkeypatch.setattr(sc, "_EMBEDDER", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: False)
    p = _privacy(client)
    assert p["degraded"] is False
    assert _layer(p, "embedding")["status"] == "not installed in this build"
    assert "Semantic check (not installed in this build)" in p["summary"]
    assert _layer(p, "regex")["status"] == "on" and _layer(p, "keyword")["status"] == "on"
