"""The voice front's seat lifecycle (local voice spec P1, §5).

Arming serves the front through the Arbiter's own llama backend on its own
port with its own flags (one cached slot), parks the brain for the call only
when the front does not fit beside it, prefills the session prefix, and gives
everything back when the call ends, including on a failed arm. A front that
is installed and cannot start refuses the session; with no front installed
the brain answers. No server, no model: a fake loader and a fake Arbiter.
"""
import pytest

import agent_friday.routes.voice as rv
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import voice_front as vf


class _Loader:
    def __init__(self, fail=False):
        self.loaded, self.evicted, self.fail = [], [], fail

    def load(self, model_id, num_ctx, *, gguf_path, port):
        if self.fail:
            raise RuntimeError("llama-server exited: out of memory")
        self.loaded.append((model_id, num_ctx, gguf_path, port))

    def evict(self, model_id):
        self.evicted.append(model_id)


class _Arbiter:
    def __init__(self, loader):
        self.llama = loader
        self.events = []

    def grant(self, kind, ttl_s=300):
        self.events.append(("grant", kind))
        return {"ok": True}

    def release(self, kind=None):
        # The real Arbiter.release: `kind` names the lease the caller holds.
        self.events.append(("release",))
        self.released_kind = kind
        return {"ok": True}

    def renew(self, kind, ttl_s):
        self.events.append(("renew", kind))
        return {"ok": True}


@pytest.fixture
def env(monkeypatch, tmp_path):
    loader = _Loader()
    arb = _Arbiter(loader)
    monkeypatch.setattr(ra, "get_arbiter", lambda: arb)
    monkeypatch.setattr(vf, "model_path", lambda m: tmp_path / vf.FRONT_MODELS[m]["file"])
    monkeypatch.setattr(vf.FrontSeat, "healthy", lambda self: False)
    monkeypatch.setattr(vf.FrontSeat, "prefill",
                        lambda self, system, contract: {"ms": 900, "prompt_n": 6100})
    monkeypatch.setattr(rv, "_build_front_system_prompt",
                        lambda settings, contract, label: "FRONT PROMPT")
    monkeypatch.setattr("agent_friday.services.build_hours.is_active", lambda *a, **k: False)
    monkeypatch.setattr(vf, "_SEAT", None)
    return {"loader": loader, "arb": arb, "dir": tmp_path}


def _install(env, model):
    (env["dir"] / vf.FRONT_MODELS[model]["file"]).write_bytes(b"GGUF")


def test_no_front_installed_means_the_brain_answers(env):
    assert rv._arm_voice_front({"voice_front_model": "qwen3-4b-instruct-2507"}) is None
    assert env["loader"].loaded == [] and env["arb"].events == []


def test_the_4b_front_parks_the_brain_and_serves_on_its_own_port(env):
    _install(env, "qwen3-4b-instruct-2507")
    f = rv._arm_voice_front({"voice_front_model": "qwen3-4b-instruct-2507"})
    assert env["arb"].events == [("grant", "voice_call")]
    (mid, ctx, path, port), = env["loader"].loaded
    assert mid == "voice-front:qwen3-4b-instruct-2507" and port == vf.VOICE_FRONT_PORT == 8125
    assert ctx == 16384 and path.endswith("Qwen3-4B-Instruct-2507-Q4_K_M.gguf")
    assert f["prompt"] == "FRONT PROMPT" and f["lease"] == "voice_call"
    rv._release_voice_front(f)
    assert env["loader"].evicted == ["voice-front:qwen3-4b-instruct-2507"]
    assert env["arb"].events[-1] == ("release",)
    assert env["arb"].released_kind == "voice_call", "only its own lease is released"


def test_the_17b_front_sits_beside_the_brain(env, monkeypatch):
    # Beside the brain when the card has room for it (the fit is asked, not
    # assumed: tests/unit/test_ternary_bonsai_voice.py covers a full card).
    from agent_friday.services import voice_workers
    monkeypatch.setattr(voice_workers, "admit_gpu", lambda need, stage: {"ok": True})
    _install(env, "qwen3-1.7b")
    f = rv._arm_voice_front({"voice_front_model": "qwen3-1.7b"})
    assert env["arb"].events == [], "a co-resident front must not park the brain"
    assert f["lease"] is None


def test_owner_policy_resident_never_parks(env):
    _install(env, "qwen3-4b-instruct-2507")
    rv._arm_voice_front({"voice_front_model": "qwen3-4b-instruct-2507",
                         "voice_brain_during_calls": "resident"})
    assert env["arb"].events == []


def test_a_failed_arm_gives_the_brain_back_and_refuses(env):
    _install(env, "qwen3-4b-instruct-2507")
    env["loader"].fail = True
    with pytest.raises(RuntimeError, match="out of memory"):
        rv._arm_voice_front({"voice_front_model": "qwen3-4b-instruct-2507"})
    assert env["arb"].events == [("grant", "voice_call"), ("release",)]
    assert env["arb"].released_kind == "voice_call", "only its own lease is released"


def test_the_front_serves_with_one_cached_slot():
    from agent_friday.services.residency_arbiter import LlamaServerBackend
    args = LlamaServerBackend._declared_serve_args("voice-front:qwen3-4b-instruct-2507")
    i = args.index("-np")
    assert args[i + 1] == "1"


def test_served_by_names_the_front_and_the_parked_brain():
    class _E:
        def describe(self):
            return {"engine": "faster-whisper", "device": "cpu"}
    sb = rv._served_by(_E(), _E(), "bonsai2:27b", front="Qwen3-4B-Instruct-2507",
                       brain_parked=True)
    assert sb["mind"] == "Qwen3-4B-Instruct-2507@gpu"
    assert sb["brain"] == "parked (voice call)"


def test_only_a_voice_call_lease_renews():
    import threading
    import time
    from types import SimpleNamespace
    from agent_friday.services.residency_arbiter import Arbiter
    held = SimpleNamespace(_lock=threading.RLock(),
                           lease={"kind": "voice_call", "expires_at": 0})
    out = Arbiter.renew(held, "voice_call", 600)
    assert out["ok"] and held.lease["expires_at"] > time.time() + 500
    other = SimpleNamespace(_lock=threading.RLock(),
                            lease={"kind": "image_job", "expires_at": 0})
    assert Arbiter.renew(other, "image_job", 600)["ok"] is False
    assert Arbiter.renew(other, "voice_call", 600)["ok"] is False
