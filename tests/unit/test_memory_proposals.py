"""Model-driven fact proposal: the seat is pinned, and a barren run is loud.

The thing this replaces ran nightly, reported green, and extracted 0 durable
facts from 215 turns. So the tests that matter here are not "does it parse
JSON" -- they are:

  * a barren run is flagged, not silently successful
  * nothing reaches durable memory without approval
  * the assigned seat is PINNED: no fallback to a paid provider, ever
"""
from unittest.mock import patch

import pytest

from agent_friday.services import memory_proposals as mp


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "dreams.db")
    yield


def _seat(model="gemma4:12b", provider="ollama-local"):
    return patch.object(mp, "seat", return_value={
        "assigned": bool(model), "model": model, "provider": provider,
        "reason": "" if model else "no model"})


def _turns(n=5):
    return [{"role": "user", "text": f"turn {i} about deadlines and tooling"}
            for i in range(n)]


# ── The seat is pinned ───────────────────────────────────────────────────────
def test_cloud_seat_is_refused_rather_than_billed():
    """A cloud seat must not silently read his history at his expense."""
    with pytest.raises(mp.SeatUnavailable) as exc:
        mp._ask_seat("hi", "claude-sonnet-5", "anthropic")
    assert "local-only" in str(exc.value)


def test_seat_failure_does_not_fall_back():
    """A dead local seat fails the run. It does NOT walk to a paid provider.

    _generate_text would have tried every other provider here; that is exactly
    the billing hazard this path avoids.
    """
    with patch("agent_friday.services.model_router._call_ollama",
               side_effect=RuntimeError("llama-server hiccup")):
        with pytest.raises(mp.SeatUnavailable) as exc:
            mp._ask_seat("hi", "gemma4:12b", "ollama-local")
    assert "Nothing was stored" in str(exc.value)
    assert "does not fall back" in str(exc.value)


def test_empty_seat_response_is_a_failure_not_an_empty_result():
    with patch("agent_friday.services.model_router._call_ollama",
               return_value=("   ", [])):
        with pytest.raises(mp.SeatUnavailable):
            mp._ask_seat("hi", "gemma4:12b", "ollama-local")


def test_unassigned_seat_reports_why_and_stores_nothing():
    """Uses the REAL seat() so the message a user would see is the one tested."""
    with patch("agent_friday.core._load_settings",
               return_value={"capability_routing": {"memory_manager": {}}}):
        out = mp.propose("2026-08-23")
    assert out["ok"] is False and out["stored"] == 0
    assert "memory_manager" in out["reason"]
    assert "Settings" in out["reason"]           # tells them where to fix it


# ── A barren run is loud ─────────────────────────────────────────────────────
def test_barren_run_is_flagged_not_green(caplog):
    """Turns in, no facts out, and it says so. The whole point of the module."""
    with _seat(), patch.object(mp, "_ask_seat", return_value="[]"), \
         patch("agent_friday.services.memory_dreaming._pull_turns",
               return_value=(_turns(215), False)):
        out = mp.propose("2026-08-23")
    assert out["ok"] is True
    assert out["barren"] is True
    assert out["stored"] == 0
    assert "no durable facts" in out["summary"]
    assert "probably is" in out["summary"]      # invites a look, not a shrug
    assert any("proposed NOTHING" in r.message for r in caplog.records)


def test_no_turns_is_not_barren():
    """Nothing to read is a different thing from reading and finding nothing."""
    with _seat(), patch("agent_friday.services.memory_dreaming._pull_turns",
                        return_value=([], False)):
        out = mp.propose("2026-08-23")
    assert out["ok"] is True and out["barren"] is False


# ── Nothing becomes durable without approval ─────────────────────────────────
_GOOD = ('[{"category":"workflow","text":"Stephen commits one build at a time",'
         '"evidence":"one commit per build"}]')


def test_proposals_land_below_the_durability_gate():
    """memory_dreaming keeps facts at >=0.6. Model facts must sit under it."""
    assert mp.MODEL_CONFIDENCE < 0.6
    with _seat(), patch.object(mp, "_ask_seat", return_value=_GOOD), \
         patch("agent_friday.services.memory_dreaming._pull_turns",
               return_value=(_turns(), False)):
        out = mp.propose("2026-08-23")
    assert out["stored"] == 1
    assert all(f["confidence"] < 0.6 for f in out["facts"])


def test_propose_never_writes_to_user_model():
    """Proposing is not remembering."""
    with _seat(), patch.object(mp, "_ask_seat", return_value=_GOOD), \
         patch("agent_friday.services.memory_dreaming._pull_turns",
               return_value=(_turns(), False)), \
         patch("agent_friday.services.user_model.note_fact") as note:
        mp.propose("2026-08-23")
    note.assert_not_called()


def test_approve_promotes_above_pattern_confidence():
    """A human confirming outranks both the regex and the model's own guess."""
    with _seat(), patch.object(mp, "_ask_seat", return_value=_GOOD), \
         patch("agent_friday.services.memory_dreaming._pull_turns",
               return_value=(_turns(), False)):
        mp.propose("2026-08-23")
    fid = mp.pending()[0]["fact_id"]
    with patch("agent_friday.services.user_model.note_fact") as note:
        res = mp.approve([fid])
    assert res["approved"] == 1
    assert note.call_args.kwargs["confidence"] == mp.APPROVED_CONFIDENCE
    assert mp.APPROVED_CONFIDENCE > 0.7            # above the best regex match
    assert mp.pending() == []


def test_reject_keeps_the_record():
    with _seat(), patch.object(mp, "_ask_seat", return_value=_GOOD), \
         patch("agent_friday.services.memory_dreaming._pull_turns",
               return_value=(_turns(), False)):
        mp.propose("2026-08-23")
    fid = mp.pending()[0]["fact_id"]
    assert mp.reject([fid])["rejected"] == 1
    assert mp.pending() == []
    assert mp.state()["rejected"] == 1


def test_reproposing_the_same_fact_does_not_duplicate():
    with _seat(), patch.object(mp, "_ask_seat", return_value=_GOOD), \
         patch("agent_friday.services.memory_dreaming._pull_turns",
               return_value=(_turns(), False)):
        mp.propose("2026-08-23")
        second = mp.propose("2026-08-23")
    assert second["stored"] == 0
    assert len(mp.pending()) == 1


def test_state_says_it_is_not_scheduled():
    """Manual-first is a promise the status surface has to keep making."""
    assert mp.state()["scheduled"] is False


# ── Parsing is tolerant but bounded ──────────────────────────────────────────
@pytest.mark.parametrize("raw", [
    _GOOD,
    "```json\n" + _GOOD + "\n```",
    "Here you go:\n" + _GOOD + "\nHope that helps!",
])
def test_parse_tolerates_wrapping(raw):
    assert len(mp._parse_facts(raw)) == 1


@pytest.mark.parametrize("raw", ["[]", "", "not json at all", "{}", "null"])
def test_parse_returns_nothing_rather_than_inventing(raw):
    assert mp._parse_facts(raw) == []


def test_parse_caps_and_dedupes():
    many = "[" + ",".join(
        '{"category":"bio","text":"fact number %d here"}' % i
        for i in range(200)) + "]"
    assert len(mp._parse_facts(many)) <= mp.MAX_FACTS
    dupes = ('[{"category":"bio","text":"the same fact twice over"},'
             '{"category":"bio","text":"The Same Fact Twice Over"}]')
    assert len(mp._parse_facts(dupes)) == 1


def test_unknown_category_is_coerced_not_dropped():
    out = mp._parse_facts('[{"category":"wildly-invented","text":"a real fact"}]')
    assert out and out[0]["category"] in mp._CATEGORIES


def test_prompt_input_is_bounded():
    """A growing history must not become an unbounded prompt."""
    huge = [{"role": "user", "text": "x" * 5000} for _ in range(500)]
    rendered = mp._render_turns(huge)
    assert len(rendered) <= mp.MAX_CHARS + 5000
