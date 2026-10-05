"""The 3D engine, its record sources and the Library shelves speak the brand.

A static scan: no refusal colour as decoration, no colour the engine's BRAND
table or the --fr-* tokens do not own in the Library's file, no magenta Delete
button, and a BRAND table that equals the tokens it names.

The two older files still carry a fixed set of literal colours (card tints, text
greys); LEGACY is that set and may only shrink. A new literal fails the scan.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE = ROOT / "static" / "studio_files3d.js"
RECORDS = ROOT / "static" / "friday3d_records.js"
SHELVES = ROOT / "static" / "library_shelves.js"
#: The one browser over the engine (every workspace's 3D view) keeps the same rule.
ONE = ROOT / "static" / "friday_files3d.js"
FILES = (ENGINE, RECORDS, SHELVES, ONE)

HEX = re.compile(r"(?<![\w$])(?:0x([0-9a-fA-F]{6})|#([0-9a-fA-F]{6})|#([0-9a-fA-F]{3}))(?![\w])")

LEGACY = set("""
#000103 #00d4ff #00ff80 #02040a #02050b #03050d #03060d #0b0f18 #0b1220 #16213a #24406a #2b4470 #2e5a8f #2ed3b7
#3d7fd0 #3fa9ff #4ecdc4 #5b9dff #5fa8ff #5fd068 #66758c #6f86a6 #6f8fbf #6fb6ff #7b61ff #7de1ff #7f93ad #8c7cff
#8fa3bf #8fa6c4 #8fb2dd #8fd3ff #9aa0b8 #9aa4b2 #9ab #9fb0c8 #9fb6d6 #9fd0ff #9fe6ff #a9bbd4 #a9c8ff #b0ffc8
#b8c7dc #c6d6ea #c77dff #cfe0f5 #cfe3ff #dbe8fa #e6f0ff #e6f6ff #e6f9ff #e8fbff #ef4444 #f59e0b #f5d76e #feca57
#ff00ff #ff4fa3 #ff5fa2 #ff6b6b #ff8a8a #ff8bcb #ff9a9a #ff9f43 #ffb4b4 #ffd1ea #ffd699 #fff #ffffff
""".split())


def _literals(path):
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for m in HEX.finditer(line):
            out.append((n, "#" + (m.group(1) or m.group(2) or m.group(3)).lower(), line))
    return out


def test_the_refusal_colour_is_never_decoration():
    for path in FILES:
        for n, lit, line in _literals(path):
            if lit == "#ff0080":
                # the one documented use is the refusal itself, spelled as its token
                assert "--fr-deny" in line, (path.name, n, line.strip()[:120])


def test_the_shimmer_triad_uses_the_magenta_token():
    text = ENGINE.read_text(encoding="utf-8")
    m = re.search(r"cyan:\s*0x([0-9a-f]{6}),\s*violet:\s*0x([0-9a-f]{6}),\s*magenta:\s*0x([0-9a-f]{6})", text, re.I)
    assert m, "the BRAND table is gone"
    tokens = dict(re.findall(r"--fr-([\w-]+):\s*(#[0-9a-fA-F]{6})", (ROOT / "index.html").read_text(encoding="utf-8")))
    assert m.group(1).lower() == tokens["cyan"][1:].lower()
    assert m.group(2).lower() == tokens["violet"][1:].lower()
    assert m.group(3).lower() == tokens["magenta"][1:].lower(), "the triad's last stop is --fr-magenta"
    brand = dict(re.findall(r"(amber|danger):\s*0x([0-9a-f]{6})", text, re.I))
    assert brand["amber"].lower() == tokens["warn"][1:].lower()
    assert brand["danger"].lower() == tokens["error"][1:].lower()


def test_no_hard_coded_colour_in_the_library_shelves_file():
    assert SHELVES.exists() and SHELVES.stat().st_size > 5000, "the Shelves build is the placeholder"
    found = _literals(SHELVES)
    assert found == [], [(n, lit) for n, lit, _ in found]


def test_older_files_add_no_colour_the_brand_does_not_own():
    for path in (ENGINE, RECORDS):
        bad = [(n, lit) for n, lit, _ in _literals(path) if lit not in LEGACY]
        assert bad == [], (path.name, bad)


def test_a_delete_button_is_error_red_not_magenta():
    for path in FILES:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "btn-magenta" in line:
                assert not re.search(r"delete", line, re.I), (path.name, n, line.strip()[:140])
    line = next(ln for ln in ENGINE.read_text(encoding="utf-8").splitlines() if "'Delete…'" in ln)
    assert "var(--fr-error)" in line and "btn-magenta" not in line


def test_the_shelves_file_has_no_figure_in_it():
    text = SHELVES.read_text(encoding="utf-8").lower()
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    for word in ("avatar", "face", "eye", "eyes", "hand", "hands", "finger", "pointing"):
        assert not re.search(r"\b" + word + r"\b", code), word
    assert "amber" not in code and "f59e0b" not in code
