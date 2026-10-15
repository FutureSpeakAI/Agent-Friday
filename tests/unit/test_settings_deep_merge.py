"""A settings POST that carries part of a nested block keeps the rest of it.

Reported from an install: a partial podcasts save, {"podcasts": {...one
field...}}, replaced the whole podcasts block, so the hosts, the other shows'
formats and lengths and the routines' switches were gone. core._save_settings
merged only a short list of blocks, and only one level deep. Now a listed
block merges key by key at every depth; a list still replaces the old list
whole; null is stored as null; user-keyed maps that callers prune by omission
are still replaced whole; and the keys a settings write may not set stay
refused.
"""
from __future__ import annotations

import json

from flask import Flask

from agent_friday import core


_PODCASTS = {
    "enabled_for_routines": {"front_page": True, "briefing": False,
                             "weekly": True, "editorial": True},
    "format": {"briefing": "solo", "front_page": "solo", "editorial": "solo",
               "weekly": "duo", "any": "duo"},
    "length": {"front_page": "short", "briefing": "long",
               "weekly": "standard", "editorial": "standard", "any": "standard"},
    "hosts": {"a": {"name": "Host One", "voice": "voice_one"},
              "b": {"name": "Host Two", "voice": "voice_two"}},
    "on_ready": "silent",
    "cloud_voice": False,
}


def _write_file(data: dict) -> None:
    core.FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    core.SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")
    core._invalidate_settings_cache()


def _on_disk() -> dict:
    return json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8-sig"))


def _client():
    from agent_friday.routes import core_routes as cr
    app = Flask(__name__)
    app.register_blueprint(cr.core_bp)
    return app.test_client()


def test_a_partial_podcasts_post_keeps_the_rest_of_the_block(friday_dir):
    _write_file({"podcasts": json.loads(json.dumps(_PODCASTS))})
    r = _client().post("/api/settings",
                       json={"settings": {"podcasts": {"format": {"briefing": "duo"}}}})
    assert r.status_code == 200, r.get_data(as_text=True)
    pod = _on_disk()["podcasts"]
    assert pod["format"]["briefing"] == "duo"
    # The other shows in the same nested table survive ...
    assert pod["format"]["weekly"] == "duo" and pod["format"]["front_page"] == "solo"
    # ... and so does every sibling of the edited table.
    assert pod["hosts"] == _PODCASTS["hosts"], "a partial save wiped the hosts"
    assert pod["length"] == _PODCASTS["length"]
    assert pod["enabled_for_routines"] == _PODCASTS["enabled_for_routines"]
    assert pod["on_ready"] == "silent"


def test_one_host_voice_keeps_that_hosts_name(friday_dir):
    _write_file({"podcasts": json.loads(json.dumps(_PODCASTS))})
    core._save_settings({"podcasts": {"hosts": {"a": {"voice": "voice_three"}}}})
    hosts = _on_disk()["podcasts"]["hosts"]
    assert hosts["a"] == {"name": "Host One", "voice": "voice_three"}
    assert hosts["b"] == _PODCASTS["hosts"]["b"]


def test_a_partial_block_on_a_file_without_that_block_stores_only_what_was_saved(friday_dir):
    # A settings.json written before the block existed: the save stores the
    # delta, not the default block (a default saved to disk would read as the
    # owner's choice and never follow a later default), and the reader fills
    # the rest from its defaults.
    from agent_friday.services import podcast_engine as pe
    _write_file({"agent_name": "TEST AGENT"})
    core._save_settings({"podcasts": {"on_ready": "silent"}})
    assert _on_disk()["podcasts"] == {"on_ready": "silent"}
    core._invalidate_settings_cache()
    cfg = pe.settings()
    assert cfg["on_ready"] == "silent"
    assert cfg["format"] == pe.DEFAULTS["format"]
    assert cfg["length"] == pe.DEFAULTS["length"]
    # The default second host is filled in (the reader may add derived keys,
    # e.g. pronouns from the voice, so compare the stored fields).
    for k, v in pe.DEFAULTS["hosts"]["b"].items():
        assert cfg["hosts"]["b"][k] == v, k


def test_other_partial_writers_keep_their_siblings(friday_dir):
    _write_file({"scheduling": {"timezone": "Etc/UTC",
                                "working_hours": {"start": "08:00", "end": "16:00"},
                                "working_days": [0, 1, 2], "min_notice_hours": 4,
                                "buffer_minutes": 5}})
    core._save_settings({"scheduling": {"working_hours": {"end": "18:00"}}})
    sch = _on_disk()["scheduling"]
    assert sch["working_hours"] == {"start": "08:00", "end": "18:00"}
    assert sch["timezone"] == "Etc/UTC" and sch["min_notice_hours"] == 4


def test_a_list_replaces_the_old_list_whole(friday_dir):
    _write_file({"scheduling": {"timezone": "", "working_days": [0, 1, 2, 3, 4],
                                "working_hours": {"start": "09:00", "end": "17:00"},
                                "min_notice_hours": 12, "buffer_minutes": 15}})
    core._save_settings({"scheduling": {"working_days": [5]}})
    assert _on_disk()["scheduling"]["working_days"] == [5]


def test_null_is_stored_not_a_delete(friday_dir):
    _write_file({"scheduled_cloud": {"answered": True, "allow": True,
                                     "at": "2026-01-01T00:00:00"}})
    core._save_settings({"scheduled_cloud": {"at": None}})
    sc = _on_disk()["scheduled_cloud"]
    assert "at" in sc and sc["at"] is None
    assert sc["answered"] is True and sc["allow"] is True


def test_user_keyed_maps_are_still_replaced_whole(friday_dir):
    # Callers remove an entry by saving the map without it.
    _write_file({"workspace_layouts": {"one": "fullscreen_chat", "two": "fullscreen_chat"},
                 "model_routing": {"mode": "cloud_only",
                                   "task_overrides": {"x": "m1", "y": "m2"}}})
    core._save_settings({"workspace_layouts": {"one": "fullscreen_chat"},
                         "model_routing": {"task_overrides": {"x": "m1"}}})
    disk = _on_disk()
    assert disk["workspace_layouts"] == {"one": "fullscreen_chat"}
    assert disk["model_routing"]["task_overrides"] == {"x": "m1"}
    assert disk["model_routing"]["mode"] == "cloud_only"


def test_a_capability_seat_is_one_choice_not_a_mix(friday_dir):
    from agent_friday.core import _deep_merge_block
    out = _deep_merge_block(
        {"reasoning": {"provider": "p1", "model": "m1", "extra": 1}},
        {"reasoning": {"provider": "p2", "model": "m2"}}, "capability_routing")
    assert out["reasoning"] == {"provider": "p2", "model": "m2"}


def test_protected_keys_are_still_refused_through_the_post(friday_dir):
    _write_file({"podcasts": json.loads(json.dumps(_PODCASTS)),
                 "model_routing": {"mode": "cloud_only"}})
    r = _client().post("/api/settings", json={"settings": {
        "meeting_archive": "on",
        "voice_engine_choice": {"choice": "cloud"},
        "model_routing": {"cloud_consent": {"answered": True,
                                            "choice": "cloud_unrestricted"}},
        "podcasts": {"on_ready": "notify"},
    }})
    assert r.status_code == 200, r.get_data(as_text=True)
    disk = _on_disk()
    assert disk.get("meeting_archive", "") != "on"
    assert disk.get("voice_engine_choice") != {"choice": "cloud"}
    mr = disk["model_routing"]
    assert mr["mode"] == "cloud_only"
    assert (mr.get("cloud_consent") or {}).get("choice") != "cloud_unrestricted"
    # The allowed part of the same write landed, and merged.
    assert disk["podcasts"]["on_ready"] == "notify"
    assert disk["podcasts"]["hosts"] == _PODCASTS["hosts"]


def test_an_invalid_podcast_length_is_still_refused(friday_dir):
    _write_file({"podcasts": json.loads(json.dumps(_PODCASTS))})
    r = _client().post("/api/settings",
                       json={"settings": {"podcasts": {"length": {"briefing": "epic"}}}})
    assert r.status_code == 400
    assert _on_disk()["podcasts"]["length"]["briefing"] == "long"
