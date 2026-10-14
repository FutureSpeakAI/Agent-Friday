"""Every registered tool belongs to a group, and its annotations follow its ring.

Same pattern as test_every_tool_declares_a_ring.py: the registry is discovered
from every place a callable tool is registered, and an ungrouped one fails by
name. Annotations are derived from TOOL_RINGS plus a reviewed override list in
resources/tool_groups.json; they describe a tool and grant nothing.
"""
from __future__ import annotations

import pytest

from agent_friday.services import tool_catalogue as tc


@pytest.fixture(scope="module")
def registry():
    from agent_friday.services import agent
    return agent


def _registered_names(agent):
    from agent_friday.services import voice_engine
    names = {t.get("name") for t in agent.CLAUDE_TOOLS}
    for tools in agent.WORKSPACE_TOOLS.values():
        names |= {t.get("name") for t in tools}
    names |= set(agent.CLAUDE_TOOL_HANDLERS)
    names |= set(agent.TOOL_RINGS)
    names |= {t[0] for t in voice_engine._VOICE_LIVE_TOOLS}
    return {n for n in names if n}


def test_the_discovery_sees_the_registry_it_claims_to(registry):
    names = _registered_names(registry)
    assert len(names) > 150
    assert {"search_web", "send_to_session", "ask_friday", "codebase_edit"} <= names


def test_every_registered_tool_has_a_group(registry):
    missing = sorted(n for n in _registered_names(registry) if not tc.groups_of(n))
    assert not missing, "tools with no group in tool_groups.json: %s" % ", ".join(missing)


def test_every_group_named_is_declared():
    data = tc.load_groups()
    declared = set(data["groups"])
    used = {g for gs in data["tools"].values() for g in gs}
    used |= set(data["prefix_groups"].values())
    assert used <= declared, sorted(used - declared)


def test_a_tool_has_one_primary_and_at_most_one_secondary():
    for name, gs in tc.load_groups()["tools"].items():
        assert 1 <= len(gs) <= 2, name
        assert len(set(gs)) == len(gs), name


def _withheld(agent):
    """Tools the capability preflight removed at import on this machine: the
    computer-control tools where pyautogui is not installed (it ships in the
    Windows extra only) or in kiosk mode. They are declared, then withheld."""
    return set(agent._WITHHELD_TOOLS)


def test_a_withheld_tool_is_absent_and_keeps_its_declared_ring(registry):
    from agent_friday.services import capability_preflight as cp
    withheld = _withheld(registry)
    assert withheld == set(cp.missing_tools())
    assert not withheld & _registered_names(registry), "withheld but still registered"
    assert set(registry._WITHHELD_RINGS) == withheld
    groups = tc.load_groups()["tools"]
    for name in withheld:
        assert name in groups, "a withheld tool still needs its group: " + name


def test_the_json_names_no_tool_that_is_not_registered(registry):
    # A name in the JSON is registered here, or declared and withheld here by the
    # capability preflight; anything else is stale on every machine.
    stale = sorted(set(tc.load_groups()["tools"]) - _registered_names(registry) - _withheld(registry))
    assert not stale, "groups for tools that are not registered: %s" % ", ".join(stale)


def test_the_spec_groups_exist():
    spec = {"mail", "calendar", "files", "web", "wiki_knowledge", "media", "system_desktop",
            "workspace", "news", "career", "creative", "code", "people_trust", "settings",
            "workflows", "phone"}
    assert spec <= set(tc.load_groups()["groups"])


def test_a_connector_tool_takes_the_connectors_group():
    assert tc.group_of("mcp_example_server_do_thing") == "connectors"
    assert tc.group_of("not_a_tool_anywhere") is None


def test_tools_in_group_finds_primary_and_secondary():
    assert "search_email" in tc.tools_in_group("mail")
    assert "open_url" in tc.tools_in_group("system_desktop")   # secondary
    assert "open_url" in tc.tools_in_group("web")              # primary


# ── annotations ──────────────────────────────────────────────────────────────

HINTS = ("readOnlyHint", "destructiveHint", "openWorldHint")


def test_annotations_are_derived_from_the_ring():
    probe = "zz_unreviewed_probe"
    assert tc.annotations(probe, ring=0) == {
        "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
    assert tc.annotations(probe, ring=1) == {
        "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
    assert tc.annotations(probe, ring=2) == {
        "readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}
    assert tc.annotations(probe, ring=3) == {
        "readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}


def test_every_tool_is_annotated_consistently_with_its_ring(registry):
    bad = []
    for name in sorted(_registered_names(registry)):
        ring = registry.TOOL_RINGS.get(name, 2)
        a = tc.annotations(name)
        if set(a) != set(HINTS) or not all(isinstance(v, bool) for v in a.values()):
            bad.append((name, "shape", a))
            continue
        if a["readOnlyHint"] and a["destructiveHint"]:
            bad.append((name, "read-only and destructive", a))
        if ring == 0 and not a["readOnlyHint"] and name not in tc.load_groups()["annotation_overrides"]:
            bad.append((name, "ring 0 not read-only", a))
        if ring == 0 and a["destructiveHint"]:
            bad.append((name, "ring 0 destructive", a))
        if ring == 3 and not a["destructiveHint"] and not a["readOnlyHint"]:
            bad.append((name, "ring 3 neither destructive nor read-only", a))
        if ring >= 1 and a["readOnlyHint"] and name not in tc.load_groups()["annotation_overrides"]:
            bad.append((name, "writer marked read-only without a reviewed override", a))
    assert not bad, bad


def test_annotation_overrides_name_registered_tools_and_change_something(registry):
    names = _registered_names(registry) | _withheld(registry)
    data = tc.load_groups()
    for name, ov in data["annotation_overrides"].items():
        assert name in names, "override for an unregistered tool: " + name
        assert set(ov) <= set(HINTS), name
        ring = tc._ring_of(name)
        derived = tc.annotations("zz_unreviewed_probe", ring=ring)
        assert any(derived[k] != v for k, v in ov.items()), "no-op override: " + name


def test_known_tools_carry_the_hints_a_reviewer_expects():
    assert tc.annotations("read_wiki")["readOnlyHint"] is True
    assert tc.annotations("search_web") == {
        "readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
    assert tc.annotations("delete_task")["destructiveHint"] is True
    assert tc.annotations("screenshot")["readOnlyHint"] is True
    assert tc.annotations("click")["destructiveHint"] is True
    assert tc.annotations("draft_email")["openWorldHint"] is False
