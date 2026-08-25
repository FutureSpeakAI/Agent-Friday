"""Unit tests for WO-2 item 1 (2026-08-25): the voice egress gate must fail
CLOSED, not open.

Before this fix, `routes/voice.py`'s tool-result and live-text egress paths
wrapped `egress_gate._gate_text` in a single broad `except Exception`. Since
`_gate_text` raises `NeverSendBlocked` for never-send material BY DESIGN (see
its docstring in services/egress_gate.py — this is the gate's STRONGEST
verdict, not a bug), that exception landed in the broad except, which logged
a warning and left the pre-gate, UNGATED payload to be sent to Google. The
gate's strongest verdict was exactly the case that bypassed it.

The fix extracted the gating logic into two module-level, directly testable
functions: `_gate_voice_tool_result` (tool results → Gemini) and
`_gate_voice_text` (typed live.text turns → Gemini). Both are FAIL-CLOSED:
every exception, including NeverSendBlocked, returns a withheld placeholder,
never the raw payload.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.routes import voice as v
from agent_friday.services import egress_gate

SECRET = "the vault's never-send token lives here: XYZZY-NEVER-SEND"


def _raise_never_send(text, provider, field, log_path=None):
    raise egress_gate.NeverSendBlocked("synthetic never-send block for a test")


def _raise_generic(text, provider, field, log_path=None):
    raise RuntimeError("synthetic gate failure for a test")


# ── Tool results (routes/voice.py:_run_tool_calls) ─────────────────────────
class TestToolResultFailsClosed:
    def test_never_send_blocked_withholds_not_passthrough(self, monkeypatch):
        monkeypatch.setattr(egress_gate, "_gate_text", _raise_never_send)
        out = v._gate_voice_tool_result(SECRET, "read_file")
        assert SECRET not in out, (
            "a NeverSendBlocked verdict must withhold the tool result, not "
            "pass the raw payload through to Gemini"
        )
        assert out == egress_gate._TOOL_RESULT_WITHHELD

    def test_generic_gate_exception_withholds_not_passthrough(self, monkeypatch):
        monkeypatch.setattr(egress_gate, "_gate_text", _raise_generic)
        out = v._gate_voice_tool_result(SECRET, "read_file")
        assert SECRET not in out, (
            "any gate failure must withhold the tool result, not pass the "
            "raw payload through to Gemini"
        )

    def test_normal_allow_still_reaches_the_model(self, monkeypatch):
        # Sanity: the fix must not turn the gate into an unconditional
        # blackhole — ordinary allowed content still passes, which is what
        # makes the withholding assertions above meaningful.
        monkeypatch.setattr(egress_gate, "_gate_text",
                            lambda text, provider, field, log_path=None: text)
        out = v._gate_voice_tool_result("today's weather is sunny", "search_news")
        assert out == "today's weather is sunny"


# ── Typed live.text turns ───────────────────────────────────────────────────
class TestLiveTextFailsClosed:
    def test_never_send_blocked_withholds_not_passthrough(self, monkeypatch):
        monkeypatch.setattr(egress_gate, "_gate_text", _raise_never_send)
        out = v._gate_voice_text(SECRET)
        assert SECRET not in out
        assert out == v._LIVE_TEXT_WITHHELD

    def test_generic_gate_exception_withholds_not_passthrough(self, monkeypatch):
        monkeypatch.setattr(egress_gate, "_gate_text", _raise_generic)
        out = v._gate_voice_text(SECRET)
        assert SECRET not in out

    def test_normal_allow_still_reaches_the_model(self, monkeypatch):
        monkeypatch.setattr(egress_gate, "_gate_text",
                            lambda text, provider, field, log_path=None: text)
        out = v._gate_voice_text("what's on my calendar today")
        assert out == "what's on my calendar today"


# ── Falsifiability ──────────────────────────────────────────────────────────
def test_fail_open_reproduction_is_falsifiable(monkeypatch):
    """Reproduce the EXACT shape of the original N-1 bug (a broad `except`
    that leaves `result` at its pre-gate value) against the SAME neutralised
    gate the tests above use, and confirm it WOULD have leaked. If this stops
    leaking, the tests above are not actually distinguishing fail-open from
    fail-closed behavior — they would pass even against the old code.
    """
    monkeypatch.setattr(egress_gate, "_gate_text", _raise_never_send)

    def _old_buggy_gate(result, fname):
        try:
            gated = egress_gate._gate_text(
                result, "google-gemini", f"live.tool.{fname}")
            if result and not gated:
                gated = egress_gate._TOOL_RESULT_WITHHELD
            result = gated
        except Exception:
            pass  # the original bug: `result` keeps its pre-gate value
        return result

    leaked = _old_buggy_gate(SECRET, "read_file")
    assert SECRET in leaked, (
        "the reproduction of the original fail-open bug should leak the "
        "secret; it did not, so this file's assertions are not actually "
        "exercising the fail-open/fail-closed distinction"
    )
