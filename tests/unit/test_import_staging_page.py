"""The review list for imported items is on the Contacts page, in both copies of the page.

index.html is what runs; ui_parts/app.html is its source copy. The LinkedIn import now stages
its rows (services/import_staging), so the page must offer the answers, or nothing an import
brings in could ever be saved. On the old tree there is no review list, so these fail by
assertion.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


def _component(text: str) -> str:
    m = re.search(r"function StagedImportReview\(\{ source, refresh, onDone \}\) \{.*?\n\}\n", text, re.S)
    assert m, "there is no review list for imported items (StagedImportReview) in the page"
    return m.group(0)


def test_both_copies_carry_the_same_review_list():
    a, b = (_component(p.read_text(encoding="utf-8")) for p in FILES)
    assert a == b


def test_the_contacts_panel_shows_it_for_the_linkedin_import():
    idx = FILES[0].read_text(encoding="utf-8")
    src = FILES[1].read_text(encoding="utf-8")
    assert "e(StagedImportReview, { source: 'linkedin_csv'" in idx
    assert '<StagedImportReview source="linkedin_csv"' in src


def test_answers_go_through_the_pages_own_fetch_and_say_nothing_is_saved_first():
    comp = _component(FILES[0].read_text(encoding="utf-8"))
    assert "apiFetch('/api/import-staging/review'" in comp      # carries the desk key on this PC
    assert "Nothing is saved until you accept it." in comp
    for word in ("'Accept'", "'Reject'", "'Later'", "'Accept all '", "'Reject all'"):
        assert word in comp
