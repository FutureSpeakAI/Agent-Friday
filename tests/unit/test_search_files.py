"""WO-14 / WO-16 Stage A — search_files: local file discovery.

THE REPORTED FAILURE (voice session 2026-08-25, 09:18): Stephen asked Friday
to find his resume in Downloads. She guessed `resume.pdf`, it did not exist,
and she asked him for the exact name — there was no discovery verb anywhere
in the registry, only read-by-exact-path. Every test here is written against
that gap and fails without search_files.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import file_search as fs


@pytest.fixture
def roots(tmp_path, monkeypatch):
    documents = tmp_path / "Documents"
    downloads = tmp_path / "Downloads"
    desktop = tmp_path / "Desktop"
    creations = tmp_path / "creations"
    vault = tmp_path / ".friday" / "vault"
    for d in (documents, downloads, desktop, creations, vault):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(fs, "_home_roots", lambda: {
        "documents": documents, "downloads": downloads,
        "desktop": desktop, "creations": creations,
    })
    monkeypatch.setattr(fs, "_configured_roots", lambda: {
        "documents": documents, "downloads": downloads,
        "desktop": desktop, "creations": creations,
    })
    monkeypatch.setattr(fs, "_vault_root", lambda: vault)
    return {"documents": documents, "downloads": downloads,
            "desktop": desktop, "creations": creations, "vault": vault}


class TestNameSearch:
    def test_finds_the_real_file_the_09_18_failure_needed(self, roots):
        """The exact replay of the reported failure: she guessed
        'resume.pdf', which did not exist. Name search finds the real file
        by any substring it actually contains ('cv'); Stage A is substring/
        fuzzy filename matching, not a synonym dictionary for 'resume'."""
        real = roots["downloads"] / "Webster_Stephen_CV_cyanotype_magenta_v2.pdf"
        real.write_bytes(b"%PDF-1.4\n%%EOF")

        result = fs.search_files(query="cv", root="downloads")

        names = [r["name"] for r in result["results"]]
        assert real.name in names

    def test_blank_query_lists_the_newest_file_without_a_name(self, roots):
        """The other half of the 09:18 failure: Stephen should not have had
        to know ANY part of the filename — 'the latest thing in Downloads'
        must work with no query at all."""
        real = roots["downloads"] / "Webster_Stephen_CV_cyanotype_magenta_v2.pdf"
        real.write_bytes(b"%PDF-1.4\n%%EOF")

        result = fs.search_files(query="", root="downloads")

        names = [r["name"] for r in result["results"]]
        assert real.name in names

    def test_no_query_lists_a_roots_newest_files(self, roots):
        for i in range(3):
            p = roots["desktop"] / f"note{i}.txt"
            p.write_text("x")
        result = fs.search_files(query="", root="desktop")
        assert len(result["results"]) == 3

    def test_unknown_root_gets_an_honest_message_naming_the_remedy(self, roots):
        result = fs.search_files(query="anything", root="C:/nonexistent/nowhere")
        assert result["results"] == []
        assert "error" in result
        assert "file_search_roots" in result["error"] or "Settings" in result["error"]


class TestVaultExclusion:
    def test_vault_files_never_appear_in_any_result(self, roots):
        secret = roots["vault"] / "resume_and_ssn.md"
        secret.write_text("never should surface")

        result = fs.search_files(query="resume")

        paths = [r["path"] for r in result["results"]]
        assert not any("vault" in p.lower() for p in paths)

    def test_vault_note_is_always_present_and_honest(self, roots):
        (roots["vault"] / "doc1.md").write_text("x")
        (roots["vault"] / "doc2.md").write_text("x")

        result = fs.search_files(query="anything")

        assert result["vault"]["searched"] is False
        assert result["vault"]["document_count"] == 2


class TestContentSearch:
    def test_finds_the_phrase_inside_a_markdown_file(self, roots):
        p = roots["documents"] / "career_notes.md"
        p.write_text("Some header\n\nI keep thinking about the Sanofi pivot "
                      "and whether it's the right move.\n\nMore text.")

        result = fs.search_files(content_query="Sanofi pivot")

        assert len(result["results"]) == 1
        assert "Sanofi pivot" in result["results"][0]["snippet"]

    def test_content_search_is_hollow_for_undecodable_binary_today(self, roots, monkeypatch):
        """Documents WO-14.1's known limitation: content search only works on
        what extract_text() can read. A binary file with no extractor never
        matches, which is the honest (not silently-wrong) behaviour."""
        p = roots["documents"] / "scan.bin"
        p.write_bytes(bytes(range(256)) * 5)

        result = fs.search_files(content_query="anything")

        assert result["results"] == []


class TestPartialResultsUnderBudget:
    def test_exceeding_the_scan_budget_returns_a_receipt_not_silence(self, roots, monkeypatch):
        monkeypatch.setattr(fs, "_MAX_FILES_SCANNED", 3)
        for i in range(10):
            (roots["documents"] / f"f{i}.txt").write_text("x")

        result = fs.search_files(query="")

        assert result["truncated"] is True
        assert "receipt" in result
        assert str(result["scanned"]) in result["receipt"]


class TestJSONShapeThroughTheStandardGate:
    """WO-16's stated privacy design: search_files does its OWN gating for
    nothing — results ride the same field-wise JSON gate every tool result
    already passes through. These tests prove that pipe actually produces the
    anti-over-gating property the spec requires, not just that search_files
    returns plausible-looking JSON."""

    def test_paths_pass_a_cloud_seat_and_a_trip_wire_name_becomes_a_marker_not_a_missing_row(self, roots):
        from agent_friday.services.egress_gate import _gate_text

        ordinary = roots["documents"] / "resume_draft.txt"
        ordinary.write_text("x")
        # A regex-detectable hard identifier (Layer 1a) — deterministic, so
        # it trips even diluted inside a larger JSON blob, unlike a purely
        # keyword/embedding signal which this test found gets diluted away
        # by the surrounding neutral path/size/mtime fields.
        sensitive = roots["documents"] / "backup notes 123-45-6789.txt"  # pragma: allowlist secret
        sensitive.write_text("x")

        result = fs.search_files(query="")
        payload = json.dumps(result)

        out = _gate_text(payload, "anthropic", "tool_result")
        parsed = json.loads(out)

        names = [r["name"] for r in parsed["results"]]
        # The ordinary path survives ungated (anti-over-gating).
        assert "resume_draft.txt" in names
        # The row for the trip-wire name still exists — count stays true —
        # but its content is a marker naming the gate, not silently gone.
        assert len(parsed["results"]) == 2
        assert any("withheld by egress gate" in (r.get("name") or "") for r in parsed["results"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
