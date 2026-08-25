"""A false orphan is worse than no probe, because this report exists to be believed.

Observed 2026-08-25. The seat map put `sidekick_fast` and `memory_manager` on
TwIL-LM3 with `backend: ollama`, and the embedder on embeddinggemma:300m served
by the daemon. `_probe_seat_drift` asked `owned_endpoint`, which only knows
llama-server processes the Arbiter spawned, so all three came back None and were
reported ORPHANED — "the plan says resident, nothing is serving it" — while
every one of them was answering on :11434.

That reads as "Stephen lost his memory manager and fast sidekick this
afternoon", and it nearly cost a session chasing seats that were never missing.
The probe has to ask where calls actually land, not who owns the process.
"""
import pytest

from agent_friday.services import liveness_audit as la


class _Arb:
    def __init__(self, seats):
        self.plan = {"seats": seats}


@pytest.fixture
def arbiter(monkeypatch):
    def _install(seats):
        monkeypatch.setattr(la, "get_arbiter", lambda: _Arb(seats), raising=False)
        import agent_friday.services.residency_arbiter as ra
        monkeypatch.setattr(ra, "ARBITER", _Arb(seats))
        return seats
    return _install


def _routes(monkeypatch, mapping):
    """Stub where each model's calls actually land."""
    from agent_friday.services import local_call
    monkeypatch.setattr(
        local_call, "describe_dispatch",
        lambda m, daemon_tags=None: {"route": mapping.get(m, "unreachable")})


def test_a_daemon_backed_pinned_seat_is_not_an_orphan(monkeypatch, arbiter):
    """The exact false alarm. Serving on :11434 is serving."""
    arbiter({"memory_manager": {"model_id": "twil:3b", "status": "pinned",
                                "backend": "ollama"}})
    _routes(monkeypatch, {"twil:3b": "daemon"})
    assert la._probe_seat_drift() == [], (
        "a seat answering on the Ollama daemon was reported orphaned")


def test_a_seat_nothing_is_serving_is_still_an_orphan(monkeypatch, arbiter):
    """The probe must not be softened into uselessness by the fix."""
    arbiter({"interactive_brain": {"model_id": "gemma4:12b", "status": "pinned",
                                   "backend": "llama-server"}})
    _routes(monkeypatch, {})          # nothing reachable
    found = la._probe_seat_drift()
    assert len(found) == 1 and "gemma4:12b" in found[0]["detail"]


def test_a_leased_seat_that_is_down_is_not_reported(monkeypatch, arbiter):
    """Only 'resident'/'pinned' claim to be up. A lease is absent by design."""
    arbiter({"heavy_hitter": {"model_id": "gemma4:26b", "status": "leased",
                              "backend": "llama-server"}})
    _routes(monkeypatch, {})
    assert la._probe_seat_drift() == []


def test_an_owned_llama_seat_is_live(monkeypatch, arbiter):
    arbiter({"interactive_brain": {"model_id": "gemma4:12b", "status": "pinned",
                                   "backend": "llama-server"}})
    _routes(monkeypatch, {"gemma4:12b": "seat"})
    assert la._probe_seat_drift() == []
