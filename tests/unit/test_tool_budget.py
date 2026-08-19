"""The request has to be buildable before anything else can be true.

Measured on Stephen's machine 2026-08-18, from the daemon itself:

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
    yield
    tb._ANNOUNCED.clear()


def _tool(name, size=200):
    return {"name": name, "description": "d" * size,
            "input_schema": {"type": "object",
                             "properties": {"a": {"type": "string",
                                                  "description": "x" * size}}}}


def _seat(monkeypatch, window):
    monkeypatch.setattr(tb, "_window", lambda m: window)


CORE = [_tool(f"core_{i}") for i in range(20)]
CONN = [_tool(f"mcp_hf_{i}") for i in range(112)]


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


def test_fridays_own_tools_are_never_dropped(monkeypatch):
    """They are what she IS. Trimming them would change who answered."""
    _seat(monkeypatch, 2048)
    kept, _ = tb.fit_tools_to_seat("tiny:1b", CORE + CONN)
    assert [t["name"] for t in kept] == [t["name"] for t in CORE]


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


def test_no_connectors_means_no_change(monkeypatch):
    _seat(monkeypatch, 4096)
    kept, note = tb.fit_tools_to_seat("seat:9b", CORE)
    assert kept == CORE and note is None


@pytest.mark.parametrize("junk", [None, []])
def test_junk_is_survivable(junk, monkeypatch):
    _seat(monkeypatch, 32768)
    kept, note = tb.fit_tools_to_seat("seat:9b", junk)
    assert kept == [] and note is None
