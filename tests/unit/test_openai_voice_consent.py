"""OpenAI voice is a cloud voice, so it goes through the one consent flow (S2b).

Moving voice to OpenAI asks "just this call, or always?" exactly as Gemini Live does; nothing moves
silently; local-only refuses; a missing key is said ("Add an OpenAI key to use this.") and no question is
raised that has no working answer; "just this call" puts voice back on what it was when the call ends.
The settings store and the notifications engine are stand-ins, as in test_cloud_voice_scope.
"""
from __future__ import annotations

import copy

import pytest

from agent_friday.services import local_scheduler as ls
from agent_friday.services import local_voice_readiness as lvr
from agent_friday.services import openai_realtime as orx

T0 = 1_000_000.0


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(lvr, "_state_path", lambda: tmp_path / "local_voice_readiness.json")
    ls.reset_for_tests()
    lvr.reset_for_tests()
    monkeypatch.setattr(lvr, "_now", lambda: T0)
    monkeypatch.setattr(orx, "key_state", lambda: "connected")
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ne, "resolve", lambda key: 1)
    yield
    ls.reset_for_tests()
    lvr.reset_for_tests()


class Store:
    def __init__(self, monkeypatch, engine="local", mode="local_preferred"):
        self.data = {"voice_engine": engine, "model_routing": {"mode": mode}, lvr.OFFER_KEY: ""}
        monkeypatch.setattr(lvr, "_settings", lambda: copy.deepcopy(self.data))
        import agent_friday.core as core
        monkeypatch.setattr(core, "_save_settings", lambda patch, **kw: self.data.update(copy.deepcopy(patch)))


def test_openai_is_a_cloud_voice_and_an_allowed_setting_value():
    assert lvr._is_cloud("openai")
    from agent_friday.routes import core_routes
    assert core_routes._check_voice_enums({"voice_engine": "openai"}) is None
    assert core_routes._check_voice_enums({"voice_engine": "openia"}) is not None


def test_a_settings_write_to_openai_becomes_the_question_and_changes_nothing(monkeypatch):
    st = Store(monkeypatch)
    gate = lvr.gate_settings_write({"voice_engine": "openai"})
    assert gate["http"] == 409 and gate["body"]["status"] == "needs_choice"
    assert gate["body"]["question"]["engine"] == "openai"
    assert st.data["voice_engine"] == "local"


def test_no_key_is_said_and_no_question_is_raised(monkeypatch):
    Store(monkeypatch)
    monkeypatch.setattr(orx, "key_state", lambda: "missing")
    gate = lvr.gate_settings_write({"voice_engine": "openai"})
    assert gate["http"] == 409 and gate["body"]["status"] == "refused"
    assert gate["body"]["message"] == "Add an OpenAI key to use this."
    assert lvr.cloud_question_card() is None


def test_local_only_refuses_openai_voice(monkeypatch):
    Store(monkeypatch, mode="local_only")
    gate = lvr.gate_settings_write({"voice_engine": "openai"})
    assert gate["body"]["status"] == "refused" and gate["body"]["reason"] == "local_only"


def test_always_moves_voice_to_openai_and_records_the_engine(monkeypatch):
    st = Store(monkeypatch)
    q = lvr.ask_cloud_switch(source="settings", engine="openai")["question"]
    assert q["engine"] == "openai" and q["preselected"] is None
    res = lvr.answer_cloud_switch("always", question_id=q["id"])
    assert res["ok"] and st.data["voice_engine"] == "openai"
    assert st.data[lvr.CHOICE_KEY]["engine"] == "openai" and st.data[lvr.CHOICE_KEY]["previous"] == "local"


def test_just_this_call_goes_back_to_local_when_the_openai_call_ends(monkeypatch):
    st = Store(monkeypatch)
    q = lvr.ask_cloud_switch(source="settings", engine="openai")["question"]
    lvr.answer_cloud_switch("call", question_id=q["id"])
    assert st.data["voice_engine"] == "openai"
    assert lvr.call_ended() is True and st.data["voice_engine"] == "local"


def test_gemini_still_asks_and_answers_as_before(monkeypatch):
    st = Store(monkeypatch)
    monkeypatch.setattr(lvr, "_cloud_voice_connected", lambda: True)
    q = lvr.ask_cloud_switch(source="settings")["question"]
    assert q["engine"] == "gemini"
    lvr.answer_cloud_switch("always", question_id=q["id"])
    assert st.data["voice_engine"] == "gemini"


def test_cancel_leaves_voice_where_it_was(monkeypatch):
    st = Store(monkeypatch)
    q = lvr.ask_cloud_switch(source="settings", engine="openai")["question"]
    assert lvr.answer_cloud_switch("cancel", question_id=q["id"])["choice"] == "cancel"
    assert st.data["voice_engine"] == "local"


CAPABLE_SNAP = {"capable": True}


def _on_openai_by_always(monkeypatch):
    st = Store(monkeypatch)
    q = lvr.ask_cloud_switch(source="settings", engine="openai")["question"]
    lvr.answer_cloud_switch("always", question_id=q["id"])
    return st


def test_a_session_on_openai_says_why_it_is_on_the_cloud_and_local_is_ready(monkeypatch):
    Store(monkeypatch, engine="openai")
    monkeypatch.setattr(lvr, "voice_state", lambda snap=None: lvr.CAPABLE)
    out = lvr.why_cloud(engine="openai")
    assert out["on_cloud"] is True and "Local voice would also run" in out["text"]


def test_the_old_once_only_offer_covers_a_hand_set_openai_voice_too(monkeypatch):
    Store(monkeypatch, engine="openai")
    monkeypatch.setattr(lvr, "voice_state", lambda snap=None: lvr.CAPABLE)
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: {"measured": True})
    lvr._save({lvr.OFFER_KEY: lvr.OFFER_PENDING})
    assert lvr.offer_card() is not None


def test_after_always_on_openai_local_voice_ready_again_offers_the_switch_back_once(monkeypatch):
    st = _on_openai_by_always(monkeypatch)
    monkeypatch.setattr(lvr, "voice_state", lambda snap=None: lvr.CAPABLE)
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: {"measured": True})
    monkeypatch.setattr(lvr, "_now", lambda: T0 + 3600)
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is True
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1900) is False
    card = lvr.switch_back_card()
    assert [c["id"] for c in card["choices"]] == ["switch_back", "keep_cloud"]
    assert st.data["voice_engine"] == "openai"                 # offered, never switched
