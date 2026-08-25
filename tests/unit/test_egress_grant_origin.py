"""WO-17 non-negotiable acceptance: every grant-passed span is attributable
to its grant id in the egress log — both when a granted file passes whole,
and when it is one surviving paragraph among withheld siblings (the span-wise
partial-rescue path, which used to log no origin at all for a rescued
paragraph — only the aggregate "N trusted" count)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services.egress_gate import _gate_text


@pytest.fixture(autouse=True)
def _clean_registries():
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
        eg._OVERRIDE_PARAS.clear()
        eg._PROVIDER_ECHO.clear()
    yield
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
        eg._OVERRIDE_PARAS.clear()
        eg._PROVIDER_ECHO.clear()


def _read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class TestWholeFieldGrantOrigin:
    def test_allow_line_carries_the_grant_id(self, tmp_path):
        log = tmp_path / "egress.jsonl"
        text = "This resume covers senior AI leadership experience."
        eg.register_public_text(text, origin="user-grant:abc123")

        out = _gate_text(text, "anthropic", "tool_result", log_path=log)

        assert out == text
        entries = _read_log(log)
        allow = [e for e in entries if e["action"] == "allow"]
        assert any("user-grant:abc123" in e["reason"] for e in allow)


class TestSpanWiseGrantOrigin:
    def test_a_surviving_grant_paragraph_is_individually_attributed(self, tmp_path):
        """The bug this closes: a partial (span-level) rescue only ever logged
        an aggregate 'N trusted' count with no origin at all — attributable to
        nothing. A grant-origin paragraph inside a multi-paragraph field must
        get its own ALLOW line naming the grant."""
        log = tmp_path / "egress.jsonl"
        granted_para = "Sanofi pivot analysis: strong fit for the role."
        private_para = "todo: call the accountant about the account balance"
        eg.register_public_text(granted_para, origin="user-grant:cv-77")

        payload = granted_para + "\n\n" + private_para
        out = _gate_text(payload, "anthropic", "tool_result", log_path=log)

        assert granted_para in out
        assert private_para not in out
        entries = _read_log(log)
        grant_lines = [e for e in entries
                       if e["action"] == "allow" and "user-grant:cv-77" in e["reason"]]
        assert grant_lines, f"no origin-attributed ALLOW line found in {entries}"


class TestProviderEchoOrigin:
    # A payload that unambiguously does NOT classify PUBLIC on its own — the
    # SSN regex layer is deterministic (no embedding model needed) — so a
    # pass here is evidence of the echo mechanism, not of the text merely
    # being harmless anyway.
    ANALYSIS = ("Reviewing the resume: the SSN listed is 123-45-6789 and the "  # pragma: allowlist secret
                "candidate should redact it before wider distribution.")

    def test_echo_registered_text_is_attributed_on_replay(self, tmp_path):
        log = tmp_path / "egress.jsonl"
        eg.register_provider_echo(self.ANALYSIS, "anthropic")

        out = _gate_text(self.ANALYSIS, "anthropic", "message[3].content", log_path=log)

        assert out == self.ANALYSIS
        entries = _read_log(log)
        assert any(e["action"] == "allow" and "provider-echo:anthropic" in e["reason"]
                   for e in entries)

    def test_echo_does_not_cross_providers(self):
        """The whole point: what Anthropic produced is replayable to
        Anthropic, not to a different cloud provider it never saw."""
        eg.register_provider_echo(self.ANALYSIS, "anthropic")

        out = _gate_text(self.ANALYSIS, "openai", "message[3].content")

        assert self.ANALYSIS not in out

    def test_without_registration_the_same_text_is_still_withheld(self):
        """Falsifiability: prove the payload alone is not sufficient — it
        must actually be registered, for the actual provider, to pass."""
        out = _gate_text(self.ANALYSIS, "anthropic", "message[3].content")
        assert self.ANALYSIS not in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
