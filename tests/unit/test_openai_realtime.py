"""OpenAI Realtime, the third voice engine: its rules, price and wire messages (S1; the TV lane's
OpenAiRules/OpenAiProtocol say the same). Pure: no socket, no key, no Flask."""
from __future__ import annotations

import importlib
import importlib.util
import json

import pytest

MOD = "agent_friday.services.openai_realtime"


class _Module:
    """The module, imported on first use, so a missing file fails the test on its assertion."""

    def __getattr__(self, name):
        assert importlib.util.find_spec(MOD) is not None, "services/openai_realtime.py does not exist"
        return getattr(importlib.import_module(MOD), name)


@pytest.fixture
def orx():
    return _Module()


def test_the_model_is_gpt_realtime_2_1_with_gpt_realtime_to_try_once(orx):
    assert orx.MODEL == "gpt-realtime-2.1" and orx.FALLBACK_MODEL == "gpt-realtime"
    assert orx.ws_url() == "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"


def test_the_key_is_in_the_header_only_and_there_is_no_beta_header(orx):
    assert orx.headers("sk-test-123") == {"Authorization": "Bearer sk-test-123"}
    assert "sk-test-123" not in orx.ws_url()


def test_the_key_comes_from_the_owners_key_store_never_the_environment(orx, monkeypatch):
    from agent_friday.services import credential_store as cs
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setattr(cs, "get_provider_key", lambda p, use=True: None)
    assert orx.api_key() is None
    monkeypatch.setattr(cs, "get_provider_key", lambda p, use=True: "sk-owner" if p == "openai" else None)
    assert orx.api_key() == "sk-owner"


def test_a_minute_costs_about_ten_cents_and_the_card_says_so(orx):
    assert orx.per_minute_line() == "about $0.10 a minute"


def test_cost_prices_audio_and_text_tokens_separately(orx):
    usage = {"input_tokens": 1000, "output_tokens": 500,
             "input_token_details": {"audio_tokens": 800, "text_tokens": 200},
             "output_token_details": {"audio_tokens": 400, "text_tokens": 100}}
    want = (800 * 32 + 200 * 4 + 400 * 64 + 100 * 24) / 1e6
    assert orx.cost_usd(usage) == pytest.approx(want)


def test_cost_counts_what_it_cannot_split_as_audio_so_a_receipt_never_understates(orx):
    assert orx.cost_usd({"input_tokens": 1000, "output_tokens": 1000}) == pytest.approx((32 + 64) * 1000 / 1e6)


def test_cost_never_raises_and_is_never_negative(orx):
    for bad in (None, "x", 5, {}, {"input_tokens": "NaN"}, {"input_tokens": -5, "output_tokens": -1}):
        assert orx.cost_usd(bad) >= 0.0


def _ok(**kw):
    return dict(dict(key="sk-x"), **kw)


def test_no_key_says_add_an_openai_key_and_is_never_silent(orx):
    r = orx.readiness(key=None)
    assert r["ok"] is False and r["reason"] == "no_key" and r["say"] == "Add an OpenAI key to use this."


def test_a_key_that_cannot_be_read_is_not_called_missing(orx):
    r = orx.readiness(key_status="present_but_unreadable")
    assert r["reason"] == "key_unreadable" and "can not be read" in r["say"]


def test_kids_mode_and_local_only_refuse_and_say_why(orx):
    assert orx.readiness(**_ok(kids=True))["reason"] == "kids"
    r = orx.readiness(**_ok(local_only=True))
    assert r["reason"] == "local_only" and "stays on this PC" in r["say"]


def test_every_refusal_has_a_sentence(orx):
    for kw in (dict(key=None), dict(key_status="present_but_unreadable"), _ok(kids=True), _ok(local_only=True),
               _ok(offline=True), _ok(blocked_until=100.0, now=50.0),
               _ok(cap_month_usd=5.0, spent_month_usd=4.95)):
        r = orx.readiness(**kw)
        assert not r["ok"] and r["say"].strip(), kw


def test_the_owner_cap_refuses_when_less_than_a_minutes_money_is_left(orx):
    assert orx.readiness(**_ok(cap_month_usd=5.0, spent_month_usd=4.85))["ok"] is True
    r = orx.readiness(**_ok(cap_month_usd=5.0, spent_month_usd=4.95))
    assert r["reason"] == "cap_reached" and "$5.00" in r["say"]
    assert orx.readiness(**_ok(cap_month_usd=None, spent_month_usd=9999.0))["ok"] is True


def test_out_of_credit_blocks_openai_for_the_hour_then_lets_it_try_again(orx):
    until = 1000.0 + orx.NO_CREDIT_BLOCK_S
    assert orx.readiness(**_ok(blocked_until=until, now=1500.0))["reason"] == "no_credit"
    assert orx.readiness(**_ok(blocked_until=until, now=until + 1))["ok"] is True


def test_failover_is_an_offer_unless_the_owner_turned_auto_on_and_it_is_an_outage(orx):
    f = orx.failover("no_credit", gemini_ready=True, local_ready=True)
    assert f == {"engine": "gemini", "automatic": False, "say": "Switch to Gemini Live for this press?"}
    f = orx.failover("no_credit", gemini_ready=True, local_ready=True, auto=True)
    assert f["engine"] == "gemini" and f["automatic"] is True and "OpenAI voice is not available" in f["say"]
    # no key / kids are the owner's own state, not an outage: never automatic
    assert orx.failover("no_key", gemini_ready=True, local_ready=True, auto=True)["automatic"] is False


def test_failover_never_reaches_a_cloud_voice_in_local_only_or_kids(orx):
    for why in ("local_only", "kids"):
        f = orx.failover(why, gemini_ready=True, local_ready=True, auto=True)
        assert f["engine"] == "local" and f["automatic"] is False


def test_failover_with_nothing_ready_says_so(orx):
    f = orx.failover("no_credit", gemini_ready=False, local_ready=False)
    assert f["engine"] is None and f["say"]


def test_which_model_answers_is_named(orx):
    assert "OpenAI Realtime (gpt-realtime-2.1)" in orx.which_answers(engine="openai")
    assert "Gemini Live" in orx.which_answers(engine="gemini", reason="no OpenAI key")


def test_session_update_sets_persona_tools_and_barge_in(orx):
    contract = [{"type": "function", "function": {"name": "search_web", "description": "d",
                                                  "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
    m = orx.session_update("You are Friday.", contract)
    s = m["session"]
    assert m["type"] == "session.update" and s["type"] == "realtime" and s["model"] == "gpt-realtime-2.1"
    assert s["instructions"] == "You are Friday." and s["output_modalities"] == ["audio"]
    td = s["audio"]["input"]["turn_detection"]
    assert td["type"] == "semantic_vad" and td["interrupt_response"] is True
    assert s["tools"] == [{"type": "function", "name": "search_web", "description": "d",
                           "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}]


def test_client_messages_have_the_documented_shapes(orx):
    assert orx.audio_append("QQ==") == {"type": "input_audio_buffer.append", "audio": "QQ=="}
    assert orx.function_output("call_1", {"ok": True}) == {"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": "call_1", "output": '{"ok": true}'}}
    assert orx.response_create() == {"type": "response.create"}
    assert orx.truncate("item_9", 1500) == {"type": "conversation.item.truncate", "item_id": "item_9",
                                            "content_index": 0, "audio_end_ms": 1500}
    assert orx.truncate("i", -5)["audio_end_ms"] == 0


def test_a_tool_call_is_read_from_response_done_with_its_cost(orx):
    ev = {"type": "response.done", "response": {"status": "completed",
          "usage": {"input_tokens": 100, "output_tokens": 50},
          "output": [{"type": "function_call", "name": "search_web", "call_id": "call_7",
                      "arguments": "{\"q\": \"rain\"}"}, {"type": "message"}]}}
    p = orx.parse(json.dumps(ev))
    assert p["kind"] == "done" and p["cancelled"] is False and p["cost"] > 0
    assert p["tool_calls"] == [{"call_id": "call_7", "name": "search_web", "args": {"q": "rain"}}]


def test_barge_in_is_read_and_a_cancelled_response_says_so(orx):
    assert orx.parse({"type": "input_audio_buffer.speech_started"}) == {"kind": "speech_started"}
    assert orx.parse({"type": "response.done", "response": {"status": "cancelled"}})["cancelled"] is True


def test_audio_and_transcript_deltas_pass_through(orx):
    assert orx.parse({"type": "response.output_audio.delta", "delta": "AAA=", "item_id": "i1"})["data"] == "AAA="
    assert orx.parse({"type": "response.output_audio_transcript.delta", "delta": "Hi"}) == {"kind": "transcript", "text": "Hi"}


def test_out_of_credit_is_not_a_rate_limit(orx):
    broke = orx.parse({"type": "error", "error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}})
    slow = orx.parse({"type": "error", "error": {"type": "rate_limit_exceeded", "message": "slow down"}})
    assert broke["no_credit"] is True and slow["no_credit"] is False
    assert orx.parse({"type": "error", "error": {"code": "model_not_found", "message": "The model does not exist"}})["unknown_model"] is True


def test_junk_events_are_ignored(orx):
    for junk in (None, 5, "not json", {"type": "session.created"}, b"{}"):
        assert orx.parse(junk) is None


def test_16k_audio_becomes_24k(orx):
    import array
    a = array.array("h", [0, 1000, 2000, 3000, 4000, 5000])
    out = array.array("h")
    out.frombytes(orx.resample_16k_to_24k(a.tobytes()))
    assert len(out) == 9 and out[0] == 0 and out[-1] <= 5000
    assert orx.resample_16k_to_24k(b"") == b""


def test_level_is_zero_for_silence_and_rises_with_loudness(orx):
    import array
    quiet = array.array("h", [0] * 100).tobytes()
    loud = array.array("h", [20000, -20000] * 50).tobytes()
    assert orx.level(quiet) == 0.0 and 0.5 < orx.level(loud) <= 1.0
