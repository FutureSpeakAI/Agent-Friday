"""The seat control is binding, and when it cannot bind it says so.

Observed 2026-09-18 from the persisted chat records and friday.log: the
picker wrote `capability_routing.reasoning = gemma4:12b` (not installed;
Ollama held zero models, the only local weights were the FridayWeaver e2b
set), the save returned 200, the UI announced success, `local_seats.resolve`
substituted e2b at INFO, and -- Computer Control being on -- the chat route
then sent every turn to claude-sonnet-5 with a print() to a stdout nobody
reads. The user asked three times for the local model and was told "Done".

Four things that could fail:

  1. A request to bind an UNINSTALLED local model is refused out loud,
     naming what is missing and what is installed, and changes nothing.
  2. Binding an INSTALLED local model is accepted.
  3. A turn on an installed local seat is answered locally: the receipt says
     `seat == "local"` and names that model, with no notice.
  4. When Computer Control forces the cloud, the turn's receipt says cloud
     AND carries a notice; a system line lands in the transcript.
"""
from __future__ import annotations

import pytest

import agent_friday.routes.chat as chat_mod
import agent_friday.routes.core_routes as core_routes
from agent_friday.core import DEFAULT_SETTINGS

pytestmark = pytest.mark.real_provider_paths

SEAT = "gemma4:e2b-fridayweaver-1.0"


class _Recorder:
    def __init__(self, result=("reply", [])):
        self.calls = 0
        self.kwargs = None
        self._result = result

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return self._result


def _installed(monkeypatch, names):
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "installed",
                        lambda force=False: [(n, 4.6) for n in names])


# ── 1 + 2: the save ──────────────────────────────────────────────────────────

def test_binding_an_uninstalled_local_model_is_refused_out_loud(client, monkeypatch):
    _installed(monkeypatch, [SEAT])
    saved = _Recorder()
    monkeypatch.setattr(core_routes, "_save_settings", saved)
    r = client.post("/api/settings", json={"settings": {"capability_routing": {
        "reasoning": {"model": "gemma4:12b", "provider": "ollama-local"}}}})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "seat_not_installed"
    assert body["model"] == "gemma4:12b"
    assert "gemma4:12b is not installed" in body["detail"]
    assert SEAT in body["detail"]              # what IS installed is named
    assert "Nothing was changed" in body["detail"]
    assert saved.calls == 0                    # and nothing was


def test_binding_an_installed_local_model_is_accepted(client, monkeypatch):
    _installed(monkeypatch, [SEAT])
    saved = _Recorder(result=dict(DEFAULT_SETTINGS))
    monkeypatch.setattr(core_routes, "_save_settings", saved)
    r = client.post("/api/settings", json={"settings": {"capability_routing": {
        "reasoning": {"model": SEAT, "provider": "llama-cpp-local"}}}})
    assert r.status_code == 200, r.get_json()
    assert saved.calls == 1


# ── 3 + 4: the turn ──────────────────────────────────────────────────────────

def _settings(reasoning_model=SEAT):
    s = dict(DEFAULT_SETTINGS)
    s["model_routing"] = dict(s.get("model_routing") or {},
                              mode="local_preferred", local_model=SEAT)
    s["capability_routing"] = dict(s.get("capability_routing") or {})
    s["capability_routing"]["reasoning"] = {"model": reasoning_model,
                                            "provider": "llama-cpp-local"}
    s["orchestrator_model"] = reasoning_model
    return s


def _arrange_turn(monkeypatch, reasoning_model=SEAT):
    monkeypatch.setattr(chat_mod, "_load_settings",
                        lambda: _settings(reasoning_model), raising=False)
    import agent_friday.services.demo_mode as _dm
    monkeypatch.setattr(_dm, "is_demo", lambda *a, **k: False, raising=False)
    # The router sees exactly one local seat: the installed one.
    from agent_friday.routing import model_router as _rm
    monkeypatch.setattr(_rm.ModelRouter, "_local_candidates",
                        lambda self: [{"name": SEAT, "size_gb": 4.6}])
    _installed(monkeypatch, [SEAT])
    local = _Recorder(("local reply", []))
    cloud = _Recorder(("cloud reply", []))
    monkeypatch.setattr(chat_mod, "_call_ollama", local, raising=False)
    monkeypatch.setattr(chat_mod, "_call_claude_agent", cloud, raising=False)
    monkeypatch.setattr(chat_mod, "get_anthropic_client",
                        lambda *a, **k: object(), raising=False)
    chat_mod._SEAT_NOTICES_SENT.clear()
    return local, cloud


def test_a_turn_on_an_installed_local_seat_is_answered_locally(client, monkeypatch):
    local, cloud = _arrange_turn(monkeypatch)
    chat_mod._CC_PERMISSION.clear()
    r = client.post("/api/chat", json={"message": "hello there",
                                       "conversation_id": "conv-test-seat"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    body = r.get_json()
    assert body["seat"] == "local"
    assert body["model"] == SEAT
    assert body.get("seat_notice") in (None, "")
    assert local.calls == 1 and cloud.calls == 0


def test_computer_control_override_is_announced_not_silent(client, monkeypatch):
    local, cloud = _arrange_turn(monkeypatch)
    chat_mod._CC_PERMISSION.set()
    try:
        r = client.post("/api/chat", json={"message": "hello there",
                                           "conversation_id": "conv-test-seat"})
    finally:
        chat_mod._CC_PERMISSION.clear()
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    body = r.get_json()
    assert body["seat"] == "cloud"
    assert cloud.calls == 1 and local.calls == 0
    assert "Computer Control is on" in (body.get("seat_notice") or "")
    assert SEAT in body["seat_notice"]       # names the seat it displaced
    # The transcript carries it as a system line, not only the JSON.
    sys_lines = [m for m in chat_mod.CHAT_HISTORY
                 if isinstance(m, dict) and m.get("kind") == "seat_notice"]
    assert sys_lines and "Computer Control is on" in sys_lines[-1]["text"]


def test_a_substituted_seat_is_announced_in_the_turn(client, monkeypatch):
    """The picker holds gemma4:12b (not installed); the router substitutes the
    installed FridayWeaver seat. The turn must say so -- on the response and
    as a system line -- instead of an INFO line in a log nobody reads.
    (Live on 2026-09-18 a scratch conversation was created bound to the
    serving seat, so this path is pinned here rather than by a live turn.)"""
    local, cloud = _arrange_turn(monkeypatch, reasoning_model="gemma4:12b")
    from agent_friday.routing import model_router as _rm
    monkeypatch.setattr(_rm.ModelRouter, "_chosen_seat",
                        lambda self, ctx=None: ("gemma4:12b", "ollama-local"))
    chat_mod._CC_PERMISSION.clear()
    r = client.post("/api/chat", json={"message": "hello there",
                                       "conversation_id": "conv-test-seat"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    body = r.get_json()
    assert body["seat"] == "local" and body["model"] == SEAT
    notice = body.get("seat_notice") or ""
    assert "You chose gemma4:12b" in notice and SEAT in notice
    assert "not installed" in notice
    sys_lines = [m for m in chat_mod.CHAT_HISTORY
                 if isinstance(m, dict) and m.get("kind") == "seat_notice"]
    assert sys_lines and "gemma4:12b" in sys_lines[-1]["text"]
    assert local.calls == 1 and cloud.calls == 0
