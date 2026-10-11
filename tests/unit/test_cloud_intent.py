"""services/cloud_intent: the one choke point between a request and a cloud model.

The rules (routing-modes addendum 2.1):
  * cloud_only: the intent is implicit (`cloud_mode`).
  * local_preferred / smart: the cloud needs a recorded intent.
  * local_only: only an explicit request the owner CONFIRMED for this turn, or a
    job pinned to a cloud model. It never escalates, and a remembered choice does
    not reach it.
  * a provider that runs on this machine needs no intent in any mode.
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import local_only_guard as guard


@pytest.fixture(autouse=True)
def _clean():
    ci.clear()
    ci.forget_recent()
    yield
    ci.clear()


@pytest.fixture
def mode(monkeypatch):
    def _set(name):
        monkeypatch.setattr(ci, "routing_mode", lambda: name)
        return name
    return _set


CLOUD = "anthropic"


class TestRequire:
    def test_cloud_only_is_implicit(self, mode):
        mode("cloud_only")
        tok = ci.require(CLOUD, "model call", "claude-sonnet-5")
        assert tok.intent == "cloud_mode" and tok.provider == CLOUD

    @pytest.mark.parametrize("m", ["local_preferred", "smart", "local_only"])
    def test_a_local_mode_refuses_the_cloud_with_no_intent(self, mode, m):
        mode(m)
        with pytest.raises(ci.NoCloudIntent) as ei:
            ci.require(CLOUD, "model call", "claude-sonnet-5")
        assert ei.value.mode == m and ei.value.provider == CLOUD
        # Plain words, and it says nothing was sent.
        assert "Nothing was sent" in str(ei.value)

    @pytest.mark.parametrize("intent", list(ci.EXPLICIT_INTENTS))
    def test_local_preferred_accepts_every_explicit_intent(self, mode, intent):
        mode("local_preferred")
        with ci.intent_scope(intent, source="test"):
            tok = ci.require(CLOUD, "model call")
        assert tok.intent == intent and tok.source == "test"

    def test_local_only_needs_a_confirmed_explicit_request(self, mode):
        mode("local_only")
        with ci.intent_scope("user_explicit", source="route_mode"):
            with pytest.raises(ci.NoCloudIntent):
                ci.require(CLOUD, "model call")
        with ci.intent_scope("user_explicit", source="route_mode", confirmed=True):
            assert ci.require(CLOUD, "model call").intent == "user_explicit"

    @pytest.mark.parametrize("intent", ["laya_escalation", "remembered_choice"])
    def test_local_only_never_escalates_and_ignores_a_remembered_choice(self, mode, intent):
        mode("local_only")
        with ci.intent_scope(intent, source="test", confirmed=True):
            with pytest.raises(ci.NoCloudIntent):
                ci.require(CLOUD, "model call")

    def test_a_pinned_scheduled_job_carries_scheduled_cloud(self, mode):
        mode("local_preferred")
        with guard.cloud_pinned("claude-haiku-4-5-20251001", "heartbeat"):
            assert ci.require(CLOUD, "model call").intent == "scheduled_cloud"
        mode("local_only")
        with guard.cloud_pinned("claude-haiku-4-5-20251001", "heartbeat"):
            assert ci.require(CLOUD, "model call").intent == "scheduled_cloud"

    @pytest.mark.parametrize("m", ["cloud_only", "local_preferred", "local_only"])
    def test_a_local_provider_needs_no_intent_in_any_mode(self, mode, m):
        mode(m)
        assert ci.require("arbiter-local", "model call", "bonsai2:27b").intent == "local"
        assert ci.require({"name": "my-vllm", "classification": "local",
                           "adapter": "openai-compatible",
                           "base_url": "http://127.0.0.1:8000/v1"},
                          "model call", "m").intent == "local"

    def test_a_cloud_relayed_model_tag_is_not_local(self, mode):
        mode("local_preferred")
        with pytest.raises(ci.NoCloudIntent):
            ci.require("ollama-local", "model call", "glm-4.6:cloud")

    def test_an_unreadable_mode_fails_closed(self, monkeypatch):
        import agent_friday.core as core
        monkeypatch.setattr(core, "_load_settings",
                            lambda: (_ for _ in ()).throw(RuntimeError("unreadable")))
        assert ci.routing_mode() == "local_only"
        with pytest.raises(ci.NoCloudIntent):
            ci.require(CLOUD, "model call")

    def test_a_missing_mode_is_the_factory_default(self, monkeypatch):
        import agent_friday.core as core
        monkeypatch.setattr(core, "_load_settings", lambda: {})
        assert ci.routing_mode() == "cloud_only"

    def test_a_local_only_run_makes_every_mode_local_only(self, monkeypatch):
        import agent_friday.core as core
        monkeypatch.setattr(core, "_load_settings",
                            lambda: {"model_routing": {"mode": "cloud_only"}})
        with guard.local_only("news"):
            assert ci.routing_mode() == "local_only"


class TestCarrying:
    def test_set_intent_rejects_what_the_owner_cannot_give(self):
        with pytest.raises(ValueError):
            ci.set_intent("cloud_mode")
        with pytest.raises(ValueError):
            ci.set_intent("because_i_said_so")

    def test_the_scope_ends_with_the_block(self):
        assert ci.current() is None
        with ci.intent_scope("user_explicit", source="x"):
            assert ci.current().intent == "user_explicit"
        assert ci.current() is None

    def test_a_turn_scope_drops_what_the_turn_set(self):
        @ci.per_turn
        def turn():
            ci.set_intent("user_explicit", source="chat_seat")
            return ci.current().intent
        assert turn() == "user_explicit"
        assert ci.current() is None

    def test_a_turn_starts_with_no_intent_even_inside_an_outer_one(self):
        seen = []

        @ci.per_turn
        def turn():
            seen.append(ci.current())
        with ci.intent_scope("user_explicit", source="outer"):
            turn()
            assert ci.current() is not None
        assert seen == [None]

    def test_a_thread_does_not_inherit_an_intent_unless_it_is_carried(self):
        out = {}

        def worker(key):
            out[key] = ci.current()
        with ci.intent_scope("user_explicit", source="x"):
            t1 = threading.Thread(target=worker, args=("plain",))
            t2 = threading.Thread(target=ci.carry(lambda: worker("carried")))
            for t in (t1, t2):
                t.start()
                t.join()
        assert out["plain"] is None
        assert out["carried"] is not None and out["carried"].intent == "user_explicit"


class TestReceipts:
    def test_a_call_leaves_a_receipt_with_the_intent(self, mode):
        mode("local_preferred")
        with ci.intent_scope("laya_escalation", source="card"):
            ci.require(CLOUD, "model call", "claude-sonnet-5")
        row = ci.recent(1)[0]
        assert row["intent"] == "laya_escalation" and row["source"] == "card"
        assert row["provider"] == CLOUD and row["model"] == "claude-sonnet-5"
        assert row["mode"] == "local_preferred"

    def test_the_turn_attribution_carries_the_last_intent(self, mode):
        from agent_friday.services import attribution
        mode("cloud_only")
        attribution.reset()
        ci.require(CLOUD, "model call")
        assert attribution.cloud_intents()[-1]["intent"] == "cloud_mode"

    def test_a_refusal_leaves_no_receipt_of_a_send(self, mode):
        mode("local_preferred")
        with pytest.raises(ci.NoCloudIntent):
            ci.require(CLOUD, "model call")
        assert ci.recent() == []

    def test_a_local_call_leaves_no_cloud_receipt(self, mode):
        mode("local_preferred")
        ci.require("arbiter-local", "model call", "bonsai2:27b")
        assert ci.recent() == []

    def test_the_receipt_file_is_append_only_and_closes_under_a_new_name(self, mode, monkeypatch, tmp_path):
        from agent_friday import paths
        monkeypatch.setattr(paths, "runtime_dir", lambda: tmp_path)
        monkeypatch.setattr(ci, "_RECEIPT_FILE_MAX", 200)
        mode("cloud_only")
        for _ in range(12):
            ci.require(CLOUD, "model call", "claude-sonnet-5")
        files = sorted(tmp_path.glob("cloud_intents*.jsonl"))
        assert len(files) >= 2, "a full receipt file is closed under its own name"
        rows = [line for f in files for line in f.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 12, "no receipt was erased by a rotation"

    def test_the_receipt_holds_no_message_text(self, mode):
        mode("cloud_only")
        ci.require(CLOUD, "model call", "claude-sonnet-5")
        assert set(ci.recent(1)[0]) == {"intent", "provider", "purpose", "model",
                                        "mode", "source", "at"}


class TestOwnerRequestsAndEngines:
    def test_an_owner_request_tool_runs_under_user_explicit_inside_an_owner_turn(self, mode):
        mode("local_preferred")

        @ci.owner_request("creative_image")
        def tool():
            return ci.require("google-gemini", "image").intent

        with ci.turn_scope():
            assert tool() == "user_explicit"
        with pytest.raises(ci.NoCloudIntent):
            tool()                       # no owner turn (a background job)

    def test_an_owner_request_is_still_refused_by_local_only_until_confirmed(self, mode):
        mode("local_only")

        @ci.owner_request("creative_video")
        def tool():
            return ci.require("google-gemini", "video").intent
        with ci.turn_scope(), pytest.raises(ci.NoCloudIntent):
            tool()

    def test_preserve_scope_drops_an_intent_set_inside(self):
        with ci.preserve_scope():
            ci.set_intent("remembered_choice", source="task_override")
            assert ci.current() is not None
        assert ci.current() is None

    def test_a_voice_engine_leaves_a_user_explicit_receipt(self, mode):
        mode("local_preferred")
        ci.record_owner_engine("google-gemini", "speech synthesis", "gemini-tts")
        row = ci.recent(1)[0]
        assert row["intent"] == "user_explicit" and row["source"] == "voice_engine"
