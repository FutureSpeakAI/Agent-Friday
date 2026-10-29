"""Friday says plainly how her folder on this PC and a Friday Bundle are
protected, and how they differ (north star §8.5-3): the Windows account
guards the folder, the passphrase guards a bundle, and a scheduled bundle's
passphrase sits with the account. The Doctor page and `friday bundle` say the
same sentences."""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _lines():
    from agent_friday.services import friday_bundle as fb
    assert getattr(fb, "PROTECTION_LINES", None), "Friday never says how a bundle and the folder differ"
    return fb.PROTECTION_LINES


def test_the_three_sentences_name_who_can_read_what():
    folder, bundle, scheduled = _lines()
    assert "Windows account" in folder and "administrator" in folder
    assert "passphrase" in bundle and "not even someone signed in as you" in bundle
    assert "credential store" in scheduled and "can open those" in scheduled


def test_the_doctor_page_says_the_same_sentences():
    lines = _lines()
    page = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    m = re.search(r'<ul id="bundle-protection">(.*?)</ul>', page, re.S)
    assert m, "the Doctor page has no 'who can read what'"
    items = [_html.unescape(re.sub(r"\s+", " ", x).strip()) for x in re.findall(r"<li>(.*?)</li>", m.group(1), re.S)]
    assert items == list(lines)


def test_friday_bundle_says_it_after_making_one():
    _lines()
    src = (ROOT / "src" / "agent_friday" / "cli.py").read_text(encoding="utf-8")
    i = src.index("Keep the passphrase: without it the bundle cannot be opened.")
    assert "for line in fb.PROTECTION_LINES" in src[i:i + 300]
