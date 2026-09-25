"""Gauntlet finding Q19, SEVERE: local_only mode's own documented promise
(services/seat_transparency.py's _MODE_MEANING dict) says "ALL turns go to
the local seat — the cloud orchestrator is not consulted." That promise
held only for SCHEDULED/BACKGROUND work
(routing/model_router.py's `_route_basic` TOOL_USE branch). An ORDINARY
interactive chat turn -- the single most common case -- fell through
`_route_basic` with NO check of `self.mode` at all and went straight to
the cloud default. Empirically verified pre-fix (by the finder, running
the real ModelRouter in-process with factory-default settings):
local_only -> cloud claude-sonnet-5, local_preferred -> cloud
claude-sonnet-5, smart -> cloud claude-sonnet-5 -- mode was not consulted
for this branch at all, for any of the three non-cloud_only modes.

routes/chat.py hardcodes has_tools=True on every /api/chat call, so
classify_task() classifies EVERY ordinary interactive message as
TOOL_USE -- this is not an edge case, it is the default shape of a
normal conversation.

All three non-cloud modes are honoured for ordinary chat, not just
local_only: local_preferred and smart ALSO prefer a local seat for
ordinary interactive chat, the same way they do for background/scheduled
work, with cloud remaining the fallback for those two (unlike local_only,
whose own absolute promise leaves no fallback: no local model means
refuse-then-offer, see below, not a silent cloud answer).

local_only's failure mode when no local model is available is NOT a bare
refusal: fail closed, surface the error, then offer an explicit cloud
path as something the user chooses -- never a silent fallback. This is a
standing transparency principle, not a rule scoped to this one case: the
user always knows what is being done with their data and which model is
in use. See
routes/chat.py's handling of a refuse=True/offer_cloud_switch result for
where that offer is actually surfaced to the user.

This probe must be RED before the fix (all three modes route ordinary
interactive chat to cloud, mirroring the pre-fix empirical measurement)
and GREEN after.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import agent_friday.routing.model_router as mr
from agent_friday.routing.model_router import ModelRouter, TaskType


def _msgs(text: str) -> list:
    return [{"role": "user", "content": text}]


def _patch_ollama(monkeypatch, available=False, models=None):
    """Mirrors tests/unit/test_model_router.py's TestRouteIntegration
    helper -- model_router imports ollama_manager lazily inside methods,
    so we patch ollama_manager.get_manager directly on the already-
    imported module."""
    import agent_friday.routing.ollama_manager as ollama_manager

    _avail = available
    _models = list(models or [])

    class FakeOllama:
        def is_available(self):
            return _avail

        def list_models(self):
            return _models

    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **kw: FakeOllama())


def _patch_no_friday_seats(monkeypatch):
    """_local_candidates() also checks Friday's own model_store seats
    before Ollama -- keep those empty so a test controls candidates via
    _patch_ollama alone, matching the finder's own empirical setup."""
    import agent_friday.services.model_store as model_store
    monkeypatch.setattr(model_store, "available", lambda: {})


class TestLocalOnlyRoutesOrdinaryInteractiveChatLocally:
    def test_ordinary_tool_use_turn_goes_local_when_a_seat_exists(self, monkeypatch):
        """The core fix: an ordinary /api/chat-shaped turn (has_tools=True,
        no is_background_task/scheduled flag -- exactly what routes/chat.py
        sends for every interactive message) must route local under
        local_only when a local model is available, matching the mode's
        own absolute UI promise."""
        _patch_no_friday_seats(monkeypatch)
        _patch_ollama(monkeypatch, available=True,
                      models=[{"name": "gemma4:e4b", "size_gb": 5.0}])
        r = ModelRouter(config={"mode": "local_only"})

        result = r.route(_msgs("what should I have for dinner tonight?"),
                         task_context={"has_tools": True})

        assert result["provider"] == "local", (
            "local_only mode sent an ordinary interactive turn to the "
            "cloud despite its own UI text promising 'ALL turns go to the "
            "local seat — the cloud orchestrator is not consulted' "
            "(findings.jsonl Q19)"
        )
        assert result["is_local"] is True
        assert result["refuse"] is False

    def test_no_local_seat_available_refuses_rather_than_silently_going_cloud(
            self, monkeypatch):
        """Fail-closed, matching F16/F20/F33's already-established pattern
        for voice: if local_only is on and truly nothing local can serve
        the turn, refuse and say so — never silently answer from the
        cloud, which is exactly the promise-breaking behavior this finding
        is about."""
        _patch_no_friday_seats(monkeypatch)
        _patch_ollama(monkeypatch, available=False)
        r = ModelRouter(config={"mode": "local_only"})

        result = r.route(_msgs("what should I have for dinner tonight?"),
                         task_context={"has_tools": True})

        assert result["provider"] != "cloud", (
            "local_only with zero local candidates available fell back to "
            "cloud instead of refusing — the mode's own promise says the "
            "cloud orchestrator is never consulted, not 'consulted only "
            "when local isn't available'"
        )
        assert result["refuse"] is True, (
            "route()'s final _finalize() call must propagate the refuse "
            "flag _route_basic set — if this is False, route() silently "
            "downgraded a genuine refusal back to a normal (non-refusing) "
            "result"
        )
        assert result["warning"], "a refusal must carry a user-facing explanation"

    def test_a_cloud_task_override_is_overridden_by_local_only(self, monkeypatch):
        """local_only's absolute guarantee wins over a conflicting explicit
        choice, the same direction cloud_only already forces a conflicting
        LOCAL choice back to cloud (see the existing
        TestCloudOnlyStillHonoursTheChoice class in
        tests/unit/test_model_router.py for the symmetric, already-correct
        precedent this mirrors)."""
        _patch_no_friday_seats(monkeypatch)
        _patch_ollama(monkeypatch, available=True,
                      models=[{"name": "gemma4:e4b", "size_gb": 5.0}])
        overrides = {TaskType.TOOL_USE: {"provider": "cloud",
                                         "model": "claude-sonnet-5"}}
        r = ModelRouter(config={"mode": "local_only", "task_overrides": overrides})

        result = r.route(_msgs("hi"), task_context={"has_tools": True})

        assert result["provider"] == "local", (
            "a task_override naming a cloud provider/model was honored "
            "over local_only mode's own absolute guarantee -- local_only "
            "must win, the same direction cloud_only already forces a "
            "conflicting local override back to cloud"
        )

    def test_local_preferred_and_smart_also_prefer_local_for_ordinary_chat(
            self, monkeypatch):
        """Ordinary chat respects local preference in local_preferred and
        smart modes too; local-preference is not scoped to background/
        scheduled work only."""
        _patch_no_friday_seats(monkeypatch)
        _patch_ollama(monkeypatch, available=True,
                      models=[{"name": "gemma4:e4b", "size_gb": 5.0}])
        for mode in ("local_preferred", "smart"):
            r = ModelRouter(config={"mode": mode})
            result = r.route(_msgs("what should I have for dinner tonight?"),
                             task_context={"has_tools": True})
            assert result["provider"] == "local", (
                f"mode={mode} sent an ordinary interactive turn to the "
                "cloud, but local_preferred/smart "
                "also prefer local for ordinary chat, not just "
                "background/scheduled work"
            )

    def test_local_preferred_and_smart_still_fall_back_to_cloud_with_no_local_seat(
            self, monkeypatch):
        """No-op-shaped sanity check: unlike local_only (an absolute
        promise, refuse-then-offer when unmet), local_preferred/smart's
        own names promise "cloud ONLY AS FALLBACK" -- when no local seat
        exists at all, cloud remains the correct, silent fallback for
        these two modes. This distinction is deliberate, not an
        oversight."""
        _patch_no_friday_seats(monkeypatch)
        _patch_ollama(monkeypatch, available=False)
        for mode in ("local_preferred", "smart"):
            r = ModelRouter(config={"mode": mode})
            result = r.route(_msgs("what should I have for dinner tonight?"),
                             task_context={"has_tools": True})
            assert result["provider"] == "cloud", (
                f"mode={mode} with no local seat available should still "
                "fall back to cloud, matching its own 'cloud only as "
                "fallback' promise -- only local_only refuses outright"
            )
            assert result["refuse"] is False
