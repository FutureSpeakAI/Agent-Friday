"""Every tool Friday can run declares its risk ring.

`_execute_tool` falls back to ring 2 for a name TOOL_RINGS does not know. That
default keeps an undeclared tool gated, but it is a ring nobody chose: a new
tool inherits it silently and its real risk is never written down. So the
registry is discovered here, from every place a callable tool is registered,
and an undeclared one fails by name.
"""
from __future__ import annotations

import pytest

#: Voice tools whose dispatcher governs them under another tool's name.
VOICE_ALIASES = {"navigate_workspace": "navigate"}

RINGS = {0, 1, 2, 3}


@pytest.fixture(scope="module")
def registry():
    from agent_friday.services import agent
    return agent


def _undeclared(names, rings):
    return sorted(n for n in names if n not in rings)


def test_every_handler_declares_a_ring(registry):
    missing = _undeclared(registry.CLAUDE_TOOL_HANDLERS, registry.TOOL_RINGS)
    assert not missing, "tools with no declared ring: %s" % ", ".join(missing)


def test_every_schema_the_model_can_be_sent_declares_a_ring(registry):
    names = {t.get("name") for t in registry.CLAUDE_TOOLS}
    for tools in registry.WORKSPACE_TOOLS.values():
        names |= {t.get("name") for t in tools}
    missing = _undeclared(names - {None}, registry.TOOL_RINGS)
    assert not missing, "schemas with no declared ring: %s" % ", ".join(missing)


def test_every_voice_tool_declares_a_ring(registry):
    from agent_friday.services import voice_engine
    names = {VOICE_ALIASES.get(t[0], t[0]) for t in voice_engine._VOICE_LIVE_TOOLS}
    missing = _undeclared(names, registry.TOOL_RINGS)
    assert not missing, "voice tools with no declared ring: %s" % ", ".join(missing)


def test_every_declared_ring_is_a_real_ring(registry):
    bad = {n: r for n, r in registry.TOOL_RINGS.items() if r not in RINGS}
    assert not bad, bad


def test_the_discovery_sees_the_registry_it_claims_to(registry):
    """A discovery that finds nothing passes for the wrong reason."""
    assert len(registry.CLAUDE_TOOL_HANDLERS) > 100
    assert "switch_model" in registry.CLAUDE_TOOL_HANDLERS
    assert registry.TOOL_RINGS["switch_model"] == 2
