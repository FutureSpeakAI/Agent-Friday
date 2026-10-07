"""Voice readiness describes evidence without disabling unproven tool paths."""
import io
import json
import threading

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import voice_manifest as vm
from agent_friday.services import voice_persona as vp


def _answer(seat="example-brain", *, front=False, fits=True):
    return {"seat": seat, "base": "http://127.0.0.1:1", "content": "OK",
            "contract": {"fits": fits, "tools": ["knowledge_query", "memory_recall"],
                         "knowledge_graph": not front, "memory": not front,
                         "front": front, "window": 131072}}


@pytest.fixture
def manifest(monkeypatch):
    settings = {"voice_engine": "gemini", "voice_front_model": "qwen3-1.7b"}
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(vm, "_settings", lambda: dict(settings))
    monkeypatch.setattr(vm, "_PROMPT_BUILDER", None)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mind", lambda *a: _answer())
    m = vm.VoiceManifest(clock=lambda: clock["now"])
    monkeypatch.setattr(vm, "get_manifest", lambda: m)
    monkeypatch.setattr(rv, "_voice_tool_names",
                        lambda: ["home_cards", "customize_workspace", "ask_friday"])
    return m, settings, clock


@pytest.mark.parametrize("expired", [False, True], ids=["fresh", "expired"])
@pytest.mark.parametrize("vault_open", [False, True], ids=["local-vault", "open-vault"])
def test_unproven_relay_does_not_claim_absence_or_disable_native_tools(manifest,
                                                                     expired,
                                                                     vault_open):
    m, _settings, clock = manifest
    if expired:
        assert m.prove("mind")["ready"]
        clock["now"] += vm.DEFAULT_TTL_S + 1
    reach = rv._voice_context_reach("gemini", vault_open=vault_open)
    assert not m.snapshot_stage("mind")["ready"]
    assert reach["tool_capable"] and reach["tools"] == 3
    assert not reach["full_context"] and not reach["via_local"]
    assert not reach["memory"] and not reach["knowledge_graph"]
    descriptions = [reach["line"] + " " + reach["notice"],
                    m.describe_for_model(vault_open=vault_open),
                    vp.vault_rule(vault_open, False)]
    for text in descriptions:
        assert "readiness check" in text and "ask_friday" in text
        assert "actual result" in text
        for false_absence in ("not running", "not available", "unavailable",
                              "out of reach", "no path to"):
            assert false_absence not in text.lower(), text
    if not vault_open:
        assert all("local-only" in text and "privacy gate" in text
                   for text in descriptions)
    snap = m.snapshot()
    assert not snap["contract"]["memory"] and not snap["contract"]["knowledge_graph"]
    assert "context is reached" not in snap["contract"]["line"]


def test_current_brain_proof_still_confirms_relay(manifest):
    m, _settings, _clock = manifest
    assert m.prove("mind")["ready"]
    reach = rv._voice_context_reach("gemini")
    assert reach["full_context"] and reach["via_local"]
    assert reach["notice"] == ""
    assert "privacy gate" in vp.vault_rule(False, True)


@pytest.fixture
def fake_seats(monkeypatch):
    from agent_friday.services import local_seats, tool_budget, voice_front
    calls = []
    monkeypatch.setattr(voice_front, "installed", lambda model: True)
    monkeypatch.setattr(vm, "_run_front_mind",
                        lambda model, progress: calls.append(("front", model)) or
                        _answer("voice-front:" + model, front=True))
    monkeypatch.setattr(local_seats, "resolve",
                        lambda role: calls.append(("resolve", role)) or "example-brain")
    monkeypatch.setattr(tool_budget, "_seat_base", lambda model: "http://127.0.0.1:1")
    monkeypatch.setattr(vm, "compute_contract", lambda model: {
        **_answer()["contract"], "_system": "Synthetic readiness proof."})

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def request(req, timeout=None):
        body = json.loads(req.data)
        calls.append(("completion", body["model"]))
        return Response(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mind", vm._run_mind)
    return calls


def test_gemini_proves_brain_even_when_local_voice_front_is_installed(manifest, fake_seats):
    m, _settings, _clock = manifest
    stage = m.prove("mind")
    assert stage["ready"] and stage["effective"]["cloud_relay"]
    assert stage["effective"]["model"] == "example-brain"
    assert fake_seats == [("resolve", "brain"), ("completion", "example-brain")]


def test_local_voice_keeps_its_installed_front(manifest, fake_seats):
    m, settings, _clock = manifest
    settings["voice_engine"] = "local"
    m.refresh_selection()
    stage = m.prove("mind")
    assert stage["ready"] and not stage["effective"]["cloud_relay"]
    assert fake_seats == [("front", "qwen3-1.7b")]


def test_runner_uses_captured_target_and_front(manifest, fake_seats):
    _m, settings, _clock = manifest
    settings["voice_engine"] = "local"
    selected = vm.read_selection()["mind"]
    settings.update(voice_engine="gemini", voice_front_model="qwen3-4b-instruct-2507")
    vm._run_mind(selected, None)
    assert fake_seats == [("front", "qwen3-1.7b")]


def test_local_voice_without_front_still_proves_brain(manifest, fake_seats, monkeypatch):
    from agent_friday.services import voice_front
    m, settings, _clock = manifest
    settings["voice_engine"] = "local"
    monkeypatch.setattr(voice_front, "installed", lambda model: False)
    m.refresh_selection()
    stage = m.prove("mind")
    assert stage["ready"] and not stage["effective"]["cloud_relay"]
    assert fake_seats == [("resolve", "brain"), ("completion", "example-brain")]


def test_engine_change_invalidates_mind_before_context_reach(manifest):
    m, settings, _clock = manifest
    assert m.prove("mind")["ready"]
    settings["voice_engine"] = "local"
    assert not rv._local_mind_proven()
    stage = m.snapshot_stage("mind")
    assert not stage["ready"] and stage["effective"] == {}


def test_front_change_invalidates_local_mind_proof(manifest):
    m, settings, _clock = manifest
    settings["voice_engine"] = "local"
    m.refresh_selection()
    assert m.prove("mind")["ready"]
    settings["voice_front_model"] = "qwen3-4b-instruct-2507"
    m.refresh_selection()
    assert not m.snapshot_stage("mind")["ready"]
    assert m.snapshot_stage("mind")["effective"] == {}


@pytest.mark.parametrize("outcome", ["success", "refusal", "error", "oversized"])
@pytest.mark.parametrize("change_back", [False, True], ids=["new-target", "changed-back"])
def test_old_mind_proof_cannot_publish_after_selection_change(manifest, monkeypatch,
                                                            outcome, change_back):
    m, settings, _clock = manifest
    started = threading.Event()
    release = threading.Event()
    errors = []

    def delayed(selected, progress):
        started.set()
        if not release.wait(3):
            raise RuntimeError("synthetic proof was not released")
        progress("asking the old target")
        if outcome == "refusal":
            raise vm.ProofRefused("example_refused", "Old target refused.")
        if outcome == "error":
            raise RuntimeError("old target failed")
        return _answer(fits=outcome != "oversized")

    def prove():
        try:
            m.prove("mind")
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mind", delayed)
    worker = threading.Thread(target=prove)
    worker.start()
    try:
        assert started.wait(3)
        settings["voice_engine"] = "local"
        m.refresh_selection()
        if change_back:
            settings["voice_engine"] = "gemini"
            m.refresh_selection()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors
    stage = m.snapshot_stage("mind")
    assert stage["proof"]["state"] == "unproven"
    assert stage["effective"] == {} and stage["progress"] == ""
    assert not stage["ready"]
