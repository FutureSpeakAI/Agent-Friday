"""Voice remembers earlier conversations, and recalls them by provenance.

Earlier voice calls were stored but not searchable from voice, a new call
started with a locally written summary of every conversation (local ones
included) sent to Gemini, and nothing recorded which provider had heard a
turn. Now turns record provenance; voice can search past conversations with
dates; what an earlier Gemini call already heard is recalled directly, and
everything else only through the local model and the payload card. Older
turns count as heard by Gemini only when the egress audit log shows a Gemini
Live session open at the time. Off-record turns are never recalled, and an
off-record call is never distilled.
"""
import inspect
import json
import time

import pytest

import agent_friday.routes.voice as rv
import agent_friday.services.voice_engine as ve
from agent_friday.services import conversation_provenance as prov
from agent_friday.services import conversation_recall as rc
from agent_friday.services import conversations as cv

FACT = "The boat trip is booked for the eleventh, leaving from the north marina at nine."


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    log = tmp_path / "egress-log.jsonl"
    log.write_text("", encoding="utf-8")
    monkeypatch.setattr(prov, "_egress_log_path", lambda: log)
    prov._CACHE.update(key=None, windows=[])
    monkeypatch.setattr(ve, "_index_chat_turn", lambda *a, **k: None)
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: None)
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: None)
    settings = {"off_record": False}
    monkeypatch.setattr(ve, "_load_settings", lambda: settings)
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "_settings", lambda: settings)
    return {"log": log, "settings": settings}


def _new_conv(title):
    return cv.create(title)["id"]


def _session(cid):
    return {"conversation_id": cid, "owner_text": ""}


def test_a_voice_turn_records_who_heard_it(store):
    cid = _new_conv("Weekend plans")
    ve._persist_voice_turn("where does the boat trip leave from?", FACT,
                           conversation_id=cid, provider="google-gemini")
    msgs = cv.messages(cid)
    assert msgs and all(m["meta"]["provider"] == prov.GEMINI for m in msgs)
    assert all(m["meta"]["sent_to"] == [prov.GEMINI] for m in msgs)
    assert all(m["meta"]["off_record"] is False and m["meta"]["via"] == "voice" for m in msgs)


def test_a_fact_from_an_earlier_call_is_found_in_a_new_one(store, monkeypatch):
    old = _new_conv("Weekend plans")
    ve._persist_voice_turn("where does the boat trip leave from?", FACT,
                           conversation_id=old, provider="google-gemini")
    started = []
    monkeypatch.setattr(ve, "_start_local_share", lambda *a, **k: started.append(a) or None)
    out = ve._tool_search_past_conversations({"query": "boat trip marina"},
                                             session=_session(_new_conv("Today")))
    assert "From earlier calls with you" in out and "north marina" in out
    assert time.strftime("%Y-%m-%d") in out, "results carry their dates"
    assert started == [], "nothing needed the card"


def test_a_local_only_fact_goes_through_the_card(store, monkeypatch):
    local = _new_conv("Planning notes")
    ve._persist_voice_turn("remind me about the boat trip", FACT,
                           conversation_id=local, provider="local")
    started = []
    monkeypatch.setattr(ve, "_voice_local_only", lambda: False)
    monkeypatch.setattr(ve, "_start_local_share",
                        lambda q, cid, answer_fn=None: started.append((q, cid)) or "appr_x")
    now = _new_conv("Today")
    out = ve._tool_search_past_conversations({"query": "boat trip"}, session=_session(now))
    assert "north marina" not in out, "local-only content was returned directly"
    assert "stayed on his PC" in out and "appr_x" in out
    assert started and started[0][1] == now


def test_older_voice_turns_count_as_gemini_only_inside_a_logged_session(store):
    t0 = time.time() - 3600
    store["log"].write_text("\n".join(json.dumps(e) for e in (
        {"ts": t0, "provider": "google-gemini", "field": "mic_audio", "reason": "live voice session opened"},
        {"ts": t0 + 600, "provider": "google-gemini", "field": "mic_audio", "reason": "live voice session closed"},
    )) + "\n", encoding="utf-8")
    windows = prov.gemini_windows()
    inside = {"ts": t0 + 300, "meta": {"kind": "turn", "via": "voice"}}
    outside = {"ts": t0 + 5000, "meta": {"kind": "turn", "via": "voice"}}
    chat = {"ts": t0 + 300, "meta": {"kind": "turn"}}
    assert prov.sent_to(inside, windows) == {prov.GEMINI}
    assert prov.sent_to(outside, windows) == set()
    assert prov.sent_to(chat, windows) == set(), "a chat turn is not inferred from voice sessions"


def test_off_record_turns_are_never_recalled(store, monkeypatch):
    store["settings"]["off_record"] = True
    cid = _new_conv("Private")
    ve._persist_voice_turn("the boat trip", FACT, conversation_id=cid, provider="google-gemini")
    assert cv.messages(cid) and all(m["meta"]["off_record"] for m in cv.messages(cid))
    assert rc.search("boat trip marina") == []
    assert "north marina" not in rc.recent_voice_pin()


def test_off_record_keeps_the_turn_in_memory_only(store, tmp_path):
    store["settings"].update(off_record=True)
    cid = _new_conv("Private")
    ve._persist_voice_turn("the boat trip", FACT, conversation_id=cid, provider="google-gemini")
    assert [m["text"] for m in cv.messages(cid)][-1] == FACT
    assert "north marina" not in "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "conversations").rglob("*") if p.is_file())


def test_an_off_record_call_is_never_distilled(store, monkeypatch):
    calls = []
    monkeypatch.setattr(ve, "_spawn_voice_distill_unchecked", lambda log: calls.append(log))
    store["settings"]["off_record"] = True
    ve._spawn_voice_distill([("u", "a")])
    assert calls == []
    store["settings"]["off_record"] = False
    ve._spawn_voice_distill([("u", "a")])
    assert calls == [[("u", "a")]]


def test_the_session_pin_holds_only_what_gemini_heard(store):
    g = _new_conv("Call about the trip")
    ve._persist_voice_turn("book the boat trip for the eleventh", FACT,
                           conversation_id=g, provider="google-gemini")
    loc = _new_conv("Local chat")
    ve._persist_voice_turn("my private budget numbers", "Noted.", conversation_id=loc,
                           provider="local")
    pin = rc.recent_voice_pin()
    assert "book the boat trip" in pin
    assert "private budget" not in pin
    src = inspect.getsource(rv).replace("\r\n", "\n")
    assert src.count("_build_session_continuity_block()") == 1, \
        "only the local voice prompt may carry the local daily summary"
    assert "full_ctx += \"\\n\" + recent_voice_pin()" in src


def test_a_chat_turn_records_the_provider_that_answered(store, monkeypatch):
    from agent_friday.services import attribution
    import agent_friday.routes.chat as chat
    monkeypatch.setattr(chat, "_load_settings", lambda: {"off_record": False})
    attribution.record_generation("some-model", provider="anthropic", seat="cloud")
    cid = _new_conv("Chat")
    chat._persist_turn(cid, {"id": "u1", "text": "hello"}, {"id": "f1", "text": "hi"},
                       meta={"model": "some-model"})
    msgs = cv.messages(cid)
    assert [m["meta"]["sent_to"] for m in msgs] == [["anthropic"], ["anthropic"]]
    assert msgs[1]["meta"]["model"] == "some-model"
