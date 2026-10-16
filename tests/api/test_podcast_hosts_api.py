"""/api/podcasts/hosts: the Podcasts view reads and saves the hosts' names
(main host and co-host), and an episode asked for over the API can name its
own. Synthetic names only."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import podcast_engine as pe


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import agent_friday.core as core
    saved = {"s": {}}

    def save(delta):
        block = saved["s"].setdefault("podcasts", {})
        for k, v in (delta.get("podcasts") or {}).items():
            if isinstance(v, dict) and isinstance(block.get(k), dict):
                for kk, vv in v.items():
                    if isinstance(vv, dict) and isinstance(block[k].get(kk), dict):
                        block[k][kk].update(vv)
                    else:
                        block[k][kk] = json.loads(json.dumps(vv))
            else:
                block[k] = json.loads(json.dumps(v))
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: saved["s"])
    monkeypatch.setattr(core, "_save_settings", save)
    monkeypatch.setattr(pe, "wake", lambda: None)
    yield saved


def test_the_hosts_names_are_read_and_saved(client):
    got = client.get("/api/podcasts/hosts").get_json()["hosts"]
    # She has her default name, so an unnamed co-host is the distinct default, never "Agent Friday"
    # beside "Friday" (owner decision; podcast_engine.cohost_default_name).
    assert (got["a"]["name"], got["b"]["name"]) == ("Friday", pe.COHOST_NEUTRAL_NAME)
    assert got["b"]["own"] == "" and got["b"]["default"] == pe.COHOST_NEUTRAL_NAME
    r = client.put("/api/podcasts/hosts", json={"b": "Agent Orion"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["hosts"]["b"]["name"] == "Agent Orion"
    assert pe.settings()["hosts"]["b"]["name"] == "Agent Orion"
    bad = client.put("/api/podcasts/hosts", json={"b": "Friday"})
    assert bad.status_code == 409 and "different names" in bad.get_json().get("message", bad.get_data(as_text=True))


def test_an_episode_asked_for_over_the_api_can_name_its_hosts(client):
    r = client.post("/api/podcasts", json={"sources": [{"kind": "text", "text": "Council voted 7 to 2."}],
                                           "hosts": "duo", "host_names": ["Agent Vega", "Agent Orion"]})
    assert r.status_code == 202, r.get_data(as_text=True)
    ep = pe.load(r.get_json()["episode"]["id"])
    assert (ep["hosts"]["a"]["name"], ep["hosts"]["b"]["name"]) == ("Agent Vega", "Agent Orion")
