"""Voice reaches what chat reaches, and any limit that is voice-only is the owner's.

The policy (2026-09-29, Stephen): a restriction that exists only in voice
becomes a setting the owner controls, off by default, unless it protects
something the constitution requires. Approval cards and the never-send floor
are NOT voice restrictions — they apply identically to a typed request — so
they stay and are not settings.

Two of these tests are structural on purpose. A capability gap and a new
voice-only limit are both things a later change can introduce by accident, and
neither shows up as a failing feature: voice simply cannot do a thing, quietly.
"""
import pytest

ve = pytest.importorskip("agent_friday.services.voice_engine")


def _ids():
    return {t[0] for t in ve._VOICE_LIVE_TOOLS}


# ── Firing a workflow by voice ─────────────────────────────────────────────

def test_voice_can_start_a_stored_workflow_by_name():
    """Stephen: voice should fire workflows, including ones with local steps.

    A workflow's steps run wherever the workflow says, which can be the local
    model — so this is how a spoken request reaches private work without any
    of it passing through the cloud voice model.
    """
    assert "run_workflow" in _ids()
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "run_workflow")
    assert "name" in spec[2] and spec[3] == ["name"]


def test_voice_can_ask_which_workflows_exist_and_how_one_is_doing():
    """Without the listing, 'run my morning routine' needs a name nobody said."""
    assert "workflow_status" in _ids()
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "workflow_status")
    assert spec[3] == [], "omitting the name must be allowed: that lists them"


def test_the_workflow_tools_are_told_not_to_guess_the_outcome():
    """They return when the first step is queued, not when the work is done."""
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "run_workflow")
    assert "do NOT guess" in spec[1] or "do not guess" in spec[1].lower()
    assert "approval card" in spec[1], (
        "an outward step inside a workflow still needs its card; say so")


def test_a_spoken_workflow_reports_into_the_call_not_into_main(monkeypatch):
    """The chain reads its conversation from a contextvar the chat path sets.

    Unset, a spoken 'run my morning routine' starts correctly and then reports
    somewhere nobody in the call is looking.
    """
    from agent_friday.services import agent as ag
    seen = {}

    def fake_run(inp):
        seen["cid"] = ag._CURRENT_CONVERSATION.get()
        return "started"

    monkeypatch.setattr(ag, "_tool_run_workflow", fake_run)
    monkeypatch.setattr(ag, "_execute_tool",
                        lambda tool, a, handler=None, session_ctx=None: handler(a))
    out = ve._voice_tool_run("run_workflow", {"name": "morning"},
                             lambda *a, **k: None,
                             {"conversation_id": "conv-parity"})
    assert out == "started"
    assert seen["cid"] == "conv-parity", (
        "the chain must report into the conversation that started it")


# ── The two voice-only limits are the owner's ──────────────────────────────

def test_the_direct_tool_ceiling_is_the_owners_and_can_be_removed():
    from agent_friday.routes.voice import voice_tool_limit_s
    assert voice_tool_limit_s({}) == 20, "the shipped default"
    assert voice_tool_limit_s({"voice_tool_hard_limit_s": 45}) == 45
    assert voice_tool_limit_s({"voice_tool_hard_limit_s": 0}) == 0, "0 means no limit"
    assert voice_tool_limit_s({"voice_tool_hard_limit_s": "nonsense"}) == 20, (
        "an unreadable value falls back to the default, never to no limit")


def test_the_ceiling_refuses_nothing_it_only_moves_the_work():
    """Why this one is not a capability restriction, stated where it is read."""
    import inspect
    from agent_friday.routes.voice import voice_tool_limit_s
    doc = inspect.getdoc(voice_tool_limit_s) or ""
    assert "subtracts no capability" in doc


def test_room_mode_spoken_approvals_need_friday_by_default():
    from agent_friday.services import local_context as lc
    assert lc.spoken_decision("yes, send it", room_mode=True) is None, (
        "in a room, an unnamed yes could be anyone")
    assert lc.spoken_decision("Friday, send it", room_mode=True) == "approve"
    assert lc.spoken_decision("yes, send it", room_mode=False) == "approve", (
        "one person talking to Friday needs no name")


def test_the_owner_can_turn_the_name_requirement_off(monkeypatch):
    """His call, and the tradeoff is that anyone in earshot can then approve."""
    from agent_friday.services import local_context as lc
    monkeypatch.setattr(lc, "_room_approvals_need_name", lambda: False)
    assert lc.spoken_decision("yes, send it", room_mode=True) == "approve"


def test_an_unreadable_setting_keeps_the_stricter_rule(monkeypatch):
    from agent_friday.services import local_context as lc
    import agent_friday.services.agent as ag
    monkeypatch.setattr(ag, "_load_settings",
                        lambda: (_ for _ in ()).throw(OSError("no settings")))
    assert lc._room_approvals_need_name() is True


def test_no_still_wins_over_yes_in_the_same_breath():
    from agent_friday.services import local_context as lc
    assert lc.spoken_decision("Friday, yes — no, don't send it",
                              room_mode=True) == "deny"


# ── The policy itself, enforced ────────────────────────────────────────────

_EXEMPT_KINDS = {"governance", "privacy", "identity"}


def test_every_voice_limit_is_either_the_owners_or_says_why_not():
    """The structural half of the policy.

    A later change that adds a voice-only limit without making it a setting
    should fail here rather than quietly narrow what voice can do.
    """
    for r in ve.voice_restrictions({}):
        assert r.get("why"), "every limit explains itself: %r" % r["id"]
        if r["kind"] == "your setting":
            continue
        assert r["kind"] in _EXEMPT_KINDS, (
            "%r is neither the owner's setting nor an exempt kind (%s). Either "
            "give it a setting or say which rule requires it."
            % (r["id"], sorted(_EXEMPT_KINDS)))


def test_the_exempt_limits_are_the_ones_that_apply_to_chat_too():
    """Stephen: approval gates and cLaws are not voice restrictions."""
    by_id = {r["id"]: r for r in ve.voice_restrictions({})}
    assert by_id["approvals"]["kind"] == "governance"
    assert "in voice as in chat" in by_id["approvals"]["why"]
    assert by_id["never_send"]["kind"] == "privacy"
    assert "every cloud model" in by_id["never_send"]["why"]


def test_the_settings_backed_limits_name_their_setting():
    """So the Voice tab can offer the switch beside the sentence."""
    for r in ve.voice_restrictions({}):
        if r["id"] in ("direct_time_limit", "room_approvals"):
            assert r.get("setting"), "%r must name its settings key" % r["id"]
            from agent_friday.core import DEFAULT_SETTINGS
            assert r["setting"] in DEFAULT_SETTINGS, (
                "%r points at a key that does not exist" % r["setting"])


def test_voice_is_documented_as_reaching_everything_chat_does():
    import inspect
    doc = inspect.getdoc(ve.voice_restrictions) or ""
    assert "anything chat can" in doc


# ── The contract doc stays true ────────────────────────────────────────────

CONTRACT = "docs/reference/voice-tool-contract.md"


def _contract_text():
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / CONTRACT
    assert p.exists(), "%s is the contract other sessions follow" % CONTRACT
    return p.read_text(encoding="utf-8")


def test_every_symbol_the_contract_names_still_exists():
    """A contract that names a moved function sends the next session wrong.

    Checked by import rather than by grep, so a rename fails here instead of
    being discovered by whoever follows the instructions.
    """
    text = _contract_text()
    from agent_friday.core import DEFAULT_SETTINGS, _scrub_pii  # noqa: F401
    from agent_friday.services import agent as ag
    from agent_friday.services import judgment_gate as jg
    from agent_friday.services import local_context as lc
    from agent_friday.routes import voice as vr

    named = {
        "_VOICE_LIVE_TOOLS": lambda: ve._VOICE_LIVE_TOOLS,
        "_voice_tool_run": lambda: ve._voice_tool_run,
        "voice_restrictions": lambda: ve.voice_restrictions,
        "_execute_tool": lambda: ag._execute_tool,
        "_CURRENT_CONVERSATION": lambda: ag._CURRENT_CONVERSATION,
        "spoken_decision": lambda: lc.spoken_decision,
        "decide_by_voice": lambda: lc.decide_by_voice,
        "never_send_hits": lambda: jg.never_send_hits,
        "hard_identifier_hits": lambda: jg.hard_identifier_hits,
        "voice_tool_hard_limit_s": lambda: DEFAULT_SETTINGS["voice_tool_hard_limit_s"],
        "voice_room_approvals_require_name":
            lambda: DEFAULT_SETTINGS["voice_room_approvals_require_name"],
    }
    for symbol, get in named.items():
        if symbol in text:
            assert get() is not None, (
                "the contract names %r, which no longer resolves" % symbol)


def test_the_contract_states_the_private_data_rule():
    """The one rule in it that is not a convenience."""
    text = _contract_text()
    assert "ask_local_for_context" in text
    assert "Never pass raw private data" in text
    assert "fails" in text and "closed" in text


def test_the_contract_does_not_present_the_unbuilt_spec_as_built():
    """Owner rules are specified, not implemented. Saying otherwise would
    have the next session rely on a rule nothing enforces."""
    text = _contract_text()
    assert "not yet built" in text
