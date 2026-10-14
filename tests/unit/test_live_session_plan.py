"""CV-A: the plan for one Gemini Live call (services/live_session_plan).

Gates, from the addendum section 6 and the package brief:
  * the instruction never exceeds its cap, and safety text is always present;
  * the kits always include ask_friday and more_tools;
  * more_tools widens the NEXT leg, never the running one;
  * shadow mode declares the full set;
  * prefetch makes no network call;
  * the soft budget speaks once.
No network, no model, no key: synthetic text only.
"""
import ast
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_friday.services import live_session_plan as lsp

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_encoder(monkeypatch):
    """slice_head would start loading its sentence encoder on the first reading; no model in a unit test."""
    from agent_friday.services import slice_head
    monkeypatch.setattr(slice_head, "scores_for", lambda text, block=False: None)


class _NoNetwork(BaseException):
    """A BaseException, so no `except Exception` in the code under test can hide it."""


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise _NoNetwork("a network call was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _policy():
    from agent_friday.services.action_policy import ACTION_PERMISSION_POLICY
    return ACTION_PERMISSION_POLICY


def _filler(tag: str, n: int) -> str:
    return "".join("%s line %d about the garden and the ferry.\n" % (tag, i) for i in range(n))


CLAWS = ("== ASIMOV cLAWS (compiled, non-negotiable) ==\n"
         "Law one: never harm the user. Law two: obey the user within law one. Law three: protect yourself.\n\n")


def _surface(names):
    return ("=== VOICE TOOL SURFACE (OVERRIDES ANY TOOL LIST ABOVE) ===\n"
            "This is a LIVE VOICE session.\nIn voice you can call EXACTLY %d tools, and nothing else:\n"
            % len(names) + "".join("  • %s\n" % n for n in names) +
            "\nEVERYTHING ELSE CHAT CAN DO IS REACHABLE: one call to delegate_to_friday.\n\n")


def _instruction(ctx=300, selfk=500, names=("ask_friday", "query_calendar", "check_email", "search_news",
                                          "more_tools", "delegate_to_friday")):
    return (
        "You are Agent Friday. Speak naturally. NEVER use markdown.\n\n"
        "=== YOUR PERSONALITY ===\nWarm, dry, direct.\n\n"
        "=== ABOUT AGENT FRIDAY (PUBLIC / ALWAYS SHAREABLE) ===\n" + _filler("Spec", 60) + "\n"
        "=== TAKING ACTIONS (ASK FIRST) ===\nAsk before you open a URL.\n\n"
        "== AGENT PERSONALITY ==\nBe family, not a tool.\n\n" + CLAWS +
        "== SELF-KNOWLEDGE ==\n" + _filler("Self", selfk) + "\n"
        "== AUTONOMOUS OPERATION ==\nReal-world actions go through the policy.\n\n"
        "== TOOLS ==\n" + _filler("Tool", 30) + "\n"
        "== COMPUTER CONTROL ==\nTake a screenshot first.\n\n"
        "== PACKAGE INSTALLATION ==\nUse install_package.\n\n"
        "== RECENT MEMORIES ==\n" + _filler("Mem", ctx) + "\n"
        "== PERSONAL CONTEXT (smart-loaded for this turn) ==\n" + _filler("Note", ctx) + "\n"
        "== HONEST DEGRADATION (applies to every model) ==\nIf a tool fails, say so and stop.\n\n"
        "== AUTHORITATIVE CLOCK ==\nIt is a Monday.\n\n"
        + _surface(names)
        + _policy())


# ── LV-1: the cap ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("cap", [3000, 5000, 9000])
def test_the_instruction_never_exceeds_its_cap_and_keeps_the_safety_text(cap):
    text = _instruction()
    assert lsp.estimate_tokens(text) > cap, "the fixture must be over the cap or the test proves nothing"
    out, rep = lsp.fit_instruction(text, cap)
    assert lsp.estimate_tokens(out) <= cap
    assert not rep.floor_over_cap and rep.applied
    assert _policy() in out
    assert "ASIMOV cLAWS" in out and "Law one" in out
    assert "AUTONOMOUS OPERATION" in out and "HONEST DEGRADATION" in out
    assert "YOUR PERSONALITY" in out and "TAKING ACTIONS" in out and "VOICE TOOL SURFACE" in out


def test_a_cap_below_the_safety_floor_sends_the_floor_and_says_so():
    text = _instruction()
    out, rep = lsp.fit_instruction(text, 50)
    assert rep.floor_over_cap is True            # honest: the cap did not win
    assert _policy() in out and "Law one" in out  # safety did
    assert "Mem line 3" not in out and "Self line 3" not in out and "Tool line 3" not in out


def test_optional_context_goes_before_anything_mandatory_and_in_a_fixed_order():
    text = _instruction()
    floor, _ = lsp.fit_instruction(text, 1)
    floor_tokens = lsp.estimate_tokens(floor)
    # Room for the floor plus a little: text-chat boilerplate and self-knowledge go first.
    out, rep = lsp.fit_instruction(text, floor_tokens + 600)
    names = [n for n, _t, _r in rep.dropped]
    assert "tools" in names and "computer_control" in names and "package_installation" in names
    assert "self_knowledge" in names
    assert names.index("self_knowledge") < names.index("recent_memories") if "recent_memories" in names else True
    assert _policy() in out


def test_text_chat_only_blocks_are_not_in_a_voice_instruction_even_under_the_cap():
    out, rep = lsp.fit_instruction(_instruction(ctx=2, selfk=2), 100000)
    assert "== TOOLS ==" not in out and "COMPUTER CONTROL" not in out and "PACKAGE INSTALLATION" not in out
    assert "RECENT MEMORIES" in out          # context stays while there is room
    assert any(r == "text-chat only" for _n, _t, r in rep.dropped)


def test_cap_zero_is_the_pre_1_1_instruction_unchanged():
    text = _instruction()
    out, rep = lsp.fit_instruction(text, 0)
    assert out == text and not rep.applied


def test_text_after_the_policy_is_optional_and_not_protected_by_it():
    text = _instruction(ctx=2, selfk=2) + "\n== LAST SESSION ==\n" + _filler("Pin", 400)
    out, rep = lsp.fit_instruction(text, 3500)
    assert _policy() in out
    assert "Pin line 3" not in out
    assert lsp.estimate_tokens(out) <= 3500


def test_a_cap_that_would_lose_the_safety_text_sends_the_input_unchanged(monkeypatch):
    """The fail-closed path: if the policy is missing from the result, nothing is capped."""
    text = _instruction()
    real = lsp.split_blocks

    def lossy(t):
        return [b for b in real(t) if "action_permission_policy" not in b.name]
    monkeypatch.setattr(lsp, "split_blocks", lossy)
    out, rep = lsp.fit_instruction(text, 3000)
    assert out == text and rep.applied is False and "safety" in rep.reason


def test_safety_ok_can_fail():
    text = _instruction()
    assert lsp.safety_ok(text, text)
    assert not lsp.safety_ok(text, text.replace(_policy(), ""))
    assert not lsp.safety_ok(text, text.replace("ASIMOV cLAWS", "SOMETHING ELSE"))


def test_unheaded_voice_rules_at_the_front_are_mandatory():
    out, _ = lsp.fit_instruction(_instruction(), 1)
    assert out.startswith("You are Agent Friday. Speak naturally.")


def test_a_header_this_module_has_never_seen_is_optional_not_mandatory():
    text = _instruction(ctx=2, selfk=2) + ""
    text = text.replace("== AUTHORITATIVE CLOCK ==", "== SOMETHING NEW ==" + "\n" + _filler("New", 300) + "\n== AUTHORITATIVE CLOCK ==")
    out, rep = lsp.fit_instruction(text, 4000)
    assert "New line 3" not in out and _policy() in out


def test_the_surface_note_names_only_the_tools_this_leg_declares():
    text = _instruction()
    out = lsp.narrow_surface_note(text, ["ask_friday", "more_tools"])
    assert "  • ask_friday\n" in out and "  • more_tools\n" in out
    assert "  • query_calendar\n" not in out
    assert "EXACTLY 2 tools" in out
    assert "more_tools with its group" in out


def test_slice_head_ordering_drops_a_withheld_slice_first():
    plan = {"memory": {"action": "withhold", "p": 0.01, "applied": False},
            "wiki": {"action": "include", "p": 0.9, "applied": False}}
    text = ("You are Friday.\n\n== RECENT MEMORIES ==\n" + _filler("Mem", 60)
            + "\n== WIKI BRIEFING DATA ==\n" + _filler("Wiki", 60) + "\n" + _policy())
    cap = lsp.estimate_tokens(text) - 100
    out, rep = lsp.fit_instruction(text, cap, slice_plan=plan)
    dropped = [n for n, _t, _r in rep.dropped]
    assert dropped == ["recent_memories"]
    assert "Wiki line 3" in out


def test_an_applied_withhold_is_dropped_even_under_the_cap():
    plan = {"memory": {"action": "withhold", "p": 0.01, "applied": True}}
    text = "You are Friday.\n\n== RECENT MEMORIES ==\n" + _filler("Mem", 3) + "\n" + _policy()
    out, rep = lsp.fit_instruction(text, 100000, slice_plan=plan)
    assert "Mem line" not in out and rep.dropped[0][2].startswith("not needed")


# ── LV-2: the kit ───────────────────────────────────────────────────────────

ALL = ["ask_friday", "more_tools", "call_cost", "delegate_to_friday", "answer_card", "answer_share_request",
       "undo_action", "navigate_workspace", "note_conversation_state", "query_calendar", "check_email",
       "search_news", "get_briefing", "search_web", "open_url", "navigate_to", "search_wiki", "spawn_task",
       "read_file", "write_file", "search_files", "media_play", "podcast_play", "codebase_run", "open_project",
       "set_setting", "run_workflow", "show_my_day"]


@pytest.mark.parametrize("workspace,hour,opening", [
    ("", 3, ""), ("mail", 9, ""), ("code", 14, "open the project"), ("media", 21, "play something"),
    ("no-such-workspace", 12, "zzz qqq")])
@pytest.mark.parametrize("size", [8, 12, 20, 40])
def test_the_kit_always_includes_ask_friday_and_more_tools(workspace, hour, opening, size):
    kit = lsp.choose_kit(ALL, size=size, workspace=workspace, hour=hour, opening=opening)
    assert "ask_friday" in kit and "more_tools" in kit
    assert len(kit) == len(set(kit))


def test_the_kit_keeps_approvals_answerable_by_voice():
    kit = lsp.choose_kit(ALL, size=12, workspace="code", hour=14)
    assert "answer_card" in kit and "answer_share_request" in kit and "undo_action" in kit


def test_the_kit_follows_the_workspace_and_the_opening_words(tmp_path):
    u = lsp.UsageStore(tmp_path / "u.json")
    code = lsp.choose_kit(ALL, size=14, workspace="code", hour=14, usage=u)
    media = lsp.choose_kit(ALL, size=14, workspace="media", hour=21, usage=u)
    assert "codebase_run" in code and "codebase_run" not in media
    assert "podcast_play" in media and "podcast_play" not in code
    said = lsp.choose_kit(ALL, size=14, workspace="", hour=12, opening="read the file and write the file", usage=u)
    assert "read_file" in said


def test_recent_use_pulls_a_tool_into_the_kit(tmp_path):
    u = lsp.UsageStore(tmp_path / "u.json")
    before = lsp.choose_kit(ALL, size=12, workspace="", hour=12, usage=u)
    assert "run_workflow" not in before
    for _ in range(4):
        u.record("run_workflow")
    after = lsp.choose_kit(ALL, size=12, workspace="", hour=12, usage=u)
    assert "run_workflow" in after


def test_the_kit_is_deterministic():
    a = lsp.choose_kit(ALL, size=16, workspace="mail", hour=9, opening="check my mail")
    b = lsp.choose_kit(ALL, size=16, workspace="mail", hour=9, opening="check my mail")
    assert a == b


@pytest.fixture
def plan_factory(tmp_path):
    def make(mode="on", settings=None, available=ALL, **kw):
        s = {"voice_live_trim": mode, "voice_live_tool_kit_size": 12}
        s.update(settings or {})
        return lsp.SessionPlan(s, available, shadow_path=tmp_path / "shadow.jsonl",
                               usage=lsp.UsageStore(tmp_path / "u.json"), **kw)
    return make


def test_on_declares_the_kit_and_not_the_rest(plan_factory):
    p = plan_factory("on", workspace="mail", now=time.mktime((2026, 10, 12, 9, 0, 0, 0, 0, -1)))
    names = p.declared_names()
    assert 12 <= len(names) <= 20 < len(ALL) + 1
    assert "ask_friday" in names and "more_tools" in names
    assert "codebase_run" not in names and p.kit_arg()["compact"] is True


def test_more_tools_widens_the_next_leg_not_the_running_one(plan_factory):
    p = plan_factory("on", workspace="mail", now=time.mktime((2026, 10, 12, 9, 0, 0, 0, 0, -1)))
    from google.genai import types
    first = p.declared_names()
    assert "codebase_run" not in first
    msg = p.widen("code")
    assert "reconnects" in msg and "do not call them before" in msg.lower()
    assert p.relegs_pending is True
    assert p.reconnect_due(have_handle=True) and not p.reconnect_due(have_handle=False)
    assert "codebase_run" in p.declared_names()
    # The tools of the leg that is already open were built from `first`; the
    # new set is built only when the next leg asks for it.
    tools = p.tools_for_leg(types)
    assert tools is not None
    declared = {d.name for t in tools for d in t.function_declarations}
    assert "codebase_run" in declared and "ask_friday" in declared
    assert p.relegs_pending is False
    assert p.tools_for_leg(types) is None, "nothing changed since: the leg keeps its tools"


def test_more_tools_everything_and_fewer(plan_factory):
    p = plan_factory("on")
    p.widen("everything")
    assert set(p.declared_names()) == set(ALL) and p.kit_arg() is None
    p.widen("fewer")
    assert len(p.declared_names()) < len(ALL) and p.kit_arg() is not None


def test_more_tools_with_an_unknown_group_widens_nothing(plan_factory):
    p = plan_factory("on")
    before = p.declared_names()
    msg = p.widen("nonsense")
    assert "No tool group" in msg and p.declared_names() == before and not p.relegs_pending


def test_shadow_declares_the_full_set_as_today(plan_factory):
    p = plan_factory("shadow", workspace="mail")
    assert set(p.declared_names()) == set(ALL) - {"more_tools"}
    assert p.kit_arg() is None, "full descriptions, nothing filtered"
    off = plan_factory("off")
    assert set(off.declared_names()) == set(ALL) - {"more_tools"} and off.kit_arg() is None


def test_shadow_builds_the_same_declarations_as_no_plan_at_all(plan_factory):
    from google.genai import types
    from agent_friday.services import voice_engine as ve
    p = plan_factory("shadow", available=ve._voice_tool_names())
    today = ve._build_voice_live_tools(types)
    shadow = ve._build_voice_live_tools(types, kit=p.kit_arg())
    assert [d.model_dump() for d in today[0].function_declarations] == \
        [d.model_dump() for d in shadow[0].function_declarations]


def test_shadow_logs_the_kit_miss_rate_and_changes_nothing_it_declares(plan_factory, tmp_path):
    p = plan_factory("shadow", workspace="mail", now=time.mktime((2026, 10, 12, 9, 0, 0, 0, 0, -1)))
    assert p.would_drop(), "a kit smaller than the full set drops something"
    keep = next(n for n in p.kit if n not in ("ask_friday", "more_tools", "call_cost"))
    gone = p.would_drop()[0]
    p.note_call(keep)
    p.note_call(gone)
    p.note_call(gone)
    stats = lsp.shadow_stats(tmp_path / "shadow.jsonl")
    assert stats["sessions"] == 1 and stats["calls"] == 3 and stats["misses"] == 2
    assert abs(stats["miss_rate"] - 2 / 3) < 1e-9
    log = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")
    assert "arguments" not in log and "args" not in log, "counts and tool names only"


def test_compact_declarations_are_shorter_and_keep_names_types_and_required():
    from google.genai import types
    from agent_friday.services import voice_engine as ve
    names = {"ask_friday", "navigate_to", "organize_email", "read_file"}
    full = ve._build_voice_live_tools(types)[0].function_declarations
    compact = ve._build_voice_live_tools(types, kit={"names": names, "compact": True})[0].function_declarations
    assert {d.name for d in compact} == names
    by = {d.name: d for d in full}
    total_full = total_c = 0
    for d in compact:
        f = by[d.name]
        total_full += len(json.dumps(f.model_dump(exclude_none=True), default=str))
        total_c += len(json.dumps(d.model_dump(exclude_none=True), default=str))
        assert (d.parameters.required if d.parameters else None) == (f.parameters.required if f.parameters else None)
        assert set((d.parameters.properties or {}) if d.parameters else {}) == set((f.parameters.properties or {}) if f.parameters else {})
    assert total_c < total_full * 0.8


def test_the_two_session_tools_are_cloud_only_and_cost_the_local_front_nothing():
    from agent_friday.services import voice_engine as ve
    contract = ve.build_voice_tool_contract()
    assert "more_tools" not in contract["names"] and "call_cost" not in contract["names"]
    assert {"more_tools", "call_cost"} <= {t[0] for t in ve._VOICE_LIVE_TOOLS}
    assert contract["fits"]


# ── LV-6: prefetch ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expect", [
    ("can you check the email from Dana about the invoice", [("mail_from", "Dana")]),
    ("what do I have tomorrow", [("calendar", "upcoming")]),
    ("tell Marcus Hale I'll be late, and what's on Friday", [("mail_from", "Marcus Hale"), ("calendar", "upcoming")]),
    ("open the news", []),
    ("email from Google about Tuesday", [("calendar", "upcoming")]),
    ("what is on March 3", [("calendar", "upcoming")]),
])
def test_detect_prefetch(text, expect):
    assert lsp.detect_prefetch(text) == expect


def test_prefetch_makes_no_network_call_and_sends_nothing(no_network):
    seen = []
    pf = lsp.Prefetcher(mail_headers=lambda who: seen.append(("mail", who)),
                        calendar=lambda: seen.append(("cal",)), run_async=False)
    todo = pf.on_text("any mail from Dana, and what's on tomorrow")
    assert todo == [("mail_from", "Dana"), ("calendar", "upcoming")]
    assert seen == [("mail", "Dana"), ("cal",)]
    # and the default adapters are cache readers, not senders: no client-content hook exists here
    assert not hasattr(pf, "send") and "send_client_content" not in Path(lsp.__file__).read_text(encoding="utf-8")


def test_detection_itself_touches_no_socket(no_network):
    lsp.detect_prefetch("tell Dana about tomorrow")


def test_prefetch_warms_each_thing_once_and_stops_at_four(no_network):
    calls = []
    pf = lsp.Prefetcher(mail_headers=lambda w: calls.append(w), calendar=lambda: calls.append("cal"), run_async=False)
    for text in ("email from Dana", "email from Dana again", "mail from Ravi", "mail from Priya",
                 "mail from Tomas", "mail from Edith", "what about tomorrow"):
        pf.on_text(text)
    assert calls.count("Dana") == 1
    assert len(calls) == 4


def test_a_failing_fetch_is_swallowed(no_network):
    def boom(*a):
        raise RuntimeError("down")
    pf = lsp.Prefetcher(mail_headers=boom, calendar=boom, run_async=False)
    pf.on_text("mail from Dana tomorrow")       # must not raise
    assert pf.done == []


# ── LV-6: the soft budget ───────────────────────────────────────────────────

class _Usage:
    def __init__(self, p, r):
        self.usage_metadata = type("U", (), {"prompt_token_count": p, "response_token_count": r})()


def _turns(plan, n, p=20000, r=300):
    notes = []
    for _ in range(n):
        plan.on_usage(_Usage(p, r))
        notes.append(plan.on_turn_complete("gemini-3.8-live")["note"])
    return notes


def test_the_soft_budget_speaks_once(plan_factory):
    p = plan_factory("off", settings={"voice_live_soft_budget_usd": 0.10})
    notes = _turns(p, 6)                      # 20K tokens at $0.003/1K = $0.06 a turn
    assert sum(1 for n in notes if n) == 1
    assert notes.index(next(n for n in notes if n)) == 1       # second turn crosses $0.10
    assert "ONE short sentence" in next(n for n in notes if n)
    assert p.heads_up_given and p.session_usd > 0.3


def test_the_soft_budget_is_off_by_default_and_never_cuts_anything(plan_factory):
    p = plan_factory("off")
    assert _turns(p, 30) == [None] * 30
    assert not hasattr(p, "end_call") and "done.set" not in Path(lsp.__file__).read_text(encoding="utf-8")


def test_a_turn_without_usage_commits_nothing(plan_factory):
    p = plan_factory("off", settings={"voice_live_soft_budget_usd": 0.01})
    assert p.on_turn_complete("gemini-3.8-live") == {"note": None, "frame": None}


def test_the_panel_shows_the_call_and_the_day(plan_factory, monkeypatch):
    p = plan_factory("off")
    monkeypatch.setattr(p, "day_total_usd", lambda: 1.25)
    _turns(p, 2)
    frame = p.panel_frame()
    assert frame["type"] == "voice_budget" and frame["day_usd"] == 1.25
    assert "voice today $1.25" in frame["line"] and frame["session_usd"] > 0
    assert "$1.25" in p.cost_report()


# ── hooks, settings, compression ────────────────────────────────────────────

def test_compression_gets_explicit_trigger_and_target(tmp_path):
    got = {}

    class T:
        class SlidingWindow:
            def __init__(self, target_tokens=None):
                got["target"] = target_tokens

        class ContextWindowCompressionConfig:
            def __init__(self, trigger_tokens=None, sliding_window=None):
                got["trigger"] = trigger_tokens
    sp = tmp_path / "s.jsonl"
    p = lsp.SessionPlan({"voice_live_trim": "on", "voice_live_compression_trigger_tokens": 20000,
                         "voice_live_compression_target_tokens": 9000}, ALL, shadow_path=sp)
    p.compression_config(T)
    assert got == {"target": 9000, "trigger": 20000}
    got.clear()
    lsp.SessionPlan({"voice_live_trim": "on", "voice_live_compression_trigger_tokens": 0}, ALL,
                    shadow_path=sp).compression_config(T)
    assert got == {"target": None, "trigger": None}, "0 means Google's default window"
    got.clear()
    for mode in ("shadow", "off"):      # not "on": today's default window
        lsp.SessionPlan({"voice_live_trim": mode}, ALL, shadow_path=sp).compression_config(T)
        assert got == {"target": None, "trigger": None}, mode
        got.clear()


def test_the_defaults_are_in_default_settings():
    from agent_friday.core import DEFAULT_SETTINGS
    for k, v in lsp.DEFAULTS.items():
        assert DEFAULT_SETTINGS[k] == v, k
    assert lsp.DEFAULTS["voice_live_trim"] == "shadow"
    assert lsp.DEFAULTS["voice_live_soft_budget_usd"] == 0


def test_plan_session_fails_open(tmp_path):
    out, plan = lsp.plan_session(_instruction(), {"voice_live_instruction_max_tokens": "garbage"}, ["ask_friday"],
                                 shadow_path=tmp_path / "s.jsonl")
    assert _policy() in out and plan.mode == "shadow"
    out2, _ = lsp.plan_session("x", {}, lambda: (_ for _ in ()).throw(RuntimeError("no tools")),
                               shadow_path=tmp_path / "s.jsonl")
    assert out2.startswith("x")


def _ws_live_source():
    return (ROOT / "src/agent_friday/routes/voice.py").read_text(encoding="utf-8")


def test_the_hooks_are_in_ws_live_in_the_order_the_design_needs():
    src = _ws_live_source()
    plan_at = src.index("_lsp.plan_session(")
    gate_at = src.index("sys_text = _gate_voice_system_instruction(sys_text)")
    guard_at = src.index('_sg.check("gemini", what="a Gemini Live voice session")')
    assert plan_at < gate_at < guard_at, "capped before the egress gate, and the gate still runs"
    assert "_lsp_plan.compression_config(types)" in src
    assert src.count("kit=_lsp_plan.kit_arg()") == 2          # first build and the NON_BLOCKING rebuild
    assert "_lsp_plan.tools_for_leg(" in src and "_lsp_plan.reconnect_due(" in src
    assert "_lsp_plan.on_input_text(" in src and "_lsp_plan.on_usage(chunk)" in src
    assert '_voice_session["lsp"] = _lsp_plan' in src
    ast.parse(src)


def test_the_voice_tool_dispatcher_notes_calls_and_serves_the_two_tools():
    src = (ROOT / "src/agent_friday/services/voice_engine.py").read_text(encoding="utf-8")
    assert "_lsp.note_call(session, name)" in src
    assert 'name == "more_tools"' in src and 'name == "call_cost"' in src


def test_more_tools_through_the_session_dict_reaches_the_plan(plan_factory):
    p = plan_factory("on")
    s = {"lsp": p}
    assert "Widening" in lsp.tool_more_tools({"group": "code"}, s)
    assert "This call so far" in lsp.tool_call_cost({}, s)
    assert "already available" in lsp.tool_more_tools({"group": "code"}, {})


def test_the_two_tools_have_a_ring_a_gate_class_and_a_group():
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import agent, tool_catalogue as tc
    assert agent.TOOL_RINGS["more_tools"] == 1 and agent.TOOL_RINGS["call_cost"] == 0
    assert "more_tools" in ag.INTERNAL_TOOLS and "call_cost" in ag.INTERNAL_TOOLS
    assert tc.group_of("more_tools") == "voice" == tc.group_of("call_cost")


def test_every_group_with_a_voice_tool_is_one_more_tools_names():
    from agent_friday.services import voice_engine as ve
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "more_tools")
    declarable = ve._voice_tool_names()
    for g in lsp.group_ids():
        if g in ("connectors", "voice") or not lsp.tools_of_group(g, declarable):
            continue
        assert g in spec[1], "more_tools' description must list the group %r" % g


# ── the Laya contract for the call itself ───────────────────────────────────

def test_the_session_phrasings_are_held_to_real_live_tools_and_the_gate():
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import agent, laya_manifests as lm, voice_engine as ve
    data = json.loads((ROOT / "src/agent_friday/resources/voice_session_phrasings.json").read_text(encoding="utf-8"))
    live = {t[0] for t in ve._VOICE_LIVE_TOOLS}
    ids = [v["id"] for v in data["verbs"]]
    assert len(ids) == len(set(ids)) and all(i.startswith("voice_session.") for i in ids)
    wanted = {"use fewer tools", "give me everything", "how much has this call cost?"}
    have = {p for v in data["verbs"] for p in v["phrasings"]}
    assert wanted <= have
    for v in data["verbs"]:
        assert v["tool"] in live and v["voice"] == {"live": True, "shared": False}
        assert v["gate"]["class"] == lm.gate_class(v["tool"]) == "internal"
        assert ag.classify(v["tool"], {})[0] == ag.INTERNAL
        assert v["gate"]["ring"] == agent.TOOL_RINGS[v["tool"]]
        assert v["phrasings"]
    for c in data["held_out_eval"]:
        assert c["expect_tool"] in live
    held = {c["text"] for c in data["held_out_eval"]}
    assert not held & have, "held-out cases must not be phrasings the verbs list"


# ── the harness ─────────────────────────────────────────────────────────────

def _harness():
    sys.path.insert(0, str(ROOT))
    import importlib
    return importlib.import_module("tools.voice_session_tokens")


def test_the_harness_context_is_synthetic_deterministic_and_sized():
    h = _harness()
    assert h.synthetic_context("empty") == ""
    a, b = h.synthetic_context("typical"), h.synthetic_context("typical")
    assert a == b and a and len(h.synthetic_context("heavy")) > 3 * len(a)
    assert "@" not in a and "http" not in a


def test_the_harness_drift_guard_its_mirror_names_what_ws_live_names():
    src = _ws_live_source()
    ws_live = src[src.index("def ws_live(ws):"):]
    harness = (ROOT / "tools/voice_session_tokens.py").read_text(encoding="utf-8")
    for token in ("VOICE_LENGTH_RULE", "VOICE_STATE_NOTE_RULE", "vault_rule(", "VOICE_CROSSTALK_RULE",
                  "VOICE_ENGLISH_RULE", "VOICE_ANCHOR_RULES", "VOICE_TOOL_CHOREOGRAPHY",
                  "_load_voice_demo()", "_voice_tool_surface_note()", "strip_text_chat_hints(",
                  "compose_live_instruction(", "seal_system_prompt(", "_build_emotional_tone_block()"):
        assert token in ws_live, "ws_live no longer uses " + token
        assert token in harness, "the harness mirror no longer uses " + token
    assert "TAKING ACTIONS (ASK FIRST)" in ws_live and "TAKING ACTIONS (ASK FIRST)" in harness


def test_the_harness_network_guard_refuses_and_counts(monkeypatch):
    h = _harness()
    monkeypatch.setattr(h, "NETWORK_ATTEMPTS", [])
    monkeypatch.setattr(h, "LOOPBACK_PROBES", [])
    h._note(("203.0.113.9", 443))
    h._note(("127.0.0.1", 11434))
    assert h.NETWORK_ATTEMPTS == ["('203.0.113.9', 443)"] and len(h.LOOPBACK_PROBES) == 1
    assert issubclass(h.NetworkBlocked, ConnectionRefusedError)


def test_the_owner_local_audit_hook_refuses_writes_and_allows_reads(tmp_path):
    """Run in a child process: an audit hook cannot be removed once installed."""
    home = tmp_path / "owner"
    home.mkdir()
    (home / "keep.txt").write_text("x", encoding="utf-8")
    code = (
        "import sys, pathlib\n"
        "sys.path.insert(0, %r)\n"
        "import tools.voice_session_tokens as h\n"
        "home = pathlib.Path(%r)\n"
        "h._read_only_audit(home)\n"
        "assert (home / 'keep.txt').read_text() == 'x'\n"
        "out = []\n"
        "for fn in (lambda: open(home / 'new.txt', 'w'), lambda: (home / 'keep.txt').unlink(),\n"
        "           lambda: (home / 'd').mkdir(), lambda: (home / 'keep.txt').rename(home / 'z')):\n"
        "    try:\n"
        "        fn(); out.append('ALLOWED')\n"
        "    except PermissionError:\n"
        "        out.append('blocked')\n"
        "open(%r, 'w').close()   # outside the owner folder: not ours to block\n"
        "print(out, len(h.WRITES_BLOCKED))\n" % (str(ROOT), str(home), str(tmp_path / "elsewhere.txt")))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "ALLOWED" not in r.stdout and r.stdout.count("blocked") == 4, r.stdout
    assert (home / "keep.txt").exists() and not (home / "new.txt").exists()
    assert (tmp_path / "elsewhere.txt").exists()


def test_the_owner_local_report_has_counts_and_no_text():
    src = (ROOT / "tools/voice_session_tokens.py").read_text(encoding="utf-8")
    body = src[src.index("def run_owner_local"):src.index("def _render")]
    assert '"counts_only": True' in body
    assert "pieces.items()" in body and "b.name" not in body and "rep.dropped" in body
    assert not re.search(r"\bname\b\s*:\s*b\.|\[n\b for n", body.replace('"tokenizer": name', "")), "no block titles in the report"


def test_a_slow_slice_reading_is_cut_at_its_budget_and_the_plan_goes_on(monkeypatch):
    from agent_friday.services import slice_head
    monkeypatch.setattr(slice_head, "scores_for", lambda text, block=False: time.sleep(1.0) or {"memory": 0.9})
    t0 = time.perf_counter()
    assert lsp._slice_plan("mail", {}) == {}
    assert time.perf_counter() - t0 < 0.2


def test_a_ready_slice_reading_orders_the_plan(monkeypatch):
    from agent_friday.services import slice_head
    monkeypatch.setattr(slice_head, "scores_for", lambda text, block=False: {"memory": 0.02, "wiki": 0.95})
    plan = lsp._slice_plan("notes", {})
    assert plan["memory"]["action"] == "withhold" and plan["wiki"]["action"] == "include"
    assert plan["memory"]["applied"] is False, "not opted in: it orders drops, it does not withhold by itself"


def test_the_plan_adds_under_50ms_p95_at_session_start(tmp_path):
    """The latency gate: the plan is built before connect, off the audio path."""
    u = lsp.UsageStore(tmp_path / "u.json")
    text = _instruction(ctx=400, selfk=600)
    runs = []
    for i in range(60):
        t0 = time.perf_counter()
        lsp.plan_session(text, {"voice_live_trim": "on"}, ALL, workspace="mail", usage=u,
                         opening="what's on tomorrow", shadow_path=tmp_path / "s.jsonl")
        runs.append((time.perf_counter() - t0) * 1000)
    runs.sort()
    assert runs[int(0.95 * len(runs))] <= 50.0, runs[-5:]


# ── one setting, voice_live_trim: off | shadow | on ─────────────────────────

SENTINEL = "ferry"      # the synthetic body text every filler line carries


def _plan_for(mode, tmp_path, text=None, **kw):
    sp = tmp_path / "shadow.jsonl"
    settings = {"voice_live_trim": mode}
    settings.update(kw.pop("settings", {}))
    out, plan = lsp.plan_session(text if text is not None else _instruction(), settings, ALL,
                                 workspace="mail", usage=lsp.UsageStore(tmp_path / "u.json"),
                                 shadow_path=sp, **kw)
    return out, plan, sp


def test_trim_defaults_to_shadow_and_a_bad_value_reads_as_shadow():
    assert lsp.DEFAULTS["voice_live_trim"] == "shadow"
    assert lsp.trim_mode({}) == "shadow" and lsp.trim_mode({"voice_live_trim": "bogus"}) == "shadow"
    assert [lsp.trim_mode({"voice_live_trim": m}) for m in ("off", "shadow", "ON")] == ["off", "shadow", "on"]
    assert "voice_live_tool_kits" not in lsp.DEFAULTS, "folded into voice_live_trim"


def test_shadow_declares_the_same_instruction_and_tools_as_off(tmp_path):
    from google.genai import types
    from agent_friday.services import voice_engine as ve
    text = _instruction()
    off_text, off, _ = _plan_for("off", tmp_path, text)
    sh_text, sh, _ = _plan_for("shadow", tmp_path, text)
    assert off_text == sh_text == text, "byte for byte today's instruction"
    assert off.declared_names() == sh.declared_names()
    assert off.kit_arg() is None and sh.kit_arg() is None
    names = ve._voice_tool_names()
    p = lsp.SessionPlan({"voice_live_trim": "shadow"}, names, shadow_path=tmp_path / "x.jsonl")
    dump = lambda tools: [d.model_dump() for d in tools[0].function_declarations]
    assert dump(ve._build_voice_live_tools(types)) == dump(ve._build_voice_live_tools(types, kit=p.kit_arg()))
    assert "more_tools" not in {d["name"] for d in dump(ve._build_voice_live_tools(types))}


def test_on_applies_both_the_cap_and_the_kit(tmp_path):
    text = _instruction()
    out, plan, _ = _plan_for("on", tmp_path, text)
    assert lsp.estimate_tokens(out) <= 9000 < lsp.estimate_tokens(text)
    assert _policy() in out and "Law one" in out
    assert plan.kit_arg() is not None and plan.kit_arg()["compact"] is True
    assert "ask_friday" in plan.declared_names() and "more_tools" in plan.declared_names()
    assert len(plan.declared_names()) < len(ALL)


def test_shadow_reports_what_the_cap_and_the_kit_would_drop(tmp_path):
    _out, plan, sp = _plan_for("shadow", tmp_path, settings={"voice_live_instruction_max_tokens": 3000})
    recs = [json.loads(x) for x in sp.read_text(encoding="utf-8").splitlines()]
    inst = next(r for r in recs if r["event"] == "instruction")
    assert inst["tokens_if_on"] < inst["tokens_now"] and inst["floor"] > 0
    names = {n for n, _t, _r in inst["would_drop"]}
    assert {"recent_memories", "personal_context_smart_loaded_for_this_turn"} <= names
    assert all(isinstance(t, int) and t > 0 for _n, t, _r in inst["would_drop"])
    planrec = next(r for r in recs if r["event"] == "plan")
    assert planrec["would_drop"] == len(planrec["would_drop_tools"]) > 0


def test_the_shadow_log_holds_names_and_counts_never_text(tmp_path):
    text = _instruction().replace(
        "== AUTHORITATIVE CLOCK ==",
        "== PASSPHRASE NOTES ==\nthe owner's secret garden word is zinnia\n" + _filler("Big", 900)
        + "\n== AUTHORITATIVE CLOCK ==")
    out, plan, sp = _plan_for("shadow", tmp_path, text, opening="what is zinnia about",
                              settings={"voice_live_instruction_max_tokens": 3000})
    plan.note_call("ask_friday")
    plan.note_call("codebase_run")
    log = sp.read_text(encoding="utf-8")
    for needle in (SENTINEL, "zinnia", "garden", "Law one", "Warm, dry", "what is"):
        assert needle not in log, needle
    assert "passphrase_notes" in log, "a block is named, never quoted"


def test_a_tool_miss_and_a_context_miss_are_logged_apart(tmp_path):
    _out, plan, sp = _plan_for("shadow", tmp_path, settings={"voice_live_instruction_max_tokens": 3000})
    # Which tools the kit keeps depends on the router's choice, so take a dropped tool that is
    # not a context tool rather than the first dropped one.
    dropped_tool = next((n for n in plan.would_drop() if n not in lsp.CONTEXT_TOOLS), None)
    assert dropped_tool, "the capped kit drops at least one non-context tool"
    plan.note_call(dropped_tool)         # tool miss only
    plan.note_call("ask_friday")         # context miss (ask_friday is always in the kit: not a tool miss)
    recs = [json.loads(x) for x in sp.read_text(encoding="utf-8").splitlines() if '"call"' in x]
    assert [(r["miss"], r["context_miss"]) for r in recs] == [(True, False), (False, True)]


def test_the_two_rates_that_gate_on(tmp_path):
    sp = tmp_path / "shadow.jsonl"
    for i in range(10):
        out, plan = lsp.plan_session(_instruction(), {"voice_live_trim": "shadow",
                                                      "voice_live_instruction_max_tokens": 3000}, ALL, workspace="mail",
                                     usage=lsp.UsageStore(tmp_path / "u.json"), shadow_path=sp,
                                     now=1_700_000_000 + i)
        for _ in range(9):
            plan.note_call("query_calendar")
        if i == 0:
            plan.note_call("ask_friday")        # one of ten sessions needed dropped context
            plan.note_call("codebase_run")      # and one call was outside the kit
    st = lsp.shadow_stats(sp)
    assert st["sessions"] == 10 and st["sessions_with_dropped_context"] == 10
    assert st["context_miss_sessions"] == 1 and abs(st["context_miss_rate"] - 0.1) < 1e-9
    assert st["calls"] >= 90 and 0 < st["tool_miss_rate"] < 0.03


def test_no_context_dropped_means_no_context_miss(tmp_path):
    lean = "You are Friday.\n\n" + _policy()
    _out, plan, sp = _plan_for("shadow", tmp_path, lean)
    plan.note_call("ask_friday")
    st = lsp.shadow_stats(sp)
    assert st["context_miss_sessions"] == 0 and st["sessions_with_dropped_context"] == 0


def test_off_writes_no_log_at_all(tmp_path):
    _out, plan, sp = _plan_for("off", tmp_path)
    plan.note_call("ask_friday")
    assert not sp.exists()


def test_the_trim_row_is_in_both_html_files_with_no_preselected_choice():
    for f in ("index.html", "ui_parts/app.html"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert src.count("voice_live_trim") >= 2, f
        assert "Trim what each call carries" in src, f
    jsx = (ROOT / "ui_parts/app.html").read_text(encoding="utf-8")
    at = jsx.index("Trim what each call carries")
    row = jsx[at:at + 3000]
    assert "'shadow'" in row and "'on'" in row and "'off'" in row
    assert "(recommended)" in row and "defaultChecked" not in row


def test_the_settings_manifest_notes_the_trim_row():
    m = json.loads((ROOT / "src/agent_friday/resources/laya_manifests/settings.json").read_text(encoding="utf-8"))
    note = next(n for n in m["notes"] if n["id"] == "voice_live_trim")
    assert "voice_live_trim" in note["text"]
    assert note["verbs"] == ["voice_session.fewer_tools", "voice_session.everything"]
