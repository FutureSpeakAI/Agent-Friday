"""The wording guard for the evidence pack (K6 test 8) passes on the tree and can fail."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_evidence_wording.py"
spec = importlib.util.spec_from_file_location("check_evidence_wording", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_the_tree_passes_the_guard():
    assert guard.run() == []


def test_a_certification_or_compliance_claim_is_found():
    for text in ("Friday is certified", "fully compliant with ISO", "ISO accredited export",
                 "a Compliance report", "our certification"):
        assert guard.findings("x", [text]), text


def test_the_fixed_sentence_is_the_only_exception_and_only_when_exact():
    assert guard.findings("x", [guard.SENTENCE]) == []
    assert guard.findings("x", [guard.SENTENCE.replace("is not a", "is a")])
    assert guard.findings("x", [guard.SENTENCE + " It is certified."])


def test_a_changed_or_missing_fixed_sentence_in_the_module_fails_the_guard(monkeypatch):
    monkeypatch.setattr(guard, "SENTENCE", guard.SENTENCE + " Changed.")
    assert any("fixed sentence" in p for p in guard.run())
