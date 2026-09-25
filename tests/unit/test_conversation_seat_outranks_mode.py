"""A per-conversation binding is an instruction, not a preference.

The router deliberately lets `local_preferred`/`smart` override a GLOBAL cloud
seat: settings ship with a cloud reasoning model, so honouring it would make
those modes unreachable. That ruling is recorded in the router and is right.

A seat bound to ONE CONVERSATION is a different statement. It is the user
saying "this thread, that model", per thread, which is the whole point of
being able to open several chats at once. Without this rule a conversation
bound to claude-sonnet-5 is answered by bonsai2:27b while one bound to
bonsai2:27b is answered correctly - a per-chat picker that works in exactly
one of its two directions, which is worse than not having it, because it
looks like it works.

The cost is real and is stated where the decision is logged: a chat bound to a
cloud model leaves the machine even in local_preferred, and is billed. That is
what binding it means.
"""
from __future__ import annotations

import pytest

from agent_friday.routing.model_router import ModelRouter, get_router


def _router(monkeypatch, mode="local_preferred", local=("bonsai2:27b",)):
    r = get_router({"mode": mode})
    # `_local_candidates` returns records, not names - checked against the
    # real implementation rather than assumed, after a first version of this
    # fixture handed back bare strings and produced a TypeError deep inside
    # the router that looked like a product bug.
    #
    # Patched on the CLASS, never on the process-wide router instance: undoing
    # an instance patch writes the real bound method back into the instance's
    # own __dict__, where it outranks every later class-level patch, and the
    # next file's seat tests are then routed by the real candidate list.
    monkeypatch.setattr(ModelRouter, "_local_candidates",
                        lambda self: [{"name": n, "size_gb": 16.0} for n in local])
    monkeypatch.setattr(ModelRouter, "_is_registry_local",
                        lambda self, m: ":" in str(m or ""))
    return r


def _route(r, seat=None):
    return r.route([{"role": "user", "content": "hello"}], task_context={
        "has_tools": True,
        "workspace": "chat",
        "cloud_model": "claude-sonnet-5",
        "conversation_seat": seat,
    }) or {}


def test_a_conversation_bound_to_a_cloud_model_goes_to_the_cloud(monkeypatch):
    """THE CRUX, and the direction that was broken."""
    r = _router(monkeypatch)
    out = _route(r, {"model": "claude-sonnet-5"})
    assert out.get("provider") == "cloud", out
    assert out.get("model") == "claude-sonnet-5", out
    assert "bound" in (out.get("reason") or "").lower(), \
        "the reason must say WHY it left the machine - this one costs money"


def test_a_conversation_bound_to_a_local_model_stays_local(monkeypatch):
    r = _router(monkeypatch)
    out = _route(r, {"model": "bonsai2:27b"})
    assert out.get("provider") == "local", out


def test_no_binding_still_respects_local_preferred(monkeypatch):
    """The ruling this change is narrow around: without a per-conversation
    binding, local_preferred still keeps an ordinary turn on the machine even
    though the global reasoning seat is a cloud model."""
    r = _router(monkeypatch)
    out = _route(r, None)
    assert out.get("provider") == "local", out


def test_an_empty_binding_is_not_a_binding(monkeypatch):
    r = _router(monkeypatch)
    for seat in ({}, {"model": ""}, {"model": "   "}, "not-a-dict", None):
        out = _route(r, seat)
        assert out.get("provider") == "local", (seat, out)


def test_with_no_local_seat_the_binding_is_moot_but_harmless(monkeypatch):
    """Nothing local to keep it on. The turn goes to the cloud either way;
    this asserts the new branch does not break that path."""
    r = _router(monkeypatch, local=())
    out = _route(r, {"model": "claude-sonnet-5"})
    assert out.get("provider") == "cloud", out
