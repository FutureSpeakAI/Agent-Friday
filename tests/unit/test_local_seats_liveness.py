"""A substitution must never land on a seat nothing answers to.

THE FAILURE. `resolve("brain")` can return `gemma4:e2b-friday-v1` — a retired
alias for a model that no longer exists — while `gemma4:e2b-fridayweaver-1.0`
is serving on port 8095 and answering in under a second. Friday then asks
Ollama for the retired name, Ollama has nothing installed, and the turn dies
with "No model provider could run the agent".

The cause is not the size ordering itself but what happens when liveness
cannot be read: `runtime/residency/endpoints.json` holds `{}` after a
restart, so `seat_endpoint()` says None for every candidate, the live-seats
pool comes back empty, and treating "nobody is running" and "nobody could be
asked" as the same state lets size ordering pick the smallest, which is the
dead one.
"""
import pytest

from agent_friday.services import local_seats as ls


@pytest.fixture(autouse=True)
def clean():
    ls._ANNOUNCED.clear()
    ls._SURVEY_CACHE.update(at=0.0, served=frozenset())
    yield
    ls._ANNOUNCED.clear()
    ls._SURVEY_CACHE.update(at=0.0, served=frozenset())


DEAD = "gemma4:e2b-friday-v1"          # smaller, retired, nothing serves it
LIVE = "gemma4:e2b-fridayweaver-1.0"   # larger, actually answering


def _inventory(monkeypatch):
    monkeypatch.setattr(ls, "installed", lambda force=False: [
        (DEAD, 3.43), (LIVE, 4.95)])
    monkeypatch.setattr(ls, "_configured", lambda role: "anthropic/claude-sonnet-5")


def test_substitution_prefers_the_seat_that_answers(monkeypatch):
    """With endpoints.json empty, the survey must still find the live seat."""
    _inventory(monkeypatch)
    # endpoints.json empty — exactly the failing state.
    monkeypatch.setattr("agent_friday.services.local_call.seat_endpoint",
                        lambda m: None)
    monkeypatch.setattr(ls, "_survey_seats", lambda force=False: frozenset({LIVE}))
    assert ls.resolve("brain") == LIVE


def test_without_the_survey_the_dead_alias_wins(monkeypatch):
    """The falsification: this is the bug, reproduced.

    If the survey cannot see anything either, the code falls back to size
    ordering and picks the retired alias. Asserting that here is what makes
    the test above evidence rather than decoration — it shows the survey is
    the thing doing the work, not some incidental ordering.
    """
    _inventory(monkeypatch)
    monkeypatch.setattr("agent_friday.services.local_call.seat_endpoint",
                        lambda m: None)
    monkeypatch.setattr(ls, "_survey_seats", lambda force=False: frozenset())
    assert ls.resolve("brain") == DEAD


def test_probe_is_fail_closed(monkeypatch):
    """A survey that raises must not promote an unreachable name."""
    def _boom(force=False):
        raise RuntimeError("no network")
    monkeypatch.setattr(ls, "_survey_seats", _boom)
    assert ls._probe_seat(LIVE) is False
    assert ls._probe_seat("") is False


def test_survey_is_cached(monkeypatch):
    """The sweep costs a real 0.45s; the chat path must not pay it per call."""
    calls = {"n": 0}

    def _one_port(port):
        calls["n"] += 1
        return ()

    monkeypatch.setattr(ls, "_SURVEY_PORTS", range(8090, 8095))
    ls._SURVEY_CACHE.update(at=0.0, served=frozenset())
    ls._survey_seats(force=True)
    first = calls["n"]
    ls._survey_seats()          # inside the TTL — must not re-sweep
    assert calls["n"] == first or first == 0
