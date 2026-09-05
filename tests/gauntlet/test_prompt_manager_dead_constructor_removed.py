"""Gauntlet finding F59 (claim-corpus sweep, 2026-09-04):
prompt_manager.create_default_manager() had zero callers anywhere in the
codebase and its own body never fulfilled its own docstring ("standard
Friday segments pre-registered" -- it registered none). The real per-
request system prompt (model_router._get_friday_system_prompt()) never
used PromptManager at all; the only live consumer is routes/platform.py's
debug/preview endpoints, which build their own PromptManager directly.

This probe proves the dead constructor is gone and the real, live path
(the preview route's own PromptManager usage) still works untouched.
"""
from __future__ import annotations

import agent_friday.services.prompt_manager as prompt_manager


def test_create_default_manager_no_longer_exists():
    assert not hasattr(prompt_manager, "create_default_manager"), (
        "create_default_manager() was removed as dead code (F59) but has "
        "reappeared -- if it's back, it must actually register segments "
        "and have a real caller, not just restore the old no-op"
    )


def test_prompt_manager_class_and_segment_keys_still_live_for_the_preview_route():
    pm = prompt_manager.PromptManager(total_budget=100)
    pm.set("base_personality", "You are Friday.",
           priority=prompt_manager.SEGMENT_KEYS["base_personality"])
    pm.set("hints", "Be concise.", priority=prompt_manager.SEGMENT_KEYS["hints"])

    built = pm.build()

    assert "You are Friday." in built
    assert "Be concise." in built
    assert built.index("You are Friday.") < built.index("Be concise.")
