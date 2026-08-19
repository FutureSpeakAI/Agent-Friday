"""One connector's JSON must not be able to silence every cloud conversation.

Measured 2026-08-18 on Stephen's machine: after the Higgsfield connector
registered 86 tools, EVERY Anthropic turn came back "[Friday offline]" with

    tools.90.custom.input_schema: input_schema does not support
    oneOf, allOf, or anyOf at the top level

The API rejects the whole request, not the offending tool, and names an index
rather than a name — so the symptom was "cloud chat is dead" with no way to
tell which of 112 tools did it. These tests pin the repair and, as much,
the requirement that the capability SURVIVES it.
"""
import pytest

from agent_friday.services.agent import _mcp_normalize_schema as norm


def test_a_top_level_anyof_is_flattened_into_one_object_schema():
    schema = {
        "anyOf": [
            {"properties": {"url": {"type": "string"}}, "required": ["url"]},
            {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        ]
    }
    out = norm(schema, "mcp_x_fetch")
    assert "anyOf" not in out
    assert out["type"] == "object"
    assert set(out["properties"]) == {"url", "path"}


def test_the_tool_is_kept_not_dropped():
    """Dropping it would be easy and would silently remove a capability."""
    out = norm({"oneOf": [{"properties": {"a": {"type": "string"}}}]}, "t")
    assert out["properties"]["a"] == {"type": "string"}


def test_a_field_optional_in_any_branch_is_not_required():
    """If a caller can legally omit it, calling it required breaks valid calls."""
    schema = {"anyOf": [
        {"properties": {"id": {"type": "string"}, "q": {"type": "string"}},
         "required": ["id", "q"]},
        {"properties": {"id": {"type": "string"}}, "required": ["id"]},
    ]}
    out = norm(schema, "t")
    assert out["required"] == ["id"]


def test_allof_unions_properties():
    schema = {"allOf": [
        {"properties": {"a": {"type": "string"}}, "required": ["a"]},
        {"properties": {"b": {"type": "number"}}, "required": ["b"]},
    ]}
    out = norm(schema, "t")
    assert set(out["properties"]) == {"a", "b"}
    assert "allOf" not in out


def test_a_clean_schema_is_untouched_apart_from_a_missing_type():
    schema = {"type": "object", "properties": {"q": {"type": "string"}},
              "required": ["q"]}
    assert norm(dict(schema), "t") == schema


def test_a_schema_without_a_type_gets_one():
    out = norm({"properties": {"q": {"type": "string"}}}, "t")
    assert out["type"] == "object"


def test_a_nested_combinator_is_left_alone():
    """The API only objects at the TOP level; rewriting deeper would lose meaning."""
    schema = {"type": "object",
              "properties": {"target": {"anyOf": [{"type": "string"},
                                                  {"type": "number"}]}}}
    out = norm(schema, "t")
    assert out["properties"]["target"]["anyOf"]


@pytest.mark.parametrize("junk", [None, [], "nope", 3])
def test_junk_never_raises(junk):
    out = norm(junk, "t")
    assert out["type"] == "object"


def test_the_normalisation_is_announced(capsys):
    """A silent rewrite of someone's schema is its own kind of defect."""
    norm({"anyOf": [{"properties": {"a": {"type": "string"}}}]}, "mcp_hf_generate")
    assert "mcp_hf_generate" in capsys.readouterr().out
