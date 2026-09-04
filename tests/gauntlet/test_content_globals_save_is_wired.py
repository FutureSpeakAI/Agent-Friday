"""Gauntlet finding F14: the Content workspace's Accounts-tab global controls
("Conflict window (hours)" and "Staging host") had a double defect.

1. index.html's `saveGlobals` POSTed a raw `fetch('/api/settings', {body:
   JSON.stringify({content_staging_base_url: ..., content_conflict_window_hours: ...})})`
   -- no `settings` envelope at all. core_routes.py's handler does
   `data.get('settings') or {}`, so this body produced an EMPTY patch every
   time. The UI still flashed "Saved" because `.then()` fired on any 200
   response without checking what the server actually did.
2. Even with the envelope fixed, the keys were flat
   (`content_staging_base_url`) while every reader expects the nested path
   `content.staging_base_url` / `content.conflict_window_hours`
   (content_pipeline.py, services/platforms/instagram.py) -- the third
   instance of the flat-vs-nested settings-key mismatch class in this
   codebase (after vault_local_only and orchestrator_model/capability_routing).

Real consequence: instagram.py holds URL-pull media publishing when
staging_base_url is empty. A user who fills in a staging host and clicks
Save believes they've unblocked publishing; nothing changed, and posts kept
silently holding.

Fixed: saveGlobals in both index.html and ui_parts/app.html now sends
{"settings": {"content": {"staging_base_url": ..., "conflict_window_hours": ...}}}
via apiFetch and only flashes "Saved" when the server echoes status:'ok'
(the JS side isn't unit-testable here, so it's covered by manual/visual
review of the diff). "content" was also added to _DEEP_MERGED_BLOCKS so a
partial {staging_base_url, conflict_window_hours} update doesn't
wholesale-replace the block and silently reset `enabled`/`psi_daily_cap` --
that half IS backend-testable, and is what this probe pins.
"""
from __future__ import annotations

import agent_friday.core as core


class TestContentSettingsDeepMerge:
    def test_partial_content_update_preserves_untouched_sibling_fields(self):
        core._save_settings({"content": {
            "enabled": False,          # a value that differs from the default,
            "psi_daily_cap": 999,      # so a wholesale-replace would be visible
        }})

        core._save_settings({"content": {
            "staging_base_url": "https://stage.example.com",
            "conflict_window_hours": 4,
        }})

        raw = core._load_settings_raw()
        content = raw.get("content") or {}
        assert content.get("staging_base_url") == "https://stage.example.com"
        assert content.get("conflict_window_hours") == 4
        assert content.get("enabled") is False, (
            "a partial content update wiped out 'enabled' -- the Content "
            "workspace's global-controls Save button only ever sends "
            "staging_base_url/conflict_window_hours, so without deep-merge "
            "every OTHER field in the content block silently resets on "
            "every save (findings.jsonl F14)"
        )
        assert content.get("psi_daily_cap") == 999, (
            "a partial content update wiped out 'psi_daily_cap' -- same "
            "wholesale-replace defect as 'enabled' above"
        )
