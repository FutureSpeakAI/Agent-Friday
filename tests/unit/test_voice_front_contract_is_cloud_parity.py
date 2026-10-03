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
