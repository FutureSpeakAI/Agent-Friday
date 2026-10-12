"""Cloud voice is asked for, and offered back once (the owner's decision on LV-7).

  * Every switch to cloud voice (Settings, the voice, the offer after a local voice
    failure) asks "just this call, or always?": two choices, one marked Recommended,
    nothing preselected; nothing switches until the owner answers.
  * "Just this call" puts voice back on local when that cloud call ends; "always" is
    recorded with the time and the reason.
  * After an "always", when local voice is capable again, ONE switch-back offer is made
    (emitter LV7); no answer changes nothing; it is not repeated until readiness changes.
  * Nothing switches silently, in either direction.
  * The voice can ask and answer ("just this call", "always", "switch back", "keep cloud"),
    only on the owner's own words.
  * cloud_only has no local to go back to (no offer); local_only refuses cloud voice.

No models, no network, no real settings: the settings store, the notifications engine and
the owner's words are stand-ins.
"""
from __future__ import annotations

import contextlib
import copy
import json
from pathlib import Path

import pytest

from agent_friday.services import local_scheduler as ls
from agent_friday.services import local_voice_readiness as lvr
from agent_friday.services import local_voice_tools as lvt
from agent_friday.services import voice_live_channel as vlc

ROOT = Path(__file__).resolve().parents[2]

CAPABLE_SNAP = {"capable": True, "state": "capable", "assessed_at": "2026-10-11T00:00:00+00:00",
                "roles": {"voice": {"ok": True, "state": "capable", "why": None}}}
REFUSED_SNAP = {"capable": False, "state": "refused", "assessed_at": "2026-10-11T00:00:00+00:00",
                "roles": {"voice": {"ok": False, "state": "refused", "why": "no room on the card"}}}
NUMBERS = {"measured": True,
           "stt": {"model_id": "stt-x", "host_ram_mib": 480.0, "load_s": 2.1, "measured_at": "2026-10-11"},
           "tts": {"model_id": "tts-x", "host_ram_mib": 120.0, "load_s": 1.4, "measured_at": "2026-10-11"}}

T0 = 1_000_000.0


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(lvr, "_state_path", lambda: tmp_path / "local_voice_readiness.json")
    ls.reset_for_tests()
    lvr.reset_for_tests()
    clock = [T0]
    monkeypatch.setattr(lvr, "_now", lambda: clock[0])
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: NUMBERS)
    yield clock
    ls.reset_for_tests()
    lvr.reset_for_tests()
    for cid in list(vlc._CHANNELS):
        vlc._CHANNELS.pop(cid, None)


class Store:
    """What `_load_settings` reads and `_save_settings` writes, with every write kept."""

    def __init__(self, monkeypatch, engine="local", mode="local_preferred", **extra):
        self.data = {"voice_engine": engine, "model_routing": {"mode": mode}, lvr.OFFER_KEY: ""}
        self.data.update(extra)
        self.writes = []
        monkeypatch.setattr(lvr, "_settings", lambda: copy.deepcopy(self.data))
        import agent_friday.core as core
        monkeypatch.setattr(core, "_save_settings", self._save)

    def _save(self, patch, **kw):
        self.writes.append(copy.deepcopy(patch))
        self.data.update(copy.deepcopy(patch))
        return copy.deepcopy(self.data)

    def engine_writes(self):
        return [w for w in self.writes if "voice_engine" in w]


class Pushes:
    def __init__(self, monkeypatch):
        self.cards = []
        self.resolved = []
        import agent_friday.notifications_engine as ne
        monkeypatch.setattr(ne, "push", lambda **kw: self.cards.append(kw) or kw)
        monkeypatch.setattr(ne, "resolve", lambda key: self.resolved.append(key) or 1)

    def of(self, emitter):
        return [c for c in self.cards if c.get("emitter") == emitter]


class Words:
    """The owner's own words for the current turn, as the tool reads them."""

    def __init__(self, monkeypatch):
        self.said = ""
        self.at = None            # None: said one second after "now" (so after any question asked now)
        monkeypatch.setattr(lvt, "_owner_words", lambda: self.said)
        monkeypatch.setattr(lvt, "_owner_words_at", lambda: (
            (self.said, self.at if self.at is not None else lvr._now() + 1) if self.said else ("", None)))

    def say(self, text):
        self.said = text
        return self


def _tool(**inp):
    return json.loads(lvt._tool_local_voice_readiness(inp))


# ── 1. the switch asks, and preselects nothing ───────────────────────────────

def test_the_switch_asks_call_or_always_with_nothing_preselected(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    out = lvr.ask_cloud_switch(source="voice")
    assert out["ok"] and out["asked"] is True
    card = lvr.cloud_question_card()
    assert card["question"] == "Use cloud voice for just this call, or always?"
    assert [c["id"] for c in card["choices"]] == ["call", "always"]
    assert [c["recommended"] for c in card["choices"]] == [True, False], "exactly one Recommended: just this call"
    assert card["preselected"] is None and "selected" not in json.dumps(card["choices"])
    assert store.data["voice_engine"] == "local" and store.engine_writes() == [], "asking switches nothing"


def test_just_this_call_is_the_recommended_choice_because_it_keeps_local_the_default():
    rec = [c for c in lvr.scope_choices() if c["recommended"]]
    assert [c["id"] for c in rec] == ["call"]
    assert "Local voice stays your default" in rec[0]["detail"]
    assert "default" in lvr.spoken_question()


def test_a_settings_write_to_cloud_voice_becomes_the_question(monkeypatch):
    store = Store(monkeypatch)
    out = lvr.gate_settings_write({"voice_engine": "gemini"})
    assert out["http"] == 409 and out["body"]["status"] == "needs_choice"
    assert out["body"]["question"]["preselected"] is None
    assert store.writes == [] and store.data["voice_engine"] == "local"
    assert lvr.cloud_question_card() is not None, "the Settings row has a question to show"


def test_other_voice_writes_pass_the_gate(monkeypatch):
    Store(monkeypatch)
    assert lvr.gate_settings_write({"voice_engine": "local"}) is None
    assert lvr.gate_settings_write({"voice_engine": "local-gpu"}) is None
    assert lvr.gate_settings_write({"voice_silence_ms": 900}) is None
    Store(monkeypatch, engine="gemini")
    assert lvr.gate_settings_write({"voice_engine": "gemini"}) is None, "already on the cloud: not a switch"


def test_the_settings_route_asks_before_it_saves():
    from agent_friday.routes import core_routes
    import inspect
    src = inspect.getsource(core_routes.api_settings)
    gate, save = src.index("_check_cloud_voice_switch(new_settings)"), src.index("merged = _save_settings(")
    assert gate < save, "the gate runs before anything is written"
    assert "voice_engine_choice" in src and "voice_switch_back" in src, "a settings write cannot set the record"


def test_the_ask_route_and_the_answer_routes_exist_and_need_login():
    import agent_friday.routes.voice as rv
    rules = {r.rule: r for r in _url_rules(rv)}
    for path in ("/api/voice/cloud-switch", "/api/voice/cloud-switch/answer",
                 "/api/voice/local-readiness/switch-back"):
        assert path in rules, path
    for fn in (rv.voice_cloud_switch, rv.voice_cloud_switch_answer, rv.voice_switch_back_answer):
        assert fn.__name__ and getattr(fn, "__wrapped__", None) is not None, "login_required"


def _url_rules(rv):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(rv.voice_bp)
    return list(app.url_map.iter_rules())


# ── 2. just this call reverts at call end; always persists ───────────────────

def test_just_this_call_reverts_to_local_when_the_call_ends(monkeypatch):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="voice")
    res = lvr.answer_cloud_switch("call")
    assert res["ok"] and store.data["voice_engine"] == "gemini"
    assert store.data[lvr.CHOICE_KEY]["scope"] == "call" and store.data[lvr.CHOICE_KEY]["previous"] == "local"
    assert lvr.call_ended() is True
    assert store.data["voice_engine"] == "local" and store.data[lvr.CHOICE_KEY] == {}
    line = p.of("LV9")
    assert len(line) == 1 and line[0]["kind"] == "cloud_voice_call_ended", "going back is said, not silent"
    assert lvr.call_ended() is False, "nothing left to revert"


def test_just_this_call_goes_back_to_the_engine_it_was(monkeypatch):
    store = Store(monkeypatch, engine="local-gpu")
    Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    lvr.answer_cloud_switch("call")
    lvr.call_ended()
    assert store.data["voice_engine"] == "local-gpu"


def test_the_cloud_call_end_hook_is_wired_into_the_live_session():
    src = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    live = src[src.index("def ws_live(ws):"):]
    assert "call_ended()" in live and live.index("call_ended()") > live.index("ws.close()")


def test_an_unused_just_this_call_does_not_hold_the_cloud_for_good(monkeypatch, env):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="voice")
    lvr.answer_cloud_switch("call")
    env[0] = T0 + lvr.CALL_SCOPE_TTL_S - 60
    assert lvr.expire_unused_call_scope() is False and store.data["voice_engine"] == "gemini"
    vlc._CHANNELS["c1"] = lambda *a, **k: True
    env[0] = T0 + lvr.CALL_SCOPE_TTL_S + 60
    assert lvr.expire_unused_call_scope() is False, "not while a voice session is open"
    vlc._CHANNELS.clear()
    assert lvr.expire_unused_call_scope() is True and store.data["voice_engine"] == "local"
    assert len(p.of("LV9")) == 1


def test_always_persists_with_the_time_and_the_reason(monkeypatch, env):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    lvr.note_local_voice_failed()
    env[0] = T0 + 60
    lvr.ask_cloud_switch(source="failure")
    env[0] = T0 + 90
    res = lvr.answer_cloud_switch("always")
    assert res["ok"] and store.data["voice_engine"] == "gemini"
    rec = store.data[lvr.CHOICE_KEY]
    assert rec["scope"] == "always" and rec["at"] == T0 + 90
    assert rec["reason"] == "local_voice_failed" and rec["source"] == "failure"
    assert lvr.call_ended() is False and store.data["voice_engine"] == "gemini", "a call ending does not undo always"
    summary = lvr.cloud_choice_summary()
    assert summary["scope"] == "always" and summary["reason_text"] == "Local voice did not start"


def test_the_reason_is_your_choice_when_local_voice_did_not_fail(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    lvr.answer_cloud_switch("always")
    assert store.data[lvr.CHOICE_KEY]["reason"] == "your_choice"


def test_an_answer_needs_a_waiting_question_and_a_real_choice(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    assert lvr.answer_cloud_switch("always")["ok"] is False and store.writes == []
    lvr.ask_cloud_switch(source="settings")
    assert lvr.answer_cloud_switch("whenever")["ok"] is False and store.writes == []
    res = lvr.answer_cloud_switch("cancel")
    assert res["ok"] and store.data["voice_engine"] == "local" and store.engine_writes() == []


# ── 3. the switch-back offer: once, never silent ─────────────────────────────

def _on_cloud_by_always(monkeypatch, env, **kw):
    env[0] = T0
    store = Store(monkeypatch, **kw)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    lvr.answer_cloud_switch("always")
    store.writes.clear()
    p.cards.clear()
    env[0] = T0 + 3600
    return store, p


def test_capable_again_offers_exactly_once(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is True
    cards = p.of("LV7")
    assert len(cards) == 1 and cards[0]["kind"] == "local_voice_switch_offer"
    assert "480 MB" in cards[0]["body"] and "Switch back?" in cards[0]["body"]
    assert store.data[lvr.SWITCHBACK_KEY]["state"] == "pending"
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1900) is False
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 2000) is False
    assert len(p.of("LV7")) == 1, "once"
    card = lvr.switch_back_card()
    assert [c["id"] for c in card["choices"]] == ["switch_back", "keep_cloud"]
    assert card["preselected"] is None and sum(1 for c in card["choices"] if c["recommended"]) == 1


def test_no_offer_until_a_reassessment_after_the_switch(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 - 5) is False
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=None) is False
    assert p.cards == [] and store.writes == []


@pytest.mark.parametrize("snap", [REFUSED_SNAP, {"state": "unknown", "roles": {"voice": {"state": "unknown"}}}])
def test_no_offer_while_local_voice_is_not_capable(monkeypatch, env, snap):
    store, p = _on_cloud_by_always(monkeypatch, env)
    assert lvr.maybe_offer_switch_back(snap, assessed_at=T0 + 1800) is False
    assert p.cards == []


def test_no_offer_without_measured_numbers(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: {"measured": False, "stt": None, "tts": None})
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is False


def test_it_is_offered_again_only_after_readiness_changed(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is True
    lvr.decide_switch_back("keep_cloud")
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 2400) is False, "kept: not asked again"
    assert lvr.maybe_offer_switch_back(REFUSED_SNAP, assessed_at=T0 + 3000) is False
    assert store.data[lvr.SWITCHBACK_KEY]["state"] == "kept", "one reading that is not capable is not a change"
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 3100) is False
    assert lvr.maybe_offer_switch_back(REFUSED_SNAP, assessed_at=T0 + 3200) is False
    assert lvr.maybe_offer_switch_back(REFUSED_SNAP, assessed_at=T0 + 3300) is False
    assert store.data[lvr.SWITCHBACK_KEY] == {}, "two readings in a row: readiness changed, the past offer is forgotten"
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 3600) is True
    assert len(p.of("LV7")) == 2


def test_no_answer_means_no_change(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    assert store.data["voice_engine"] == "gemini" and store.engine_writes() == []
    for _ in range(3):
        lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 2000)
        lvr.expire_unused_call_scope()
        lvr.call_ended()
    assert store.data["voice_engine"] == "gemini" and store.engine_writes() == []


def test_switch_back_goes_to_the_engine_it_was_and_says_so(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env, engine="local-gpu")
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    res = lvr.decide_switch_back("switch_back")
    assert res["ok"] and res["message"] == "Switched back to local voice."
    assert store.data["voice_engine"] == "local-gpu" and store.data[lvr.CHOICE_KEY] == {}
    assert store.data[lvr.SWITCHBACK_KEY]["state"] == "switched"
    assert lvr.OFFER_DEDUPE_KEY in p.resolved, "the card is closed"


def test_keep_cloud_changes_nothing_but_the_record(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    store.writes.clear()
    res = lvr.decide_switch_back("keep_cloud")
    assert res["ok"] and store.engine_writes() == [] and store.data["voice_engine"] == "gemini"
    assert store.data[lvr.SWITCHBACK_KEY]["state"] == "kept" and store.data[lvr.CHOICE_KEY]["scope"] == "always"


def test_only_a_pending_offer_can_be_answered(monkeypatch, env):
    store, _ = _on_cloud_by_always(monkeypatch, env)
    assert lvr.decide_switch_back("switch_back")["ok"] is False
    assert store.engine_writes() == []
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    assert lvr.decide_switch_back("maybe")["ok"] is False


def test_a_cloud_choice_by_just_this_call_gets_no_switch_back_offer(monkeypatch, env):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    lvr.answer_cloud_switch("call")
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 100) is False
    assert lvr.maybe_offer_switch(CAPABLE_SNAP) is False, "the old once-only offer yields to a recorded choice"
    assert p.cards == []


def test_the_owner_moving_to_local_by_hand_ends_the_record_and_the_offer(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    store.data["voice_engine"] = "local"
    lvr.note_engine_written("local")
    assert store.data[lvr.CHOICE_KEY] == {} and store.data[lvr.SWITCHBACK_KEY] == {}
    assert lvr.switch_back_card() is None


def test_the_reassessment_job_hands_its_result_to_the_switch_back_offer():
    import inspect
    assert "maybe_offer_switch_back(snap" in inspect.getsource(lvr.run_pending)
    assert "expire_unused_call_scope" in inspect.getsource(lvr.tick)


def test_after_an_always_the_idle_check_looks_daily_not_weekly(monkeypatch, env):
    store, _ = _on_cloud_by_always(monkeypatch, env)
    assert lvr._watching_for_switch_back() is True
    store.data[lvr.SWITCHBACK_KEY] = {"state": "kept"}
    assert lvr._watching_for_switch_back() is False
    assert lvr.SWITCH_BACK_RECHECK_S < lvr.WEEK_S


# ── 4. the four modes ────────────────────────────────────────────────────────

def test_no_switch_back_offer_in_local_only(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    store.data["model_routing"] = {"mode": "local_only"}
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is False
    assert p.cards == [] and lvr._watching_for_switch_back() is False


def test_cloud_only_the_factory_default_offers_the_switch_back_like_any_mode(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    for blank in ({"mode": "cloud_only"}, {}, {"mode": ""}):
        store.data["model_routing"] = blank
        store.data[lvr.SWITCHBACK_KEY] = {}
        p.cards.clear()
        assert lvr._watching_for_switch_back() is True
        assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is True, blank
        assert len(p.of("LV7")) == 1 and store.engine_writes() == []


def test_the_old_offer_is_not_suppressed_without_the_new_one_taking_its_place(monkeypatch, env):
    """A recorded 'always' silences the old once-only offer; the switch-back offer must then fire for the same owner."""
    store, p = _on_cloud_by_always(monkeypatch, env, model_routing={"mode": "cloud_only"})
    assert lvr.maybe_offer_switch(CAPABLE_SNAP) is False
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is True


@pytest.mark.parametrize("mode", ["local_preferred", "cloud_preferred", "cloud_only"])
def test_the_other_modes_do_offer_the_switch_back(monkeypatch, env, mode):
    store, p = _on_cloud_by_always(monkeypatch, env)
    store.data["model_routing"] = {"mode": mode}
    assert lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800) is True


def test_local_only_refuses_the_switch_with_a_reason(monkeypatch):
    store = Store(monkeypatch, mode="local_only")
    p = Pushes(monkeypatch)
    out = lvr.ask_cloud_switch(source="voice")
    assert out["refused"] is True and out["reason"] == "local_only"
    assert "Local only mode is on" in out["message"] and "not allowed" in out["message"]
    assert lvr.cloud_question_card() is None and p.cards == [] and store.writes == []
    gate = lvr.gate_settings_write({"voice_engine": "gemini"})
    assert gate["http"] == 409 and gate["body"]["status"] == "refused" and gate["body"]["reason"] == "local_only"
    assert lvr.answer_cloud_switch("always")["ok"] is False and store.engine_writes() == []


def test_a_question_that_waited_across_a_switch_to_local_only_is_refused(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    store.data["model_routing"] = {"mode": "local_only"}
    res = lvr.answer_cloud_switch("always")
    assert res["ok"] is False and res["reason"] == "local_only"
    assert store.data["voice_engine"] == "local" and store.engine_writes() == []


@pytest.mark.parametrize("mode", ["cloud_only", "cloud_preferred", "local_preferred"])
def test_the_other_modes_ask_the_same_question(monkeypatch, mode):
    Store(monkeypatch, mode=mode)
    Pushes(monkeypatch)
    assert lvr.ask_cloud_switch(source="settings")["asked"] is True


# ── 5. nothing switches silently ─────────────────────────────────────────────

def test_nothing_switches_the_engine_without_an_owner_answer(monkeypatch, env):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    Words(monkeypatch)
    lvr.note_local_voice_failed()
    lvr.ask_cloud_switch(source="failure")
    lvr.gate_settings_write({"voice_engine": "gemini"})
    lvr.maybe_offer_switch(CAPABLE_SNAP)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 10)
    lvr.expire_unused_call_scope()
    lvr.call_ended()
    _tool(action="cloud")
    _tool(action="answer", choice="always")
    _tool(action="status")
    _tool(action="measure")
    lvr.panel_status()
    assert store.engine_writes() == [] and store.data["voice_engine"] == "local"


def test_going_back_is_always_said_and_always_the_owners_choice(monkeypatch, env):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    lvr.answer_cloud_switch("call")
    lvr.call_ended()
    assert len(p.of("LV9")) == 1, "the revert of a chosen 'just this call' is a visible line"
    store2 = Store(monkeypatch, engine="gemini", **{lvr.CHOICE_KEY: {"scope": "always", "at": T0, "previous": "local"}})
    env[0] = T0 + 100
    p2 = Pushes(monkeypatch)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 50)
    assert store2.engine_writes() == [] and len(p2.of("LV7")) == 1


def test_a_failure_note_changes_no_setting(monkeypatch):
    store = Store(monkeypatch)
    lvr.note_local_voice_failed()
    assert store.writes == []


# ── 6. the notices all go through the registry ───────────────────────────────

def test_the_new_notices_are_registered_emitters():
    from agent_friday.services import notice_registry as reg
    q, end = reg.get("LV8"), reg.get("LV9")
    assert q.place == reg.NEEDS_YOU and q.kind == "cloud_voice_scope_question" and q.key == "cloud-voice-scope"
    assert end.place == reg.UPDATES and end.kind == "cloud_voice_call_ended" and end.key == "cloud-voice-call-ended"
    assert not q.safety_critical and not end.safety_critical
    assert reg.resolve("cloud_voice_scope_question", "local-voice").id == "LV8"
    assert reg.resolve("cloud_voice_call_ended", "local-voice").id == "LV9"
    assert reg.get("LV7").key == "local-voice-switch-offer", "the switch-back offer is the LV7 emitter"


def test_the_question_card_goes_through_the_engine_for_voice_and_failure_not_for_settings(monkeypatch):
    Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    assert p.cards == [], "Settings already has the panel in front of the owner"
    lvr.ask_cloud_switch(source="voice")
    assert len(p.of("LV8")) == 1 and p.of("LV8")[0]["dedupe_key"] == "cloud-voice-scope"
    assert "Recommended" in p.of("LV8")[0]["body"]


# ── 7. the voice answers ─────────────────────────────────────────────────────

def test_the_voice_asks_and_nothing_changes(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    Words(monkeypatch).say("use cloud voice")
    out = _tool(action="cloud")
    assert out["kind"] == "asked" and out["pending"] == "scope" and out["choices"] == ["call", "always"]
    assert "just this call, or always" in out["spoken"] and "default" in out["spoken"]
    assert store.engine_writes() == []


def test_the_voice_answer_just_this_call_works(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("use cloud voice")
    _tool(action="cloud")
    w.say("just this call")
    out = _tool(action="answer", choice="call")
    assert out["kind"] == "answered" and out["choice"] == "call"
    assert store.data["voice_engine"] == "gemini" and store.data[lvr.CHOICE_KEY]["scope"] == "call"
    assert "when it ends" in out["spoken"]


def test_the_voice_answer_always_works(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("switch me to cloud voice")
    _tool(action="cloud")
    w.say("always")
    out = _tool(action="answer", choice="always")
    assert out["kind"] == "answered" and store.data[lvr.CHOICE_KEY]["scope"] == "always"
    assert store.data[lvr.CHOICE_KEY]["source"] == "voice"


def test_an_answer_with_no_choice_named_by_the_model_is_not_accepted(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("use cloud voice")
    _tool(action="cloud")
    w.say("just this call please")
    out = _tool(action="answer")
    assert out["kind"] == "asked_again" and store.engine_writes() == []
    assert _tool(action="answer", choice="call")["kind"] == "answered"


def test_the_same_breath_that_asked_is_not_the_answer(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    Words(monkeypatch).say("use cloud voice for just this call")
    _tool(action="cloud")
    out = _tool(action="answer", choice="call")
    assert out["kind"] == "asked_again" and store.engine_writes() == []


def test_a_models_choice_without_the_owners_words_changes_nothing(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("use cloud voice")
    _tool(action="cloud")
    w.say("")
    out = _tool(action="answer", choice="always")
    assert out["kind"] == "asked_again" and "just this call, or always" in out["spoken"]
    w.say("what is the weather")
    assert _tool(action="answer", choice="always")["kind"] == "asked_again"
    assert store.engine_writes() == []


def test_a_choice_that_disagrees_with_the_owners_words_changes_nothing(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("use cloud voice")
    _tool(action="cloud")
    w.say("just this call")
    assert _tool(action="answer", choice="always")["kind"] == "asked_again"
    assert store.engine_writes() == []


def test_words_that_give_two_answers_are_asked_again(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("use cloud voice")
    _tool(action="cloud")
    w.say("always, no, just this call")
    assert _tool(action="answer")["kind"] == "asked_again" and store.engine_writes() == []


def test_the_voice_can_switch_back_and_keep_cloud(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    w = Words(monkeypatch)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    w.say("keep cloud")
    out = _tool(action="answer", choice="keep_cloud")
    assert out["kind"] == "answered" and store.data["voice_engine"] == "gemini"
    assert store.data[lvr.SWITCHBACK_KEY]["state"] == "kept"
    store2, p2 = _on_cloud_by_always(monkeypatch, env)
    Words(monkeypatch).say("yes, switch back")
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    out = _tool(action="answer", choice="switch_back")
    assert out["kind"] == "answered" and store2.data["voice_engine"] == "local"


def test_an_answer_for_the_other_question_is_not_taken(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("use cloud voice")
    _tool(action="cloud")
    w.say("keep cloud")
    out = _tool(action="answer", choice="keep_cloud")
    assert out["kind"] == "asked_again" and "other question" in out["spoken"]
    assert store.engine_writes() == []


def test_an_answer_with_no_question_waiting_says_so(monkeypatch):
    store = Store(monkeypatch)
    Words(monkeypatch).say("always")
    out = _tool(action="answer", choice="always")
    assert out["kind"] == "no_question" and store.writes == []


def test_local_only_refuses_by_voice_with_the_reason(monkeypatch):
    store = Store(monkeypatch, mode="local_only")
    Words(monkeypatch).say("use cloud voice")
    out = _tool(action="cloud")
    assert out["kind"] == "refused" and out["reason"] == "local_only" and "Local only mode" in out["spoken"]
    assert store.writes == []


@pytest.mark.parametrize("words,expect", [
    ("just this call", "call"), ("Just this call, please.", "call"), ("only for this call", "call"),
    ("always", "always"), ("Always.", "always"), ("from now on", "always"),
    ("switch back", "switch_back"), ("go back to local", "switch_back"), ("yes, switch back", "switch_back"),
    ("keep cloud", "keep_cloud"), ("keep the cloud", "keep_cloud"), ("stay on cloud", "keep_cloud"),
    ("never mind", None), ("cancel", None), ("stay on local", "cancel"),
    # a retraction or a correction anywhere, or someone else's words, is not an answer
    ("always, no, wait", None), ("Always. No.", None), ("just this call, no", None),
    ("always wait", None), ("actually always", None), ("she said always", None),
    ("he says switch back", None), ("\"always\"", None), ("switch back never mind", None),
    ("always, no, just this call", None), ("", None), ("what is the weather", None),
    # a negation just before the phrase is a doubt, never the answer
    ("no, not always", None), ("not always", None), ("don't switch back", None), ("do not switch back", None),
    ("I don't want to keep cloud", None), ("never always", None), ("no, just this call", None),
    # whole words only, and questions and long talk are not answers
    ("who is this call from?", None), ("because local is fine", None), ("alwaysish", None),
    ("is it always cloud?", None), ("maybe always", None), ("should I keep cloud", None),
    ("I always forget which setting that is for this call", None), ("use local", None), ("not yet", None),
    ("callous", None), ("recall", None),
])
def test_what_the_owners_words_answer(words, expect):
    assert lvr.classify_answer(words) == expect


def test_the_tool_schema_and_registration():
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import agent
    props = lvt.TOOLS[0]["input_schema"]["properties"]
    assert set(props["action"]["enum"]) == {"status", "measure", "cloud", "answer"}
    assert set(props["choice"]["enum"]) == {"call", "always", "switch_back", "keep_cloud", "cancel"}
    assert agent.TOOL_RINGS["local_voice_readiness"] == 1
    assert ag.classify("local_voice_readiness", {"action": "answer", "choice": "always"})[0] == ag.INTERNAL
    assert "local_voice_readiness" in agent.ON_DEMAND_TOOLS


def test_the_voice_contract_is_not_touched():
    """The tool stays out of the voice contract (voice reaches it through ask_friday), so the
    compact contract keeps its size; the two ceiling tests hold it at 9,700."""
    from agent_friday.services import voice_engine as ve
    assert "local_voice_readiness" not in ve._voice_tool_names()
    from agent_friday.services import laya_router as lr
    assert "local_voice_readiness" not in lr.TOOLS


def test_ask_friday_hands_the_tool_the_callers_words_and_nothing_else_moves():
    src = (ROOT / "src" / "agent_friday" / "services" / "voice_engine.py").read_text(encoding="utf-8")
    assert src.count('"voice_owner_words": str((session or {}).get("owner_text") or "")[:600]') == 2
    tools = (ROOT / "src" / "agent_friday" / "services" / "local_voice_tools.py").read_text(encoding="utf-8")
    assert "voice_owner_words" in tools and 'startswith("voice")' in tools


# ── 8. the panel, the routes, the manifest and the UI ────────────────────────

def test_the_panel_status_carries_the_question_the_offer_and_the_choice(monkeypatch, env):
    from agent_friday.privacy import cloud_consent as cc
    monkeypatch.setattr(cc, "stored_snapshot", lambda: CAPABLE_SNAP)
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    st = lvr.panel_status()
    assert st["cloud_question"]["preselected"] is None and st["switch_back"] is None and st["cloud_choice"] is None
    lvr.answer_cloud_switch("always")
    st = lvr.panel_status()
    assert st["cloud_question"] is None and st["cloud_choice"]["scope"] == "always"
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 10_000)
    env[0] = T0 + 20_000
    store.data[lvr.SWITCHBACK_KEY] = {"state": "pending", "made_at": T0 + 1}
    assert lvr.panel_status()["switch_back"]["choices"][0]["id"] == "switch_back"


def _client(monkeypatch, store, page=True):
    import agent_friday.routes.voice as rv
    from flask import Flask
    from agent_friday.privacy import cloud_consent as cc
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: page)
    monkeypatch.setattr(rv, "_is_local_request", lambda: True)
    monkeypatch.setattr(cc, "stored_snapshot", lambda: CAPABLE_SNAP)
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session on loopback
    app.register_blueprint(rv.voice_bp)
    return app.test_client()


def test_the_routes_ask_answer_and_refuse(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    client = _client(monkeypatch, store)
    r = client.post("/api/voice/cloud-switch", json={"source": "settings"})
    body = r.get_json()
    assert r.status_code == 200 and body["cloud_question"]["preselected"] is None
    assert store.engine_writes() == []
    bad = client.post("/api/voice/cloud-switch/answer", json={"choice": "sometimes"})
    assert bad.status_code == 409 and store.engine_writes() == []
    qid = body["cloud_question"]["id"]
    gone = client.post("/api/voice/cloud-switch/answer", json={"choice": "call", "id": "not-this-one"})
    assert gone.status_code == 409 and store.engine_writes() == [], "another question's id is refused"
    ok = client.post("/api/voice/cloud-switch/answer", json={"choice": "call", "id": qid})
    assert ok.status_code == 200 and store.data["voice_engine"] == "gemini"
    none = client.post("/api/voice/cloud-switch/answer", json={"choice": "always"})
    assert none.status_code == 409, "no question is waiting any more"


def test_the_route_refuses_in_local_only(monkeypatch):
    store = Store(monkeypatch, mode="local_only")
    client = _client(monkeypatch, store)
    r = client.post("/api/voice/cloud-switch", json={})
    assert r.status_code == 409 and r.get_json()["reason"] == "local_only"


def test_the_switch_back_route(monkeypatch, env):
    store, _ = _on_cloud_by_always(monkeypatch, env)
    client = _client(monkeypatch, store)
    assert client.post("/api/voice/local-readiness/switch-back", json={"choice": "switch_back"}).status_code == 409
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    r = client.post("/api/voice/local-readiness/switch-back", json={"choice": "switch_back"})
    assert r.status_code == 200 and store.data["voice_engine"] == "local"


def test_a_local_voice_failure_remembers_why_and_offers_the_same_question():
    src = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    assert src.count("_note_local_voice_failed()") >= 3          # the helper's definition and two failure sites
    assert src.count('"offer": _CLOUD_SWITCH_OFFER') == 2
    assert "I will ask whether that is" in src


def test_the_manifest_and_the_ui_action_fixture_carry_the_cloud_voice_scope():
    text = (ROOT / "src" / "agent_friday" / "resources" / "laya_manifests" / "settings.json").read_text(encoding="utf-8")
    fixture = (ROOT / "tests" / "fixtures" / "laya_ws_ui_actions.json").read_text(encoding="utf-8")
    assert "settings.cloud_voice_scope" in text and "settings.cloud_voice_scope" in fixture
    assert "local_voice_readiness answer" in text


def _component(html: str) -> str:
    a = html.index("function LocalVoiceReadiness(")
    return html[a:html.index("// ── Settings → Voice → Mode", a)]


def test_both_ui_files_ask_call_or_always_and_offer_the_switch_back():
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    assert _component(idx) == _component(app), "index.html and ui_parts/app.html diverged"
    c = _component(idx)
    for needle in ("/api/voice/cloud-switch/answer", "/api/voice/local-readiness/switch-back",
                   "data-cloud-voice-question", "data-switch-back-offer", "data-cloud-choice",
                   "st.cloud_question.choices", "st.switch_back.choices", "Stay on local voice",
                   "friday:cloud-voice-asked"):
        assert needle in c, needle
    assert c.count("(Recommended)") >= 3
    assert "'active'" not in c and "settings-btn active" not in c, "no choice is preselected"
    for html in (idx, app):
        assert "pending.id === 'gemini' ? 'Continue: just this call or always?'" in html
        assert "'/api/voice/cloud-switch'" in html
        assert "save({ voice_engine: pending.id }); setPending(null);" not in html, "gemini is no longer a plain save"


def test_the_settings_keys_are_declared_and_documented():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["voice_engine_choice"] == {} and DEFAULT_SETTINGS["voice_switch_back"] == {}
    docs = (ROOT / "docs" / "user-guide" / "configuration.md").read_text(encoding="utf-8")
    assert "`voice_engine_choice`" in docs and "`voice_switch_back`" in docs


# ── 9. the critic's findings ──────────────────────────────────────────────────

def test_the_routes_answer_only_to_the_owners_page(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    client = _client(monkeypatch, store, page=False)
    assert client.post("/api/voice/cloud-switch", json={}).status_code == 403
    assert client.post("/api/voice/cloud-switch/answer", json={"choice": "always"}).status_code == 403
    assert client.post("/api/voice/local-readiness/switch-back", json={"choice": "switch_back"}).status_code == 403
    assert store.writes == [] and lvr.cloud_question_card() is None


def test_a_forged_reason_is_ignored_the_server_derives_it(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    client = _client(monkeypatch, store)
    r = client.post("/api/voice/cloud-switch", json={"source": "settings", "reason": "local_voice_failed"})
    qid = r.get_json()["cloud_question"]["id"]
    assert r.get_json()["cloud_question"]["reason"] == "your_choice"
    client.post("/api/voice/cloud-switch/answer", json={"choice": "always", "id": qid})
    assert store.data[lvr.CHOICE_KEY]["reason"] == "your_choice"


def test_a_question_runs_out(monkeypatch, env):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="voice")
    card = lvr.cloud_question_card()
    assert card["id"] and card["expires_at"] == T0 + lvr.QUESTION_TTL_S
    env[0] = T0 + lvr.QUESTION_TTL_S + 1
    assert lvr.cloud_question_card() is None
    res = lvr.answer_cloud_switch("always")
    assert res["ok"] is False and store.engine_writes() == []
    assert "cloud-voice-scope" in p.resolved, "its card goes with it"


def test_an_answer_names_its_question(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    qid = lvr.cloud_question_card()["id"]
    assert lvr.answer_cloud_switch("always", question_id="other")["ok"] is False
    assert store.engine_writes() == []
    lvr.ask_cloud_switch(source="settings")                      # asked again: a new id
    assert lvr.answer_cloud_switch("always", question_id=qid)["ok"] is False, "the old question is gone"
    assert lvr.answer_cloud_switch("always", question_id=lvr.cloud_question_card()["id"])["ok"] is True


def test_words_spoken_before_the_question_are_not_an_answer(monkeypatch, env):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    w = Words(monkeypatch).say("use cloud voice")
    _tool(action="cloud")
    w.say("just this call")
    w.at = T0 - 5                                               # said before the question was asked
    out = _tool(action="answer", choice="call")
    assert out["kind"] == "asked_again" and store.engine_writes() == []
    w.at = T0 + 2
    assert _tool(action="answer", choice="call")["kind"] == "answered"


def test_a_relay_cannot_carry_an_older_utterance_into_an_answer(monkeypatch, env):
    """Owner words with no stamp, or too old, are not owner words for an answer."""
    src = (ROOT / "src" / "agent_friday" / "services" / "local_voice_tools.py").read_text(encoding="utf-8")
    assert "OWNER_WORDS_MAX_AGE_S" in src and "voice_owner_at" in src and "owner_text_at" in src
    vs = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    assert vs.count('["owner_text_at"] = _time.time()') == 2 and '"owner_text_at": _time.time()' in vs
    ve = (ROOT / "src" / "agent_friday" / "services" / "voice_engine.py").read_text(encoding="utf-8")
    assert ve.count('"voice_owner_at": (session or {}).get("owner_text_at")') == 2


def test_a_spoken_switch_back_must_come_after_the_offer(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    w = Words(monkeypatch)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    made = store.data[lvr.SWITCHBACK_KEY]["made_at"]
    w.say("switch back")
    w.at = made - 10
    assert _tool(action="answer", choice="switch_back")["kind"] == "asked_again"
    assert store.data["voice_engine"] == "gemini"
    w.at = made + lvr.SWITCH_BACK_VOICE_TTL_S + 10
    assert _tool(action="answer", choice="switch_back")["kind"] == "asked_again"
    w.at = made + 5
    assert _tool(action="answer", choice="switch_back")["kind"] == "answered"
    assert store.data["voice_engine"] == "local"


def test_the_page_answer_names_the_offer_too(monkeypatch, env):
    store, _ = _on_cloud_by_always(monkeypatch, env)
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    assert lvr.decide_switch_back("switch_back", question_id="stale")["ok"] is False
    assert store.data["voice_engine"] == "gemini"
    assert lvr.decide_switch_back("switch_back", question_id=lvr.switch_back_card()["id"])["ok"] is True


def test_federation_never_syncs_the_voice_engine_or_the_choice_records():
    from agent_friday.routes import federation
    for key in ("voice_engine", "voice_engine_choice", "voice_switch_back"):
        assert key not in federation._SYNC_SAFE_KEYS, key


def test_a_restart_in_the_middle_of_just_this_call_goes_back_at_boot(monkeypatch):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    lvr.answer_cloud_switch("call")
    assert store.data["voice_engine"] == "gemini"
    lvr.note_cloud_call_started()                               # a call really used it
    assert lvr.reconcile_at_boot() is True
    assert store.data["voice_engine"] == "local" and store.data[lvr.CHOICE_KEY] == {}
    assert len(p.of("LV9")) == 1 and "restarted during a cloud call" in p.of("LV9")[0]["body"]
    assert lvr.reconcile_at_boot() is False


def test_boot_leaves_an_always_alone(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    lvr.answer_cloud_switch("always")
    assert lvr.reconcile_at_boot() is False and store.data["voice_engine"] == "gemini"


def test_the_readiness_loop_reconciles_before_its_first_look():
    import inspect
    src = inspect.getsource(lvr.loop)
    assert src.index("reconcile_at_boot()") < src.index("time.sleep(90.0)")


def test_the_settings_gate_fails_closed(monkeypatch):
    from agent_friday.routes import core_routes
    monkeypatch.setattr(lvr, "gate_settings_write", lambda ns: (_ for _ in ()).throw(RuntimeError("boom")))
    out = core_routes._check_cloud_voice_switch({"voice_engine": "gemini"})
    assert out["http"] == 409 and out["body"]["status"] == "error"
    assert core_routes._check_cloud_voice_switch({"voice_engine": "local"}) is None
    assert core_routes._check_cloud_voice_switch({"theme": "dark"}) is None


def test_one_failed_measurement_does_not_repeat_a_kept_offer(monkeypatch, env):
    store, p = _on_cloud_by_always(monkeypatch, env)
    unknown = {"state": "unknown", "roles": {"voice": {"state": "unknown"}}}
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    lvr.decide_switch_back("keep_cloud")
    p.cards.clear()
    for at, snap in ((T0 + 2000, unknown), (T0 + 2100, CAPABLE_SNAP), (T0 + 2200, unknown), (T0 + 2300, CAPABLE_SNAP)):
        assert lvr.maybe_offer_switch_back(snap, assessed_at=at) is False
    assert p.cards == [] and store.data[lvr.SWITCHBACK_KEY]["state"] == "kept"


def test_the_switch_back_is_one_delivery_a_tray_card_not_also_a_call(monkeypatch, env):
    import inspect
    assert not hasattr(lvr, "_say_in_calls")
    assert "_CHANNELS" not in inspect.getsource(lvr)
    store, p = _on_cloud_by_always(monkeypatch, env)
    delivered = []
    monkeypatch.setattr(vlc, "deliver", lambda *a, **k: delivered.append(a) or True)
    vlc._CHANNELS["c1"] = lambda *a, **k: True
    lvr.maybe_offer_switch_back(CAPABLE_SNAP, assessed_at=T0 + 1800)
    assert delivered == [] and len(p.of("LV7")) == 1


def test_a_question_made_moot_closes_its_card(monkeypatch):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="voice")
    store.data["model_routing"] = {"mode": "local_only"}
    assert lvr.drop_stale_question() is True
    assert "cloud-voice-scope" in p.resolved and lvr.cloud_question_card() is None
    assert lvr.drop_stale_question() is False
    store.data["model_routing"] = {"mode": "local_preferred"}
    lvr.ask_cloud_switch(source="voice")
    store.data["voice_engine"] = "gemini"                       # moved by another path
    p.resolved.clear()
    lvr.note_engine_written("gemini")
    assert "cloud-voice-scope" in p.resolved


def test_the_answer_is_read_and_applied_under_one_lock(monkeypatch):
    import inspect
    assert "with _ANSWER_LOCK:" in inspect.getsource(lvr.answer_cloud_switch)
    assert "with _ANSWER_LOCK:" in inspect.getsource(lvr.decide_switch_back)
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    lvr.ask_cloud_switch(source="settings")
    assert lvr.answer_cloud_switch("always")["ok"] is True
    assert lvr.answer_cloud_switch("call")["ok"] is False, "the question is used up: the second answer does not apply"
    assert [w.get("voice_engine") for w in store.writes if "voice_engine" in w] == ["gemini"]


def test_setup_asks_before_it_chooses_cloud_voice():
    src = (ROOT / "src" / "agent_friday" / "setup_wizard.py").read_text(encoding="utf-8")
    assert "def step_cloud_voice_scope(config" in src
    assert "step_cloud_voice_scope(config)" in src
    assert 'Prompt.ask("  [cyan]Choose (1-2)[/cyan]", choices=["1", "2"])' in src, "no default: nothing preselected"
    assert "Recommended: back to local voice" in src


def test_the_stale_descriptions_are_corrected():
    root = ROOT / "src" / "agent_friday"
    agent = (root / "services" / "agent.py").read_text(encoding="utf-8")
    assert "(never switches)" not in agent
    reg = (root / "services" / "notice_registry.py").read_text(encoding="utf-8")
    assert "maybe_offer_switch_back" in reg
    docs = (ROOT / "docs" / "user-guide" / "configuration.md").read_text(encoding="utf-8")
    assert "no local voice to go back to" not in docs and "default cloud-only mode included" in docs
    assert "when Friday restarts in the middle of one" in docs
    change = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "cloud-only, the default, included" in change


def test_both_ui_files_wire_the_failure_offer_the_ids_and_the_refusal():
    for name in ("index.html", "ui_parts/app.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        assert "m.offer.kind === 'cloud_switch'" in html and "m.offer.endpoint" in html, name
        assert "id: st.cloud_question.id" in html and "id: st.switch_back.id" in html, name
        assert "data-cloud-voice-refused" in html and "setRefusal(" in html, name


# ── 10. the re-review's findings ─────────────────────────────────────────────

def _with_tool_ctx(ctx):
    from agent_friday.services import agent
    return agent._CURRENT_TOOL_CONTEXT, agent._CURRENT_TOOL_CONTEXT.set(ctx)


@pytest.mark.parametrize("ctx,local", [
    ({"surface": "voice-live-relay", "voice_owner_local": False}, False),
    ({"surface": "voice-live-relay"}, False),
    ({"surface": "voice-local", "owner_local": False}, False),
    ({"surface": "voice-live-relay", "voice_owner_local": True}, True),
    ({"surface": "voice-local", "owner_local": True}, True),
    ({"surface": "chat"}, True),
])
def test_a_voice_call_that_is_not_this_pcs_own_cannot_ask_or_answer(monkeypatch, ctx, local):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    var, tok = _with_tool_ctx(ctx)
    try:
        out = _tool(action="cloud")
        if local:
            assert out["kind"] == "asked"
        else:
            assert out["kind"] == "not_local" and "this PC's own screen" in out["spoken"]
            assert lvr.cloud_question_card() is None
        lvr.ask_cloud_switch(source="settings")
        w = Words(monkeypatch).say("always")
        out = _tool(action="answer", choice="always")
        if local:
            assert out["kind"] == "answered"
        else:
            assert out["kind"] == "not_local" and store.engine_writes() == []
    finally:
        var.reset(tok)


def test_the_voice_sessions_know_whether_they_are_the_owners_own_call():
    vs = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    assert '"owner_local": bool(_owner_voice)' in vs and '"owner_local": _owner_local_live' in vs
    assert "_owner_local_live = bool(_is_local_request())" in vs
    ve = (ROOT / "src" / "agent_friday" / "services" / "voice_engine.py").read_text(encoding="utf-8")
    assert ve.count('"voice_owner_local": bool((session or {}).get("owner_local"))') == 2
    assert 'ctx["owner_local"] = bool(session.get("owner_local"))' in ve


def test_a_choice_made_at_setup_is_not_undone_at_first_boot(monkeypatch, env):
    store = Store(monkeypatch, engine="gemini", **{lvr.CHOICE_KEY: {
        "scope": "call", "engine": "gemini", "previous": "local", "at": T0, "reason": "your_choice",
        "source": "setup"}})
    p = Pushes(monkeypatch)
    env[0] = T0 + 60
    assert lvr.reconcile_at_boot() is False
    assert store.data["voice_engine"] == "gemini" and p.cards == [] and store.engine_writes() == []
    lvr.note_cloud_call_started()
    assert store.data[lvr.CHOICE_KEY]["call_seen"] is True
    assert lvr.reconcile_at_boot() is True and store.data["voice_engine"] == "local"


def test_an_expired_unused_call_choice_goes_back_at_boot_with_an_honest_message(monkeypatch, env):
    store = Store(monkeypatch, engine="gemini", **{lvr.CHOICE_KEY: {
        "scope": "call", "engine": "gemini", "previous": "local", "at": T0, "source": "setup"}})
    p = Pushes(monkeypatch)
    env[0] = T0 + lvr.CALL_SCOPE_TTL_S + 5
    assert lvr.reconcile_at_boot() is True
    body = p.of("LV9")[0]["body"]
    assert "No cloud call was started" in body and "restarted" not in body


def test_the_live_session_tells_the_record_a_call_used_it():
    vs = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    assert "_lvr_start.note_cloud_call_started()" in vs


def test_a_non_local_owner_is_told_where_to_choose(monkeypatch):
    store = Store(monkeypatch)
    gate = lvr.gate_settings_write({"voice_engine": "gemini"})
    assert "this PC's own screen" in gate["body"]["message"]
    import agent_friday.routes.voice as rv
    assert "this PC's own screen" in rv._NOT_THE_PAGE[0]["message"] and rv._NOT_THE_PAGE[1] == 403
    for name in ("index.html", "ui_parts/app.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        assert "d.status === 'refused' || d.status === 'error'" in html, name


def test_the_cloud_calls_last_words_are_cleared_at_the_end_of_each_turn():
    vs = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    flush = vs[vs.index("def _flush_turn(conversation_id=None):"):]
    flush = flush[:flush.index("user_text = ''.join(in_buf).strip()") + 2000]
    assert '_voice_session["owner_text"] = ""' in flush and '_voice_session["owner_text_at"] = 0.0' in flush


def test_the_lv7_registry_site_names_both_offers():
    from agent_friday.services import notice_registry as reg
    assert "maybe_offer_switch_back" in reg.get("LV7").where
