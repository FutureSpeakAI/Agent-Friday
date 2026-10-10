"""An Ollama model the daemon relays to ollama.com is cloud, whatever the
provider name says.

`glm-4.6:cloud` and `gpt-oss:120b-cloud` are served through the local daemon
but run in Ollama's datacenter. The gate used to decide from the provider name
alone, so provider "ollama" meant "stays on the PC" and the payload left
unscrubbed. Synthetic data only.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services.egress_gate import (
    _is_cloud, gate_ollama_body, gate_text, gate_worker_payload, seal_outbound)

CLOUD_TAGGED = ["glm-4.6:cloud", "gpt-oss:120b-cloud"]
LOCAL_MODEL = "gemma3:12b"
CANARY = "my SSN is 123-45-6789 and the custody hearing is Friday"  # pragma: allowlist secret
CANARY_FRAGMENT = "123-45-6789"  # pragma: allowlist secret

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


@pytest.fixture(autouse=True)
def _gated_not_unrestricted(monkeypatch):
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    monkeypatch.setattr(eg, "gate_operational", lambda: True)


def _payload():
    return {"system": "you are a helpful assistant",
            "messages": [{"role": "user", "content": CANARY}]}


def _flat(obj) -> str:
    return repr(obj)


class TestIsCloudIsModelAware:
    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_cloud_tagged_model_is_cloud_under_ollama(self, model):
        assert _is_cloud("ollama", model) is True
        assert _is_cloud("local", model) is True

    def test_plain_local_model_stays_local(self):
        assert _is_cloud("ollama", LOCAL_MODEL) is False
        assert _is_cloud("ollama") is False

    def test_unknown_provider_still_fails_closed(self):
        assert _is_cloud("", LOCAL_MODEL) is True
        assert _is_cloud("some-new-vendor", LOCAL_MODEL) is True


class TestSealOutbound:
    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_cloud_tagged_ollama_is_scrubbed_like_a_cloud_provider(self, model, tmp_path):
        sealed = seal_outbound(_payload(), "ollama", tmp_path / "egress.log", model=model)
        assert CANARY_FRAGMENT not in _flat(sealed)
        named_cloud = seal_outbound(_payload(), "anthropic", tmp_path / "b.log")
        assert CANARY_FRAGMENT not in _flat(named_cloud)
        assert sealed["messages"] != _payload()["messages"]

    def test_plain_local_ollama_model_is_ungated(self, tmp_path):
        payload = _payload()
        assert seal_outbound(payload, "ollama", tmp_path / "e.log",
                             model=LOCAL_MODEL) is payload

    def test_no_model_given_keeps_the_old_name_only_behaviour(self, tmp_path):
        payload = _payload()
        assert seal_outbound(payload, "ollama", tmp_path / "e.log") is payload


class TestGateText:
    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_cloud_tagged_text_is_gated(self, model, tmp_path):
        out = gate_text(CANARY, "ollama", "prompt", tmp_path / "e.log", model=model)
        assert CANARY_FRAGMENT not in out

    def test_plain_local_text_passes(self, tmp_path):
        assert gate_text(CANARY, "ollama", "prompt", tmp_path / "e.log",
                         model=LOCAL_MODEL) == CANARY


class TestOllamaBodies:
    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_generate_body_prompt_is_sealed(self, model):
        body = {"model": model, "prompt": CANARY, "stream": False}
        out = gate_ollama_body(body, model)
        assert CANARY_FRAGMENT not in _flat(out)

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_worker_payload_reads_the_model_from_the_body(self, model):
        body = {"model": model, "prompt": CANARY}
        out = gate_worker_payload(body, base_url="http://localhost:11434",
                                  provider="ollama")
        assert CANARY_FRAGMENT not in _flat(out)

    def test_plain_local_body_is_untouched(self):
        body = {"model": LOCAL_MODEL, "prompt": CANARY}
        assert gate_ollama_body(body, LOCAL_MODEL) is body
        assert gate_worker_payload(body, base_url="http://localhost:11434",
                                   provider="ollama") is body

    def test_cloud_tagged_send_refused_when_the_gate_is_not_operational(self, monkeypatch):
        monkeypatch.setattr(eg, "gate_operational", lambda: False)
        with pytest.raises(RuntimeError):
            gate_ollama_body({"model": "glm-4.6:cloud", "prompt": "hi"}, "glm-4.6:cloud")


class TestUnrestrictedCloudKeepsItsSemantics:
    def test_cloud_tagged_model_follows_what_a_cloud_provider_does(self, monkeypatch, tmp_path):
        monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
        a = seal_outbound(_payload(), "ollama", tmp_path / "a.log", model="glm-4.6:cloud")
        b = seal_outbound(_payload(), "anthropic", tmp_path / "b.log")
        assert a == b


class TestChatCompletionIsGated:
    """OllamaManager.chat_completion is where every Ollama chat turn passes."""

    def _manager(self, monkeypatch):
        from agent_friday.routing.ollama_manager import OllamaManager
        sent = []

        def fake_post(self, path, body, timeout=30):
            sent.append((path, body))
            return {"message": {"content": "ok"}}
        monkeypatch.setattr(OllamaManager, "_post", fake_post)
        monkeypatch.setattr("agent_friday.services.gpu_headroom.display_at_risk",
                            lambda *a, **k: {})
        return OllamaManager("http://localhost:11434"), sent

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_cloud_tagged_turn_reaches_the_daemon_scrubbed(self, monkeypatch, model):
        mgr, sent = self._manager(monkeypatch)
        mgr.chat_completion([{"role": "user", "content": CANARY}], model)
        assert sent and CANARY_FRAGMENT not in _flat(sent[0][1])

    def test_plain_local_turn_is_sent_as_written(self, monkeypatch):
        mgr, sent = self._manager(monkeypatch)
        mgr.chat_completion([{"role": "user", "content": CANARY}], LOCAL_MODEL)
        assert CANARY_FRAGMENT in _flat(sent[0][1])


class TestCatalogHidesBothSuffixForms:
    def test_live_ollama_models_drop_cloud_models(self, monkeypatch):
        import agent_friday.routing.ollama_manager as om
        import agent_friday.services.model_catalog as mc

        class _Mgr:
            def is_available(self):
                return True

            def list_models(self):
                return [{"name": n} for n in
                        ["gemma3:12b", "glm-4.6:cloud", "gpt-oss:120b-cloud", "llama3"]]
        monkeypatch.setattr(om, "get_manager", lambda *a, **k: _Mgr())
        assert mc._live_ollama_models_uncached("http://x") == ["gemma3:12b", "llama3"]


class TestVaultTiersAtAssembly:
    """TIER_2/3 vault sections are decided at assembly, not left to the gate."""

    def _sections(self):
        from agent_friday.privacy.vault_access import Tier
        return [(Tier.PUBLIC, "== NEWS ==\npublic-headline"),
                (Tier.PRIVATE, "== CONTACTS ==\nprivate-contact-line"),
                (Tier.SENSITIVE, "== RECORDS ==\nsensitive-record-line")]

    def _vault(self):
        from agent_friday.privacy.vault_access import VaultAccessControl
        return VaultAccessControl(enabled=False)

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_cloud_tagged_ollama_gets_the_cloud_policy(self, model):
        out = self._vault().assemble_prompt(self._sections(), "ollama", model=model)
        assert "public-headline" in out
        assert "private-contact-line" not in out
        assert "sensitive-record-line" not in out

    def test_plain_local_model_sees_everything_as_before(self):
        v = self._vault()
        for kw in ({"model": LOCAL_MODEL}, {}):
            out = v.assemble_prompt(self._sections(), "ollama", **kw)
            assert "private-contact-line" in out and "sensitive-record-line" in out

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_gate_content_and_zero_trust_check_follow_the_model(self, model):
        v = self._vault()
        assert v.can_access("ollama", model) is False
        assert v.can_access("ollama", LOCAL_MODEL) is True
        from agent_friday.privacy.vault_access import Tier
        assert v.gate_content("x", "ollama", tier=Tier.SENSITIVE, model=model) == ""
        assert v.gate_content("x", "ollama", tier=Tier.SENSITIVE, model=LOCAL_MODEL) == "x"

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_predicted_provider_for_a_cloud_tagged_local_seat_is_cloud(self, monkeypatch, model):
        import agent_friday.routing.model_router as rr
        import agent_friday.services.model_router as sm

        class _R:
            def route(self, *a, **k):
                return {"provider": "local", "model": model}
        monkeypatch.setattr(rr, "get_router", lambda cfg: _R())
        assert sm._predict_route_provider(keywords="hi", workspace="chat") == "cloud"

    def test_predicted_provider_for_a_plain_local_seat_stays_local(self, monkeypatch):
        import agent_friday.routing.model_router as rr
        import agent_friday.services.model_router as sm

        class _R:
            def route(self, *a, **k):
                return {"provider": "local", "model": LOCAL_MODEL}
        monkeypatch.setattr(rr, "get_router", lambda cfg: _R())
        assert sm._predict_route_provider(keywords="hi", workspace="chat") == "local"


class TestSpendCapHoldsForCloudTaggedModels:
    def test_cap_stops_a_cloud_tagged_ollama_call_but_not_a_local_one(self, monkeypatch):
        from agent_friday.services import spend_guard as sg
        monkeypatch.setattr(sg, "tripped",
                            lambda: {"period": "daily", "spend": 5.0, "limit": 1.0})
        monkeypatch.setattr(sg, "_record_halt", lambda *a, **k: None)
        monkeypatch.setattr(sg, "_attribution", lambda: {})
        sg.check("ollama", what="local call", model=LOCAL_MODEL)   # no raise
        sg.check("ollama", what="local call")                      # no raise
        for model in CLOUD_TAGGED:
            with pytest.raises(sg.SpendCapReached):
                sg.check("ollama", what="cloud call", model=model)

    def test_the_ollama_seam_checks_the_cap_before_sending(self, monkeypatch):
        from agent_friday.services import spend_guard as sg
        monkeypatch.setattr(sg, "tripped",
                            lambda: {"period": "daily", "spend": 5.0, "limit": 1.0})
        monkeypatch.setattr(sg, "_record_halt", lambda *a, **k: None)
        monkeypatch.setattr(sg, "_attribution", lambda: {})
        with pytest.raises(sg.SpendCapReached):
            gate_ollama_body({"model": "glm-4.6:cloud", "prompt": "hi"}, "glm-4.6:cloud")


class TestRetrievalLedgerLabelsCloudRelayedAsCloud:
    def test_destination_class(self, monkeypatch):
        import agent_friday.services.model_router as sm
        from agent_friday.services import retrieval_ledger as rl
        seen = []
        monkeypatch.setattr(rl, "record_assembly",
                            lambda sections, **k: seen.append(k["destination_class"]))
        for model, want in (("glm-4.6:cloud", "cloud"), (LOCAL_MODEL, "local")):
            sm._build_context_prompt("hello", provider="ollama", model=model)
            assert seen[-1] == want


class TestNativeOllamaImagesAreWithheldFromCloudTaggedModels:
    """No image gate exists, and the text gate never read ``images``: a
    cloud-tagged model gets none, and each drop is recorded."""

    PIXELS = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="  # synthetic

    @pytest.fixture(autouse=True)
    def _log(self, monkeypatch, tmp_path):
        self.log = tmp_path / "egress.log"
        monkeypatch.setattr(eg, "_DEFAULT_LOG", self.log)

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_message_images_never_reach_a_cloud_tagged_model(self, model):
        body = {"model": model, "stream": False, "messages": [
            {"role": "user", "content": "what is on this screen", "images": [self.PIXELS]}]}
        out = gate_ollama_body(body, model)
        assert self.PIXELS not in _flat(out)
        assert all("images" not in m for m in out["messages"])
        assert out["messages"][0]["role"] == "user"
        assert self.PIXELS in _flat(body), "the caller's own body is not edited"

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_generate_body_images_never_reach_a_cloud_tagged_model(self, model):
        out = gate_ollama_body({"model": model, "prompt": "describe", "images": [self.PIXELS]}, model)
        assert "images" not in out and self.PIXELS not in _flat(out)

    def test_the_withholding_is_recorded(self):
        gate_ollama_body({"model": "glm-4.6:cloud", "messages": [
            {"role": "user", "content": "hi", "images": [self.PIXELS]}]}, "glm-4.6:cloud")
        assert "withheld" in self.log.read_text(encoding="utf-8")

    def test_a_plain_local_model_keeps_its_images(self):
        body = {"model": LOCAL_MODEL, "messages": [
            {"role": "user", "content": "hi", "images": [self.PIXELS]}]}
        assert gate_ollama_body(body, LOCAL_MODEL) is body


class TestOpenAIShapeSeatServingACloudTaggedModelIsSealed:
    """``_call_openai`` skips the seal for a verified-local provider, unless the
    model is cloud-tagged (an LM Studio / vLLM host relaying ``glm-4.6:cloud``)."""

    LOCAL_PROVIDER = {"name": "lmstudio-test", "adapter": "openai-compatible",
                      "classification": "local", "base_url": "http://127.0.0.1:1234/v1",
                      "auth": {"type": "none"}}

    def _run(self, monkeypatch, model, content=None):
        import json as _json
        import requests
        import agent_friday.services.model_router as smr
        sent = []

        class _Resp:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            def raise_for_status(self):
                pass

            def json(self):
                return {"model": model, "choices": [{"message": {"role": "assistant", "content": "ok"},
                                                      "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

            def close(self):
                pass

        def post(url, headers=None, json=None, timeout=None, **kw):
            sent.append(_json.dumps(json))
            return _Resp()
        monkeypatch.setattr(requests, "post", post)
        smr._call_openai([{"role": "user", "content": content or CANARY}], model=model, tools=None,
                         provider=dict(self.LOCAL_PROVIDER), stream=False)
        return sent

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_cloud_tagged_model_on_a_local_openai_host_is_scrubbed(self, monkeypatch, model):
        sent = self._run(monkeypatch, model)
        assert sent and CANARY_FRAGMENT not in sent[0]

    def test_plain_local_model_on_the_same_host_is_sent_as_written(self, monkeypatch):
        sent = self._run(monkeypatch, LOCAL_MODEL)
        assert sent and CANARY_FRAGMENT in sent[0]


# ── Guard: every gate call in an Ollama path carries the model ───────────────

_OLLAMA_FILES = [
    "routing/ollama_manager.py",
    "services/local_call.py",
    "services/worker_adapters/ollama_adapter.py",
    "services/model_router.py",
]
_GATE_CALLEES = {"seal_outbound", "gate_text", "_gate_text", "gate_worker_payload",
                 "gate_ollama_body", "_seal_or_block"}


def _callee(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


@pytest.mark.parametrize("rel", _OLLAMA_FILES)
def test_every_gate_call_in_an_ollama_path_passes_the_model(rel):
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8-sig"))
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee(node) not in _GATE_CALLEES:
            continue
        name = _callee(node)
        seen += 1
        has_model = any(k.arg == "model" for k in node.keywords)
        if name == "gate_ollama_body":      # model is its second positional
            has_model = has_model or len(node.args) >= 2
        if has_model:
            continue
        # Allow-list: the Anthropic call in model_router is a cloud provider
        # by name, so there is no Ollama model to carry.
        if (rel == "services/model_router.py" and name == "_seal_or_block"
                and any(isinstance(a, ast.Constant) and a.value == "anthropic"
                        for a in node.args)):
            continue
        pytest.fail("%s:%d %s() in an Ollama path has no model=" % (rel, node.lineno, name))
    assert seen, "expected at least one gate call in %s" % rel


def test_the_ollama_seams_call_the_gate():
    for rel in ("routing/ollama_manager.py", "services/local_call.py"):
        assert "gate_ollama_body(" in (SRC / rel).read_text(encoding="utf-8-sig"), rel


class TestOpenAIStyleImagePartsAreWithheldFromCloudTaggedModels:
    """``_call_openai`` already drops a message whose content list holds anything
    but text blocks, so an image part cannot enter by that door; the payload
    seam strips it anyway, and records it, for any path that builds one."""
    PIXELS = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"  # synthetic

    def _body(self):
        return {"model": "glm-4.6:cloud", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": self.PIXELS}}]}]}

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_the_seam_strips_image_parts_and_records_it(self, monkeypatch, tmp_path, model):
        monkeypatch.setattr(eg, "_DEFAULT_LOG", tmp_path / "egress.log")
        body = self._body()
        out = eg.withhold_cloud_tagged_images(body, "lmstudio-test", model)
        assert out["messages"][0]["content"] == [{"type": "text", "text": "what is this"}]
        assert self.PIXELS in _flat(body), "the caller's body is not edited"
        assert "withheld" in (tmp_path / "egress.log").read_text(encoding="utf-8")

    def test_a_body_without_images_is_equal(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        assert eg.withhold_cloud_tagged_images(body, "p", "glm-4.6:cloud") == body

    @pytest.mark.parametrize("model", CLOUD_TAGGED)
    def test_no_image_reaches_the_wire_from_call_openai(self, monkeypatch, model):
        run = TestOpenAIShapeSeatServingACloudTaggedModelIsSealed()._run
        sent = run(monkeypatch, model, content=self._body()["messages"][0]["content"])
        assert all("iVBORw0KGgo" not in s for s in sent)
