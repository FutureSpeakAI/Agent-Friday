"""Tool arguments are repaired where the meaning is clear and refused where it is not.

The validator is small on purpose; these tests pin the shapes a local model
actually sends and the error text the model needs to fix the call.
"""
from __future__ import annotations

import json

from agent_friday.services import tool_args as ta

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "limit": {"type": "integer"},
        "mode": {"type": "string", "enum": ["head", "tail"]},
        "edits": {"type": "array", "items": {"type": "object",
                                             "properties": {"old": {"type": "string"}},
                                             "required": ["old"]}},
        "dry": {"type": "boolean"},
    },
    "required": ["path"],
}


class TestParse:
    def test_a_json_string_becomes_a_dict(self):
        assert ta.parse('{"path": "a.txt"}') == {"path": "a.txt"}

    def test_an_object_passes_through(self):
        assert ta.parse({"path": "a.txt"}) == {"path": "a.txt"}

    def test_absent_or_blank_means_no_arguments(self):
        assert ta.parse(None) == {}
        assert ta.parse("   ") == {}

    def test_garbage_is_a_failure_not_an_empty_call(self):
        bad = ta.parse('{"path": "a.txt"')
        assert isinstance(bad, ta.ParseFailure)
        msg = bad.message("read_file")
        assert "did not run" in msg and '{"path": "a.txt"' in msg

    def test_a_json_list_is_a_failure(self):
        bad = ta.parse("[1, 2]")
        assert isinstance(bad, ta.ParseFailure)
        assert "array" in bad.error

    def test_json_wrapped_in_a_string_is_unwrapped(self):
        assert ta.parse(json.dumps(json.dumps({"path": "x"}))) == {"path": "x"}


class TestRepair:
    def test_stringified_json_object(self):
        assert ta.repair('{"path": "a"}', SCHEMA) == {"path": "a"}

    def test_bare_scalar_for_a_one_field_schema(self):
        one = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        assert ta.repair("weather", one) == {"query": "weather"}

    def test_single_object_where_a_list_was_expected(self):
        out = ta.repair({"path": "a", "edits": {"old": "x"}}, SCHEMA)
        assert out["edits"] == [{"old": "x"}]

    def test_list_sent_as_a_json_string(self):
        out = ta.repair({"path": "a", "edits": '[{"old": "x"}]'}, SCHEMA)
        assert out["edits"] == [{"old": "x"}]

    def test_numbers_and_booleans_spelled_as_strings(self):
        out = ta.repair({"path": "a", "limit": "20", "dry": "true"}, SCHEMA)
        assert out["limit"] == 20 and out["dry"] is True

    def test_repair_never_invents_fields(self):
        assert ta.repair({"limit": 3}, SCHEMA) == {"limit": 3}


class TestValidate:
    def test_a_good_call_has_no_problems(self):
        assert ta.validate({"path": "a", "limit": 5, "mode": "head"}, SCHEMA) == []

    def test_missing_required_is_named(self):
        assert ta.validate({"limit": 5}, SCHEMA) == ["'path' is required"]

    def test_wrong_type_is_named_with_what_arrived(self):
        [p] = ta.validate({"path": "a", "limit": "lots"}, SCHEMA)
        assert p.startswith("'limit' should be integer") and "string" in p

    def test_enum_is_enforced(self):
        [p] = ta.validate({"path": "a", "mode": "middle"}, SCHEMA)
        assert "head" in p and "tail" in p and "middle" in p

    def test_array_items_are_checked(self):
        probs = ta.validate({"path": "a", "edits": [{"old": "x"}, {"new": "y"}]}, SCHEMA)
        assert probs == ["'edits[1].old' is required"]

    def test_a_boolean_is_not_an_integer(self):
        assert ta.validate({"path": "a", "limit": True}, SCHEMA) != []

    def test_unknown_fields_and_unknown_types_never_block(self):
        odd = {"type": "object", "properties": {"x": {"type": "weird"}}}
        assert ta.validate({"x": 1, "extra": 2}, odd) == []

    def test_no_schema_means_no_check(self):
        assert ta.validate({"anything": 1}, None) == []
        assert ta.validate("not even a dict", None) == []


class TestCheck:
    def test_a_fixable_call_runs_with_the_repaired_arguments(self):
        args, err = ta.check("read_file", '{"path": "a", "limit": "3"}', SCHEMA)
        assert err is None and args == {"path": "a", "limit": 3}

    def test_an_unfixable_call_gets_a_precise_error_and_does_not_run(self):
        args, err = ta.check("read_file", {"limit": 3}, SCHEMA)
        assert "INVALID ARGUMENTS for read_file" in err
        assert "'path' is required" in err
        assert "did not run" in err
        assert "path: string (required)" in err
        assert '{"limit": 3}' in err

    def test_schema_lookup_handles_both_wire_shapes(self):
        anth = [{"name": "a", "input_schema": {"type": "object", "properties": {"p": {}}}}]
        oai = [{"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}}]
        assert ta.schema_for("a", anth) == anth[0]["input_schema"]
        assert ta.schema_for("a", oai) == {"type": "object"}
        assert ta.schema_for("zzz", anth) is None
