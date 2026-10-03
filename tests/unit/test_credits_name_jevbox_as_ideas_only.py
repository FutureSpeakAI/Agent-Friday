"""jevbox has no licence, so the Library is built from its ideas alone and the
credit says so: it names jevbox, says the ideas are what was used, and that no
code, prompt, wording, icon or asset was."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _entry() -> str:
    text = (ROOT / "CREDITS.md").read_text(encoding="utf-8")
    m = re.search(r"^### jevbox[^\n]*\n(.*?)(?=^### |^## )", text, re.S | re.M)
    assert m, "CREDITS.md has no jevbox entry"
    return re.sub(r"\s+", " ", m.group(0))


def test_the_credit_names_jevbox_and_its_missing_licence():
    e = _entry()
    assert "github.com/extend-hq/jevbox" in e
    assert "No licence published" in e


def test_the_credit_says_ideas_only():
    e = _entry()
    assert "Ideas from jevbox" in e
    for word in ("no code", "prompt", "wording", "icon", "asset"):
        assert word in e, word
    assert "written from a description of the ideas" in e


def test_no_jevbox_source_or_text_is_in_the_shipped_tree():
    """Nothing under src or static quotes a jevbox file name or copies its
    documented question wording."""
    banned = ("jev.ts", "beam-search.ts", "answer-policy.ts", "splitPassages", "Nucleo")
    hits = []
    for base in ("src", "static"):
        for p in (ROOT / base).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".js", ".ts", ".html", ".md"} and "vendor" not in p.parts:
                t = p.read_text(encoding="utf-8", errors="ignore")
                hits += [(str(p.relative_to(ROOT)), b) for b in banned if b in t]
    assert not hits, hits
