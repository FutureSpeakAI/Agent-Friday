"""Regression: spoken workspace names must resolve to the REAL dock ids.

For months 'settings' resolved to the System workspace — Friday would say
"opening settings" while the UI opened System, and the voice tool's stale
hard-coded list didn't even offer settings/marketplace as targets.
"""
import re
from pathlib import Path

import pytest

from agent_friday.services.agent import (
    _WORKSPACE_ALIASES, _WORKSPACE_LABELS, _resolve_workspace)

_INDEX_HTML = Path(__file__).resolve().parents[2] / "index.html"


def _dock_ids() -> set:
    """The dock ids, READ FROM index.html rather than copied.

    This list used to be a hand-maintained literal here, and it drifted: the
    Workflows dock shipped in index.html, `_WORKSPACE_ALIASES` gained the
    aliases that reach it, and this copy never learned about it — so a
    correct alias for a real dock read as "targets a nonexistent workspace"
    and three separate sessions spent time proving the failure wasn't theirs.

    index.html is the UI source of truth (`DOCK_GROUPS`), so ask it. If the
    parse ever stops matching, this fails loudly rather than silently
    validating against an empty set.
    """
    text = _INDEX_HTML.read_text(encoding="utf-8", errors="replace")
    start = text.find("const DOCK_GROUPS")
    assert start != -1, "DOCK_GROUPS not found in index.html — parser is stale"
    # The dock array ends where the next top-level `const ` declaration begins.
    end = text.find("\nconst ", start + 1)
    block = text[start:end if end != -1 else start + 20000]
    ids = set(re.findall(r"\bid:\s*'([a-z0-9_-]+)'", block))
    assert len(ids) > 10, f"parsed only {len(ids)} dock ids — parser is stale"
    return ids


DOCK_IDS = _dock_ids()


def test_settings_resolves_to_settings_not_system():
    assert _resolve_workspace('settings') == 'settings'
    assert _resolve_workspace('the settings') == 'settings'
    assert _resolve_workspace('settings menu') == 'settings'
    assert _resolve_workspace('preferences') == 'settings'
    assert _resolve_workspace('options') == 'settings'
    assert _resolve_workspace('system settings') == 'settings'


def test_system_still_resolves_to_system():
    assert _resolve_workspace('system') == 'system'
    assert _resolve_workspace('system health') == 'system'


def test_marketplace_is_navigable():
    assert _resolve_workspace('marketplace') == 'marketplace'
    assert _resolve_workspace('the marketplace') == 'marketplace'
    assert _resolve_workspace('skill store') == 'marketplace'


def test_every_alias_targets_a_real_dock_id():
    bogus = {v for v in _WORKSPACE_ALIASES.values()} - DOCK_IDS
    assert not bogus, f"aliases target nonexistent workspaces: {bogus}"


def test_every_target_has_a_display_label():
    missing = {v for v in _WORKSPACE_ALIASES.values()} - set(_WORKSPACE_LABELS)
    assert not missing, f"targets missing display labels: {missing}"


def test_voice_tool_description_lists_current_ids():
    from agent_friday.services.voice_engine import (
        _VOICE_LIVE_TOOLS, _navigate_tool_description)
    nav = next(t for t in _VOICE_LIVE_TOOLS if t[0] == 'navigate_workspace')
    desc = _navigate_tool_description(nav[1])
    assert '{workspace_ids}' not in desc
    assert 'settings' in desc and 'marketplace' in desc
