"""voice-mode-diagnosis-and-repair.md, route half: F3 (session-info carries
the reach of the engine it recommends) and F5 (/api/voice/warm loads the
selected local engine off the first spoken turn, and no-ops for cloud).
"""
import threading
import time

import pytest

import agent_friday.core as core


class _FakeEngine:
    """Enough of LocalVoiceEngine for the warm route: a slow-ish load that
    the route must run on a thread and report as loading -> ready."""

    def __init__(self, load_s=0.15, ok=True):
        self._ready = False
        self._load_s, self._ok = load_s, ok
        self._tier = "cpu"
        self.last_error = ""
        self.loads = 0
        self.progress = []

    def available(self):
        return True

    def models_ready(self):
        return True

    def resolve_tier(self, settings=None):
        return "cpu"

    def select_tier(self, tier):
        self._tier = tier

    def active_tier(self):
        return self._tier

    def ensure_ready(self, progress=None):
        self.loads += 1
        if progress:
            progress("Loading Kokoro voice (af_heart)…")
        time.sleep(self._load_s)
        if not self._ok:
            self.last_error = "Kokoro needs a CUDA GPU. Use Piper instead."
            return False
        self._ready = True
        return True

    def running_status(self):
        if not self._ready:
            return None
        return {"tier": self._tier, "ready": True, "tts_engine": "kokoro"}


@pytest.fixture(autouse=True)
def _reset_warm():
    import agent_friday.routes.voice as rv
    with rv._WARM_LOCK:
        rv._WARM.update(state="idle", progress="", started=0.0, finished=0.0,
                        tier=None, error="", engine=None)
    yield
    with rv._WARM_LOCK:
        rv._WARM.update(state="idle", progress="", started=0.0, finished=0.0,
                        tier=None, error="", engine=None)


def _wait_not_loading(client, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get("/api/voice/warm").get_json()
        if d["state"] != "loading":
            return d
        time.sleep(0.05)
    raise AssertionError("warm never left loading")


# ── F3 ───────────────────────────────────────────────────────────────────────

def test_session_info_cloud_carries_context_reach(client, monkeypatch):
    import agent_friday.routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _FakeEngine())
    monkeypatch.setattr(rv, "_load_settings", lambda: {"voice_engine": "gemini"})
    monkeypatch.setattr(core, "GEMINI_API_KEY", "AQ.fake-key-for-test")  # pragma: allowlist secret
    monkeypatch.setattr(rv, "_network_status", lambda: {"offline": False})
    monkeypatch.setattr(rv, "resolve_gemini_key", lambda: {"valid": True})
    # The relay is only reach when the local mind is proven (§3.1).
    monkeypatch.setattr(rv, "_local_mind_proven", lambda: True)
    body = client.get("/api/voice/session-info").get_json()
    assert body["engine"] == "gemini"
    cr = body["context_reach"]
    assert cr["engine"] == "gemini"
    # Clean-sheet §4.5 (D7): with `ask_friday` on the Live tool table the
    # knowledge graph and memory ARE reachable -- through the local model --
    # and the reach line says so instead of the old F3 warning.
    assert cr["knowledge_graph"] is True and cr["memory"] is True
    assert cr["full_context"] is True and cr["via_local"] is True
    assert "ask_friday" in cr["line"] and "local model" in cr["line"]
    assert cr["notice"] == ""


def test_session_info_local_carries_full_reach(client, monkeypatch):
    import agent_friday.routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _FakeEngine())
    monkeypatch.setattr(rv, "_local_brain_ready", lambda: True)
    monkeypatch.setattr(rv, "_load_settings", lambda: {"voice_engine": "local"})
    body = client.get("/api/voice/session-info").get_json()
    assert body["engine"] == "local"
    assert body["context_reach"]["full_context"] is True
    assert body["context_reach"]["notice"] == ""


# ── F5 ───────────────────────────────────────────────────────────────────────

def test_warm_loads_local_engine_off_the_first_turn(client, monkeypatch):
    import agent_friday.routes.voice as rv
    eng = _FakeEngine(load_s=0.2)
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: eng)
    monkeypatch.setattr(rv, "_resolve_voice_engine",
                        lambda settings=None: {"engine": "local", "models_ready": True})
    r = client.post("/api/voice/warm")
    assert r.status_code == 200
    d = r.get_json()
    assert d["state"] == "loading"
    assert d["elapsed_s"] >= 0
    d = _wait_not_loading(client)
    assert d["state"] == "ready"
    assert d["engine"] == "kokoro"
    assert d["tier"] == "cpu"
    assert d["running"]["ready"] is True
    assert eng.loads == 1
    # A second POST when already loaded does not reload.
    d = client.post("/api/voice/warm").get_json()
    assert d["state"] == "ready"
    assert eng.loads == 1


def test_warm_is_a_single_flight(client, monkeypatch):
    import agent_friday.routes.voice as rv
    eng = _FakeEngine(load_s=0.3)
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: eng)
    monkeypatch.setattr(rv, "_resolve_voice_engine",
                        lambda settings=None: {"engine": "local", "models_ready": True})
    for _ in range(4):
        client.post("/api/voice/warm")
    _wait_not_loading(client)
    assert eng.loads == 1


def test_warm_reports_failure_with_the_engines_reason(client, monkeypatch):
    import agent_friday.routes.voice as rv
    eng = _FakeEngine(load_s=0.05, ok=False)
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: eng)
    monkeypatch.setattr(rv, "_resolve_voice_engine",
                        lambda settings=None: {"engine": "local", "models_ready": True})
    client.post("/api/voice/warm")
    d = _wait_not_loading(client)
    assert d["state"] == "failed"
    assert "Piper" in d["error"]


def test_warm_skips_when_cloud_voice_is_selected(client, monkeypatch):
    import agent_friday.routes.voice as rv
    eng = _FakeEngine()
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: eng)
    monkeypatch.setattr(rv, "_resolve_voice_engine",
                        lambda settings=None: {"engine": "gemini", "models_ready": True})
    d = client.post("/api/voice/warm").get_json()
    assert d["state"] == "skipped"
    assert "gemini" in d["reason"]
    assert eng.loads == 0


def test_warm_skips_when_models_not_downloaded(client, monkeypatch):
    import agent_friday.routes.voice as rv
    eng = _FakeEngine()
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: eng)
    monkeypatch.setattr(rv, "_resolve_voice_engine",
                        lambda settings=None: {"engine": "local", "models_ready": False})
    d = client.post("/api/voice/warm").get_json()
    assert d["state"] == "skipped"
    assert eng.loads == 0


def test_warm_get_is_read_only(client, monkeypatch):
    import agent_friday.routes.voice as rv
    eng = _FakeEngine()
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: eng)
    d = client.get("/api/voice/warm").get_json()
    assert d["state"] == "idle"
    assert eng.loads == 0
