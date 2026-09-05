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
via apiFetch and only flashes "Saved" when the server echoes status:'ok'.
"content" was also added to _DEEP_MERGED_BLOCKS so a partial
{staging_base_url, conflict_window_hours} update doesn't wholesale-replace
the block and silently reset `enabled`/`psi_daily_cap` -- that half is
backend-testable and is what TestContentSettingsDeepMerge below pins.

CORRECTION (2026-09-04, flagged by Stephen's independent cold
re-verification): this probe originally covered ONLY the backend
deep-merge hardening above -- the docstring itself admitted "the JS side
isn't unit-testable here, so it's covered by manual/visual review of the
diff." That left the actual defect (saveGlobals's own request body shape
and its unconditional 'Saved' flash) with zero automated coverage; the
deep-merge fix alone cannot fail if saveGlobals regresses back to a flat,
unwrapped body, since nothing would ever call _save_settings with the
correct shape to exercise it. TestSaveGlobalsRequestShape below extracts
saveGlobals's actual source text from both index.html and
ui_parts/app.html (this codebase's established pattern for pinning
embedded-JS behavior source-textually, e.g.
test_app_html_missing_high_severity_components.py) and asserts on the
real request body shape and the response-status gate directly, in both
files.
"""
from __future__ import annotations
import re
from pathlib import Path

import agent_friday.core as core

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX_HTML = _REPO_ROOT / "index.html"
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"


def _save_globals_source(html_path: Path) -> str:
    text = html_path.read_text(encoding="utf-8")
    i_start = text.index("const saveGlobals")
    # Both files terminate the statement with `flash('Save failed'));` --
    # slice through that, not just to the next semicolon, since the
    # statement itself contains several inner semicolons/braces.
    i_end = text.index("flash('Save failed'));", i_start) + len("flash('Save failed'));")
    return text[i_start:i_end]


def _collapsed(src: str) -> str:
    """Whitespace-insensitive comparison -- index.html is Babel-formatted
    (spaced), ui_parts/app.html is hand-minified (no spaces)."""
    return re.sub(r"\s+", "", src)


class TestSaveGlobalsRequestShape:
    """Pins the actual fix (saveGlobals's request body + response check),
    not just the backend deep-merge hardening around it."""

    def test_index_html_sends_the_nested_wrapped_envelope(self):
        src = _collapsed(_save_globals_source(_INDEX_HTML))
        assert "settings:{content:{staging_base_url:" in src, (
            "index.html's saveGlobals no longer sends the nested "
            "{settings:{content:{staging_base_url:...}}} envelope -- "
            "core_routes.py's handler does data.get('settings') or {}, so "
            "a flat/unwrapped body produces an EMPTY patch every save "
            "(findings.jsonl F14)"
        )
        assert "conflict_window_hours:" in src

    def test_index_html_only_flashes_saved_when_the_server_confirms_ok(self):
        src = _collapsed(_save_globals_source(_INDEX_HTML))
        assert "d.status==='ok'?'Saved':'Savefailed'" in src, (
            "index.html's saveGlobals flashes 'Saved' unconditionally on "
            "any 200 response instead of checking the server actually "
            "applied the patch (d.status === 'ok') -- a user can be told "
            "'Saved' when the server silently did nothing"
        )

    def test_app_html_mirror_matches_the_same_fix(self):
        src = _collapsed(_save_globals_source(_APP_HTML))
        assert "settings:{content:{staging_base_url:" in src, (
            "ui_parts/app.html's saveGlobals mirror is out of sync with "
            "index.html's fix -- still sends the old, unwrapped/flat body"
        )
        assert "d.status==='ok'?'Saved':'Savefailed'" in src, (
            "ui_parts/app.html's saveGlobals mirror is out of sync with "
            "index.html's fix -- still flashes 'Saved' unconditionally"
        )


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
