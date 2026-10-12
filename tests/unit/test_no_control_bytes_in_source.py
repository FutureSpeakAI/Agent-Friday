"""No source file carries a stray control byte.

A regex written through a shell heredoc can lose its backslash, so `\b` lands in
the file as a literal backspace (0x08) and the pattern silently matches nothing.
This scans the tree's own source and tests for the bytes that only arrive that way.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BAD = {0x07: "bell", 0x08: "backspace", 0x0B: "vertical tab", 0x0C: "form feed"}
SUFFIXES = {".py", ".js", ".json", ".html"}


def test_no_source_or_test_file_carries_a_stray_control_byte():
    found = []
    for base in ("src", "tests", "static", "scripts", "tools", "ui_parts"):
        for path in (ROOT / base).rglob("*"):
            if path.suffix not in SUFFIXES or not path.is_file() or "vendor" in path.parts:
                continue
            data = path.read_bytes()
            for byte, name in BAD.items():
                if bytes([byte]) in data:
                    found.append("%s: %s" % (path.relative_to(ROOT), name))
    for name in ("index.html",):
        data = (ROOT / name).read_bytes()
        found += ["%s: %s" % (name, label) for byte, label in BAD.items() if bytes([byte]) in data]
    assert not found, found
