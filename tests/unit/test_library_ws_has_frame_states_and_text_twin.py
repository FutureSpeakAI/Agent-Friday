"""The Library workspace: registered, mounted in both page files, drawn from tokens,
with the states, keys and text twin the guidelines ask for, and footnote chips that
carry through both page files."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WS = (ROOT / "static" / "library_ws.js").read_text(encoding="utf-8")
READER = (ROOT / "static" / "library_reader.js").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
SCENE = (ROOT / "ui_parts" / "styles_and_scene.html").read_text(encoding="utf-8")


def _registry() -> dict:
    t = (ROOT / "static" / "workspace_registry.js").read_text(encoding="utf-8")
    a, b = t.index("/*BEGIN JSON*/") + len("/*BEGIN JSON*/"), t.index("/*END JSON*/")
    return json.loads(t[a:b])


def test_the_library_is_a_registered_workspace_with_an_icon_and_unique_names():
    reg = _registry()
    ws = {w["id"]: w for w in reg["workspaces"]}
    lib = ws["library"]
    assert lib["label"] == "Library" and lib["accent"] == "cyan" and (ROOT / "assets" / "icons" / "library.svg").is_file()
    assert "library" not in ws["media"]["aliases"], "Media keeps creations; the word 'library' belongs to the Library"
    owners: dict[str, set[str]] = {}
    for w in reg["workspaces"]:
        for name in [w["label"].lower()] + [x.lower() for x in w["aliases"]]:
            owners.setdefault(name, set()).add(w["id"])
    assert all(len(v) == 1 for v in owners.values()), {k: v for k, v in owners.items() if len(v) > 1}


def test_both_page_files_load_and_mount_it():
    for text in (INDEX, SCENE):
        assert text.index('/static/library_reader.js') < text.index('/static/library_ws.js') < text.index('/static/library_shelves.js')
        assert text.index('/static/media_ws.js') < text.index('/static/library_reader.js')
    assert "window.LibraryWS" in INDEX and "window.LibraryWS" in APP
    assert "library:" in INDEX[INDEX.index("const wsMap"):INDEX.index("const wsMap") + 4000]
    assert "library:window.LibraryWS" in APP


def test_the_footnote_chip_is_in_both_page_files_with_its_attributes_and_shift_click():
    for text in (INDEX, APP):
        assert "data-lib-doc" in text and "data-lib-block" in text
        assert re.search(r"\\\[lib:\(\\d\+\)#\(\\d\+\)\\\]", text), "the [lib:doc#block] replace"
        assert "fridayOpenLibrary" in text and "e.shiftKey" in text
        assert "unverified-lib" in text
        attrs = next(ln for ln in text.splitlines() if "FRIDAY_MD_ATTRS = new Set" in ln)
        assert "'data-lib-doc'" in attrs and "'data-lib-block'" in attrs
        assert re.search(r"web\|unverified-web\|lib\|unverified-lib", text), "the speech strip"


def test_the_states_the_guidelines_name_are_all_there():
    def has(words: str) -> bool:
        return words in WS or words.encode("ascii", "backslashreplace").decode("unicode_escape") in WS

    for words in ("Your Library is empty.", "Add a folder and Friday will read it here, on this PC.", "Add a folder",
                  "Looking\\u2026", "Couldn\\u2019t read", "Try again", "Everything added was read.",
                  "Indexed on this PC", "nothing sent", "How I looked", "Forget everywhere", "Remove from Library"):
        assert has(words), words
    assert "being read" in WS and has("couldn\u2019t be read")


def test_it_has_the_frame_the_workspaces_share():
    assert "lb-root ws-fill" in WS
    assert "role: 'tree'" in WS and "aria-label" in WS
    assert "lb-twin" in WS and "Your Library as a list" in WS
    for key in ("'/'", "Escape"):
        assert key in WS
    assert "aria-pressed" in WS and "role: 'status'" in WS


def test_nothing_shows_a_file_path_as_a_label():
    for banned in ("row.path", "doc.path", "d.path", "detail.path", "n.path", "scope.path"):
        assert banned not in WS, banned


def test_colours_come_from_tokens_only():
    for name, src in (("library_ws.js", WS), ("library_reader.js", READER)):
        css = src[src.index("const CSS"):src.index("`;", src.index("const CSS"))] if "const CSS" in src else ""
        hexes = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css))
        assert hexes <= {"#fff"}, (name, hexes)
        assert "var(--fr-" in css
    assert "amber" not in WS.lower() and "--fr-warn" not in WS


def test_motion_is_a_real_event_and_reduced_motion_is_gentler_not_absent_here():
    assert "prefers-reduced-motion" in WS and "prefers-reduced-motion" in READER
    assert "setInterval" in WS and "status.reading" in WS            # polling only while documents are being read
    assert "animation:" not in WS.replace("animation:none", "")      # nothing pulses while waiting


def test_the_navigation_the_dock_palette_and_voice_use_is_declared():
    assert "__fridayNavDecls" in WS and "['library'" in WS
    for k in ("'view'", "'lib'", "'q'"):
        assert k in WS
    assert "friday-nav" in WS and "useTabState" in WS or "fridayUseTabState" in WS


def test_the_ask_screen_asks_before_it_writes_an_answer_on_a_small_pc():
    assert "Write an answer" in WS and "Written here by your local model" in WS
    assert "&answer=1" in WS
