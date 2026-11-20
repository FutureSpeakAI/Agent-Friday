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


def _page(tmp_path, monkeypatch, text):
    page = tmp_path / "ledger.html"
    page.write_text(text, encoding="utf-8")
    monkeypatch.setattr(guard, "PAGE", page)


def test_a_claim_on_the_page_fails_the_guard(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, '<section id="evidence-pack"><p>%s</p><p>Fully compliant.</p></section>' % guard.SENTENCE)
    assert any("static/ledger.html" in p and "ompliant" in p for p in guard.run())


def test_a_page_without_the_fixed_sentence_or_the_section_fails_the_guard(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, '<section id="evidence-pack"><p>Export it.</p></section>')
    assert any("fixed sentence is missing" in p and "ledger.html" in p for p in guard.run())
    _page(tmp_path, monkeypatch, "<p>nothing</p>")
    assert any("section is not found" in p for p in guard.run())
