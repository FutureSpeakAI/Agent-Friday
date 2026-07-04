"""Regression: spoken workspace names must resolve to the REAL dock ids.

For months 'settings' resolved to the System workspace — Friday would say
"opening settings" while the UI opened System, and the voice tool's stale
hard-coded list didn't even offer settings/marketplace as targets.
"""
from agent_friday.services.agent import (
    _WORKSPACE_ALIASES, _WORKSPACE_LABELS, _resolve_workspace)

# The dock ids as defined in ui_parts/app.html.
DOCK_IDS = {
    'home', 'news', 'messages', 'calendar', 'family', 'health', 'finance',
    'career', 'contacts', 'code', 'futurespeak', 'draft', 'content',
    'wiki', 'trust', 'studio', 'marketplace', 'system', 'settings',
}


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
