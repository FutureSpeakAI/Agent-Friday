"""The manifest is the only source (clean-sheet §3.1 rule 4, §6.3).

``/api/voice/session-info``, ``/api/voice/arm`` and ``describe_for_model()``
must all be derived from one ``snapshot()``; a proof that changes changes all
of them. Arming proves in the background and is single-flight; reading never
proves.
"""
import time

import agent_friday.core as core
from agent_friday.services import voice_manifest as vm


class _FakeEngine:
    def available(self):
        return True

    def models_ready(self):
        return True

    def resolve_tier(self, settings=None):
        return "cpu"


def _fresh(monkeypatch, settings=None):
    vm.reset_for_tests()
    monkeypatch.setattr(vm, "_settings", lambda: dict(settings or {"voice_engine": "local"}))
    monkeypatch.setattr(vm, "_PROMPT_BUILDER", None)
    import agent_friday.routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _FakeEngine())
    monkeypatch.setattr(rv, "_local_brain_ready", lambda: True)
    monkeypatch.setattr(rv, "_load_settings", lambda: dict(settings or {"voice_engine": "local"}))
    return rv


def _runners(monkeypatch, ok=True, delay=0.0):
    def ear(sel, pcm, prog):
        time.sleep(delay)
        if not ok:
            raise RuntimeError("no ear")
        return "Friday, what time is it right now?", {"engine": "faster-whisper",
                                                      "device": "cpu", "model": "small"}

    def mouth(sel, text, prog):
        if not ok:
            raise RuntimeError("no mouth")
        return b"\x00\x01" * 24000, {"engine": "piper", "device": "cpu", "voice": "amy"}

    def mind(sel, prog):
        if not ok:
            raise vm.ProofRefused("local_voice_brain_absent", "no seat")
        return {"seat": "seat-x", "base": "http://127.0.0.1:1",
                "contract": {"tools": ["knowledge_query", "memory_recall"],
                             "floor_present": True, "knowledge_graph": True,
                             "memory": True, "window": 131072, "fits": True},
                "timings": {"prompt_n": 5}, "content": "OK"}
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "ear", ear)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mouth", mouth)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mind", mind)


def _wait_proved(client, n=100):
    for _ in range(n):
        d = client.get("/api/voice/arm").get_json()
        if not d["proving"]:
            return d
        time.sleep(0.02)
    raise AssertionError("proofs never finished")


def test_session_info_carries_the_manifest_and_never_proves(client, monkeypatch):
    _fresh(monkeypatch)
    calls = {"n": 0}

    def ear(sel, pcm, prog):
        calls["n"] += 1
        raise RuntimeError("should not run on a read")
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "ear", ear)
    body = client.get("/api/voice/session-info").get_json()
    m = body["manifest"]
    assert m["mode"] == "local"
    assert m["ready"] is False
    assert set(m["stages"]) == {"ear", "mind", "mouth"}
    assert m["stages"]["ear"]["proof"]["state"] == "unproven"
    assert "NOT been proven" in m["description"]
    assert calls["n"] == 0


def test_manifest_is_the_only_source(client, monkeypatch):
    rv = _fresh(monkeypatch)
    _runners(monkeypatch, ok=True)
    client.post("/api/voice/arm")
    armed = _wait_proved(client)
    info = client.get("/api/voice/session-info").get_json()["manifest"]
    described = vm.get_manifest().describe_for_model()
    # Three surfaces, one snapshot.
    assert armed["stages"]["mouth"]["proof"]["at"] == info["stages"]["mouth"]["proof"]["at"]
    assert armed["description"] == info["description"] == described
    assert "Piper on the CPU" in described
    assert info["ready"] is True
    # A proof that changes changes all three.
    _runners(monkeypatch, ok=False)
    client.post("/api/voice/arm", json={"force": True})
    armed = _wait_proved(client)
    info = client.get("/api/voice/session-info").get_json()["manifest"]
    described = vm.get_manifest().describe_for_model()
    assert armed["ready"] is False and info["ready"] is False
    assert armed["description"] == info["description"] == described
    assert "piper" not in described.lower()
    assert armed["stages"]["mind"]["proof"]["code"] == "local_voice_brain_absent"
    assert rv is not None


def test_arm_get_is_read_only_and_post_is_single_flight(client, monkeypatch):
    _fresh(monkeypatch)
    calls = {"n": 0}
    _runners(monkeypatch, ok=True, delay=0.1)
    _orig = vm.ENGINE_RUNNERS["ear"]

    def counting_ear(*a, **k):
        calls["n"] += 1
        return _orig(*a, **k)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "ear", counting_ear)
    d = client.get("/api/voice/arm").get_json()
    assert d["proving"] is False and calls["n"] == 0
    first = client.post("/api/voice/arm").get_json()
    assert first["started"] is True
    for _ in range(3):
        again = client.post("/api/voice/arm").get_json()
        assert again["started"] is False
    _wait_proved(client)
    assert calls["n"] == 1
    # Fresh proofs are not re-run by a plain POST.
    d = client.post("/api/voice/arm").get_json()
    assert d["started"] is False and d["ready"] is True


def test_arm_in_cloud_mode_proves_only_the_mind(client, monkeypatch):
    _fresh(monkeypatch, {"voice_engine": "gemini"})
    monkeypatch.setattr(core, "GEMINI_API_KEY", "AQ.fake-key-for-test")  # pragma: allowlist secret
    import agent_friday.routes.voice as rv
    monkeypatch.setattr(rv, "_network_status", lambda: {"offline": False})
    monkeypatch.setattr(rv, "resolve_gemini_key", lambda: {"valid": True})
    ran = []
    _runners(monkeypatch, ok=True)
    _m = vm.ENGINE_RUNNERS["mind"]
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "ear", lambda *a, **k: ran.append("ear"))
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mouth", lambda *a, **k: ran.append("mouth"))
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mind", lambda *a, **k: (ran.append("mind"), _m(*a, **k))[1])
    client.post("/api/voice/arm")
    d = _wait_proved(client)
    assert ran == ["mind"]
    assert d["stages"]["ear"]["effective"]["engine"] == "gemini-live"
    assert "sent to Google" in d["description"]


def test_prove_forces_a_reproof(client, monkeypatch):
    _fresh(monkeypatch)
    _runners(monkeypatch, ok=True)
    client.post("/api/voice/arm")
    _wait_proved(client)
    n = {"c": 0}
    _orig = vm.ENGINE_RUNNERS["mouth"]

    def mouth(*a, **k):
        n["c"] += 1
        return _orig(*a, **k)
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mouth", mouth)
    d = client.post("/api/voice/prove").get_json()
    assert d["started"] is True
    _wait_proved(client)
    assert n["c"] == 1
