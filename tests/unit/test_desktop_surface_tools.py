"""Simple Home and native Salon operations keep text/voice parity.

The declarations are rendered through the real Live schema converter. The
voice runner reaches actual local handlers through its governed dispatch seam;
all durable stores are isolated and no voice session or model is started.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import desktop_surface_tools as tools


@pytest.fixture
def stores(tmp_path, monkeypatch):
    from agent_friday.services import desktop_cards, desktop_bus, off_record
    from agent_friday.services import workspace_studio, boot_guard

    monkeypatch.setattr(desktop_cards, "CARDS_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(workspace_studio, "WS_STUDIO_DIR", tmp_path / "studio")
    monkeypatch.setattr(off_record, "active", lambda: False)
    monkeypatch.setattr(boot_guard, "safe_mode", lambda: False)
    events = []
    monkeypatch.setattr(desktop_bus, "broadcast", lambda message, **kw: events.append(message))
    return desktop_cards, workspace_studio, events


def _card(title="Continue your draft"):
    return {"id": "draft", "title": title, "body": "A local card.", "priority": 70,
            "actions": [{"label": "Open Library", "workspace": "library"}]}


def test_card_tool_writes_updates_lists_and_removes_one_stable_card(stores):
    cards, _studio, events = stores
    saved = json.loads(tools.home_cards({"action": "save", "card": _card()}))
    assert saved["saved"] is True and saved["id"] == "draft"
    created = cards.list_cards()[0]["created_at"]
    tools.home_cards({"action": "save", "card": _card("Pick up the draft")})
    listed = json.loads(tools.home_cards({"action": "list"}))["cards"]
    assert len(listed) == 1 and listed[0]["title"] == "Pick up the draft"
    assert listed[0]["created_at"] == created
    assert json.loads(tools.home_cards({"action": "remove", "id": "draft"}))["removed"] is True
    assert json.loads(tools.home_cards({"action": "remove", "id": "draft"}))["removed"] is False
    assert cards.list_cards() == []
    assert events == [{"type": "desktop_cards_changed"}] * 3


@pytest.mark.parametrize("patch", [
    {"actions": [{"label": "Run", "workspace": "library", "onclick": "run()"}]},
    {"actions": [{"label": "Browse", "workspace": "https://example.com"}]},
    {"priority": True}, {"id": "../escape"}, {"html": "<script>run()</script>"},
])
def test_card_tool_refuses_invalid_actions_without_creating_a_store(stores, patch):
    cards, _studio, _events = stores
    out = tools.home_cards({"action": "save", "card": {**_card(), **patch}})
    assert out.startswith("home_cards error:")
    assert not cards.CARDS_PATH.exists()


def test_card_write_failure_never_confirms_success(stores, monkeypatch):
    cards, _studio, events = stores
    def fail(_cards):
        raise OSError("unavailable")
    monkeypatch.setattr(cards, "_write", fail)
    result = tools.home_cards({"action": "save", "card": _card()})
    assert "No success was confirmed" in result
    assert not events


def test_off_record_blocks_card_and_salon_persistence(stores, monkeypatch):
    from agent_friday.services import off_record
    cards, studio, events = stores
    monkeypatch.setattr(off_record, "active", lambda: True)
    assert "Off the record" in tools.home_cards({"action": "save", "card": _card()})
    assert "Off the record" in tools.customize_workspace({"workspace": "library", "patch": {"note": "Draft"}})
    assert not cards.CARDS_PATH.exists()
    assert not studio.WS_STUDIO_DIR.exists()
    assert not events


def test_salon_change_is_durable_notified_and_reversible(stores):
    _cards, studio, events = stores
    patch = {"note": "Keep the current draft nearby", "density": "compact", "accent": "#00d4ff"}
    saved = json.loads(tools.customize_workspace({"workspace": "library", "patch": patch}))
    assert saved["saved"] is True
    assert studio.load_ws_doc("library")["customization"] == patch
    assert events[-1] == {"type": "workspace_customizations_changed", "workspace": "library"}
    assert studio.revert_customization("library", saved["revert_to"])
    assert studio.load_ws_doc("library")["customization"] == {}


@pytest.mark.parametrize("patch", [{}, {"css": "body {display:none}"}, {"model_routing": {}}, {"hidden": [".approval"]},
                                  {"note": "A valid field", "unexpected": "refuse the whole patch"}])
def test_salon_tool_exposes_only_its_declared_presentation_fields(stores, patch):
    _cards, studio, events = stores
    assert "Provide a note" in tools.customize_workspace({"workspace": "library", "patch": patch})
    assert not studio.WS_STUDIO_DIR.exists() and not events


def test_salon_refusal_and_store_failure_do_not_claim_saved(stores, monkeypatch):
    _cards, studio, _events = stores
    monkeypatch.setattr(studio, "apply_customization", lambda *_: ({}, None))
    assert tools.customize_workspace({"workspace": "library", "patch": {"note": "x"}}) == "No workspace change was saved."
    def fail(*_):
        raise OSError("unavailable")
    monkeypatch.setattr(studio, "apply_customization", fail)
    assert "No success was confirmed" in tools.customize_workspace({"workspace": "library", "patch": {"note": "x"}})


def test_new_tools_have_one_text_registration_and_a_voice_route():
    from agent_friday.services import agent, voice_engine
    from agent_friday.governance import action_gate
    all_specs = agent.CLAUDE_TOOLS + [entry for group in agent.WORKSPACE_TOOLS.values() for entry in group]
    for name in tools.HANDLERS:
        assert sum(entry.get("name") == name for entry in all_specs) == 1
        assert agent.CLAUDE_TOOL_HANDLERS[name] is tools.HANDLERS[name]
        assert agent.TOOL_RINGS[name] == 1
        assert name in voice_engine._VOICE_SHARED_TOOLS
        assert name in action_gate.INTERNAL_TOOLS


def test_live_declarations_preserve_cards_actions_and_nullable_customization():
    types = pytest.importorskip("google.genai.types")
    from agent_friday.services import voice_engine
    declarations = {decl.name: decl for tool in voice_engine._build_voice_live_tools(types)
                    for decl in tool.function_declarations or []}
    assert {"home_cards", "customize_workspace", "revert_workspace", "list_workspace_history"} <= declarations.keys()
    card = declarations["home_cards"].parameters.properties["card"]
    assert card.type == types.Type.OBJECT
    assert set(card.required) == {"id", "title"}
    assert card.properties["priority"].minimum == 0 and card.properties["priority"].maximum == 100
    actions = card.properties["actions"]
    assert actions.type == types.Type.ARRAY and actions.max_items == 3
    assert set(actions.items.required) == {"label"}
    assert set(actions.items.properties["view"].enum) == {"projects", "activity"}
    assert actions.items.additional_properties is None
    patch = declarations["customize_workspace"].parameters.properties["patch"]
    assert patch.properties["note"].nullable is True
    assert patch.properties["actions"].nullable is True
    assert patch.properties["actions"].items.type == types.Type.OBJECT
    assert patch.properties["actions"].max_items == 8
    density = patch.properties["density"]
    assert density.nullable is True and set(density.enum) == {"compact", "comfortable"}
    assert "None" not in density.enum


def test_schema_converter_still_refuses_free_form_or_heterogeneous_shapes():
    types = pytest.importorskip("google.genai.types")
    from agent_friday.services import voice_engine
    mapping = {"string": types.Type.STRING}
    for leaf in ({"type": "object"}, {"type": ["string", "integer"]}, {"type": ["string", "integer", "null"]},
                 {"type": "object", "properties": {"label": {"type": "string"}}, "additionalProperties": True},
                 {"type": "object", "properties": {"label": {"type": "string"}}, "additionalProperties": {"type": "string"}}):
        with pytest.raises(ValueError):
            voice_engine._json_schema_to_genai(types, {"type": "object", "properties": {"bad": leaf}}, mapping, "invalid")


def _live_wire_declarations():
    import asyncio
    from types import SimpleNamespace

    types = pytest.importorskip("google.genai.types")
    from google.genai import _common, _live_converters, live
    from agent_friday.services import voice_engine

    # Match live.connect's setup conversion without credentials or a transport.
    api = SimpleNamespace(vertexai=False)
    model = "models/test-live"
    config = asyncio.run(live._t_live_connect_config(
        api, types.LiveConnectConfig(tools=voice_engine._build_voice_live_tools(types))))
    request = _common.convert_to_dict(_live_converters._LiveConnectParameters_to_mldev(
        api_client=api,
        from_object=types.LiveConnectParameters(model=model, config=config).model_dump(exclude_none=True)))
    del request["config"]
    request = _common.encode_unserializable_types(request)
    request["setup"]["model"] = model
    wire = json.loads(json.dumps(request))

    return {decl["name"]: decl for tool in wire["setup"]["tools"]
            for decl in tool.get("functionDeclarations", tool.get("function_declarations", []))}


def test_live_wire_schemas_use_server_supported_fields():
    # FunctionDeclaration.parameters uses the server's typed OpenAPI subset,
    # not parametersJsonSchema or every field accepted by the Python SDK.
    # https://ai.google.dev/api/generate-content#Schema
    allowed = {"type", "format", "title", "description", "nullable", "enum",
               "maxItems", "max_items", "minItems", "min_items", "properties",
               "required", "minProperties", "min_properties", "maxProperties",
               "max_properties", "minLength", "min_length", "maxLength", "max_length",
               "pattern", "example", "anyOf", "any_of", "propertyOrdering",
               "property_ordering", "default", "items", "minimum", "maximum"}

    def check_schema(schema, path):
        assert isinstance(schema, dict), path
        assert not (set(schema) - allowed), (path, sorted(set(schema) - allowed))
        for name, child in schema.get("properties", {}).items():
            check_schema(child, path + "." + name)
        if "items" in schema:
            check_schema(schema["items"], path + "[]")
        for index, child in enumerate(schema.get("anyOf", schema.get("any_of", []))):
            check_schema(child, path + ".anyOf[%d]" % index)

    declarations = _live_wire_declarations()
    assert {"home_cards", "customize_workspace", "revert_workspace", "list_workspace_history"} <= declarations.keys()
    for name, declaration in declarations.items():
        if "parameters" in declaration:
            check_schema(declaration["parameters"], name)


def test_live_wire_json_keeps_nested_cards_and_nullable_workspace_fields():
    declarations = _live_wire_declarations()

    # SDK releases may spell schema fields in proto or JSON form. Check values
    # after encoding so model construction alone cannot satisfy the contract.
    def field(obj, camel, snake):
        return obj[camel] if camel in obj else obj[snake]

    assert {"home_cards", "customize_workspace", "revert_workspace", "list_workspace_history"} <= declarations.keys()
    home = declarations["home_cards"]["parameters"]
    assert home["required"] == ["action"]
    card = home["properties"]["card"]
    assert card["type"] == "OBJECT" and set(card["required"]) == {"id", "title"}
    assert {"id", "title", "body", "priority", "actions"} == card["properties"].keys()
    assert "additionalProperties" not in card and "additional_properties" not in card
    actions = card["properties"]["actions"]
    assert actions["type"] == "ARRAY" and int(field(actions, "maxItems", "max_items")) == 3
    assert actions["items"]["type"] == "OBJECT"
    assert set(actions["items"]["required"]) == {"label"}
    assert set(actions["items"]["properties"]) == {"label", "workspace", "view"}
    assert set(actions["items"]["properties"]["view"]["enum"]) == {"projects", "activity"}
    assert "additionalProperties" not in actions["items"] and "additional_properties" not in actions["items"]

    change = home["properties"]["change"]
    assert set(change["required"]) == {"op", "expected_revision"}
    assert change["properties"]["expected_revision"]["minimum"] == 0
    assert set(change["properties"]["source"]["required"]) == {"kind", "id"}
    assert change["properties"]["card"] == card
    assert int(field(change["properties"]["ids"], "maxItems", "max_items")) == 256
    assert {"track", "stop_tracking", "dismiss", "restore", "snooze", "reorder"} <= set(change["properties"]["op"]["enum"])

    customization = declarations["customize_workspace"]["parameters"]
    assert set(customization["required"]) == {"workspace", "patch"}
    patch = customization["properties"]["patch"]
    assert patch["type"] == "OBJECT"
    assert "additionalProperties" not in patch and "additional_properties" not in patch
    for name in ("note", "accent", "density", "actions"):
        assert patch["properties"][name]["nullable"] is True
    density = patch["properties"]["density"]
    assert density["type"] == "STRING" and set(density["enum"]) == {"compact", "comfortable"}
    quick_actions = patch["properties"]["actions"]
    assert quick_actions["type"] == "ARRAY" and int(field(quick_actions, "maxItems", "max_items")) == 8
    assert quick_actions["items"]["type"] == "OBJECT"
    assert set(quick_actions["items"]["required"]) == {"label", "prompt"}
    assert set(quick_actions["items"]["properties"]) == {"label", "prompt"}

    # The provider rendering does not weaken the shared tool contract.
    specs = {spec["name"]: spec["input_schema"] for spec in tools.TOOLS}
    source_card = specs["home_cards"]["properties"]["card"]
    assert source_card["additionalProperties"] is False
    assert source_card["properties"]["actions"]["items"]["additionalProperties"] is False
    assert specs["customize_workspace"]["properties"]["patch"]["additionalProperties"] is False


@pytest.mark.parametrize("name,args,check", [
    ("home_cards", {"action": "save", "card": _card()}, "draft"),
    ("customize_workspace", {"workspace": "library", "patch": {"density": "compact"}}, "library"),
])
def test_voice_runner_uses_governed_dispatch_and_actual_local_handlers(stores, monkeypatch, name, args, check):
    from agent_friday.services import agent, voice_engine
    calls = []
    def dispatch(tool, inputs, handler=None, session_ctx=None):
        calls.append((tool, inputs, session_ctx, agent._CURRENT_CONVERSATION.get()))
        return agent.CLAUDE_TOOL_HANDLERS[tool](inputs)
    monkeypatch.setattr(agent, "_execute_tool", dispatch)
    before = agent._CURRENT_CONVERSATION.get()
    result = json.loads(voice_engine._voice_tool_run(name, args, lambda _: None,
        {"conversation_id": "voice-test", "engine": "local", "owner_text": "Customize my desktop"}))
    assert result["saved"] is True
    assert result.get("id", result.get("workspace")) == check
    assert len(calls) == 1 and calls[0][0] == name and calls[0][1] == args
    context = calls[0][2]
    assert context["authenticated"] is True and context["surface"] == "voice-local"
    assert context["owner_text"] == "Customize my desktop" and calls[0][3] == "voice-test"
    assert agent._CURRENT_CONVERSATION.get() == before


def test_voice_runner_honors_governance_refusal_before_writing(stores, monkeypatch):
    from agent_friday.services import agent, voice_engine
    cards, _studio, events = stores
    monkeypatch.setattr(agent, "_execute_tool", lambda *_a, **_kw: "Action refused by policy")
    result = voice_engine._voice_tool_run("home_cards", {"action": "save", "card": _card()}, lambda _: None, {})
    assert result == "Action refused by policy"
    assert not cards.CARDS_PATH.exists() and not events


def test_partial_tracking_settings_preserve_siblings_on_disk(tmp_path, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    core._invalidate_settings_cache()
    core._save_settings({"tracking": {"hand_gain": 3.7, "neutral_face_width": .23,
                                      "depth_strength": .8, "parallax_strength": .9}})
    core._save_settings({"tracking": {"parallax_strength": .35}})
    core._save_settings({"tracking": {"head_smoothing": .75}})
    saved = json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8-sig"))["tracking"]
    assert saved == {"hand_gain": 3.7, "neutral_face_width": .23, "depth_strength": .8,
                     "parallax_strength": .35, "head_smoothing": .75}
    assert core._load_settings_raw()["tracking"] == saved
    core._invalidate_settings_cache()


def test_spoken_tracking_change_cannot_replace_a_newer_sibling_dial(monkeypatch):
    from agent_friday.services import hologram_tools
    state = {**hologram_tools._defaults(), "head_smoothing": .3}
    stale = dict(state)
    writes, pushed = [], []
    def current():
        state["head_smoothing"] = .85  # UI save lands after the voice read.
        return dict(stale)
    def persist(patch):
        writes.append(patch)
        state.update(patch)
    monkeypatch.setattr(hologram_tools, "current", current)
    monkeypatch.setattr(hologram_tools, "persist", persist)
    monkeypatch.setattr(hologram_tools, "push", lambda action: pushed.append(action) or {"delivered": True})
    hologram_tools.handle({"action": "set", "parallax_strength": .4})
    assert state["head_smoothing"] == .85
    assert writes == [{"parallax_strength": .4}]
    assert pushed == [{"type": "tracking", "tracking": {"parallax_strength": .4}}]


@pytest.mark.parametrize("op", ["next", "previous", "select", "back"])
@pytest.mark.parametrize("response", [{"delivered": True}, {"delivered": True, "acked": False}, {"delivered": True, "ack": {}}])
def test_delivered_cursor_action_without_ack_is_not_reported_as_done(monkeypatch, op, response):
    from agent_friday.services import hand_cursor_tools
    monkeypatch.setattr(hand_cursor_tools, "push", lambda _: response)
    assert "did not confirm" in hand_cursor_tools.handle_hand_cursor({"action": op})
