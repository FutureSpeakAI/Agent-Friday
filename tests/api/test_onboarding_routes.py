"""API tests for the onboarding / provider-agnostic surface:
capabilities, provider health, aggregate health, encrypted key storage (congruence
proof: secret never lands in settings.json), and distribution apply."""
import core


def test_capabilities_route(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    caps = r.get_json()["capabilities"]
    assert any(c["capability"] == "reasoning" for c in caps)
    assert all("available" in c for c in caps)


def test_providers_health_route(client):
    r = client.get("/api/providers/health")
    assert r.status_code == 200
    provs = r.get_json()["providers"]
    names = {p["provider"] for p in provs}
    assert "anthropic" in names and "ollama-local" in names


def test_health_full_route(client):
    r = client.get("/api/health/full")
    assert r.status_code == 200
    body = r.get_json()
    for k in ("providers", "capabilities", "demo", "hardware", "dependencies",
              "distribution", "server"):
        assert k in body, k


def test_provider_key_encrypted_never_in_settings(client):
    secret = "sk-secret-DONOTLEAK-pytest"  # pragma: allowlist secret
    prov = "pytest-demo-prov"  # synthetic name → no real env var is touched
    r = client.post(f"/api/providers/{prov}/key", json={"key": secret})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "connected"
    # the response must never echo the key back
    assert secret not in r.get_data(as_text=True)
    # and the secret must never be written to settings.json
    raw = core.SETTINGS_FILE.read_text(encoding="utf-8") if core.SETTINGS_FILE.exists() else "{}"
    assert secret not in raw
    # an encrypted key file exists
    from services import credential_store as cs
    assert cs.provider_key_status(prov) == "connected"
    # cleanup / DELETE flips status back to missing
    r2 = client.delete(f"/api/providers/{prov}/key")
    assert r2.status_code == 200
    assert cs.provider_key_status(prov) == "missing"


def test_distro_apply_route(client):
    r = client.post("/api/distros/researcher/apply")
    assert r.status_code == 200
    assert r.get_json()["distribution"] == "researcher"
    s = client.get("/api/settings").get_json()["settings"]
    assert s["distribution"] == "researcher"


def test_settings_capability_routing_stays_congruent(client):
    # Changing the legacy flat key must propagate into capability_routing.
    client.post("/api/settings", json={"settings": {"orchestrator_model": "gpt-4o"}})
    s = client.get("/api/settings").get_json()["settings"]
    assert s["capability_routing"]["reasoning"]["model"] == "gpt-4o"
    # restore a sane default for any later tests in the session
    client.post("/api/settings", json={"settings": {"orchestrator_model": "claude-opus-4-8"}})
