"""Unit tests for security-boundary.md §19 row 8: `elevenlabs_tools.py`'s
`speak_text` sent caller-supplied text to ElevenLabs with no gate call —
spoken text is egress like any other text field.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services import elevenlabs_tools as el
from agent_friday.services import egress_gate

TIER3_TEXT = "read this aloud: my SSN is 123-45-6789 and my bank routing is 021000021"  # pragma: allowlist secret


class TestSpeakTextQuestionFailsClosed:
    def test_never_send_blocked_prevents_the_tts_call(self, monkeypatch):
        # The test suite runs under FRIDAY_TESTING=1, which speak_text itself
        # short-circuits BEFORE the gate — bypass that here, or a passing
        # assertion below would be vacuous (nothing ever reaches _request
        # either way) rather than evidence the gate actually blocked it.
        monkeypatch.delenv("FRIDAY_TESTING", raising=False)
        calls = {"request": 0}
        monkeypatch.setattr(el, "_api_key", lambda: "fake-key")
        monkeypatch.setattr(el, "_request",
                            lambda *a, **k: calls.__setitem__("request", calls["request"] + 1) or (None, "should not be called"))
        monkeypatch.setattr(
            egress_gate, "_gate_text",
            lambda *a, **k: (_ for _ in ()).throw(
                egress_gate.NeverSendBlocked("blocked for a test")))

        out = el._tool_speak_text({"text": TIER3_TEXT})

        assert calls["request"] == 0, (
            "a never-send verdict must prevent the ElevenLabs call entirely"
        )
        assert TIER3_TEXT not in out

    def test_generic_gate_failure_prevents_the_tts_call(self, monkeypatch):
        monkeypatch.delenv("FRIDAY_TESTING", raising=False)
        calls = {"request": 0}
        monkeypatch.setattr(el, "_api_key", lambda: "fake-key")
        monkeypatch.setattr(el, "_request",
                            lambda *a, **k: calls.__setitem__("request", calls["request"] + 1) or (None, "should not be called"))
        monkeypatch.setattr(egress_gate, "_gate_text",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

        el._tool_speak_text({"text": TIER3_TEXT})
        assert calls["request"] == 0


def test_ungated_speak_text_reproduction_is_falsifiable():
    """Reproduce the pre-fix shape: `text` passed straight into the request
    body with no gate call anywhere in the module."""
    def _old_no_gate(text):
        return {"text": text, "model_id": "x"}

    leaked = _old_no_gate(TIER3_TEXT)
    assert TIER3_TEXT in leaked["text"], (
        "the reproduction should leak the raw text; it did not, so this "
        "comparison is not meaningful"
    )
