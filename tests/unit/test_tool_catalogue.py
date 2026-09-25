"""An index of the tools instead of 13,300 tokens of their schemas.

MEASURED on a reference machine: 75 tools, ~13,324 tokens of
schema, 41% of a 32,768-token window spent before the user says anything, and
a 15,930-token prompt to answer "how many conversations are stored". Only 383
of those tokens are tool NAMES; the rest is detail the model does not need
until it has already chosen a tool.

The properties that make this safe are the ones under test: nothing becomes
unreachable, an unknown name is refused out loud rather than silently dropped,
and the whole thing is off until measured.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import tool_catalogue as TC


def _tool(name, desc="Do the thing. Then explain how to do the thing at "
                     "considerable length, with caveats.", nparams=3):
    return {
        "name": name,
        "description": desc,
        "input_schema": {
            "type": "object",
            "properties": {("p%d" % i): {"type": "string",
                                         "description": "a parameter " * 6}
                           for i in range(nparams)},
            "required": ["p0"],
        },
    }


@pytest.fixture()
def tools():
    return ([_tool("search_web"), _tool("read_file"), _tool("search_files")]
            + [_tool("tool_%02d" % i) for i in range(40)])


# ── the saving ──────────────────────────────────────────────────────────────

def test_the_catalogue_is_much_smaller_than_the_schemas(tools):
    s = TC.savings(tools)
    assert s["opening_tokens"] < s["full_tokens"] / 3, s
    assert s["saved_pct"] > 60


def test_the_saving_grows_with_the_number_of_tools():
    """The more tools Friday gains, the more this matters - which is the
    opposite of the trimmer, whose losses grow too."""
    small = TC.savings([_tool("a"), _tool("b")])
    big = TC.savings([_tool("t%d" % i) for i in range(75)])
    assert big["saved_pct"] > small["saved_pct"]


# ── nothing becomes unreachable ─────────────────────────────────────────────

def test_every_tool_is_still_reachable(tools):
    """THE CRUX, and the difference from trimming. `fit_tools_to_seat` drops
    the most expensive schemas outright, so capability is lost by verbosity.
    Here everything is still there; it just arrives when asked for."""
    opening = TC.opening_set(tools)
    for t in tools:
        name = t["name"]
        new, msg = TC.expand(tools, [name], opening)
        loaded = {x["name"] for x in new} | {
            (x.get("function") or x).get("name") for x in opening}
        assert name in loaded, "%s is unreachable" % name


def test_every_tool_appears_in_the_index(tools):
    listed = {r["name"] for r in TC.index(tools)}
    assert listed == {t["name"] for t in tools}


def test_the_loader_description_names_every_tool(tools):
    """The index IS the loader's description - the model cannot ask for what
    it was never told about."""
    d = TC.loader_spec(tools)["description"]
    for t in tools:
        assert t["name"] in d


# ── asking for things ───────────────────────────────────────────────────────

def test_a_requested_tool_comes_back(tools):
    new, msg = TC.expand(tools, ["tool_07"], TC.opening_set(tools))
    assert [t["name"] for t in new] == ["tool_07"]
    assert "Loaded" in msg and "tool_07" in msg


def test_an_already_loaded_tool_is_not_sent_twice(tools):
    opening = TC.opening_set(tools)
    new, msg = TC.expand(tools, ["search_web"], opening)
    assert new == []
    assert "Already loaded" in msg


def test_an_unknown_name_is_refused_out_loud(tools):
    """A loader that silently drops an unknown name teaches the model to wait
    for a tool that will never arrive."""
    new, msg = TC.expand(tools, ["definitely_not_a_tool"], [])
    assert new == []
    assert "No such tool" in msg and "definitely_not_a_tool" in msg


def test_a_near_miss_gets_a_suggestion(tools):
    _, msg = TC.expand(tools, ["web"], [])
    assert "did you mean" in msg and "search_web" in msg


def test_asking_for_nothing_says_so(tools):
    for empty in ([], None, ["", "  "]):
        new, msg = TC.expand(tools, empty, [])
        assert new == [] and "No tool names" in msg


def test_a_mixed_request_reports_each_outcome(tools):
    opening = TC.opening_set(tools)
    new, msg = TC.expand(tools, ["tool_01", "search_web", "ghost"], opening)
    assert [t["name"] for t in new] == ["tool_01"]
    assert "Loaded" in msg and "Already loaded" in msg and "No such tool" in msg


def test_the_same_name_twice_in_one_call_loads_once(tools):
    new, _ = TC.expand(tools, ["tool_03", "tool_03"], [])
    assert [t["name"] for t in new] == ["tool_03"]


# ── the opening set ─────────────────────────────────────────────────────────

def test_the_loader_is_always_present(tools):
    names = [(t.get("function") or t).get("name") for t in TC.opening_set(tools)]
    assert TC.LOADER_NAME in names


def test_the_resident_tools_skip_the_round_trip(tools):
    """A round trip on a local 27B is tens of seconds. The handful of tools
    almost every turn reaches for should not pay it."""
    names = [(t.get("function") or t).get("name") for t in TC.opening_set(tools)]
    for r in TC.ALWAYS_RESIDENT:
        assert r in names


def test_the_resident_list_stays_short():
    """Every resident name is permanent rent on the context window."""
    assert len(TC.ALWAYS_RESIDENT) <= 5


def test_an_empty_registry_does_not_explode():
    assert TC.index([]) == []
    assert TC.savings([])["full_tokens"] >= 0
    names = [(t.get("function") or t).get("name") for t in TC.opening_set([])]
    assert names == [TC.LOADER_NAME]


# ── summaries ───────────────────────────────────────────────────────────────

def test_a_summary_is_the_first_sentence_not_the_paragraph():
    t = _tool("x", desc="Find a file by name. Then a long explanation of the "
                        "flags, the edge cases, and the history of the flags.")
    assert TC.index([t])[0]["summary"] == "Find a file by name"


def test_a_summary_is_bounded():
    t = _tool("x", desc="word " * 200)
    assert len(TC.index([t])[0]["summary"]) <= 95


def test_a_tool_with_no_description_still_lists(tools):
    t = {"name": "bare", "input_schema": {}}
    rows = TC.index([t])
    assert rows == [{"name": "bare", "summary": ""}]
    assert "bare" in TC.loader_spec([t])["description"]


# ── off until measured ──────────────────────────────────────────────────────

def test_it_is_on_by_default(monkeypatch):
    """Defaulted ON because the risk is understood rather than assumed:
    `_execute_tool` dispatches by NAME and never consults the list the
    model was sent, so the catalogue governs what Friday is told about, not
    what it can do."""
    monkeypatch.delenv("FRIDAY_TOOL_CATALOGUE", raising=False)
    assert TC.enabled() is True


@pytest.mark.parametrize("val, want", [
    ("0", False), ("false", False), ("NO", False), ("off", False),
    ("1", True), ("", True), ("anything-else", True),
])
def test_the_flag_is_an_off_switch(monkeypatch, val, want):
    """Only an explicit off turns it off. A typo must not silently cost 11,000
    tokens a turn."""
    monkeypatch.setenv("FRIDAY_TOOL_CATALOGUE", val)
    assert TC.enabled() is want


# ── the real registry ───────────────────────────────────────────────────────

def test_against_the_live_tool_set():
    from agent_friday.routes import chat as C
    live = getattr(C, "CLAUDE_TOOLS", None) or []
    if not live:
        pytest.skip("no live tool registry")
    s = TC.savings(live)
    assert s["tools"] > 50
    assert s["saved_pct"] > 70, s
    # The opening set must fit comfortably where the full one did not: the
    # trimmer was dropping three tools to make a 66-token question fit.
    assert s["opening_tokens"] < 0.15 * 32768
    opening = TC.opening_set(live)
    json.dumps(opening, default=str)          # must be serialisable to the wire
