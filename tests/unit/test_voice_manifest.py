"""The Voice Manifest (clean-sheet §3.1): readiness is proof, not presence.

No engine is loaded here. Every test swaps ``ENGINE_RUNNERS`` -- the functions
that actually run an engine -- and asserts on what the manifest is willing to
say afterwards. There is no setter for ``proof.state``; if these tests can make
a stage read ``proven`` without a runner returning evidence, the design is
broken.
"""
import time

import pytest

from agent_friday.services import voice_manifest as vm


def _boom(*a, **k):
    raise RuntimeError("engine did not run")


@pytest.fixture
def clock():
    t = {"now": 1_000_000.0}
    return t


@pytest.fixture
def manifest(monkeypatch, clock):
    monkeypatch.setattr(vm, "_settings", lambda: {"voice_engine": "local",
                                                   "local_voice_tts_engine": "kokoro"})
    monkeypatch.setattr(vm, "_PROMPT_BUILDER", None)
    m = vm.VoiceManifest(clock=lambda: clock["now"])
    return m


def _good_runners(monkeypatch, *, ear_text="Friday, what time is it right now?",
                  mouth_pcm=b"\x00\x01" * 24000, mouth_engine="kokoro",
                  mouth_device="cuda", contract=None):
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "ear",
                        lambda sel, pcm, prog: (ear_text, {"engine": "faster-whisper",
                                                           "device": "cpu",
                                                           "model": f"{sel.get('model')} int8"}))
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mouth",
                        lambda sel, text, prog: (mouth_pcm, {"engine": mouth_engine,
                                                             "device": mouth_device,
                                                             "voice": sel.get("voice")}))
    c = contract or {"tools": ["knowledge_query", "search_wiki", "read_wiki",
                               "search_web", "memory_recall"],
                     "floor_present": True, "knowledge_graph": True,
                     "memory": True, "window": 131072, "prompt_tokens": 26410,
                     "tool_tokens": 12438, "loop_reserve": 6144,
                     "reply_cap": 300, "fits": True, "note": "", "reason": ""}
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mind",
                        lambda sel, prog: {"seat": "gemma4:e2b-fridayweaver-1.0",
                                           "base": "http://127.0.0.1:8095",
                                           "contract": dict(c),
                                           "timings": {"prompt_n": 412},
                                           "content": "OK"})


# ── §3.1 rule 5 ───────────────────────────────────────────────────────────────

def test_manifest_cannot_be_proven_without_running(manifest, monkeypatch):
    for k in vm.STAGES:
        monkeypatch.setitem(vm.ENGINE_RUNNERS, k, _boom)
    snap = manifest.prove_all()
    assert snap["ready"] is False
    for k in vm.STAGES:
        assert snap["stages"][k]["proof"]["state"] == "refused", k
        assert snap["stages"][k]["ready"] is False
        assert snap["stages"][k]["effective"] == {}
    d = manifest.describe_for_model()
    assert "NOT been proven" in d
    assert "Do not describe it as running" in d
    for name in ("Kokoro", "Piper", "faster-whisper", "fridayweaver"):
        assert name.lower() not in d.lower()


def test_proof_is_set_only_by_evidence(manifest, monkeypatch):
    _good_runners(monkeypatch)
    snap = manifest.prove_all()
    assert snap["ready"] is True
    for k in vm.STAGES:
        p = snap["stages"][k]["proof"]
        assert p["state"] == "proven"
        assert isinstance(p["latency_ms"], int)
        assert p["sample"]
    d = manifest.describe_for_model()
    assert "faster-whisper small int8 on the CPU" in d
    assert "Kokoro (af_heart) on the GPU" in d
    assert "fridayweaver" in d.lower()
    assert "nothing leaves it" in d


def test_a_module_that_imports_is_not_proof(manifest, monkeypatch):
    """Importability feeds `effective`, never `proof`: a runner that loads and
    then returns nothing usable leaves the stage refused."""
    _good_runners(monkeypatch, mouth_pcm=b"")
    manifest.prove("mouth")
    st = manifest.snapshot_stage("mouth")
    assert st["proof"]["state"] == "refused"
    assert "not speech" in st["reason"]


def test_ear_proof_requires_the_expected_words(manifest, monkeypatch):
    _good_runners(monkeypatch, ear_text="hello world")
    manifest.prove("ear")
    st = manifest.snapshot_stage("ear")
    assert st["proof"]["state"] == "refused"
    assert "should have heard" in st["reason"]
    assert st["action"]["kind"] == "retry"


def test_describe_for_model_never_names_unproven_engine(manifest, monkeypatch):
    _good_runners(monkeypatch)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mouth", _boom)
    manifest.prove_all()
    d = manifest.describe_for_model()
    assert "faster-whisper" in d                # proven, named
    assert "voice (refused" in d                 # unproven, said so
    assert "kokoro" not in d.lower()
    assert "piper" not in d.lower()
    assert manifest.snapshot()["ready"] is False


# ── §3.1 rule 2: proofs expire ────────────────────────────────────────────────

def test_proofs_expire_after_ttl(manifest, monkeypatch, clock):
    _good_runners(monkeypatch)
    manifest.prove_all()
    assert manifest.snapshot()["ready"] is True
    clock["now"] += vm.DEFAULT_TTL_S + 60
    snap = manifest.snapshot()
    assert snap["ready"] is False
    ear = snap["stages"]["ear"]
    assert ear["proof"]["state"] == "unproven"
    assert ear["proof"]["stale"] is True
    assert "last proven 16 min ago" in ear["reason"]
    assert "NOT been proven" in manifest.describe_for_model()


def test_selection_change_invalidates_a_proof(manifest, monkeypatch):
    _good_runners(monkeypatch)
    manifest.prove_all()
    manifest.refresh_selection({"voice_engine": "local",
                                "local_voice_tts_engine": "piper"})
    st = manifest.snapshot_stage("mouth")
    assert st["proof"]["state"] == "unproven"
    assert "selection changed" in st["reason"]
    assert manifest.snapshot_stage("ear")["proof"]["state"] == "proven"


# ── effective != selected is said in words ───────────────────────────────────

def test_effective_differs_from_selected_is_explained(manifest, monkeypatch):
    _good_runners(monkeypatch, mouth_engine="piper", mouth_device="cpu")
    manifest.prove("mouth")
    st = manifest.snapshot_stage("mouth")
    assert st["proof"]["state"] == "proven"
    assert st["reason"] == "you chose kokoro; serving piper"


def test_gpu_required_refuses_a_cpu_engine(monkeypatch, clock):
    monkeypatch.setattr(vm, "_settings", lambda: {"local_voice_tts_engine": "kokoro",
                                                   "voice_mouth_gpu": "required"})
    m = vm.VoiceManifest(clock=lambda: clock["now"])
    _good_runners(monkeypatch, mouth_device="cpu")
    m.prove("mouth")
    st = m.snapshot_stage("mouth")
    assert st["proof"]["state"] == "refused"
    assert st["proof"]["code"] == "local_voice_gpu_refused"


# ── §3.3: the contract ───────────────────────────────────────────────────────

def test_mind_refuses_when_the_contract_does_not_fit(manifest, monkeypatch):
    bad = {"tools": [], "floor_present": False, "knowledge_graph": False,
           "memory": False, "window": 32768, "prompt_tokens": 26410,
           "tool_tokens": 0, "loop_reserve": 6144, "reply_cap": 300,
           "fits": False, "note": "",
           "reason": "This seat holds 32,768 tokens; the voice prompt needs 26,410."}
    _good_runners(monkeypatch, contract=bad)
    manifest.prove("mind")
    st = manifest.snapshot_stage("mind")
    assert st["proof"]["state"] == "refused"
    assert st["proof"]["code"] == "voice_contract_does_not_fit"
    assert "32,768" in st["reason"]
    assert st["effective"]["contract"]["fits"] is False


def test_snapshot_contract_is_the_mind_contract(manifest, monkeypatch):
    _good_runners(monkeypatch)
    manifest.prove_all()
    snap = manifest.snapshot()
    assert snap["contract"]["knowledge_graph"] is True
    assert "knowledge_query" in snap["contract"]["tools"]
    assert snap["stages"]["mind"]["effective"]["prefill_tokens"] == 412


def test_compute_contract_reserves_floor_first(monkeypatch):
    """`fits` is false exactly when a floor tool cannot be held."""
    from agent_friday.services import tool_budget as tb
    tools = [{"name": n, "description": "x" * 40, "input_schema": {"type": "object"}}
             for n in ("knowledge_query", "search_wiki", "read_wiki", "search_web",
                       "memory_recall", "open_url")]
    monkeypatch.setattr("agent_friday.services.agent.CLAUDE_TOOLS", tools)
    monkeypatch.setattr(tb, "_window", lambda m: 131072)
    monkeypatch.setattr(tb, "measure_request", lambda *a, **k: 26410)
    monkeypatch.setattr(tb, "fit_tools_to_seat",
                        lambda m, t, **k: (tb.FittedTools(t), None))
    c = vm.compute_contract("seat-x", system_prompt="voice prompt")
    assert c["fits"] is True and c["floor_present"] is True
    assert c["knowledge_graph"] is True and c["memory"] is True
    # Now the budgeter drops the floor: fits must be false with the arithmetic.
    monkeypatch.setattr(tb, "fit_tools_to_seat",
                        lambda m, t, **k: (tb.FittedTools([t[-1]]), "trimmed"))
    monkeypatch.setattr(tb, "_window", lambda m: 32768)
    c = vm.compute_contract("seat-x", system_prompt="voice prompt")
    assert c["fits"] is False and c["floor_present"] is False
    assert "no room for Friday's tools" in c["reason"]
    assert "32,768" in c["reason"]


# ── cloud mode ───────────────────────────────────────────────────────────────

def test_cloud_mode_describes_the_relay_honestly(monkeypatch, clock):
    monkeypatch.setattr(vm, "_settings", lambda: {"voice_engine": "gemini"})
    m = vm.VoiceManifest(clock=lambda: clock["now"])
    _good_runners(monkeypatch)
    d = m.describe_for_model()
    assert "sent to Google" in d
    assert "NOT available" in d               # mind unproven -> no path claimed
    m.prove("mind")
    d = m.describe_for_model()
    assert "`ask_friday`" in d
    snap = m.snapshot()
    assert snap["stages"]["ear"]["effective"]["engine"] == "gemini-live"
    assert snap["stages"]["ear"]["proof"]["state"] == "cloud"
    assert snap["ready"] is True


def test_auto_reads_as_local(monkeypatch):
    assert vm.read_selection({"voice_engine": "auto"})["mode"] == "local"


# ── single flight ────────────────────────────────────────────────────────────

def test_prove_all_is_single_flight(manifest, monkeypatch):
    calls = {"n": 0}

    def slow_ear(sel, pcm, prog):
        calls["n"] += 1
        time.sleep(0.15)
        return "Friday, what time is it right now?", {"engine": "faster-whisper",
                                                      "device": "cpu", "model": "small"}
    _good_runners(monkeypatch)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "ear", slow_ear)
    assert manifest.prove_async(("ear",)) is True
    time.sleep(0.02)
    assert manifest.prove_async(("ear",)) is False
    for _ in range(50):
        if not manifest.proving:
            break
        time.sleep(0.02)
    assert calls["n"] == 1


def test_bundled_proof_asset_is_present_and_well_formed():
    pcm = vm.load_proof_pcm()
    seconds = len(pcm) / 2 / 16000
    assert 1.5 <= seconds <= 4.0
    assert vm.transcript_matches("Friday, what time is it right now?")
    assert not vm.transcript_matches("what time is it")


def _capture_mind_request(monkeypatch, seat):
    """Route _run_mind at a fake seat and capture the JSON body it sends."""
    import io, json as _json
    from agent_friday.services import local_seats, tool_budget
    monkeypatch.setattr(local_seats, "resolve", lambda role: seat)
    monkeypatch.setattr(tool_budget, "_seat_base", lambda m: "http://127.0.0.1:1")
    monkeypatch.setattr(vm, "compute_contract", lambda s, system_prompt=None: {
        "_oai_tools": [{"type": "function", "function": {"name": "knowledge_query",
                                                          "parameters": {"type": "object"}}}],
        "_system": "sys", "tools": ["knowledge_query"], "floor_present": True,
        "knowledge_graph": True, "memory": False, "window": 131072,
        "prompt_tokens": 100, "tool_tokens": 10, "loop_reserve": 6144,
        "reply_cap": 300, "fits": True, "note": "", "reason": ""})
    sent = {}

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _urlopen(req, timeout=0):
        sent["body"] = _json.loads(req.data.decode())
        return _Resp(_json.dumps({"choices": [{"message": {"content": "OK"}}],
                                  "timings": {"prompt_n": 5}}).encode())
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return sent


def test_mind_proof_disables_thinking_on_the_gemma4_family(monkeypatch):
    """Observed live 2026-09-18: with thinking on, the FridayWeaver seat
    spent all eight tokens inside <|channel>thought, `content` came back
    empty, and the proof refused a seat the real turn would have used
    (the router already sends enable_thinking=false for this family)."""
    sent = _capture_mind_request(monkeypatch, "gemma4:e2b-fridayweaver-1.0")
    out = vm._run_mind({}, None)
    assert out["content"] == "OK"
    assert sent["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["body"]["tools"], "the proof must carry the real tools"


def test_mind_proof_leaves_thinking_alone_for_other_families(monkeypatch):
    sent = _capture_mind_request(monkeypatch, "qwen3:4b")
    vm._run_mind({}, None)
    assert "chat_template_kwargs" not in sent["body"]
