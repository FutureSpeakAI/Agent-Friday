"""Unit tests for security-boundary.md §19 row 3: `services/media_tools.py`'s
three `generate_content` call sites (inspect_image, the video-frame sampler,
inspect_audio's "listen check") send a caller-supplied `question` and raw
media bytes to Gemini with no gate call and no ledger row at all — unlike
`routes/chat.py:388,:1489`'s vision prompt (§19 row 11), the `question` here
is genuinely caller/model-supplied text, so it must be text-gated like any
other cloud-bound prompt, and the media bytes need the same
`record_binary_egress` receipt every other binary path gets.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services import media_tools as mt
from agent_friday.services import egress_gate


# ── question gating ─────────────────────────────────────────────────────────
class TestVisionQuestionFailsClosed:
    def test_never_send_blocked_withholds_not_passthrough(self, monkeypatch):
        SECRET = "my SSN is 123-45-6789, does this look genuine?"  # pragma: allowlist secret
        def _raise(text, provider, field, log_path=None):
            raise egress_gate.NeverSendBlocked("synthetic block")
        monkeypatch.setattr(egress_gate, "_gate_text", _raise)
        out = mt._gate_vision_question(SECRET, "inspect_image.question")
        assert SECRET not in out

    def test_generic_exception_withholds_not_passthrough(self, monkeypatch):
        SECRET = "my custody case documents — is this signature real?"
        monkeypatch.setattr(egress_gate, "_gate_text",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        out = mt._gate_vision_question(SECRET, "inspect_image.question")
        assert SECRET not in out

    def test_normal_question_still_reaches_the_model(self, monkeypatch):
        monkeypatch.setattr(egress_gate, "_gate_text",
                            lambda text, provider, field, log_path=None: text)
        out = mt._gate_vision_question("is the sky blue in this frame?",
                                       "inspect_image.question")
        assert out == "is the sky blue in this frame?"

    def test_gate_called_with_gemini_provider(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            egress_gate, "_gate_text",
            lambda text, provider, field, log_path=None:
                calls.append((text, provider, field)) or text)
        mt._gate_vision_question("q", "inspect_audio.question")
        assert calls == [("q", "google-gemini", "inspect_audio.question")]


def test_ungated_question_reproduction_is_falsifiable(monkeypatch):
    """Reproduce the pre-fix shape at media_tools.py:142/186/236: `question`
    passed straight into `contents=[question, ...]` with no gate call
    anywhere in the module."""
    def _old_no_gate(question):
        return question  # the pre-fix behavior: passed straight through

    SECRET = "my bank account number is 987654321"  # pragma: allowlist secret
    leaked = _old_no_gate(SECRET)
    assert SECRET in leaked, (
        "the reproduction should leak the raw question; it did not, so this "
        "comparison is not meaningful"
    )


# ── binary egress ledger ────────────────────────────────────────────────────
def test_media_binary_egress_writes_a_ledger_row(monkeypatch):
    calls = []
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda provider, field, **kw: calls.append((provider, field, kw)) or {})
    mt._record_media_binary_egress("inspect_image.image", 12345)
    assert len(calls) == 1
    provider, field, kw = calls[0]
    assert provider == "google-gemini"
    assert field == "inspect_image.image"
    assert kw["byte_len"] == 12345
    assert kw["action"] == "allow"


def test_media_binary_egress_never_raises(monkeypatch):
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    mt._record_media_binary_egress("inspect_image.image", 100)  # must not raise


# ── end-to-end wiring: _tool_inspect_image actually calls both seams ───────
class _FakeResp:
    text = "a red square"


class _FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents):
        self.calls.append((model, contents))
        return _FakeResp()


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def test_inspect_image_gates_question_and_records_binary_egress(tmp_path, monkeypatch):
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")

    gate_calls = []
    ledger_calls = []
    monkeypatch.setattr(
        egress_gate, "_gate_text",
        lambda text, provider, field, log_path=None:
            gate_calls.append((text, provider, field)) or text)
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda provider, field, **kw: ledger_calls.append((provider, field, kw)) or {})

    fake_client = _FakeClient()
    monkeypatch.setattr(mt, "_gemini_client", lambda: fake_client)
    monkeypatch.setattr(mt, "_resolve_media_path", lambda raw: (img, None))

    out = mt._tool_inspect_image({"path": str(img), "question": "what color?"})

    assert "a red square" in out
    assert gate_calls and gate_calls[0][0] == "what color?"
    assert gate_calls[0][1] == "google-gemini"
    assert ledger_calls and ledger_calls[0][1] == "inspect_image.image"
    assert ledger_calls[0][2]["byte_len"] == len(img.read_bytes())
