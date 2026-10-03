"""The voice and chat tool behind "what's the biggest model I can run?":
one read-only answer from the screen's own fit arithmetic, said in a
sentence, with 'about' where a number is an estimate; it installs nothing."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import local_models_tools as lmt
from agent_friday.services import model_fit as mf
from agent_friday.services import model_shortlist as sl
from tests import residency_fixtures as fx


@pytest.fixture(autouse=True)
def _machine(tmp_path, monkeypatch):
    from agent_friday.services import hardware_profile as hwp
    from agent_friday.services import model_store
    from agent_friday.services import residency_catalog as rc
    from agent_friday.services import context_budget
    monkeypatch.setattr(hwp, "get", lambda *a, **k: json.loads(json.dumps(fx.P1)))
    monkeypatch.setattr(model_store, "available", lambda: {})
    monkeypatch.setattr(rc, "measurements", lambda mid, fp: [])
    monkeypatch.setattr(context_budget, "overhead_tokens", lambda: 25000)
    monkeypatch.setattr(mf, "calibration_path", lambda: tmp_path / "c.json")
    sl.reload_for_tests()


def test_the_biggest_that_runs_is_bonsai2_on_the_reference_machine():
    out = lmt.advise({"question": "What's the biggest model I can run?"})
    assert out["kind"] == "biggest"
    assert out["data"]["model"] == "bonsai2:27b"
    assert "about" in out["spoken"] or "measured" in out["spoken"]
    assert "Bonsai 2" in out["spoken"]


def test_could_i_run_names_the_verdict_and_the_reason():
    out = lmt.advise({"question": "Could I run the ternary bonsai 4b?"})
    assert out["kind"] == "could" and out["data"]["model"] == "ternary-bonsai:4b"
    assert out["spoken"].split(".")[0] in ("Yes", "Just about", "Partly", "No")
    assert out["data"]["why"]


def test_a_named_model_argument_wins_over_the_words():
    out = lmt.advise({"question": "can I run it", "model": "bonsai2:27b"})
    assert out["data"]["model"] == "bonsai2:27b"


def test_what_would_i_need_gives_vram_ram_and_disk():
    out = lmt.advise({"question": "What would I need for Bonsai 2?"})
    assert out["kind"] == "need"
    assert "graphics memory" in out["spoken"] and "RAM" in out["spoken"] and "disk" in out["spoken"]
    assert out["data"]["need"]["vram_total_mib"] > 6000


def test_pretend_i_had_24_gb_answers_on_a_pretend_card():
    out = lmt.advise({"question": "Pretend I had 24 GB. What's the biggest I can run?", "pretend_vram_gb": 24})
    assert out["spoken"].startswith("On a pretend card with 24 GB")
    assert out["data"]["model"] == "bonsai2:27b" and out["data"]["verdict"] == "runs_well"


def test_an_unknown_model_is_said_plainly_without_a_guess():
    out = lmt.advise({"question": "Could I run Megatron 9000?"})
    assert out["kind"] == "unknown" and "don't know" in out["spoken"]


def test_the_tool_is_read_only_and_voice_callable():
    from agent_friday.governance import action_gate
    from agent_friday.services import voice_engine
    assert lmt.RINGS[lmt.TOOL_NAME] == 0
    assert lmt.TOOL_NAME in action_gate.INTERNAL_TOOLS, "it reaches no one, so no card"
    tool = next(t for t in voice_engine._VOICE_LIVE_TOOLS if t[0] == lmt.TOOL_NAME)
    assert tool[3] == ["question"] and "pretend_vram_gb" in tool[2]
    assert "about" in tool[1], "the description tells the model to keep 'about' as said"
    assert "installs nothing" in tool[1]


def test_the_tool_result_is_json_the_model_can_speak_from():
    out = json.loads(lmt._tool_local_models_advise({"question": "biggest?"}))
    assert set(out) == {"spoken", "kind", "data"}
