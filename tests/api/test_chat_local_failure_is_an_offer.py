"""Chat routes: a local failure is an OFFER, never a cloud call.

Routing modes (addendum 2.1 rules 2-4, 2.3 F3/F6). In `local_preferred` and
`local_only` the cloud is reached only with a recorded intent. When the local
model errors, times out, or there is none, the turn answers with three choices
(try again locally, a shorter version, send to Claude), nothing preselected, and
NOTHING is sent. The owner's "send to Claude" arrives as `route_mode=cloud` and is
recorded as the `user_explicit` intent; under local_only it needs the confirm that
names the mode.

The cloud edge is the offline double's Anthropic client and any non-loopback HTTP
call (tests/api/conftest.py); `_cloud_traffic` is what reached it.
"""
from __future__ import annotations

import copy
import json

import pytest

from agent_friday import core
from agent_friday.routing.model_router import ModelRouter
from agent_friday.services import cloud_intent
from agent_friday.services.prompt_cache import VOLATILE_MARKER

ASK = "plan my week around the dentist on Thursday"
LOCAL = "bonsai2:27b"


def _fake_context(message, *a, **k):
    return ("== PERSONA ==\nI am Friday.\n" + VOLATILE_MARKER + "\nNow: 12:00\n", [])


def _loopback(url):
    return any(h in str(url) for h in ("127.0.0.1", "localhost", "[::1]"))


def _cloud_traffic(calls):
    out = list(calls["anthropic"])
    out += [r for r in calls["requests"] if not _loopback(r.get("url"))]
    return out


@pytest.fixture
def modes(monkeypatch, patch_app):
    """Put the app in a mode with a local seat up or absent."""
    def _set(mode, local=(LOCAL,)):
        settings = copy.deepcopy(core.DEFAULT_SETTINGS)
        settings["model_routing"].update({"mode": mode, "vault_local_only": False})
        patch_app("_load_settings", lambda: settings)
        patch_app("_build_memory_context_block", lambda *a, **k: "")
        patch_app("_build_session_continuity_block", lambda *a, **k: "")
        patch_app("_build_emotional_tone_block", lambda *a, **k: "")
        patch_app("_build_context_prompt", _fake_context)
        patch_app("_index_chat_turn", lambda *a, **k: None)
        monkeypatch.setattr(ModelRouter, "_local_candidates",
                            lambda self: [{"name": n, "size_gb": 16.0} for n in local])
        monkeypatch.setattr(ModelRouter, "_pick_local_model",
                            lambda self, models, tt, m, preferred=None: models[0]["name"])
        monkeypatch.setattr(ModelRouter, "_is_registry_local",
                            lambda self, m: ":" in str(m or "") and "claude" not in str(m))
        from agent_friday.routing import model_router as rr
        monkeypatch.setattr(rr, "_router_instance", None)
        return settings
    return _set


def _local_fails(patch_app, message="HTTP 503 Loading model"):
    seen = {"n": 0}

    def boom(*a, **k):
        seen["n"] += 1
        raise RuntimeError(message)
    patch_app("_call_ollama", boom)
    return seen


def _local_answers(patch_app, text="local answer"):
    patch_app("_call_ollama", lambda *a, **k: (text, []))


def _post(client, text=ASK, path="/api/chat", **extra):
    from agent_friday.services import conversations as conv
    extra.setdefault("conversation_id", conv.create()["id"])
    r = client.post(path, json={"message": text, **extra})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r.get_json()


def _ids(offer):
    return [c["id"] for c in offer["choices"]]


@pytest.mark.parametrize("mode", ["local_preferred", "local_only"])
class TestALocalFailureIsAnOffer:
    def test_chat(self, client, modes, patch_app, offline_calls, mode):
        modes(mode)
        seen = _local_fails(patch_app)
        d = _post(client)
        assert seen["n"] >= 1
        offer = d["local_fallback"]
        assert offer["offer"] is True
        assert _ids(offer) == ["retry_local", "shorter", "send_claude"]
        assert [c["id"] for c in offer["choices"] if c["recommended"]] == ["retry_local"]
        assert all(not c.get("selected") for c in offer["choices"])
        assert "Nothing was sent to a cloud model" in d["response"] or \
            "did not send this to a cloud model" in d["response"]
        # Truthful: the local model named did NOT answer, and nothing else did.
        assert d["served_by"]["answered"] is False and d["served_by"]["local"] is True
        assert _cloud_traffic(offline_calls) == []

    def test_chat_send(self, client, modes, patch_app, offline_calls, mode):
        modes(mode)
        seen = _local_fails(patch_app)
        d = _post(client, path="/api/chat/send")
        assert seen["n"] >= 1
        assert _ids(d["local_fallback"]) == ["retry_local", "shorter", "send_claude"]
        assert d["served_by"]["answered"] is False
        assert _cloud_traffic(offline_calls) == []

    def test_stream(self, client, modes, patch_app, offline_calls, mode):
        modes(mode)
        _local_fails(patch_app)
        from agent_friday.services import conversations as conv
        r = client.post("/api/chat/stream", json={"message": ASK,
                                                  "conversation_id": conv.create()["id"]})
        assert r.status_code == 200
        done = None
        for block in r.get_data(as_text=True).split("\n\n"):
            if block.startswith("data: ") and '"done"' in block:
                done = json.loads(block[len("data: "):])
        assert done and done["done"] is True
        assert _ids(done["payload"]["local_fallback"]) == ["retry_local", "shorter", "send_claude"]
        assert _cloud_traffic(offline_calls) == []

    def test_no_local_model_at_all(self, client, modes, patch_app, offline_calls, mode):
        modes(mode, local=())
        seen = _local_fails(patch_app)
        d = _post(client)
        assert seen["n"] == 0                       # nothing local to try
        assert d["local_fallback"]["kind"] in ("no_local_model", "local_only_confirm")
        assert _ids(d["local_fallback"])[0] == "retry_local" or mode == "local_only"
        assert _cloud_traffic(offline_calls) == []

    def test_a_router_that_raises_fails_closed(self, client, modes, patch_app,
                                               offline_calls, monkeypatch, mode):
        modes(mode)
        _local_answers(patch_app)

        def boom(self, *a, **k):
            raise RuntimeError("router exploded")
        monkeypatch.setattr(ModelRouter, "route", boom)
        d = _post(client)
        assert d["local_fallback"]["offer"] is True
        assert _cloud_traffic(offline_calls) == []


class TestSendToClaude:
    def test_local_preferred_records_a_user_explicit_intent(self, client, modes, patch_app,
                                                            offline_calls):
        modes("local_preferred")
        _local_fails(patch_app)
        d = _post(client, route_mode="cloud")
        assert len(offline_calls["anthropic"]) >= 1
        assert d["cloud_intent"]["intent"] == "user_explicit"
        assert d["cloud_intent"]["source"] == "route_mode"
        assert d.get("local_fallback") is None

    def test_local_only_asks_for_a_confirm_that_names_the_mode(self, client, modes, patch_app,
                                                               offline_calls):
        modes("local_only")
        _local_answers(patch_app)
        d = _post(client, route_mode="cloud")
        assert d["local_fallback"]["kind"] == "local_only_confirm"
        assert "local-only mode" in d["response"]
        assert _ids(d["local_fallback"]) == ["send_claude", "retry_local"]
        assert _cloud_traffic(offline_calls) == []

    def test_local_only_sends_one_turn_after_the_confirm(self, client, modes, patch_app,
                                                         offline_calls):
        modes("local_only")
        _local_answers(patch_app)
        d = _post(client, route_mode="cloud", confirm_local_only=True)
        assert len(offline_calls["anthropic"]) >= 1
        assert d["cloud_intent"]["intent"] == "user_explicit"

    def test_the_offer_is_gone_from_the_next_turn(self, client, modes, patch_app, offline_calls):
        modes("local_preferred")
        _local_answers(patch_app, "fine, locally")
        d = _post(client)
        assert d["response"] == "fine, locally" or "fine, locally" in d["response"]
        assert d.get("local_fallback") is None
        assert _cloud_traffic(offline_calls) == []


class TestTheOwnersBindingIsTheIntent:
    def test_a_cloud_bound_chat_in_local_preferred_goes_to_the_cloud(self, client, modes,
                                                                     patch_app, offline_calls,
                                                                     monkeypatch):
        modes("local_preferred")
        _local_answers(patch_app)
        from agent_friday.services import conversations as conv
        monkeypatch.setattr(conv, "effective_seat", lambda cid: {"model": "claude-sonnet-5"})
        d = _post(client)
        assert len(offline_calls["anthropic"]) >= 1
        assert d["cloud_intent"]["intent"] == "user_explicit"
        assert d["cloud_intent"]["source"] == "chat_seat"

    def test_the_intent_does_not_outlive_the_turn(self, client, modes, patch_app, offline_calls,
                                                  monkeypatch):
        modes("local_preferred")
        _local_answers(patch_app)
        from agent_friday.services import conversations as conv
        monkeypatch.setattr(conv, "effective_seat", lambda cid: {"model": "claude-sonnet-5"})
        _post(client)
        assert cloud_intent.current() is None
        monkeypatch.setattr(conv, "effective_seat", lambda cid: None)
        offline_calls["anthropic"].clear()
        _post(client)                                    # a plain local turn next
        assert offline_calls["anthropic"] == []

    def test_a_cloud_binding_is_not_read_by_local_only(self, client, modes, patch_app,
                                                       offline_calls, monkeypatch):
        modes("local_only")
        _local_answers(patch_app)
        from agent_friday.services import conversations as conv
        monkeypatch.setattr(conv, "effective_seat", lambda cid: {"model": "claude-sonnet-5"})
        _post(client)
        assert _cloud_traffic(offline_calls) == []


class TestCloudOnlyIsUnchanged:
    def test_the_cloud_is_the_answer_and_the_receipt_says_so(self, client, modes, patch_app,
                                                             offline_calls):
        modes("cloud_only")
        seen = _local_fails(patch_app)
        d = _post(client)
        assert seen["n"] == 0
        assert len(offline_calls["anthropic"]) >= 1
        assert d["cloud_intent"]["intent"] == "cloud_mode"
        assert d.get("local_fallback") is None
