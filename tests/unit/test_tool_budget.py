"""The request has to be buildable before anything else can be true.

Measured on the maintainer's machine 2026-08-18, from the daemon itself:

    request (46288 tokens) exceeds the available context size (32768 tokens)

Every local turn 400'd and the router fell back to Anthropic, so what he saw
was "it took forever to reply then kicked back to Sonnet 4.6 again, which I do
not want". The model, the seat, the picker and the routing mode were all
correct. 112 connector tools simply did not fit in the window.
"""
import pytest

from agent_friday.services import tool_budget as tb


@pytest.fixture(autouse=True)
def quiet():
    tb._ANNOUNCED.clear()
    tb._SERVED_CACHE.clear()
    yield
    tb._ANNOUNCED.clear()
    tb._SERVED_CACHE.clear()


def _tool(name, size=200):
    return {"name": name, "description": "d" * size,
            "input_schema": {"type": "object",
                             "properties": {"a": {"type": "string",
                                                  "description": "x" * size}}}}


def _seat(monkeypatch, window):
    monkeypatch.setattr(tb, "_window", lambda m: window)


CORE = [_tool(f"core_{i}") for i in range(20)]
CONN = [_tool(f"mcp_hf_{i}") for i in range(112)]


def test_the_research_floor_survives_a_punishing_prompt(monkeypatch):
    """knowledge_query must not be trimmable, at any prompt length.

    THE OBSERVED FAILURE: on 2026-09-10 a 17,650-token prompt on the
    fridayweaver seat trimmed 75 core tools to 40 and dropped
    `knowledge_query`, `read_doc`, `search_drive`, `search_news` and
    `search_files` in the same turn -- every lookup route Friday had. The
    intent tier did not save it, because a turn does not have to mention the
    knowledge graph for the next turn to need it.

    THIS TEST COULD FAIL: with `_FLOOR_TOOLS` reverted to ranking-only, the
    floor tools sit at the tail of a 60-tool sort under a budget that fits
    about four, and `knowledge_query` is dropped. That is the state this
    assertion is written against, not a tautology about a list.
    """
    _seat(monkeypatch, 32768)
    # The pressure has to be hard enough that the ESSENTIAL tier alone cannot
    # save them: a first draft of this test used cheap decoys, and the floor
    # tools survived on essential-set ranking whether the floor existed or
    # not. It passed while proving nothing. So: every essential tool, all fat,
    # on a budget that fits about two of them.
    tools = [_tool(n, size=3000) for n in sorted(tb._ESSENTIAL_TOOLS)]
    kept, _note = tb.fit_tools_to_seat("seat:e2b", tools, prompt_cost=20000)
    names = {t["name"] for t in kept}
    assert len(names) < len(tools), \
        "the budget must actually have bitten, or this proves nothing"
    for n in tb._FLOOR_TOOLS:
        assert n in names, f"{n} was trimmed away; the floor did not hold"

    # And the falsification, in the test rather than in a comment: with the
    # reservation removed, these same four are dropped. Measured: 4 kept with
    # the floor, 2 kept without it and none of them a floor tool.
    monkeypatch.setattr(tb, "_FLOOR_TOOLS", ())
    kept2, _ = tb.fit_tools_to_seat("seat:e2b-nofloor", tools,
                                    prompt_cost=20000)
    names2 = {t["name"] for t in kept2}
    assert not [n for n in ("knowledge_query", "search_wiki", "read_wiki",
                            "search_web") if n in names2], \
        ("without the floor these should be trimmed -- if they survive, this "
         "test is not measuring the floor")


def test_knowledge_query_is_a_real_tool_name(monkeypatch):
    """The floor must name tools that exist, or it protects nothing.

    A protected list of misspelled names is the most comfortable kind of bug:
    every test passes and the tool is still missing at runtime.
    """
    agent = pytest.importorskip("agent_friday.services.agent")
    registry = {t.get("name") for t in getattr(agent, "CLAUDE_TOOLS", [])}
    assert registry, "no tool registry to check against"
    for n in tb._FLOOR_TOOLS:
        assert n in registry, f"{n} is in the floor but not in the registry"


def test_connector_tools_are_dropped_when_they_do_not_fit(monkeypatch):
    _seat(monkeypatch, 32768)
    kept, note = tb.fit_tools_to_seat("seat:9b", CORE + CONN)
    assert [t["name"] for t in kept] == [t["name"] for t in CORE]
    assert note and "not loaded" in note


def test_everything_travels_when_it_fits(monkeypatch):
    _seat(monkeypatch, 1_000_000)
    kept, note = tb.fit_tools_to_seat("seat:9b", CORE + CONN)
    assert len(kept) == len(CORE) + len(CONN)
    assert note is None


def test_core_is_droppable_on_a_seat_that_cannot_hold_it(monkeypatch):
    """REPLACES `test_fridays_own_tools_are_never_dropped`.

    That test asserted core tools are never trimmed — "they are what she IS".
    The sentiment is right and the invariant is not: on a seat that genuinely
    cannot hold them, sending all of core is not loyalty, it is a guaranteed
    provider 400. That is the 46k-into-32k failure this module exists to
    prevent, and on a vault turn there is no cloud leg to catch it, so the
    honest 400 is just the work not happening.

    What must survive is not the whole set — it is Friday's ability to ground
    an answer and finish a task, plus an accurate account of what is missing.
    """
    _seat(monkeypatch, 2048)
    kept, note = tb.fit_tools_to_seat("tiny:1b", CORE + CONN)
    assert len(kept) < len(CORE)
    assert not any(t["name"].startswith("mcp_") for t in kept)
    assert note


def test_essential_tools_outrank_cheap_ones(monkeypatch):
    """Survival must not be decided by description length.

    Measured on the live registry 2026-08-24: the eight tools dropped first
    were the eight most EXPENSIVE schemas, while `get_career_pipeline` (40
    tokens) outlived `spawn_task` (298). Nobody chose that ordering — it fell
    out of a sort whose only non-essential criterion was token cost.
    """
    _seat(monkeypatch, 8192)
    fat_essential = _tool("search_web", size=900)
    cheap_filler = [_tool(f"filler_{i}", size=20) for i in range(40)]
    kept, _ = tb.fit_tools_to_seat("seat:9b", cheap_filler + [fat_essential])
    assert "search_web" in [t["name"] for t in kept], (
        "an essential tool lost its place to cheaper filler")


def test_every_essential_name_exists_in_the_real_registry():
    """`write_wiki` sat in the essential set and HAS NEVER EXISTED.

    The wiki write path is propose_wiki_update / correct_wiki, so the entry was
    inert from the day it was written: the protection its docstring described
    was never actually applied to anything. A name that protects nothing is
    indistinguishable from a name that works until someone measures it.
    """
    agent = pytest.importorskip("agent_friday.services.agent")
    names = {t.get("name") for t in agent.CLAUDE_TOOLS}
    missing = sorted(n for n in tb._ESSENTIAL_TOOLS if n not in names)
    assert not missing, f"essential tools absent from the registry: {missing}"


def test_connectors_are_all_or_nothing(monkeypatch):
    """Half a connector is worse to explain than none of it.

    A caller that can see half will try the half that is missing.
    """
    _seat(monkeypatch, 32768)
    kept, _ = tb.fit_tools_to_seat("seat:9b", CORE + CONN)
    assert not any(t["name"].startswith("mcp_") for t in kept)


def test_the_note_says_what_is_missing_and_why(monkeypatch):
    """A capability that quietly is not there is the defect being fixed."""
    _seat(monkeypatch, 32768)
    _, note = tb.fit_tools_to_seat("seat:9b", CORE + CONN)
    assert "112" in note and "token" in note


def test_it_is_announced_once_not_per_turn(monkeypatch, capsys):
    _seat(monkeypatch, 32768)
    for _ in range(4):
        tb.fit_tools_to_seat("seat:9b", CORE + CONN)
    assert capsys.readouterr().out.count("dropped") == 1


def test_the_served_window_beats_the_architectural_one(monkeypatch):
    """131,072 is what the model COULD do; 32,768 is what the daemon serves.

    Budgeting against the larger number is the same as not budgeting, which
    is exactly how a 46k request got built for a 32k seat.
    """
    monkeypatch.setattr(
        "agent_friday.services.residency_policy.num_ctx_for_model",
        lambda m: 32768, raising=False)
    monkeypatch.setattr(
        "agent_friday.services.model_catalog.context_window_for",
        lambda m: 131072, raising=False)
    assert tb._window("seat:9b") == 32768


def test_prompt_cost_shrinks_the_tool_budget(monkeypatch):
    """2026-08-19: tools 'within budget' landed on top of an ordinary prompt
    and the sum exceeded the seat — 400, fallback to the cloud. The request
    is budgeted as a WHOLE or it is not budgeted.
    """
    _seat(monkeypatch, 65536)
    small_conn = [_tool(f"mcp_gh_{i}") for i in range(10)]
    kept, note = tb.fit_tools_to_seat("seat:e4b", CORE + small_conn)
    assert len(kept) == len(CORE) + len(small_conn) and note is None
    # A 60k prompt in a 65k window leaves under 1k for tools. Asserting all of
    # CORE survives that — as this test used to — is asserting the overflow.
    kept, note = tb.fit_tools_to_seat("seat:e4b", CORE + small_conn,
                                      prompt_cost=60_000)
    assert not any(t["name"].startswith("mcp_") for t in kept)
    assert len(kept) < len(CORE)
    assert note


def test_the_server_beats_the_plan(monkeypatch):
    """The plan asked for 65,536; _spawn capped the seat to 32,768 and never
    wrote it back. Measured 2026-08-19: budgeting against the plan built a
    >32k request for a 32k seat. The server is the authority — the same
    principle local_call._serves states for model identity.
    """
    monkeypatch.setattr(tb, "_served_ctx", lambda m: 32768)
    monkeypatch.setattr(
        "agent_friday.services.residency_policy.num_ctx_for_model",
        lambda m: 65536, raising=False)
    assert tb._window("seat:e4b") == 32768


def test_unreachable_server_falls_back_to_the_plan(monkeypatch):
    monkeypatch.setattr(tb, "_served_ctx", lambda m: None)
    monkeypatch.setattr(tb, "_spawn_cap", lambda m: None)
    monkeypatch.setattr(
        "agent_friday.services.residency_policy.num_ctx_for_model",
        lambda m: 65536, raising=False)
    assert tb._window("seat:e4b") == 65536


def test_plan_fallback_is_clamped_to_the_spawn_cap(monkeypatch):
    """Restart scenario: new process, empty procs, wiped endpoints.json — the
    server cannot be asked, but a GGUF-seat model will be served under
    _spawn's cap regardless of what the plan asked for.
    """
    monkeypatch.setattr(tb, "_served_ctx", lambda m: None)
    monkeypatch.setattr(tb, "_spawn_cap", lambda m: 32768)
    monkeypatch.setattr(
        "agent_friday.services.residency_policy.num_ctx_for_model",
        lambda m: 65536, raising=False)
    assert tb._window("seat:e4b") == 32768


def test_no_connectors_means_no_change_when_core_fits(monkeypatch):
    """The original asserted this at a 4,096-token window, where CORE costs
    2,677 and does NOT fit — the same "core is never dropped" belief in a
    different costume. Give it a window that can actually hold them.
    """
    _seat(monkeypatch, 32768)
    kept, note = tb.fit_tools_to_seat("seat:9b", CORE)
    assert kept == CORE and note is None


def test_the_generation_reserve_never_exceeds_the_window(monkeypatch):
    """The reserve was a flat 4,608 tokens — larger than a small seat's ENTIRE
    window. A 4,096-token seat carrying a zero-token prompt computed a negative
    budget, dropped every tool it had, and reported the cause as a request "of
    about 0 tokens" that had overflowed the seat. Both halves were false.
    """
    _seat(monkeypatch, 4096)
    kept, note = tb.fit_tools_to_seat("seat:9b", CORE)
    assert kept, "a 4,096-token seat can hold some tools"
    assert note and "about 0 tokens" not in note


def test_the_model_is_told_which_tools_it_lost(monkeypatch):
    """THE CRUX. Trimming is defensible; trimming silently is not.

    FRIDAY_SYSTEM_PROMPT names ~35 tools, tells the model to use them
    proactively, and says it must NEVER claim it cannot open a page or a file.
    A trimmed turn under that prompt produces the reported failure verbatim:
    a confident announcement and no action. The note must name names, and it
    must cancel the never-deny instruction for anything that is gone.
    """
    _seat(monkeypatch, 8192)
    kept, note = tb.fit_tools_to_seat("seat:9b", CORE + CONN, prompt_cost=4200)
    kept_names = {t["name"] for t in kept}
    gone = [t["name"] for t in CORE if t["name"] not in kept_names]
    assert gone, "fixture should trim something"
    assert "OVERRIDES ANY TOOL LIST ABOVE" in note

    # The contract is that the note leaves the model in NO DOUBT about what it
    # can call — not that it always names the casualties. `_surface_override`
    # deliberately prints whichever list is shorter, because an exhaustive
    # "you can call EXACTLY these and nothing else" whitelist is as complete a
    # disclosure as a blacklist and costs less of the budget that got us here.
    #
    # This assertion used to demand the blacklist unconditionally and passed
    # only by luck: with the old headroom the fixture happened to keep more
    # tools than it dropped. A budget change flipped which list was shorter and
    # the test failed while the behaviour was still correct. Assert the
    # contract, not the branch.
    if len(gone) <= len(kept_names):
        for n in gone:
            assert n in note, f"{n} was dropped but never named to the model"
    else:
        assert "EXACTLY these" in note, "no exhaustive whitelist given"
        for n in kept_names:
            assert n in note, f"{n} survived but was never named to the model"
        for n in gone:
            assert f"• {n}\n" not in note, f"{n} was dropped but listed as callable"


def test_an_empty_tool_list_still_corrects_the_prompt(monkeypatch):
    """Zero tools is the case where the prompt lies hardest — it still names
    all thirty-five. Returning a bare arithmetic complaint left the model with
    the constant as its only account of what it could do.
    """
    _seat(monkeypatch, 32768)
    kept, note = tb.fit_tools_to_seat("seat:9b", CORE, prompt_cost=31_000)
    assert kept == []
    assert "NO callable tools" in note


@pytest.mark.parametrize("junk", [None, []])
def test_junk_is_survivable(junk, monkeypatch):
    _seat(monkeypatch, 32768)
    kept, note = tb.fit_tools_to_seat("seat:9b", junk)
    assert kept == [] and note is None


def test_a_request_the_seat_measures_as_fitting_is_never_trimmed_on_an_estimate(monkeypatch):
    """Re-measured 2026-09-18 on the FridayWeaver seat: 75 declarations
    estimate 12,740 tokens and render to 12,433, so on that seat chars/4 is
    honest -- but another template can expand tools differently in either
    direction. When the seat will count the request, its count decides."""
    _seat(monkeypatch, 32768)
    fat = [_tool(f"fat_{i}", size=2000) for i in range(20)]     # ~20k by chars/4
    calls = []

    def measure(model_id, system, messages, tools):
        calls.append(bool(tools))
        return 10_000 if not tools else 10_000 + 8_000          # the seat says 8k
    monkeypatch.setattr(tb, "measure_request", measure)
    kept, note = tb.fit_tools_to_seat("seat", fat, system="sys", messages=[])
    assert calls == [False, True]                              # prompt, then all-in
    assert len(kept) == len(fat) and note is None              # nothing trimmed


def test_a_request_the_seat_measures_as_too_big_is_trimmed_despite_a_small_estimate(monkeypatch):
    _seat(monkeypatch, 32768)
    thin = [_tool(f"thin_{i}", size=100) for i in range(20)]   # ~2k by chars/4

    def measure(model_id, system, messages, tools):
        return 10_000 if not tools else 10_000 + 20_000         # the seat says 20k
    monkeypatch.setattr(tb, "measure_request", measure)
    kept, note = tb.fit_tools_to_seat("seat", thin, system="sys", messages=[])
    assert len(kept) < len(thin)                               # the estimate lied small
    assert note                                                # and the model is told


def test_the_tool_list_holds_still_while_the_prompt_breathes(monkeypatch):
    """A tool list that changes size costs the whole prompt, every turn.

    A chat template renders tool declarations before anything else, so one
    tool appearing or disappearing moves every token after it and the seat's
    prefix cache matches nothing. Measured 2026-09-18: consecutive turns sent
    62 tools and then 63, because the budget subtracts the prompt from the
    window and the prompt breathes as the conversation moves. The capability
    difference between 62 tools and 63 is nil; the cost was ~21,000 tokens
    reprocessed at ~500 tok/s, about forty-three seconds, on every turn.

    THE FIXTURE MATTERS, and the first version of this test was worthless.
    It used the module's CORE + CONN, where the connectors are all dropped and
    all twenty core tools survive at every prompt length in range - so it
    passed with the rounding removed, which makes it evidence of nothing.

    This one is shaped like the real catalogue instead: seventy-five of
    Friday's own tools costing ~14,000 tokens against a 32,768 window, which
    is what the machine actually runs. Without the rounding that fixture moves
    the tool count thirty-two times across a six-thousand-token prompt range,
    stepping 66, 65, 64, 63, 62 - the production symptom exactly.

    THE CLAIM IS FEWER CHANGES, NOT NONE. Crossing a step is a real change
    and should cost one turn; the rounding exists so that the other turns
    between steps cost nothing. An assertion of "never changes" would be
    false, and a test that asserts something false gets deleted by the next
    person rather than fixed.
    """
    _seat(monkeypatch, 32768)
    catalogue = [_tool("core_%02d" % i) for i in range(75)]
    for t in catalogue:
        t["description"] = "d" * 640

    shapes = []
    for cost in range(9000, 15000, 100):
        kept, _ = tb.fit_tools_to_seat("seat:27b", catalogue, prompt_cost=cost)
        shapes.append(tuple(t["name"] for t in kept))

    changes = sum(1 for i in range(1, len(shapes))
                  if shapes[i] != shapes[i - 1])
    # Measured on this fixture with the rounding removed: 32 changes across
    # this sweep, stepping 66, 65, 64, 63, 62 - one cache miss per turn.
    assert changes <= 4, (
        "the tool list changed %d times across a 6,000-token prompt sweep; "
        "each change is a full prompt reprocess" % changes)

    # And it must be a FUNCTION of the prompt, not of history: the same
    # prompt size has to give the same list, or nothing above holds.
    again = [tuple(t["name"] for t in
                   tb.fit_tools_to_seat("seat:27b", catalogue,
                                        prompt_cost=cost)[0])
             for cost in range(9000, 15000, 100)]
    assert again == shapes, "the selection is not deterministic"


def test_quantising_the_budget_never_empties_a_small_seat(monkeypatch):
    """Rounding down is safe at a fraction and ruinous at everything.

    The stability rounding above nearly shipped as an unconditional
    `budget // 2048 * 2048`. On an 8,192-token seat carrying a 4,200-token
    prompt the budget is 972 tokens, and that expression is ZERO - every tool
    dropped, on precisely the seats least able to spare them. The rounding is
    now confined to budgets of at least two whole steps, which caps the loss
    at half and keeps small seats out of it entirely.
    """
    _seat(monkeypatch, 8192)
    kept, _note = tb.fit_tools_to_seat("seat:9b", CORE + CONN, prompt_cost=4200)
    assert kept, "a small seat must still carry tools"
