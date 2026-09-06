"""Gauntlet finding: the pause-forecast "don't warn me again" escape hatch
(index.html's seat-pause confirmation dialog) POSTs `{"settings":
{"pause_warnings_off": true}}` -- correctly wrapped, so layer 1 of the
settings pipeline (the {settings: ...} envelope) is fine. But
`pause_warnings_off` had no entry in DEFAULT_SETTINGS, so
`_load_settings_raw()`'s whitelist (`{k: v for k, v in data.items() if k in
DEFAULT_SETTINGS}`) silently dropped it on every save -- the exact defect
class already fixed once this session for `knowledge_graph`. The dialog's
client-side optimistic state made it look like the "don't ask again" choice
stuck for the rest of that browser tab; it silently reverted on the next
settings read (reload, restart).

Mirrors tests/unit/test_kg_indexer.py's TestKnowledgeGraphSettingsPersist
pattern for the same defect class.
"""
from __future__ import annotations

import agent_friday.core as core


class TestPauseWarningsOffSurvivesSave:
    def test_pause_warnings_off_survives_the_settings_whitelist(self):
        core._save_settings({"pause_warnings_off": True})
        raw = core._load_settings_raw()
        assert raw.get("pause_warnings_off") is True, (
            "pause_warnings_off did not survive _load_settings_raw()'s "
            "whitelist -- it has no DEFAULT_SETTINGS entry, so the "
            "'don't warn me again' choice silently reverts on the next "
            "settings read (reload/restart) even though the UI showed it "
            "as saved"
        )

    def test_default_is_false(self):
        """No-op-shaped sanity check: the key's default must be False (the
        warning is on by default) -- adding the entry must not silently
        flip existing installs to 'never warn'."""
        assert core.DEFAULT_SETTINGS.get("pause_warnings_off") is False
