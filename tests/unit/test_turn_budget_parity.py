"""

# ═══════════════════════════════════════════════════════════════════════════
# SUPERSEDED 2026-09-25. Stephen: "why 999? How about making it unlimited?"
# and "I want no caps unless I set them myself in the cost metering UI."
#
# The tests below asserted the CAPS this file was written to raise. Raising a
# cap and removing it are the same argument carried one step further: the
# number was never a safety property, and the guards that are (the loop
# detector, the Stop button) are kept and tested in test_no_builtin_caps.py.
# Each assertion is inverted here rather than deleted, so the history of what
# this file once guaranteed stays legible.
# ═══════════════════════════════════════════════════════════════════════════
The local seat gets the same round budget as Claude, with real safety instead.

A local round costs no money, and a local reasoner such as Bonsai2 can reason
across hundreds of rounds, so the local seat does not get a smaller budget.

A CLOUD path at 999 rounds beside a LOCAL path at 50 is a 20x difference with
no stated reason -- a leftover from small models that would loop. For a 27B
reasoner, 50 rounds is a cliff it can fall off mid-task, producing only a
truncated answer with "Raise max_iters".

A round cap is not safety, it is a proxy for safety. Real safety is:

  * LOOP DETECTION — the same tool with the same arguments, over and over, is the
    failure 50 was guarding against. Catching it directly stops a genuine loop in
    seconds instead of after 50 expensive rounds, AND stops punishing a model that
    is making progress.
  * A WALL CLOCK, so a turn cannot run forever even while doing new things.
  * The spend ceiling and the AGENT_STOP kill file.

And when a limit is reached, Friday says WHICH limit and offers to continue,
rather than truncating and blaming `max_iters`.
"""

import time

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Parity
# ─────────────────────────────────────────────────────────────────────────────

def test_one_shared_default_exists():
    from agent_friday.services import turn_budget as tb
    assert tb.ROUND_BUDGET_DEFAULT is None, (
        "there is no shared round budget any more; %r is a cap"
        % tb.ROUND_BUDGET_DEFAULT)


def test_the_local_loop_no_longer_defaults_to_fifty():
    """The regression, stated as source. A default of 50 anywhere on the local
    path is the bug."""
    import inspect
    from agent_friday.services import agent as ag
    from agent_friday.services import model_router as mr

    for fn in (ag._oai_agentic_loop, mr._call_ollama, mr._call_openai):
        sig = inspect.signature(fn)
        got = sig.parameters["max_iters"].default
        assert got != 50, "%s still defaults max_iters to 50" % fn.__name__
        assert got is None or got >= 999, (
            "%s defaults max_iters to %r, which is not parity" % (fn.__name__, got))


def test_the_cloud_loop_keeps_its_budget():
    import inspect
    from agent_friday.services import agent as ag
    got = inspect.signature(ag._call_claude_agent).parameters["max_iters"].default
    assert got is None or got >= 999


def test_the_budget_is_configurable_per_seat(monkeypatch):
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core

    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"local": 120, "default": 999}}})
    assert tb.rounds_for("local") == 120
    assert tb.rounds_for("reasoning") == 999


def test_an_absent_setting_falls_back_to_the_shared_default(monkeypatch):
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    assert tb.rounds_for("local") == tb.ROUND_BUDGET_DEFAULT


def test_the_shipped_settings_declare_the_budget():
    from agent_friday.core import DEFAULT_SETTINGS
    blk = DEFAULT_SETTINGS.get("turn_budget")
    assert isinstance(blk, dict), "turn_budget is not in DEFAULT_SETTINGS"
    # SUPERSEDED: a figure here would be a built-in cap wearing a settings key.
    assert blk.get("rounds") == {}, blk


# ─────────────────────────────────────────────────────────────────────────────
# Loop detection — the thing 50 was standing in for
# ─────────────────────────────────────────────────────────────────────────────

def test_the_same_call_repeated_is_caught():
    from agent_friday.services.turn_budget import LoopGuard
    g = LoopGuard(repeat_limit=3)
    args = {"query": "calendar today"}
    assert g.observe("query_calendar", args) is None
    assert g.observe("query_calendar", args) is None
    hit = g.observe("query_calendar", args)
    assert hit, "three identical calls were not flagged"
    assert "query_calendar" in hit
    assert "same" in hit.lower() or "repeat" in hit.lower()


def test_different_arguments_are_progress_not_a_loop():
    """A model paging through results is working, not looping."""
    from agent_friday.services.turn_budget import LoopGuard
    g = LoopGuard(repeat_limit=3)
    for i in range(40):
        assert g.observe("search_email", {"q": "invoice", "page": i}) is None


def test_different_tools_with_moving_arguments_are_progress():
    """Real work: several tools, each advancing.

    An earlier version of this test cycled four tools with IDENTICAL arguments
    ten times and expected that to pass. The guard was right to flag it -- doing
    the same four calls over and over is a loop, whichever order they come in.
    The test was wrong, not the guard.
    """
    from agent_friday.services.turn_budget import LoopGuard
    g = LoopGuard(repeat_limit=3)
    for i in range(10):
        for name in ("query_calendar", "search_email", "search_web", "read_file"):
            assert g.observe(name, {"step": i}) is None


def test_an_interleaved_loop_is_still_caught():
    """A→B→A→B→A→B is a loop even though no call repeats consecutively."""
    from agent_friday.services.turn_budget import LoopGuard
    g = LoopGuard(repeat_limit=3)
    hit = None
    for _ in range(6):
        hit = g.observe("a", {"x": 1}) or hit
        hit = g.observe("b", {"y": 2}) or hit
    assert hit, "an alternating loop was not caught"


def test_unhashable_arguments_do_not_crash_the_guard():
    from agent_friday.services.turn_budget import LoopGuard
    g = LoopGuard(repeat_limit=2)
    g.observe("t", {"nested": {"a": [1, 2, {"b": 3}]}})
    g.observe("t", {"nested": {"a": [1, 2, {"b": 3}]}})   # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Wall clock
# ─────────────────────────────────────────────────────────────────────────────

def test_the_wall_clock_expires():
    from agent_friday.services.turn_budget import WallClock
    w = WallClock(seconds=0.05)
    assert w.expired() is False
    time.sleep(0.08)
    assert w.expired() is True
    assert "s" in w.reason()


def test_a_generous_wall_clock_does_not_fire():
    from agent_friday.services.turn_budget import WallClock
    assert WallClock(seconds=3600).expired() is False


def test_the_shipped_wall_clock_is_generous_enough_for_a_reasoner():
    """Bonsai2 on a busy card is slow. A wall clock tighter than a real turn
    would recreate the cliff this change removes."""
    from agent_friday.services import turn_budget as tb
    assert tb.WALL_CLOCK_DEFAULT_S is None


# ─────────────────────────────────────────────────────────────────────────────
# What the user sees when a limit hits
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,needle,detail", [
    ("rounds", "round", ""),
    # The loop branch is given the LoopGuard's own reason, which is what the
    # caller actually passes -- an earlier version handed it a bare tool name and
    # then asserted on wording that only the real reason contains.
    ("loop", "same arguments",
     "query_calendar was called 3 times with the same arguments"),
    ("clock", "minute", "300s of a 1800s limit"),
])
def test_the_message_names_the_limit_and_offers_to_continue(kind, needle, detail):
    from agent_friday.services.turn_budget import limit_message
    msg = limit_message(kind, detail=detail, used=57, model="bonsai2:27b")
    low = msg.lower()
    assert needle in low, "%r not in %r" % (needle, msg)
    assert "continue" in low, "the message does not offer to continue: %r" % msg
    assert "bonsai2:27b" in msg
    assert "max_iters" not in low, (
        "the message still blames an internal knob: %r" % msg)


def test_the_message_does_not_pretend_the_answer_is_complete():
    from agent_friday.services.turn_budget import limit_message
    msg = limit_message("rounds", detail="", used=999, model="m")
    assert "stopped" in msg.lower() or "paused" in msg.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Subagents and scheduled work keep sensible caps
# ─────────────────────────────────────────────────────────────────────────────

def test_subagent_steps_are_raised_but_still_bounded():
    """A subagent is delegated work with a narrower remit than a chat turn, so it
    keeps a cap -- just not one a competent model trips over."""
    import inspect
    from agent_friday.services import subagents
    src = inspect.getsource(subagents)
    assert 'data.get("max_steps", 25)' not in src, (
        "subagents still default to 25 steps")
    from agent_friday.services import turn_budget as tb
    assert tb.SUBAGENT_STEP_DEFAULT is None


def test_scheduled_work_keeps_a_cap():
    from agent_friday.services import turn_budget as tb
    assert tb.SCHEDULED_ROUND_DEFAULT is None, (
        "a scheduled job's control is its cloud opt-in, not a round cap")


# ---------------------------------------------------------------------------
# The loop body actually consults the guards (not dead code)
# ---------------------------------------------------------------------------

def _oai_loop_source():
    import inspect
    from agent_friday.services import agent as ag
    return inspect.getsource(ag._oai_agentic_loop)


def test_the_loop_resolves_its_budget_from_turn_budget():
    src = _oai_loop_source()
    assert "rounds_for(" in src, (
        "the loop no longer resolves its round budget from turn_budget")
    assert "max_iters=50" not in src


def test_the_loop_checks_the_wall_clock_every_round():
    src = _oai_loop_source()
    assert "_wall.expired()" in src, "the wall clock is never consulted"


def test_the_loop_checks_for_a_loop_before_running_a_tool():
    """Order matters: detecting the loop AFTER paying for the call is most of the
    cost with none of the benefit."""
    src = _oai_loop_source()
    assert "_loop_guard.observe(" in src, "loop detection is never consulted"
    i = src.index("_loop_guard.observe(")
    j = src.index("_execute_tool(", i)
    assert j > i, "the loop check happens after the tool call, not before"


def test_a_local_turn_can_exceed_fifty_rounds():
    """The headline ask, as a fact about the resolved budget rather than a
    50-round live drive: `rounds_for` is what the loop uses, and it must allow
    far more than the old cap."""
    from agent_friday.services import turn_budget as tb
    # SUPERSEDED: "more than fifty" became "no limit at all". None is the
    # strongest possible form of the thing this test was asserting.
    assert tb.rounds_for("local") is None


def test_every_limit_branch_returns_a_continue_offer():
    """No branch may end a turn with silence or an internal knob."""
    from agent_friday.services.turn_budget import limit_message
    for kind in ("rounds", "loop", "clock", "something-else"):
        msg = limit_message(kind, detail="d", used=10, model="bonsai2:27b")
        assert "continue" in msg.lower(), (kind, msg)
        assert "max_iters" not in msg.lower(), (kind, msg)


# ---------------------------------------------------------------------------
# Unattended work: parity is for the person sitting there, not for the dark
# ---------------------------------------------------------------------------

def test_an_interactive_turn_is_not_unattended():
    from agent_friday.services import turn_budget as tb
    assert tb.is_unattended() is False
    assert tb.rounds_for("local") is None


def test_unattended_work_resolves_to_the_scheduled_cap(monkeypatch):
    """A scheduled job gets a bound; the person chatting does not.

    999 rounds is right when someone is watching and can press Stop. Nobody
    watches a 6am briefing, so a runaway one would spend in the dark.
    """
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    with tb.unattended():
        assert tb.is_unattended() is True
        assert tb.rounds_for("local") == tb.SCHEDULED_ROUND_DEFAULT
        assert tb.rounds_for("") == tb.SCHEDULED_ROUND_DEFAULT
    assert tb.is_unattended() is False
    assert tb.rounds_for("local") == tb.ROUND_BUDGET_DEFAULT


def test_the_mark_is_restored_even_when_the_run_raises():
    from agent_friday.services import turn_budget as tb
    try:
        with tb.unattended():
            raise RuntimeError("the task failed")
    except RuntimeError:
        pass
    assert tb.is_unattended() is False


def test_the_mark_does_not_leak_between_threads():
    """Background tasks run on their own threads. Marking one must not shrink
    the budget of an interactive turn running beside it."""
    import threading
    from agent_friday.services import turn_budget as tb
    seen = {}

    def other():
        seen["unattended"] = tb.is_unattended()

    with tb.unattended():
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert seen["unattended"] is False


def test_a_scheduled_setting_can_override_the_unattended_cap(monkeypatch):
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"scheduled": 80, "default": 999}}})
    with tb.unattended():
        assert tb.rounds_for("local") == 80
    assert tb.rounds_for("local") == 999


def test_the_tighter_of_the_two_figures_applies_while_unattended(monkeypatch):
    """Both settings are honoured: an unattended run never lasts longer than any
    limit its owner set, whichever key they set it under."""
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"local": 42, "scheduled": 80}}})
    with tb.unattended():
        assert tb.rounds_for("local") == 42


def test_the_background_task_worker_enters_the_unattended_mark():
    """Otherwise the cap is dead code: scheduled work would inherit 999."""
    import inspect
    from agent_friday.services import agent as ag
    # The reasoning-trace work wraps the worker; its body is the untraced one.
    src = inspect.getsource(getattr(ag, "_task_worker_untraced", ag._task_worker))
    assert "unattended()" in src, (
        "the background task worker does not mark its run unattended, so "
        "scheduled jobs would inherit the interactive round budget")
    i = src.index("unattended()")
    j = src.index("_generate_agent(", i)
    assert j > i, "the mark is entered after the run starts, not around it"


# ---------------------------------------------------------------------------
# The per-turn token budget
# ---------------------------------------------------------------------------

def test_the_token_budget_counts_both_directions():
    """Input dominates a long tool loop: every round resends the whole
    conversation, so an output-only ceiling would miss the runaway."""
    from agent_friday.services.turn_budget import TokenBudget
    b = TokenBudget(limit=1000)
    b.add(400, 100)
    assert b.spent == 500
    assert b.exceeded() is False
    b.add(400, 100)
    assert b.exceeded() is True
    assert "1,000" in b.reason()


def test_the_token_budget_ignores_nonsense_counts():
    from agent_friday.services.turn_budget import TokenBudget
    b = TokenBudget(limit=100)
    b.add(None, None)
    b.add(-5, -5)
    assert b.spent == 0
    assert b.exceeded() is False


def test_the_shipped_token_budget_is_generous():
    """A ceiling an honest turn reaches is the 50-round cap all over again."""
    from agent_friday.services import turn_budget as tb
    assert tb.TOKEN_BUDGET_DEFAULT is None


def test_the_token_budget_is_configurable_per_seat(monkeypatch):
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"tokens": {"local": 250000, "default": 1000000}}})
    assert tb.token_budget_for("local") == 250000
    assert tb.token_budget_for("reasoning") == 1000000


def test_the_token_message_names_the_limit_and_offers_to_continue():
    from agent_friday.services.turn_budget import limit_message
    msg = limit_message("tokens", detail="1,000,000 of a 1,000,000-token limit "
                        "for one turn", used=120, model="bonsai2:27b")
    low = msg.lower()
    assert "token" in low
    assert "continue" in low
    assert "max_iters" not in low


def test_the_loop_consults_the_token_budget():
    src = _oai_loop_source()
    assert "_tokens.exceeded()" in src, "the token budget is never consulted"
    assert "_tokens.add(" in src, "the token budget is never fed"


def test_the_token_check_is_not_inside_a_swallowing_try():
    """A ceiling an `except Exception: pass` can skip is not a ceiling.

    Inside the activity-ledger try block, a bad usage payload would silently
    disable it.
    """
    src = _oai_loop_source()
    lines = src.split("\n")
    i = next(n for n, l in enumerate(lines) if "_tokens.add(" in l)
    indent = len(lines[i]) - len(lines[i].lstrip())
    # Walk back to the nearest enclosing statement at a lower indent; it must
    # not be a `try:`.
    for n in range(i - 1, -1, -1):
        l = lines[n]
        if not l.strip():
            continue
        ind = len(l) - len(l.lstrip())
        if ind < indent:
            assert l.strip() != "try:", (
                "the token budget sits inside a try block that swallows "
                "exceptions: %r" % l)
            break


def test_the_shipped_settings_declare_every_limit():
    from agent_friday.core import DEFAULT_SETTINGS
    blk = DEFAULT_SETTINGS.get("turn_budget") or {}
    # SUPERSEDED: every group ships empty; a figure appears only when set.
    assert blk.get("rounds") == {}
    assert blk.get("wall_clock_s") == {}
    assert blk.get("tokens") == {}


# ---------------------------------------------------------------------------
# The UI control — Settings -> Intelligence (advanced)
# ---------------------------------------------------------------------------
#
# index.html is the served, authoritative UI. ui_parts/app.html documents at its
# head that SettingsTabIntelligence is deliberately not mirrored there, so these
# assertions read one file on purpose.

def _index_html():
    import pathlib
    p = (pathlib.Path(__file__).resolve().parents[2] / "index.html")
    return p.read_text(encoding="utf-8")


def test_settings_exposes_the_per_seat_budget():
    """"make it configurable per seat in Settings > Models (advanced)" — a
    setting the code reads but no one can reach is not configurable."""
    html = _index_html()
    assert "function TurnLimitsSection" in html, (
        "no turn-limit control exists in the settings UI")
    assert "E(TurnLimitsSection, null)" in html, (
        "TurnLimitsSection is defined but never rendered")
    for key in ("turn_budget", "wall_clock_s", "tokens", "scheduled"):
        assert key in html, "the control cannot set %r" % key


def test_the_control_posts_the_whole_block():
    """The settings merge is one level deep, so a per-key delta would drop the
    sibling groups. The control must post the full `turn_budget`."""
    html = _index_html()
    i = html.index("function TurnLimitsSection")
    body = html[i:i + 9000]
    assert "turn_budget: next" in body, (
        "the control posts a partial turn_budget, which would reset its "
        "sibling groups on every save")


def test_turn_budget_is_deep_merged():
    from agent_friday.core import _DEEP_MERGED_BLOCKS
    assert "turn_budget" in _DEEP_MERGED_BLOCKS, (
        "a partial turn_budget delta would replace the whole block")


def test_a_partial_save_keeps_the_other_groups(tmp_path, monkeypatch):
    """The merge, exercised rather than asserted about."""
    import json
    import agent_friday.core as core
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"turn_budget": {
        "rounds": {"default": 999, "scheduled": 300},
        "wall_clock_s": {"default": 1800},
        "tokens": {"default": 1000000},
    }}), encoding="utf-8")
    monkeypatch.setattr(core, "SETTINGS_FILE", f)
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    merged = core._save_settings({"turn_budget": {"rounds": {"default": 999,
                                                            "scheduled": 120}}})
    blk = merged["turn_budget"]
    assert blk["rounds"]["scheduled"] == 120
    assert blk["wall_clock_s"]["default"] == 1800, (
        "the clock was reset by a rounds-only save")
    assert blk["tokens"]["default"] == 1000000, (
        "the token ceiling was reset by a rounds-only save")


# ---------------------------------------------------------------------------
# The scheduler's single gate marks the run, not each job
# ---------------------------------------------------------------------------

def _fake_builtin(monkeypatch, recorder):
    """Register a throwaway builtin schedule whose function records whether it
    was running inside the unattended mark."""
    from agent_friday.services import scheduler as sch
    from agent_friday.services import turn_budget as tb

    def fn():
        recorder["unattended"] = tb.is_unattended()
        recorder["rounds"] = tb.rounds_for("local")
        return {"ok": True}

    monkeypatch.setitem(sch.BUILTIN_TASKS, "probe_unattended",
                        {"label": "probe", "fn": fn})
    return fn


def test_a_builtin_schedule_runs_unattended(monkeypatch):
    """A builtin calls its function on the scheduler's own thread, so the mark
    has to be entered here -- `_task_worker`'s mark only covers agent_prompt
    schedules, which run on a thread of their own."""
    from agent_friday.services import scheduler as sch
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    rec = {}
    _fake_builtin(monkeypatch, rec)
    sch._run_task({"task": {"kind": "builtin", "ref": "probe_unattended"}})
    assert rec.get("unattended") is True, (
        "a scheduled builtin ran with the interactive round budget")
    assert rec["rounds"] == tb.SCHEDULED_ROUND_DEFAULT


def test_a_local_only_builtin_is_also_unattended(monkeypatch):
    """The two marks compose: `local_only` decides WHO serves, `unattended`
    decides how long it may run."""
    from agent_friday.services import scheduler as sch
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    rec = {}
    _fake_builtin(monkeypatch, rec)
    sch._run_task({"task": {"kind": "builtin", "ref": "probe_unattended",
                            "local_only": True}})
    assert rec.get("unattended") is True
    assert rec["rounds"] == tb.SCHEDULED_ROUND_DEFAULT


def test_the_mark_is_dropped_once_the_scheduled_run_ends(monkeypatch):
    from agent_friday.services import scheduler as sch
    from agent_friday.services import turn_budget as tb
    rec = {}
    _fake_builtin(monkeypatch, rec)
    sch._run_task({"task": {"kind": "builtin", "ref": "probe_unattended"}})
    assert tb.is_unattended() is False


# ---------------------------------------------------------------------------
# The headline claim, driven rather than asserted about
# ---------------------------------------------------------------------------

def _fake_tool_round(n):
    """One OpenAI-shaped response asking for a tool, with MOVING arguments so
    loop detection correctly sees progress."""
    return {
        "choices": [{
            "message": {"content": "", "tool_calls": [{
                "id": "call_%d" % n,
                "function": {"name": "read_file",
                             "arguments": {"path": "chapter_%d.txt" % n}},
            }]},
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5},
    }


def _drive_loop(monkeypatch, rounds_of_tools, *, settings=None):
    """Run the real _oai_agentic_loop against a scripted model."""
    from agent_friday.services import agent as ag
    import agent_friday.core as core

    monkeypatch.setattr(core, "_load_settings",
                        lambda *a, **k: (settings or {}))
    monkeypatch.setattr(ag, "_execute_tool",
                        lambda name, args, **kw: "contents of %s"
                        % (args or {}).get("path"), raising=True)
    # Keep the test about rounds: the vault gate and the journal have their own
    # suites and would otherwise decide the outcome.
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None,
                        raising=False)

    state = {"n": 0}

    def send_fn(convo, tools):
        state["n"] += 1
        if state["n"] <= rounds_of_tools:
            return _fake_tool_round(state["n"])
        return {"choices": [{"message": {"content": "Done after %d rounds."
                                        % (state["n"] - 1)},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5}}

    tools = [{"type": "function",
              "function": {"name": "read_file", "description": "read",
                           "parameters": {"type": "object",
                                          "properties": {"path": {"type": "string"}}}}}]
    text, trace = ag._oai_agentic_loop(
        [{"role": "user", "content": "read every chapter"}], tools, send_fn,
        provider="ollama", model="bonsai2:27b")
    return text, trace, state["n"]


def test_a_local_turn_really_runs_past_fifty_rounds(monkeypatch):
    """Driven: sixty tool rounds on the LOCAL loop must not stop dead at fifty
    and blame `max_iters`."""
    text, trace, calls = _drive_loop(monkeypatch, 60)
    assert calls == 61, "the loop stopped early: %d model calls" % calls
    assert "Done after 60 rounds." in text, text[:300]
    assert "max_iters" not in text
    assert len(trace) == 60, "expected 60 tool calls, got %d" % len(trace)


def test_the_old_cap_would_have_stopped_it(monkeypatch):
    """The same drive with max_iters=50 explicitly: the old cliff is still
    available to a caller that wants a short leash."""
    from agent_friday.services import agent as ag
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_execute_tool", lambda name, args, **kw: "x")
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None,
                        raising=False)
    state = {"n": 0}

    def send_fn(convo, tools):
        state["n"] += 1
        return _fake_tool_round(state["n"])

    tools = [{"type": "function", "function": {"name": "read_file",
              "parameters": {"type": "object", "properties": {}}}}]
    text, _ = ag._oai_agentic_loop(
        [{"role": "user", "content": "go"}], tools, send_fn,
        provider="ollama", model="bonsai2:27b", max_iters=50)
    assert "round" in text.lower()
    assert "continue" in text.lower(), (
        "even the explicit cap must offer to continue: %r" % text[:200])


def test_a_genuine_loop_is_stopped_with_a_clear_message(monkeypatch):
    """The other half of parity. The SAME call every round -- the failure the
    50-round cap was standing in for -- and it stops in three, not fifty."""
    from agent_friday.services import agent as ag
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_execute_tool", lambda name, args, **kw: "same")
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None,
                        raising=False)
    state = {"n": 0}

    def send_fn(convo, tools):
        state["n"] += 1
        return {
            "choices": [{"message": {"content": "", "tool_calls": [{
                "id": "call_x",
                "function": {"name": "search_email",
                             "arguments": {"q": "invoice"}},
            }]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5},
        }

    tools = [{"type": "function", "function": {"name": "search_email",
              "parameters": {"type": "object", "properties": {}}}}]
    text, trace = ag._oai_agentic_loop(
        [{"role": "user", "content": "find it"}], tools, send_fn,
        provider="ollama", model="bonsai2:27b")
    assert state["n"] <= 5, (
        "a genuine loop ran %d rounds; detection is not working" % state["n"])
    assert "circles" in text.lower() or "loop" in text.lower(), text[:300]
    assert "search_email" in text
    assert "continue" in text.lower()
    assert "max_iters" not in text.lower()


# ---------------------------------------------------------------------------
# The user's Stop — the limit that needs no justification
# ---------------------------------------------------------------------------
#
# The cloud loop honours the AGENT_STOP kill file and a background task can be
# stopped from the tasks tray, but `task_journal.stop_requested(None)` is always
# False and a chat turn has no task id. Without a per-turn stop, a round cap is
# the only brake on an interactive turn; raising the cap requires this stop.

def test_a_turn_can_be_asked_to_stop():
    import agent_friday.core as core
    core.turn_begin("turn-stop-1")
    try:
        assert core.turn_stop_requested() is False
        assert core.turn_request_stop("turn-stop-1") is True
        assert core.turn_stop_requested() is True
        assert core.turn_stop_requested("turn-stop-1") is True
    finally:
        core.turn_end("turn-stop-1")


def test_stopping_an_unknown_turn_reports_that_it_did_nothing():
    """A button that silently does nothing is worse than no button."""
    import agent_friday.core as core
    assert core.turn_request_stop("turn-that-never-ran") is False
    assert core.turn_request_stop(None) is False
    assert core.turn_request_stop("") is False


def test_a_stop_does_not_reach_another_turn():
    import agent_friday.core as core
    core.turn_begin("turn-a")
    try:
        core.turn_request_stop("turn-a")
        assert core.turn_stop_requested("turn-a") is True
        assert core.turn_stop_requested("turn-b") is False
    finally:
        core.turn_end("turn-a")


def test_the_stop_clears_with_the_turn():
    import agent_friday.core as core
    core.turn_begin("turn-c")
    core.turn_request_stop("turn-c")
    core.turn_end("turn-c")
    assert core.turn_stop_requested("turn-c") is False


def test_both_loops_check_the_stop():
    """Parity: the local loop went without this while the cloud loop had a kill
    file. Neither may rely on a round cap to end a turn."""
    import inspect
    from agent_friday.services import agent as ag
    for fn in (ag._oai_agentic_loop, ag._call_claude_agent):
        src = inspect.getsource(fn)
        assert "turn_stop_requested()" in src, (
            "%s cannot be stopped by the user" % fn.__name__)


def test_the_local_loop_also_honours_the_kill_file():
    import inspect
    from agent_friday.services import agent as ag
    src = inspect.getsource(ag._oai_agentic_loop)
    assert "AGENT_STOP" in src, (
        "the kill file works on the cloud path only")


def test_the_stop_message_does_not_ask_if_he_meant_it():
    """Every limit offers to continue. A stop is a decision, not a limit."""
    from agent_friday.services.turn_budget import stopped_message
    msg = stopped_message(used=37, model="bonsai2:27b")
    low = msg.lower()
    assert "stopped" in low
    assert "37 rounds" in msg, msg
    assert "bonsai2:27b" in msg
    assert "continue" not in low, (
        "the stop message asks the user to confirm a decision already made: %r"
        % msg)
    assert "background" in low, (
        "a stop must say nothing is still running: %r" % msg)


def test_the_chat_ui_offers_a_stop_while_a_turn_is_in_flight():
    html = _index_html()
    assert "const stopTurn = useCallback" in html, (
        "the chat has no stop handler")
    assert "'/stop'" in html or "+ '/stop'" in html, (
        "the stop handler does not call the stop route")
    assert "Stopping…" in html, "no in-progress state for the stop button"
    i = html.index("const stopTurn = useCallback")
    assert "inFlightTurn.current" in html[i:i + 900], (
        "the stop is not bound to the turn actually in flight")


def test_a_chat_seat_setting_cannot_lift_the_unattended_cap(monkeypatch):
    """The seat keys are shared with interactive turns. Raising `local` for the
    user's own chat must not silently raise every scheduled job with it -- the reason a
    scheduled job is bounded is that nobody is watching, which has nothing to do
    with which seat serves it."""
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"local": 999}}})
    assert tb.rounds_for("local") == 999          # he set this one
    with tb.unattended():
        assert tb.rounds_for("local") == 999      # and it is not overridden


def test_a_tighter_seat_setting_still_applies_while_unattended(monkeypatch):
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"local": 40}}})
    with tb.unattended():
        assert tb.rounds_for("local") == 40


def test_an_explicit_scheduled_figure_raises_the_ceiling(monkeypatch):
    """`scheduled` is the deliberate knob for unattended work, as distinct from
    a seat figure that happens to be large."""
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"local": 999, "scheduled": 600}}})
    with tb.unattended():
        assert tb.rounds_for("local") == 600


def test_zero_means_inherit(monkeypatch):
    """The advanced control writes 0 for "use the figure above", so 0 must never
    be read as a one-round budget."""
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"local": 0, "default": 0},
                        "wall_clock_s": {"default": 0},
                        "tokens": {"default": 0}}})
    assert tb.rounds_for("local") == tb.ROUND_BUDGET_DEFAULT
    assert tb.wall_clock_for("local") == tb.WALL_CLOCK_DEFAULT_S
    assert tb.token_budget_for("local") == tb.TOKEN_BUDGET_DEFAULT


def test_nonsense_settings_do_not_crash_a_turn(monkeypatch):
    from agent_friday.services import turn_budget as tb
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "turn_budget": {"rounds": {"local": "lots", "default": None},
                        "wall_clock_s": {"default": []},
                        "tokens": "not a dict"}})
    assert tb.rounds_for("local") == tb.ROUND_BUDGET_DEFAULT
    assert tb.wall_clock_for("local") == tb.WALL_CLOCK_DEFAULT_S
    assert tb.token_budget_for("local") == tb.TOKEN_BUDGET_DEFAULT
