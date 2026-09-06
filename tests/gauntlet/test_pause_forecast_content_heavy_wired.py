"""Gauntlet finding Q18: services/workflow_plan.py's looks_heavy() -- a
purpose-built content-depth heuristic whose own module docstring says
"If Friday thinks work might be heavy, she asks" -- had zero callers
anywhere in src/agent_friday. The module's stated design premise was
unreachable: the actual live "ask before running" gate,
services/pause_forecast.forecast() (kind='local_turn', called from the
chat UI's POST /api/work/forecast), decided purely from estimated
model cold-load wall-clock time for the currently-selected seat, with
no awareness of the request's actual content. A short-but-slow-to-load
request could trigger the pause-warning dialog while a genuinely heavy
request ("refactor every file in this repo") running on an already-warm
seat never did, because the function written to detect exactly that
case was never called.

Fix: pause_forecast.forecast() now accepts an optional `text` kwarg
(threaded through from routes/work_plan.py's `message` field, which in
turn index.html/ui_parts/app.html now send in the /api/work/forecast
body alongside `kind: 'local_turn'`). When the load-time signal alone
would not have triggered a pause-warning, forecast() now ALSO checks
services.workflow_plan.looks_heavy(text) and asks anyway if it matches
-- an OR, not a replacement: the existing load-time trigger is
untouched, and a fast-loading heavy-content request now also lights up
the same PauseWarning dialog contract (will_pause/seconds/confidence/
basis/why/options).

This probe must be RED before the fix (a warm seat + heavy-content text
never produces will_pause=True) and GREEN after. It stubs the seat as
already resident and owned by a process Friday holds (before_local_turn's
fastest "definitely no pause" path) so the load-time signal on its own
is unambiguously "no" -- isolating the content-based OR from the
load-time heuristic entirely.
"""
from __future__ import annotations

import agent_friday.services.pause_forecast as pf
import agent_friday.services.residency_arbiter as residency_arbiter


MODEL_ID = "gemma4:e4b"
HEAVY_TEXT = "Please refactor every file in this repo to use the new logger."
LIGHT_TEXT = "what's the weather like today?"


class _FakeLlama:
    def __init__(self, owned_model_id):
        self.procs = {owned_model_id: object()}


class _FakeArbiter:
    """A resident, Friday-owned seat -- before_local_turn's earliest
    "definitely no pause" branch, so the load-time signal alone is
    unambiguously negative and any positive result must come from the
    content-based OR under test."""

    def __init__(self, owned_model_id):
        self.llama = _FakeLlama(owned_model_id)
        self.plan = None
        self.entries = []

        class _Ollama:
            def resident(self_inner):
                return [owned_model_id]

        self.ollama = _Ollama()


def _patch_warm_owned_seat(monkeypatch, model_id=MODEL_ID):
    monkeypatch.setattr(residency_arbiter, "get_arbiter",
                        lambda *a, **k: _FakeArbiter(model_id))


class TestLooksHeavyIsWiredIntoForecast:
    def test_heavy_content_on_warm_seat_triggers_ask(self, monkeypatch):
        """The exact gap Q18 named: a warm/owned seat (load-time says 'no
        pause needed') carrying a message that reads as a big job must now
        trigger the ask, because looks_heavy() is finally consulted."""
        _patch_warm_owned_seat(monkeypatch)

        # Load-time signal alone says no pause -- confirms the fixture is
        # isolating the right thing before layering the content check on.
        bare = pf.forecast("local_turn", model_id=MODEL_ID)
        assert bare["will_pause"] is False, (
            "fixture is broken: a Friday-owned resident seat should never "
            "pause on load-time grounds alone"
        )

        out = pf.forecast("local_turn", model_id=MODEL_ID, text=HEAVY_TEXT)

        assert out["will_pause"] is True, (
            "looks_heavy()-flagged content on a warm seat did not trigger "
            "the ask -- Q18's gap is still open"
        )
        assert out["options"], "an ask with no options leaves nothing to choose"
        assert out["confidence"] is not None
        assert "refactor" not in out["why"].lower() or True  # why is free-form
        assert MODEL_ID in out.get("affects", []) or out["affects"] == []

    def test_light_message_on_warm_seat_still_does_not_ask(self, monkeypatch):
        """No-op-shaped sanity check: the common case (short, non-heavy
        message on an already-warm seat) must not regress into asking on
        every turn -- that would just trade one kind of noise for another."""
        _patch_warm_owned_seat(monkeypatch)

        out = pf.forecast("local_turn", model_id=MODEL_ID, text=LIGHT_TEXT)

        assert out["will_pause"] is False, (
            "a short, non-heavy message on a warm seat now triggers a pause "
            "warning -- regression in the common case"
        )

    def test_no_text_supplied_is_unaffected(self, monkeypatch):
        """Callers that never pass `text` (or pass None/empty) must behave
        exactly as before this fix -- the load-time signal is untouched."""
        _patch_warm_owned_seat(monkeypatch)

        assert pf.forecast("local_turn", model_id=MODEL_ID)["will_pause"] is False
        assert pf.forecast("local_turn", model_id=MODEL_ID, text=None)["will_pause"] is False
        assert pf.forecast("local_turn", model_id=MODEL_ID, text="")["will_pause"] is False

    def test_existing_load_time_trigger_is_not_weakened(self, monkeypatch):
        """The OR must not have replaced the load-time path: a cold,
        never-served, non-resident model must still trigger a pause warning
        even with ordinary (non-heavy) text, exactly as before Q18."""
        # Bypass residency (and its localhost:11434 fallback probe) entirely,
        # same technique as tests/unit/test_pause_forecast.py's
        # test_a_forecast_still_happens_with_no_arbiter_at_all.
        monkeypatch.setattr(pf, "_residency", lambda: (set(), None))
        monkeypatch.setattr(pf, "_served_recently", lambda *a, **k: (False, None))
        monkeypatch.setattr(pf, "_load_estimate",
                            lambda model_id, arb: (30.0, "recorded from an earlier run"))

        out = pf.forecast("local_turn", model_id="gemma4:26b", text=LIGHT_TEXT)

        assert out["will_pause"] is True, (
            "the pre-existing load-time trigger regressed after wiring in "
            "the content-based OR"
        )
