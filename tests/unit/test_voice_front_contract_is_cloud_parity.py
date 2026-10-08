"""The voice front holds the SAME tool contract cloud voice declares (local
voice spec P1, §2 parity).

The local voice seat used to receive the whole text registry (124 tools,
~22.8K tokens), which is half of why its first token took a minute or more.
``build_voice_tool_contract()`` renders the curated voice contract from the
tables Gemini Live's declarations come from, so both engines hold the same
names, ``run_command`` is in neither, and the curated contract stays under
its 9K-token ceiling. Red under named changes: making the session use
``full=True``, or dropping the ``VOICE_NEVER_DECLARED`` filter.
"""
import inspect

from agent_friday.services import voice_engine as ve


def test_the_curated_contract_names_exactly_what_cloud_voice_declares():
    c = ve.build_voice_tool_contract()
    assert c["names"] == ve._voice_tool_names(), (
        "the local front and Gemini Live must hold the same tool names in the "
        "same order")
    assert len(c["names"]) == len(set(c["names"])), "a name is declared twice"


def test_run_command_is_never_declared_curated_or_full():
    assert "run_command" not in ve.build_voice_tool_contract()["names"]
    assert "run_command" not in ve.build_voice_tool_contract(full=True)["names"]


def test_the_curated_contract_fits_its_ceiling_and_the_full_registry_is_larger():
    c = ve.build_voice_tool_contract()
    assert c["tokens"] <= ve.VOICE_CONTRACT_MAX_TOKENS and c["fits"], c["tokens"]
    full = ve.build_voice_tool_contract(full=True)
    assert len(full["names"]) >= len(c["names"])


def test_every_declaration_is_an_openai_function_with_an_object_schema():
    for t in ve.build_voice_tool_contract()["tools"]:
        assert t["type"] == "function"
        p = t["function"]["parameters"]
        assert p.get("type") == "object" and isinstance(p.get("properties"), dict)
        for req in p.get("required") or []:
            assert req in p["properties"], (t["function"]["name"], req)


def test_the_session_and_the_proof_use_the_curated_contract():
    import agent_friday.routes.voice as rv
    from agent_friday.services import voice_manifest as vm
    arm = inspect.getsource(rv._arm_voice_front)
    assert "build_voice_tool_contract()" in arm and "full=True" not in arm
    proof = inspect.getsource(vm._run_front_mind)
    assert "build_voice_tool_contract()" in proof


def _strip_descriptions(tools):
    import copy
    out = copy.deepcopy(tools)
    for t in out:
        t["function"].pop("description", None)
        for p in (t["function"]["parameters"].get("properties") or {}).values():
            if isinstance(p, dict):
                p.pop("description", None)
    return out


def test_the_compact_rendering_changes_only_descriptions_and_never_the_registry():
    from agent_friday.services.agent import CLAUDE_TOOLS
    import copy
    before = copy.deepcopy(CLAUDE_TOOLS)
    full = ve.build_voice_tool_contract(compact=False)
    lean = ve.build_voice_tool_contract()
    assert lean["names"] == full["names"]
    assert _strip_descriptions(lean["tools"]) == _strip_descriptions(full["tools"]), (
        "the compact contract changed a name, schema, type or required field")
    assert lean["tokens"] < full["tokens"]
    for t in lean["tools"]:
        assert t["function"]["description"], t["function"]["name"]
    assert CLAUDE_TOOLS == before, "rendering the contract edited the shared registry"


def test_compact_guidance_keeps_authority_constraints_and_full_discovery(monkeypatch):
    import copy
    import json
    from agent_friday.services import workflow_tools
    guide = copy.deepcopy(workflow_tools._WORKFLOW_GUIDE)
    compact = {entry["function"]["name"]: entry["function"]
               for entry in ve.build_voice_tool_contract()["tools"]}
    for name in ("organize_email", "organize_files", "organize_wiki"):
        assert "approval" in compact[name]["description"].lower()
    private = compact["ask_local_for_context"]["description"].lower()
    assert "local" in private and "private" in private and "instead" in private
    lasting = compact["voice_preferences"]["description"].lower()
    assert "default" in lasting and "explicit" in lasting
    monkeypatch.setattr(workflow_tools, "_private_discovery_allowed", lambda: False)
    discovered = json.loads(workflow_tools.discover_capabilities({"name": "workflow_action"}))
    assert discovered["guide"] == guide
    assert discovered["instructions"]["input_schema"] == compact["workflow_action"]["parameters"]
    assert "queued is not completed" in discovered["instructions"]["description"]


def test_compact_argument_guidance_keeps_permissions_choices_and_uncertainty():
    compact = {entry["function"]["name"]: entry["function"]["description"].lower()
               for entry in ve.build_voice_tool_contract()["tools"]}
    required = {
        "ask_crew": ("complete request", "invited agent id/unambiguous name", "saved model, permissions and voice",
                     "await results", "never impersonate", "assigned project id", "omitted=this chat's project"),
        "talk_crew": ("owner's question", "no tools/task changes", "steer_crew"),
        "steer_crew": ("owner's instruction", "next step", "queued is not applied"),
        "revise_share_request": ("user's exact instruction", "ask approval before sending"),
        "domain_action": ("exact name.com account/domain", "need review", "checkout",
                          "reconcile uncertain writes"),
        "ask_local_for_context": ("local", "instead", "private", "owner-approved", "cloud"),
        "search_past_conversations": ("local-only", "owner-approved", "cloud",
                                      "inclusive yyyy-mm-dd"),
        "make_podcast": ("source", "computed", "kind:news_run,routine,run_id",
                         "voice=local", "owner asks and cloud voices are enabled"),
        "ask_friday": ("local full question", "knowledge graph", "gated answer", "never guess withheld"),
        "navigate_to": ("only nav_ok confirms", "id=existing id", "workspace for kind=workspace", "mail_search",
                        "new_tab=chrome", "max=maximize"),
        "organize_files": ("approval", "items=paths", "moves=['file => folder']", "replaces=prior card"),
        "organize_wiki": ("approval", "#n from listed choices", "moves=['page => folder']"),
        "read_file": ("absolute/home-relative", "offset=1-based(default 1)", "limit≤2000(default 2000)"),
        "podcast_play": ("omit episode_id for newest finished", "only when user names a show"),
        "media_play": ("transcript match when available",),
        "set_workspace_layout": ("true:fullscreen with docked chat", "false:normal", "position="),
        "undo_action": ("organize receipt_id", "latest organize change", "mail undo needs approval"),
        "answer_card": ("just-spoken", "organize/undo card", "running is not done"),
        "revert_workspace": ("undo(default)", "as_of(when=iso time)", "version(version_id)", "reset"),
    }
    for name, fragments in required.items():
        assert all(fragment in compact[name] for fragment in fragments), (name, compact[name])
