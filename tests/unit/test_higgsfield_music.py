"""Higgsfield music dispatch, and the silent substitution it exists to stop.

The defect: `resolve_music_model` ends with a Lyria-shaped passthrough whose
fall-through is unconditional (music_engine.py:121-124), so every non-Lyria id
became `lyria-3-clip-preview`. Picking a Higgsfield music model called Google,
returned a Lyria track, and reported success — a system saying it did
something it did not do, reachable from a settings toggle.

These tests pin the branch that fixes it AND that the Lyria guard is untouched.
"""
from __future__ import annotations

import pytest

from agent_friday.services import music_engine as me
from agent_friday.services import higgsfield_generate as hg


@pytest.fixture
def hf_catalogue(monkeypatch):
    """A cache carrying one Higgsfield music model with a REQUIRED duration."""
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "cached_model_ids", lambda name: ["sonilo_music"])
    monkeypatch.setattr(md, "cached_models", lambda name: ([
        {"id": "sonilo_music", "label": "Sonilo Music",
         "modalities": ["audio", "music"],
         "constraints": {"duration": {"required": True, "default": None,
                                      "min": None, "max": None}}},
    ], False))


# ── The Lyria guard must not move ────────────────────────────────────────────

@pytest.mark.parametrize("requested,expected", [
    ("lyria-clip", "lyria-3-clip-preview"),
    ("lyria-pro", "lyria-3-pro-preview"),
    ("lyria-3-pro-preview", "lyria-3-pro-preview"),   # raw-id passthrough
    ("total-nonsense", "lyria-3-clip-preview"),       # junk seat → default
])
def test_resolve_music_model_is_unchanged(requested, expected, monkeypatch):
    """The fix branches BEFORE this function; its behaviour is bit-for-bit
    what it was, including the junk-value fallback it was written for."""
    monkeypatch.setattr(me, "_settings_overrides", lambda: {})
    assert me.resolve_music_model(requested) == expected


# ── The substitution is gone ─────────────────────────────────────────────────

def test_higgsfield_pick_does_not_become_lyria(hf_catalogue, monkeypatch):
    """The regression itself: picking sonilo_music must not call Google."""
    called = {}
    monkeypatch.setattr(hg, "generate",
                        lambda kind, prompt, **kw: called.update(
                            kind=kind, model=kw.get("model"),
                            extra=kw.get("extra")) or {"status": "ok",
                                                       "files": [],
                                                       "provider": "higgsfield"})
    # If the Lyria path were reached it would need a cloud client; make that
    # loudly impossible so a fall-through cannot pass silently.
    monkeypatch.setattr(me, "cloud_music_available",
                        lambda: pytest.fail("fell through to the Lyria path"))

    out = me.generate_music("a lofi study loop", model="sonilo_music")
    assert out["status"] == "ok"
    assert out["provider"] == "higgsfield"
    assert called["kind"] == "audio"
    assert called["model"] == "sonilo_music"


def test_seat_value_routes_too(hf_catalogue, monkeypatch):
    """The seat, not just an explicit request — that is how the picker sets it."""
    seen = {}
    monkeypatch.setattr(me, "_seat_model", lambda: "sonilo_music")
    monkeypatch.setattr(hg, "generate",
                        lambda kind, prompt, **kw: seen.update(
                            model=kw.get("model")) or {"status": "ok"})
    monkeypatch.setattr(me, "cloud_music_available",
                        lambda: pytest.fail("fell through to the Lyria path"))
    me.generate_music("something warm")
    assert seen["model"] == "sonilo_music"


def test_lyria_pick_still_goes_to_lyria(monkeypatch):
    """The other half: a Lyria id must NOT be diverted to Higgsfield."""
    monkeypatch.setattr(hg, "is_higgsfield_model", lambda mid: False)
    monkeypatch.setattr(hg, "generate",
                        lambda *a, **k: pytest.fail("Lyria pick left for Higgsfield"))
    monkeypatch.setattr(me, "cloud_music_available", lambda: (False, "no key"))
    out = me.generate_music("a jingle", model="lyria-clip")
    # Demo path, because no cloud key — the point is it stayed on Lyria.
    assert out["api_model"] == "lyria-3-clip-preview"


def test_dispatch_failure_never_breaks_the_lyria_path(monkeypatch):
    """A broken Higgsfield lookup must not take music generation down."""
    monkeypatch.setattr(hg, "is_higgsfield_model",
                        lambda mid: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(me, "cloud_music_available", lambda: (False, "no key"))
    out = me.generate_music("a jingle", model="lyria-clip")
    assert out["status"] == "demo"


# ── The required-duration wrinkle ────────────────────────────────────────────

def test_required_duration_gets_a_default(hf_catalogue):
    """sonilo_music declares duration REQUIRED — omitting it is a submit
    error, so a caller that gives none must still produce a valid request."""
    extra = me._hf_music_extra("sonilo_music", None)
    assert extra == {"duration": me.HIGGSFIELD_DEFAULT_MUSIC_SECONDS}
    assert me.HIGGSFIELD_DEFAULT_MUSIC_SECONDS == me.CLIP_MAX_SECONDS


def test_caller_duration_wins(hf_catalogue):
    assert me._hf_music_extra("sonilo_music", 12) == {"duration": 12}


def test_published_range_is_respected(monkeypatch):
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "cached_models", lambda name: ([
        {"id": "m", "constraints": {"duration": {"required": True,
                                                 "min": 5, "max": 20}}}], False))
    assert me._hf_music_extra("m", 999) == {"duration": 20}
    assert me._hf_music_extra("m", 1) == {"duration": 5}


def test_model_without_duration_gets_none(monkeypatch):
    """Never invent a parameter the model does not publish."""
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "cached_models",
                        lambda name: ([{"id": "m", "constraints": {}}], False))
    assert me._hf_music_extra("m", 30) == {}


def test_unreadable_constraints_are_not_fatal(monkeypatch):
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "cached_models",
                        lambda name: (_ for _ in ()).throw(OSError("no cache")))
    assert me._hf_music_extra("sonilo_music", None) == {}


# ── Egress: a remote MCP prompt must be gated ────────────────────────────────

def test_prompts_are_gated_before_leaving(monkeypatch):
    """Higgsfield is a REMOTE MCP server, so every prompt is cloud egress.

    This module calls mgr.call directly and so bypassed agent._mcp_gate_args,
    "the single egress choke point for remote MCP tool calls".
    """
    seen = {}

    class _Mgr:
        servers = {"higgsfield": object()}

        def call(self, server, tool, args, timeout=None):
            seen["submitted"] = True
            return {"credits": 1}

    import agent_friday.services.agent as _agent
    monkeypatch.setattr(_agent, "_MCP_MANAGER", _Mgr(), raising=False)
    monkeypatch.setattr(_agent, "_mcp_gate_args",
                        lambda s, t, a: seen.setdefault("gated", (s, t)) or (True, None))
    hg._call("generate_audio", {"params": {"model": "sonilo_music",
                                           "prompt": "hello"}})
    assert seen["gated"] == ("higgsfield", "generate_audio")
    assert seen["submitted"] is True


def test_a_blocked_prompt_is_never_submitted(monkeypatch):
    class _Mgr:
        servers = {"higgsfield": object()}

        def call(self, *a, **k):
            pytest.fail("submitted despite the egress gate refusing")

    import agent_friday.services.agent as _agent
    monkeypatch.setattr(_agent, "_MCP_MANAGER", _Mgr(), raising=False)
    monkeypatch.setattr(_agent, "_mcp_gate_args",
                        lambda s, t, a: (False, "would send vault contents"))
    with pytest.raises(hg.EgressBlocked):
        hg._call("generate_image", {"params": {"model": "z_image",
                                               "prompt": "x"}})


def test_blocked_reports_blocked_not_outage(monkeypatch):
    """A refusal is not a provider failure: nothing was sent, nothing charged."""
    monkeypatch.setattr(hg, "_call", lambda *a, **k: (_ for _ in ()).throw(
        hg.EgressBlocked("would send vault contents")))
    out = hg.generate("audio", "x", model="sonilo_music")
    assert out["status"] == "blocked"
    assert "vault" in out["reason"]
