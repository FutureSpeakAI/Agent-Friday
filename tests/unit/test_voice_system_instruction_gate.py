"""Unit tests for security-boundary.md §19 row 1: the Gemini Live system
instruction egressed to Google with NO text-gate call of any kind, protected
only by the (currently off, Stephen's deliberate posture per e1f1874)
vault-assembly gate. With `vault_local_only: false`, `_get_vault_control()`
returns None, `_get_friday_system_prompt` assembles ungated, and the
resulting `sys_text` went straight into `system_instruction=` at
routes/voice.py:1786 — no `egress_gate` call stood between it and Google.

This mirrors the existing fail-closed pattern already proven for tool
results and typed live.text turns (test_voice_gate_fail_closed.py):
`_gate_voice_system_instruction` is FAIL-CLOSED — a NeverSendBlocked verdict
or any gate exception withholds rather than sends the raw prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.routes import voice as v
from agent_friday.services import egress_gate

TIER2_MARKER = "my custody hearing is on the 14th and my SSN is 123-45-6789"  # pragma: allowlist secret


class TestSystemInstructionFailsClosed:
    def test_never_send_blocked_withholds_not_passthrough(self, monkeypatch):
        def _raise_never_send(text, provider, field, log_path=None):
            raise egress_gate.NeverSendBlocked("synthetic never-send block for a test")
        monkeypatch.setattr(egress_gate, "_gate_text", _raise_never_send)
        out = v._gate_voice_system_instruction(TIER2_MARKER)
        assert TIER2_MARKER not in out, (
            "a NeverSendBlocked verdict must withhold the system instruction, "
            "not pass the raw context prompt through to Gemini"
        )

    def test_generic_gate_exception_withholds_not_passthrough(self, monkeypatch):
        def _raise_generic(text, provider, field, log_path=None):
            raise RuntimeError("synthetic gate failure for a test")
        monkeypatch.setattr(egress_gate, "_gate_text", _raise_generic)
        out = v._gate_voice_system_instruction(TIER2_MARKER)
        assert TIER2_MARKER not in out

    def test_normal_allow_still_reaches_the_model(self, monkeypatch):
        monkeypatch.setattr(egress_gate, "_gate_text",
                            lambda text, provider, field, log_path=None: text)
        out = v._gate_voice_system_instruction("what's the weather like today")
        assert out == "what's the weather like today"

    def test_gate_is_actually_called_with_gemini_provider(self, monkeypatch):
        """The function must not be a no-op: it has to hand the text to the
        real gate, naming the cloud provider, or an ungated string could
        satisfy the tests above by accident (e.g. a function that always
        returns its input unchanged)."""
        calls = []

        def _record(text, provider, field, log_path=None):
            calls.append((text, provider, field))
            return text

        monkeypatch.setattr(egress_gate, "_gate_text", _record)
        v._gate_voice_system_instruction(TIER2_MARKER)
        assert len(calls) == 1
        text, provider, field = calls[0]
        assert text == TIER2_MARKER
        assert provider == "google-gemini"
        assert field == "voice_system_instruction"


def test_ungated_send_reproduction_is_falsifiable(monkeypatch):
    """Reproduce the EXACT pre-fix shape at routes/voice.py:1786 — sys_text
    assigned straight from the (possibly vault-ungated) context prompt with
    no gate call at all — against the same neutralised gate the tests above
    use, and confirm it WOULD leak. If this stops leaking, the tests above
    are not actually distinguishing gated from ungated behavior.
    """
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda text, provider, field, log_path=None:
                            (_ for _ in ()).throw(
                                egress_gate.NeverSendBlocked("would have blocked")))

    def _old_ungated_assignment(system_instruction, live_style):
        # This is routes/voice.py:1779-1786 before the fix: sys_text is
        # assigned directly from the (possibly ungated) system_instruction
        # with no gate call anywhere in between.
        sys_text = system_instruction
        if live_style:
            sys_text = f"Speaking style: {live_style}\n\n{sys_text}"
        return sys_text  # -> straight into types.Content(parts=[types.Part(text=sys_text)])

    leaked = _old_ungated_assignment(TIER2_MARKER, "")
    assert TIER2_MARKER in leaked, (
        "the reproduction of the pre-fix code path should leak the marker "
        "straight to Gemini; it did not, so this file's assertions are not "
        "actually exercising the gated/ungated distinction"
    )
