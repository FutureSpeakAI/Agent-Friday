"""A settings save is visible to the very next read, even with a reader in flight.

The owner clicked "Local preferred", the save returned 200, and the read-back
said "That mode did not stick". The settings cache is filled by readers that
read the file OUTSIDE the cache lock: a reader that started before the save and
finished after the save's final invalidation stored the OLD file for the full
TTL, and the UI's read-back in the same second was served that stale copy.

A read that overlapped a save must not populate the cache.
"""
from __future__ import annotations

import json

from agent_friday import core


def test_a_reader_that_overlapped_a_save_does_not_cache_the_old_file(friday_dir, monkeypatch):
    core._save_settings({"show_all_workspaces": False})
    core._invalidate_settings_cache()
    real_read = core.Path.read_text
    raced = {"done": False}

    def racing_read(self, *a, **k):
        text = real_read(self, *a, **k)            # the reader has the OLD file...
        if self == core.SETTINGS_FILE and not raced["done"]:
            raced["done"] = True
            monkeypatch.setattr(core.Path, "read_text", real_read)
            core._save_settings({"show_all_workspaces": True})   # ...a save lands...
        return text                                 # ...and the reader stores old data

    monkeypatch.setattr(core.Path, "read_text", racing_read)
    core._load_settings_raw()                        # the in-flight reader
    assert raced["done"], "the race was not exercised"
    fresh = core._load_settings_raw()                # the UI's read-back
    assert fresh["show_all_workspaces"] is True, (
        "the read-back served the pre-save file from the cache")
    assert json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8-sig"))["show_all_workspaces"] is True
