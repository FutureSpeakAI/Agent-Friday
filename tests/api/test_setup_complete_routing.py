"""The web setup saves the routing mode chosen on the consent screen.

The consent screen asks where the user's words go and the browser passes the
answer to POST /api/setup/complete. The route used to drop it, so every web
install ran on the factory mode whatever was chosen; the terminal wizard
already saved it. The mode is merged into the existing model_routing block,
never written as a partial block that would drop its other keys.

The mode chosen at setup is the owner's own answer, so the save carries
`owner_routing_change`; without it `_save_settings` drops any change of mode.
"""
from __future__ import annotations

import pytest

import agent_friday.core as core


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    out = _Saved()

    def _save(d, **kw):
        out.append(dict(d))
        out.owner.append(kw.get("owner_routing_change", False))
    monkeypatch.setattr(core, "_save_settings", _save)
    # The route module holds its own binding from `from agent_friday.core import`.
    from agent_friday.routes import core_routes
    monkeypatch.setattr(core_routes, "_save_settings", _save)
    return out


class _Saved(list):
    """Saved deltas, with the owner_routing_change flag of each save in `owner`."""
    def __init__(self):
        super().__init__()
        self.owner = []


@pytest.mark.parametrize("mode", ["cloud_only", "local_only", "local_preferred"])
def test_the_web_setup_complete_saves_the_routing_mode(client, saved, mode):
    r = client.post("/api/setup/complete", json={"agent_name": "Friday", "routing_mode": mode})
    assert r.get_json()["status"] == "ok"
    delta = saved[-1]
    assert delta["setup_complete"] is True
    assert delta["model_routing"]["mode"] == mode
    assert saved.owner[-1] is True, "the setup choice must be saved as an owner action"


def test_the_rest_of_the_routing_block_survives(client, saved, monkeypatch):
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"model_routing": {"mode": "cloud_only",
                                                   "ollama_url": "http://127.0.0.1:11434",
                                                   "vault_local_only": True}})
    client.post("/api/setup/complete", json={"routing_mode": "local_only"})
    block = saved[-1]["model_routing"]
    assert block == {"mode": "local_only", "ollama_url": "http://127.0.0.1:11434",
                     "vault_local_only": True}


def test_an_unknown_routing_mode_is_ignored(client, saved):
    client.post("/api/setup/complete", json={"routing_mode": "everywhere"})
    assert "model_routing" not in saved[-1]


@pytest.mark.parametrize("mode", ["cloud_only", "local_only", "local_preferred"])
def test_the_setup_choice_reaches_the_settings_file(client, tmp_path, monkeypatch, mode):
    """Through the real _save_settings: the owner-only rule keeps setup's choice."""
    import json
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(core, "SETTINGS_FILE", settings)
    start = "smart"
    settings.write_text(json.dumps({"model_routing": {
        "mode": start, "ollama_url": "http://127.0.0.1:11434"}}), encoding="utf-8")
    monkeypatch.setattr(core, "_load_settings", lambda: json.loads(settings.read_text("utf-8")))
    r = client.post("/api/setup/complete", json={"agent_name": "Friday", "routing_mode": mode})
    assert r.get_json()["status"] == "ok"
    block = json.loads(settings.read_text("utf-8"))["model_routing"]
    assert block["mode"] == mode
    assert block["ollama_url"] == "http://127.0.0.1:11434"
