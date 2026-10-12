"""An Ollama model the daemon relays to ollama.com is a cloud call for the routing mode.

`glm-4.6:cloud` and `gpt-oss:120b-cloud` are served through the local daemon but
run in Ollama's datacenter (the egress fix made them cloud for the privacy gate).
The routing mode must treat them the same way: in `local_preferred` and
`local_only` they need a recorded intent; a plain local model never does.

The static guard (`test_every_cloud_transport_requires_intent`) finds each relay
POST; this drives the transports. Synthetic data only; no daemon, no network.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_intent as ci

CLOUD_TAGGED = ["glm-4.6:cloud", "gpt-oss:120b-cloud"]
LOCAL_MODEL = "gemma3:12b"


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
    return _set


class TestRequireReadsTheModelTag:
    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    @pytest.mark.parametrize("m", ["local_preferred", "local_only"])
    def test_a_cloud_tagged_model_needs_an_intent_in_a_local_mode(self, mode, model, m):
        mode(m)
        with pytest.raises(ci.NoCloudIntent) as ei:
            ci.require("ollama", "Ollama chat", model)
        assert ei.value.model == model and "Nothing was sent" in str(ei.value)

    @pytest.mark.parametrize("m", ["local_preferred", "local_only", "cloud_only"])
    def test_a_plain_local_model_never_needs_one(self, mode, m):
        mode(m)
        tok = ci.require("ollama", "Ollama chat", LOCAL_MODEL)
        assert tok.intent == "local"

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_cloud_only_carries_the_implicit_intent(self, mode, model):
        mode("cloud_only")
        assert ci.require("ollama", "Ollama chat", model).intent == "cloud_mode"

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_an_owner_intent_opens_local_preferred_and_a_confirmed_one_local_only(self, mode, model):
        mode("local_preferred")
        with ci.intent_scope("user_explicit", source="chat_seat"):
            assert ci.require("ollama", "Ollama chat", model).intent == "user_explicit"
        mode("local_only")
        with ci.intent_scope("user_explicit", source="chat_seat"):
            with pytest.raises(ci.NoCloudIntent):
                ci.require("ollama", "Ollama chat", model)       # not confirmed
        with ci.intent_scope("user_explicit", source="offer", confirmed=True):
            assert ci.require("ollama", "Ollama chat", model).intent == "user_explicit"


class _Recorder:
    """Stands in for the daemon's HTTP surface; records whether anything was sent."""

    def __init__(self):
        self.sent = []

    def post(self, path, body, timeout=None):
        self.sent.append((path, body))
        return {"message": {"content": "ok"}, "response": "ok", "eval_count": 1,
                "eval_duration": 1}


@pytest.fixture
def manager(monkeypatch):
    from agent_friday.routing import ollama_manager
    rec = _Recorder()
    mgr = ollama_manager.OllamaManager("http://127.0.0.1:1")
    monkeypatch.setattr(mgr, "_post", rec.post)
    # The privacy gate is covered by test_egress_gate_ollama_cloud_tag; here only the
    # routing mode is under test.
    import agent_friday.services.egress_gate as eg
    monkeypatch.setattr(eg, "gate_ollama_body", lambda body, model="", **k: body)
    # No GPU query from a unit test.
    import agent_friday.services.gpu_headroom as gh
    monkeypatch.setattr(gh, "display_at_risk", lambda *a, **k: {"at_risk": False})
    return mgr, rec


class TestOllamaManagerTransports:
    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_chat_to_a_cloud_tagged_model_sends_nothing_without_intent(self, mode, manager, model):
        mgr, rec = manager
        mode("local_preferred")
        with pytest.raises(ci.NoCloudIntent):
            mgr.chat_completion([{"role": "user", "content": "hi"}], model)
        assert rec.sent == []

    def test_chat_to_a_plain_local_model_is_untouched(self, mode, manager):
        mgr, rec = manager
        mode("local_only")
        mgr.chat_completion([{"role": "user", "content": "hi"}], LOCAL_MODEL)
        assert rec.sent and rec.sent[0][0] == "/api/chat"

    def test_an_owner_intent_lets_the_cloud_tagged_chat_through_in_local_preferred(self, mode, manager):
        mgr, rec = manager
        mode("local_preferred")
        with ci.intent_scope("user_explicit", source="chat_seat"):
            mgr.chat_completion([{"role": "user", "content": "hi"}], CLOUD_TAGGED[0])
        assert rec.sent

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_the_health_probe_does_not_spend_the_cloud_on_its_own(self, mode, manager, model):
        mgr, rec = manager
        mode("local_preferred")
        out = mgr.probe_generate(model)
        assert out["ok"] is False and rec.sent == []


class TestLocalCall:
    def test_local_call_to_a_cloud_tagged_model_returns_empty_and_sends_nothing(
            self, mode, monkeypatch):
        import requests
        from agent_friday.services import local_call
        sent = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: sent.append(a) or None)
        monkeypatch.setattr(local_call, "seat_endpoint", lambda model: None)
        mode("local_preferred")
        assert local_call.call("sys", "hi", CLOUD_TAGGED[0]) == ""
        assert sent == []


class TestTheInventoryNeverMakesAPlainModelCloud:
    """The remote-alias set holds only rows the daemon marks with a non-empty
    `remote_host` or `remote_model`; a model it runs here stays local."""

    @pytest.fixture
    def aliases(self):
        from agent_friday.services import local_only_guard as g
        before = g._reset_aliases_for_tests(None)
        yield g
        g._reset_aliases_for_tests(before)

    def test_a_plain_local_model_in_the_inventory_is_never_cloud(self, aliases, mode):
        g = aliases
        g.note_remote_aliases([
            {"name": LOCAL_MODEL, "model": LOCAL_MODEL},
            {"name": "fixture-blank:7b", "remote_host": "", "remote_model": ""},
            {"name": "fixture-space:7b", "remote_host": "  ", "remote_model": None},
            {"name": "fixture-myglm:latest", "remote_host": "https://ollama.com",
             "remote_model": "glm-4.6"},
        ])
        assert g.aliases_filled()
        for plain in (LOCAL_MODEL, "fixture-blank:7b", "fixture-space:7b"):
            assert not g.is_cloud_model_tag(plain), plain
        for m in ("local_only", "local_preferred"):
            mode(m)
            assert ci.require("ollama", "Ollama chat", LOCAL_MODEL).intent == "local"
            # A real remote alias is still a cloud call: the egress rule holds.
            assert g.is_cloud_model_tag("fixture-myglm:latest")
            with pytest.raises(ci.NoCloudIntent):
                ci.require("ollama", "Ollama chat", "fixture-myglm:latest")

    def test_a_descriptor_registered_as_ollama_that_declares_cloud_is_not_local(
            self, mode, monkeypatch):
        from types import SimpleNamespace
        from agent_friday.services import provider_registry
        public = {"name": "ollama", "type": "ollama", "classification": "cloud",
                  "base_url": "http://localhost:11434"}
        monkeypatch.setattr(provider_registry, "get_provider_registry",
                            lambda: SimpleNamespace(get_provider=lambda n: public))
        mode("local_only")
        with pytest.raises(ci.NoCloudIntent):
            ci.require("ollama", "Ollama chat", LOCAL_MODEL)


PUBLIC_DAEMON = "http://8.8.8.8:11434"       # an IP literal: judged without DNS, never dialled


class TestADaemonOffThisMachineIsCloud:
    """local_only blocks all egress: a plain model on a daemon at a public
    address leaves the machine, so the mode gate and the egress gate both
    treat it as cloud."""

    @pytest.mark.parametrize("m", ["local_only", "local_preferred"])
    def test_require_refuses_a_plain_model_on_a_public_base_url(self, mode, m):
        mode(m)
        with pytest.raises(ci.NoCloudIntent):
            ci.require("ollama", "Ollama chat", LOCAL_MODEL, base_url=PUBLIC_DAEMON)
        assert ci.require("ollama", "Ollama chat", LOCAL_MODEL,
                          base_url="http://127.0.0.1:11434").intent == "local"

    def test_require_reads_the_configured_daemon_address(self, mode, monkeypatch):
        from agent_friday.services import local_call
        monkeypatch.setattr(local_call, "ollama_url", lambda: PUBLIC_DAEMON)
        mode("local_only")
        with pytest.raises(ci.NoCloudIntent):
            ci.require("ollama", "Ollama chat", LOCAL_MODEL)

    def test_the_manager_sends_nothing_to_a_public_daemon_in_local_only(self, mode, monkeypatch):
        from agent_friday.routing import ollama_manager
        sent = []
        mgr = ollama_manager.OllamaManager(PUBLIC_DAEMON)
        monkeypatch.setattr(mgr, "_post", lambda path, body, timeout=None: sent.append(path))
        import agent_friday.services.gpu_headroom as gh
        monkeypatch.setattr(gh, "display_at_risk", lambda *a, **k: {"at_risk": False})
        mode("local_only")
        with pytest.raises(ci.NoCloudIntent):
            mgr.chat_completion([{"role": "user", "content": "hi"}], LOCAL_MODEL)
        assert sent == []

    def test_the_egress_gate_seals_a_plain_model_bound_for_a_public_daemon(self, monkeypatch):
        from agent_friday.services import egress_gate as eg
        from agent_friday.services import local_call
        sealed = []

        def seal(body, *, base_url, provider="ollama", log_path=None, model=""):
            sealed.append(base_url)
            return {"sealed": True}
        monkeypatch.setattr(eg, "gate_worker_payload", seal)
        body = {"model": LOCAL_MODEL, "prompt": "owner text"}
        assert eg.gate_ollama_body(body, LOCAL_MODEL, base_url=PUBLIC_DAEMON) == {"sealed": True}
        monkeypatch.setattr(local_call, "ollama_url", lambda: PUBLIC_DAEMON)
        assert eg.gate_ollama_body(body, LOCAL_MODEL) == {"sealed": True}
        assert sealed == [PUBLIC_DAEMON, PUBLIC_DAEMON]
        # The same plain model on this machine's daemon passes untouched.
        assert eg.gate_ollama_body(body, LOCAL_MODEL, base_url="http://localhost:11434") is body


class TestAnInventoryNeverReadIsNotLocal:
    """Every relay transport fills the alias set before its first daemon call;
    when the inventory cannot be read, a plainly named model may be an alias of
    a cloud model, so it is not local unless this PC serves it from a seat."""

    @pytest.fixture
    def unread(self, monkeypatch):
        from agent_friday.services import local_call
        from agent_friday.services import local_only_guard as g
        before = g._reset_aliases_for_tests(None)
        reads = []
        monkeypatch.setattr(local_call, "_daemon_tags", lambda: reads.append(1) or set())
        monkeypatch.setattr(local_call, "seat_endpoint", lambda model: None)
        yield reads
        g._reset_aliases_for_tests(before)

    @pytest.mark.parametrize("m", ["local_only", "local_preferred"])
    def test_an_unreadable_inventory_refuses_a_plain_model(self, mode, manager, unread, m):
        mgr, rec = manager
        mode(m)
        with pytest.raises(ci.NoCloudIntent):
            mgr.chat_completion([{"role": "user", "content": "hi"}], LOCAL_MODEL)
        assert rec.sent == [] and unread, "the inventory was asked for before refusing"

    def test_a_model_this_pc_serves_from_a_seat_is_local_in_local_preferred(
            self, mode, manager, unread, monkeypatch):
        from agent_friday.services import local_call
        monkeypatch.setattr(local_call, "seat_endpoint", lambda model: "http://127.0.0.1:8090")
        mgr, rec = manager
        mode("local_preferred")
        mgr.chat_completion([{"role": "user", "content": "hi"}], LOCAL_MODEL)
        assert rec.sent

    def test_a_read_that_fills_the_set_decides(self, mode, manager, unread, monkeypatch):
        from agent_friday.services import local_call
        from agent_friday.services import local_only_guard as g

        def read():
            g.note_remote_aliases([{"name": LOCAL_MODEL},
                                   {"name": "fixture-myglm:latest", "remote_host": "https://h"}])
            return {LOCAL_MODEL, "fixture-myglm:latest"}
        monkeypatch.setattr(local_call, "_daemon_tags", read)
        mgr, rec = manager
        mode("local_only")
        mgr.chat_completion([{"role": "user", "content": "hi"}], LOCAL_MODEL)
        assert rec.sent
        with pytest.raises(ci.NoCloudIntent):
            ci.require("ollama", "Ollama chat", "fixture-myglm:latest")

    def test_the_residency_arbiter_load_asks_too(self, mode, unread, monkeypatch):
        from agent_friday.services import residency_arbiter as ra
        posted = []
        monkeypatch.setattr(ra, "_post", lambda url, body, *a, **k: posted.append(url))
        mode("local_only")
        with pytest.raises(ci.NoCloudIntent):
            ra.OllamaBackend("http://127.0.0.1:1").load(LOCAL_MODEL, 2048)
        assert posted == [] and unread


def test_a_descriptor_named_ollama_in_any_case_is_honoured(mode, monkeypatch):
    from types import SimpleNamespace
    from agent_friday.services import provider_registry
    cloud = {"name": "Ollama", "type": "ollama", "classification": "cloud",
             "base_url": "http://localhost:11434"}
    reg = SimpleNamespace(get_provider=lambda n: cloud if n == "Ollama" else None,
                          list_providers=lambda: [cloud])
    monkeypatch.setattr(provider_registry, "get_provider_registry", lambda: reg)
    mode("local_only")
    with pytest.raises(ci.NoCloudIntent):
        ci.require("ollama", "Ollama chat", LOCAL_MODEL)
