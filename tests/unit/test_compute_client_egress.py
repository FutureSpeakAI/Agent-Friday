"""Unit tests for security-boundary.md §19 row 4: `compute_client.request_job`
wrapped its egress-gate call in `except Exception: pass` — a gate that could
not run silently became a send that skipped it — and `task_spec["context"]`
was never passed through the gate at all, only `prompt` was. This mirrors the
fix already made to `seal_outbound` itself (test_egress_envelope.py's
TestPromptKeyTierGated): `request_job` must fail CLOSED when the gate cannot
run, and must gate `context` as well as `prompt`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import compute_client as cc
from agent_friday.services import egress_gate

SENSITIVE_CONTEXT = "custody and divorce settlement for SSN 123-45-6789"  # pragma: allowlist secret


def _leaked(blob) -> bool:
    s = str(blob)
    return "custody and divorce settlement" in s or "123-45-6789" in s  # pragma: allowlist secret


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_gate_failure_blocks_the_send_entirely(monkeypatch):
    """The N-1-style bug: a broad `except Exception: pass` around the gate
    call let a raising gate fall through to an ungated send. request_job must
    not make the HTTP request at all when the gate cannot run."""
    def _raise(*a, **k):
        raise RuntimeError("synthetic gate failure for a test")
    monkeypatch.setattr(egress_gate, "seal_outbound", _raise)

    called = {"urlopen": False}
    def _urlopen(*a, **k):
        called["urlopen"] = True
        return _FakeHTTPResponse(b'{"ok": true}')
    monkeypatch.setattr(cc.urllib.request, "urlopen", _urlopen)

    with pytest.raises(Exception):
        cc.request_job("http://peer.example/", {"prompt": "hello", "capability": "text.generate"})

    assert not called["urlopen"], (
        "a gate failure must block the send — the HTTP request must never "
        "be made when the gate could not run"
    )


def test_never_send_blocked_blocks_the_send_entirely(monkeypatch):
    def _raise_never_send(*a, **k):
        raise egress_gate.NeverSendBlocked("synthetic never-send block")
    monkeypatch.setattr(egress_gate, "seal_outbound", _raise_never_send)

    called = {"urlopen": False}
    monkeypatch.setattr(cc.urllib.request, "urlopen",
                        lambda *a, **k: called.update(urlopen=True))

    with pytest.raises(egress_gate.NeverSendBlocked):
        cc.request_job("http://peer.example/", {"prompt": "hello"})
    assert not called["urlopen"]


def test_context_field_is_gated(monkeypatch):
    """task_spec['context'] must reach the gate, not just 'prompt' — before
    the fix, `seal_outbound({"prompt": prompt}, ...)` never mentioned
    context at all, so it rode along unexamined in the outbound payload."""
    sent_bodies = []

    def _urlopen(req, timeout=15):
        # The running app has background schedulers making unrelated
        # urllib.request calls (data=None GETs) on other threads; filter to
        # this call's own POST so a race doesn't pick up someone else's row.
        if getattr(req, "full_url", "") == "http://peer.example/api/federation/compute/request":
            sent_bodies.append(req.data)
        return _FakeHTTPResponse(b'{"ok": true}')
    monkeypatch.setattr(cc.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(cc, "_own_id", lambda: "self")

    cc.request_job(
        "http://peer.example/",
        {"prompt": "please help with this task",
         "capability": "text.generate",
         "context": {"notes": SENSITIVE_CONTEXT}},
    )

    assert sent_bodies, "request_job should have sent exactly one HTTP request"
    assert not _leaked(sent_bodies[0]), (
        "task_spec['context'] must be gated before it reaches the outbound "
        "HTTP payload sent to a federation peer"
    )


def test_benign_context_and_prompt_still_send(monkeypatch):
    sent_bodies = []

    def _urlopen(req, timeout=15):
        # The running app has background schedulers making unrelated
        # urllib.request calls (data=None GETs) on other threads; filter to
        # this call's own POST so a race doesn't pick up someone else's row.
        if getattr(req, "full_url", "") == "http://peer.example/api/federation/compute/request":
            sent_bodies.append(req.data)
        return _FakeHTTPResponse(b'{"ok": true}')
    monkeypatch.setattr(cc.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(cc, "_own_id", lambda: "self")

    cc.request_job(
        "http://peer.example/",
        {"prompt": "summarize this public article",
         "capability": "text.generate",
         "context": {"topic": "weather forecasting"}},
    )
    assert sent_bodies
    body = sent_bodies[0].decode()
    assert "summarize this public article" in body
    assert "weather forecasting" in body
