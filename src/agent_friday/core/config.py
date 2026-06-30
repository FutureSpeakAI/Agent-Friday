"""
agent_friday.core.config — paths, environment bootstrap, and runtime constants.

Import from here when you only need path/config values and don't need the full
core module (Flask app, auth, settings, etc.) to be initialised.  The canonical
definitions live in core/__init__.py; this module re-exports them so callers can
be explicit about what they depend on::

    from agent_friday.core.config import FRIDAY_DIR, HOME, _RES_DIR
"""

from agent_friday.core import (  # noqa: F401
    HOME,
    FRIDAY_DIR,
    WIKI_DIR,
    WIKI_PROFESSIONAL_DIR,
    CREATIONS_DIR,
    DAILY_CREATIONS_DIR,
    JOB_SEARCH_FILE,
    CONTEXT_LOG_DIR,
    DECISION_BOM_FILE,
    SETTINGS_FILE,
    AGENT_PERSONALITY_FILE,
    CHAT_HISTORY_FILE,
    TEMP_AUDIO_DIR,
    VIBE_LOG_DIR,
    OFFLINE_QUEUE_DIR,
    _RES_DIR,
    SELF_MD_PATH,
    VOICE_DEMO_MD_PATH,
    _FRESH_INSTALL,
    _TESTING,
    _POPEN_FLAGS,
    _bootstrap_env_from_launch_scripts,
)
