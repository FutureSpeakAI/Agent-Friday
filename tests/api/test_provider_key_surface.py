"""What the Providers panel needs in order to let someone SWAP a key.

Stephen, 2026-08-26: "we need to fix the installer so the Friday that ships
to users can swap API keys from the settings menu ... view which keys are
set (masked), replace one, remove one, and ideally see whether it currently
works."

Storing and deleting already worked. The two things missing were the ones
that make a swap safe to perform:

  * WHICH key is in play. Two sources can hold a key for one provider — the
    encrypted store (written by Settings) and the environment (written by the
    wizard into start.bat, re-read at every launch). The environment used to
    win silently, so a swapped key came back dead after a restart with
    nothing on screen disagreeing.
  * WHETHER IT WORKS. The probe was a metadata read, which a key with no
    credit passes cheerfully, and the one-token ping that would have caught
    that was gated to openai-compatible providers — so Anthropic and Google,
    the two Friday ships with, were the two she could not check.

These tests drive the HTTP surface the panel actually reads.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.real_provider_paths


class TestProviderListSurface:
    def test_every_provider_reports_its_key_source(self, client):
        rows = client.get("/api/providers").get_json()["providers"]
        assert rows
        for p in rows:
            assert p.get("key_source") in ("settings", "environment", "none"), (
                "%s has no key_source — the panel cannot tell the user which "
                "key is in play" % p.get("name")
            )

    def test_a_keyless_provider_reports_none(self, client):
        rows = {p["name"]: p for p in
                client.get("/api/providers").get_json()["providers"]}
        assert rows["ollama-local"]["key_source"] == "none"
        assert not rows["ollama-local"]["key_masked"]

    def test_the_masked_key_never_carries_the_real_one(self, client, monkeypatch):
        decoy = "fake-key-NEVERSHOWTHISWHOLETHING"
        monkeypatch.setenv("ANTHROPIC_API_KEY", decoy)
        raw = client.get("/api/providers").get_data(as_text=True)
        assert decoy not in raw, "the provider list echoed a full API key"
        rows = {p["name"]: p for p in
                client.get("/api/providers").get_json()["providers"]}
        assert rows["anthropic"]["key_masked"].endswith("HING")

    def test_a_saved_key_is_reported_as_coming_from_settings(
            self, client, monkeypatch):
        """The swap case: something in the environment, something newer saved."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-STALE-from-start-bat")
        import agent_friday.services.credential_store as cs
        monkeypatch.setattr(cs, "get_provider_key",
                            lambda n: "fake-key-FRESH" if n == "anthropic" else None,
                            raising=False)
        rows = {p["name"]: p for p in
                client.get("/api/providers").get_json()["providers"]}
        assert rows["anthropic"]["key_source"] == "settings"


class TestKeyCheck:
    """POST /api/providers/<name>/test with ping — the real round trip."""

    def _post(self, client, name="anthropic"):
        return client.post("/api/providers/%s/test" % name,
                           json={"ping": True}).get_json()

    def test_a_check_returns_a_verdict_and_a_sentence(self, client):
        out = self._post(client)
        assert out.get("verdict") in ("ok", "rejected", "no_credit", "unknown")
        assert out.get("verdict_detail"), "a verdict with no wording helps nobody"

    def test_the_check_never_echoes_key_material(self, client, monkeypatch):
        decoy = "fake-key-DONOTECHOTHISANYWHERE"
        monkeypatch.setenv("ANTHROPIC_API_KEY", decoy)
        raw = client.post("/api/providers/anthropic/test",
                          json={"ping": True}).get_data(as_text=True)
        assert decoy not in raw

    def test_an_unreachable_provider_fails_open_to_unknown(self, client):
        """The offline test transport makes every call fail. That must read as
        'could not tell', never as 'your key is bad' — a pre-flight check must
        not become the reason someone distrusts a working key."""
        out = self._post(client)
        assert out["verdict"] != "rejected"
        low = out["verdict_detail"].lower()
        assert "invalid" not in low and "rejected" not in low

    def test_google_is_checkable_too(self, client):
        """Anthropic and Google were precisely the two the old ping skipped."""
        out = self._post(client, "google-gemini")
        assert out.get("verdict") in ("ok", "rejected", "no_credit", "unknown")
        assert out.get("verdict_detail")
