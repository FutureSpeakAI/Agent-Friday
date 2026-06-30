"""
agent_friday.core.settings — settings load/save, defaults, and the LRU cache.

Import from here when you need the settings layer without pulling in the full
core module::

    from agent_friday.core.settings import _load_settings, _save_settings, DEFAULT_SETTINGS
"""

from agent_friday.core import (  # noqa: F401
    DEFAULT_SETTINGS,
    SETTINGS_FILE,
    _CAP_FLAT_MAP,
    _SETTINGS_CACHE,
    _SETTINGS_CACHE_TTL,
    _SETTINGS_CACHE_LOCK,
    _invalidate_settings_cache,
    _load_settings,
    _load_settings_raw,
    _save_settings,
    _sync_capability_routing,
    _apply_offline_routing_overlay,
    _settings_system_prefix,
    _load_agent_personality,
    _save_agent_personality,
    _load_self_knowledge,
    _load_voice_demo,
)
