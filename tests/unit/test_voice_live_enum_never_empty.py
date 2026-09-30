"""No enum the Live API receives may contain an empty string.

One empty value anywhere in the setup and Gemini Live refuses the connect
outright — close code 1007, "function_declarations[33].parameters
.properties[routine].enum[0]: cannot be empty" — so a single tool that spells
"any" as "" takes voice down, and every other tool with it. Voice mode was
broken on live for exactly this reason: podcast_list and podcast_play offered
`["", "front_page", ...]`.

These tests build the real declarations the live session sends, every tool
included, and walk them. The renderer also strips empty values on the way
through, so a tool added later cannot repeat the outage; both halves are
checked, because the strip is the net and the schemas are the floor.
"""
import pytest

types = pytest.importorskip("google.genai").types
ve = pytest.importorskip("agent_friday.services.voice_engine")


def _schemas(schema, path="", out=None):
    """Every Schema in the tree, with a readable path to each."""
    out = [] if out is None else out
    if schema is None:
        return out
    out.append((path or "<root>", schema))
    for pname, sub in (getattr(schema, "properties", None) or {}).items():
        _schemas(sub, (path + "." if path else "") + pname, out)
    items = getattr(schema, "items", None)
    if items is not None:
        _schemas(items, (path or "") + "[]", out)
    return out


def _declarations():
    """The real tool surface, built the way the live session builds it."""
    tools = ve._build_voice_live_tools(types)
    assert tools, "the voice session must declare tools at all"
    decls = []
    for t in tools:
        decls.extend(getattr(t, "function_declarations", None) or [])
    assert decls, "no function declarations were built"
    return decls


# ── The whole surface, as sent ─────────────────────────────────────────────

def test_no_enum_anywhere_contains_an_empty_string():
    bad = []
    for d in _declarations():
        for path, s in _schemas(getattr(d, "parameters", None)):
            for i, v in enumerate(getattr(s, "enum", None) or []):
                if v == "":
                    bad.append("%s %s enum[%d]" % (d.name, path, i))
    assert not bad, (
        "the Live API refuses the whole setup over these: " + "; ".join(bad))


def test_no_enum_anywhere_is_an_empty_list():
    """An enum present but empty is the same refusal by another route."""
    bad = []
    for d in _declarations():
        for path, s in _schemas(getattr(d, "parameters", None)):
            e = getattr(s, "enum", None)
            if e is not None and list(e) == []:
                bad.append("%s %s" % (d.name, path))
    assert not bad, "empty enum lists: " + "; ".join(bad)


def test_the_surface_is_big_enough_to_be_the_real_one():
    """Guards the test itself: a builder that returned two tools would pass
    the assertions above while proving nothing."""
    decls = _declarations()
    assert len(decls) >= 20, (
        "expected the full tool surface, got %d" % len(decls))
    names = {d.name for d in decls}
    assert {"podcast_list", "podcast_play"} <= names, (
        "the tools that caused the outage must be in what we check")


def test_the_podcast_routine_enums_still_offer_the_real_routines():
    """Removing "" must not have emptied the choice."""
    by_name = {d.name: d for d in _declarations()}
    for tool in ("podcast_list", "podcast_play"):
        found = dict(_schemas(getattr(by_name[tool], "parameters", None)))
        routine = found.get("routine")
        assert routine is not None, tool + " lost its routine property"
        vals = list(getattr(routine, "enum", None) or [])
        assert "front_page" in vals and "briefing" in vals, (tool, vals)
        assert "" not in vals


# ── The net: the renderer strips them whatever a tool declares ─────────────

def test_the_renderer_strips_an_empty_enum_value():
    _type_map = {"string": types.Type.STRING, "integer": types.Type.INTEGER,
                 "number": types.Type.NUMBER, "boolean": types.Type.BOOLEAN}
    rendered = ve._json_schema_to_genai(types, {
        "type": "object",
        "properties": {"routine": {"type": "string",
                                   "enum": ["", "front_page", "briefing"]}},
    }, _type_map, "a_new_tool")
    vals = list(rendered.properties["routine"].enum or [])
    assert vals == ["front_page", "briefing"], vals


def test_the_renderer_drops_an_enum_left_with_nothing():
    _type_map = {"string": types.Type.STRING}
    rendered = ve._json_schema_to_genai(types, {
        "type": "object",
        "properties": {"only_empty": {"type": "string", "enum": [""]}},
    }, _type_map, "a_new_tool")
    prop = rendered.properties["only_empty"]
    assert not (getattr(prop, "enum", None) or []), (
        "an enum with nothing left in it must be dropped, not sent empty")
    assert prop.type == types.Type.STRING, "it stays a plain string"


def test_the_renderer_strips_inside_an_array():
    _type_map = {"string": types.Type.STRING}
    rendered = ve._json_schema_to_genai(types, {
        "type": "object",
        "properties": {"tags": {"type": "array",
                                "items": {"type": "string",
                                          "enum": ["", "a", "b"]}}},
    }, _type_map, "a_new_tool")
    vals = list(rendered.properties["tags"].items.enum or [])
    assert vals == ["a", "b"], vals


def test_the_warning_names_the_tool_and_the_property(caplog):
    _type_map = {"string": types.Type.STRING}
    with caplog.at_level("WARNING"):
        ve._json_schema_to_genai(types, {
            "type": "object",
            "properties": {"routine": {"type": "string", "enum": ["", "x"]}},
        }, _type_map, "a_new_tool")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "a_new_tool" in msgs and "routine" in msgs, msgs


def test_a_clean_enum_is_left_exactly_as_it_was(caplog):
    _type_map = {"string": types.Type.STRING}
    with caplog.at_level("WARNING"):
        rendered = ve._json_schema_to_genai(types, {
            "type": "object",
            "properties": {"routine": {"type": "string",
                                       "enum": ["front_page", "weekly"]}},
        }, _type_map, "fine_tool")
    assert list(rendered.properties["routine"].enum) == ["front_page", "weekly"]
    assert "fine_tool" not in " ".join(r.getMessage() for r in caplog.records), (
        "a clean tool must not be warned about")
