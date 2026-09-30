"""The podcast UI wears Friday's brand from the shared tokens (docs/brand/BRAND.md).

Every colour, face and size in the podcast block is a `--fr-*` token; the
only literal colours are each token's own value as its var() fallback (and a
drop shadow). Amber never appears: it means "needs you". The block is the
same in index.html and its ui_parts/app.html mirror.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START = "// ═══ PODCASTS — player, Studio view, News chip"
END = "const NEWS_PODCAST_ROUTINE = {"


def _block(rel: str) -> str:
    s = (ROOT / rel).read_text(encoding="utf-8")
    assert s.count(START) == 1, rel
    return s[s.index(START):s.index(END)]


def test_the_block_is_identical_in_the_page_and_its_mirror():
    assert _block("index.html") == _block("ui_parts/app.html")


def test_colours_are_brand_tokens_only():
    b = _block("index.html")
    without_fallbacks = re.sub(r"var\(--fr-[a-z0-9-]+, [^()']*(?:\([^)]*\))?[^()']*\)", "var()", b)
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", without_fallbacks)
    assert [x for x in literals if not x.startswith("rgba(0,0,0")] == []


def test_amber_is_never_decoration_here():
    b = _block("index.html").lower()
    assert "#f59e0b" not in b and "--fr-warn" not in b and "--fr-wordmark-amber" not in b


def test_type_comes_from_the_brand_faces():
    b = _block("index.html")
    for token in ("--fr-font-body", "--fr-font-display", "--fr-font-mono"):
        assert token in b
    assert not re.search(r"fontSize: \d", b), "sizes use the --fr-text-* scale"
