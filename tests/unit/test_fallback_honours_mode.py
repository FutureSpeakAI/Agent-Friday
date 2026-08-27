"""Cloud only must survive the FALLBACK ladder, not just the routing decision.

The chat route was fixed on 2026-08-26 (`0734377`): cloud_only no longer drops
to a local model when there is no Anthropic key. That closed Janet's symptom
on /api/chat and nowhere else.

Two other ladders do the same thing, and they carry everything that is not a
typed chat turn — briefings, the digest, editorial, scheduled work, subagents,
every tool-using agentic turn:

    services/agent.py        _generate_agent
    services/model_router.py _generate_text

Both end the same way:

    else:  # cloud / default
        attempts = [('cloud',  _via_claude, routed_model),
                    ('openai', _via_openai, None),
                    ('local',  _via_ollama, None)]     # <- in cloud_only

On a fresh install with no Anthropic key, `_via_claude` raises immediately
("Anthropic client unavailable"), `_via_openai` has no key either, and the
third leg runs a local model — in cloud_only mode, for the same reason and
with the same invisibility as the chat safety net. The router's verdict was
correct and the ladder below it never asked.

The mirror is broken too, and it is the one already fixed in chat.py under the
heading "LOCAL ONLY MEANS LOCAL ONLY": for a local route these ladders append
cloud and openai legs, so local_only reaches Anthropic whenever a local seat
has a bad minute. Only the vault path was protected, because only the vault
path had someone check.

THE PRIMARY LEG IS NEVER FILTERED. It is the router's actual decision, which
already honours the mode and already honours an explicit local model id
("the user explicitly chose a local brain" — _apply_cloud_provider). What gets
filtered is the drift underneath it: the legs nobody chose, that exist for
resilience and were never told what the user asked for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import model_router as mr  # noqa: E402

CLOUD_LADDER = [("cloud", "c", "m"), ("openai", "o", None), ("local", "l", None)]
LOCAL_LADDER = [("local", "l", "m"), ("cloud", "c", None), ("openai", "o", None)]


def _names(attempts):
    return [a[0] for a in attempts]


class TestCloudOnly:
    def test_the_local_fallback_leg_is_dropped(self):
        out = mr._mode_filtered_attempts(CLOUD_LADDER, {"mode": "cloud_only"})
        assert _names(out) == ["cloud", "openai"], (
            "cloud_only kept a local leg — this is Janet's bug in the ladder "
            "the chat fix did not cover"
        )

    def test_an_explicitly_chosen_local_seat_still_runs(self):
        """The primary leg is the router's decision and is never filtered.

        _apply_cloud_provider deliberately routes a local model id on-device
        "even in cloud_only mode — the user explicitly chose a local brain".
        Filtering that would break an explicit choice to enforce a default,
        which is the disease, not the cure.
        """
        out = mr._mode_filtered_attempts(LOCAL_LADDER, {"mode": "cloud_only"})
        assert _names(out)[0] == "local"


class TestLocalOnly:
    def test_cloud_legs_are_dropped(self):
        out = mr._mode_filtered_attempts(LOCAL_LADDER, {"mode": "local_only"})
        assert _names(out) == ["local"], (
            "local_only reached a cloud provider — chat.py fixed this in 2026-08"
            " and these ladders never got the same rule"
        )

    def test_the_primary_leg_survives_even_if_it_is_cloud(self):
        out = mr._mode_filtered_attempts(CLOUD_LADDER, {"mode": "local_only"})
        assert _names(out)[0] == "cloud"


class TestPermissiveModes:
    @pytest.mark.parametrize("mode", ["smart", "local_preferred", "", None])
    def test_the_ladder_is_untouched(self, mode):
        assert _names(mr._mode_filtered_attempts(
            CLOUD_LADDER, {"mode": mode})) == ["cloud", "openai", "local"]
        assert _names(mr._mode_filtered_attempts(
            LOCAL_LADDER, {"mode": mode})) == ["local", "cloud", "openai"]

    def test_a_missing_config_is_permissive(self):
        assert len(mr._mode_filtered_attempts(CLOUD_LADDER, None)) == 3


class TestNeverEmpty:
    def test_a_single_leg_ladder_is_returned_intact(self):
        """The vault path builds a one-item list. Filtering must be a no-op
        on it in every mode — a resilience rule that can empty the ladder
        turns a policy into an outage."""
        for mode in ("cloud_only", "local_only", "smart"):
            one = [("local", "l", "m")]
            assert mr._mode_filtered_attempts(one, {"mode": mode}) == one

    def test_filtering_can_never_return_nothing(self):
        for ladder in (CLOUD_LADDER, LOCAL_LADDER):
            for mode in ("cloud_only", "local_only", "smart", "local_preferred"):
                assert mr._mode_filtered_attempts(ladder, {"mode": mode})


# ── The ladders actually USE the rule ────────────────────────────────────────
#
# The tests above prove the filter is correct. They do not prove either ladder
# calls it — which is precisely the gap `services/role_consumers.py` was built
# to name: a value can be read without being obeyed. These drive the real
# `_generate_agent` with the real ladder and assert what got dialled.

class _Leg:
    def __init__(self, exc=None, result=("reply", [])):
        self.calls = 0
        self._exc, self._result = exc, result

    def __call__(self, *a, **k):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._result


@pytest.fixture
def agentic(monkeypatch):
    """A fresh install: no Anthropic key, no OpenAI key, Ollama healthy."""
    import agent_friday.services.agent as agent_mod
    import agent_friday.services.demo_mode as dm
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False, raising=False)

    def _make(mode):
        monkeypatch.setattr(
            agent_mod, "_load_settings",
            lambda: {"model_routing": {"mode": mode}}, raising=False)
        monkeypatch.setattr(agent_mod, "get_anthropic_client",
                            lambda *a, **k: None, raising=False)
        local = _Leg(result=("local answered", []))
        oai = _Leg(exc=RuntimeError("no OpenAI key"))
        monkeypatch.setattr(agent_mod, "_call_ollama", local, raising=False)
        monkeypatch.setattr(agent_mod, "_call_openai", oai, raising=False)
        return agent_mod, local

    return _make


MSG = [{"role": "user", "content": "write a haiku about espresso"}]


def test_a_cloud_only_agentic_turn_never_reaches_ollama(agentic):
    """Janet's symptom, on the path the chat fix did not cover."""
    agent_mod, local = agentic("cloud_only")
    try:
        agent_mod._generate_agent(MSG, system="be brief")
    except Exception:
        pass  # failing is fine; reaching Ollama is not
    assert local.calls == 0, (
        "cloud_only fell through to a local model in the agentic ladder — "
        "every briefing, scheduled task and subagent turn did this"
    )


def test_a_permissive_mode_still_falls_back_to_ollama(agentic):
    """The ladder exists for a reason. Scope it, do not remove it."""
    agent_mod, local = agentic("smart")
    try:
        agent_mod._generate_agent(MSG, system="be brief")
    except Exception:
        pass
    assert local.calls >= 1, (
        "scoping the ladder to cloud_only must not disable the fallback for "
        "the modes that legitimately want one"
    )
