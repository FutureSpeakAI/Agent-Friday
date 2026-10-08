"""The brand has one source of truth, and the shipped surfaces read it.

Codify, never invent: every value asserted here is one the product already
shipped before the tokens existed. The reserved status hues do not move.
"""
from __future__ import annotations

import importlib.util
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI_FILES = ("index.html", "ui_parts/head.html")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def brand():
    return _load(ROOT / "src" / "agent_friday" / "brand.py", "_brand_under_test")


@pytest.fixture(scope="module")
def guard():
    return _load(ROOT / "scripts" / "check_brand_tokens.py", "_guard_under_test")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# -- the reserved hues are the shipped values ----------------------------------
def test_reserved_status_hues_are_the_shipped_values(brand):
    assert brand.OK == "#00ff80"
    assert brand.WARN == "#f59e0b"
    assert brand.DENY == "#ff0080"
    assert brand.ERROR == "#ef4444"
    assert brand.CYAN == "#00d4ff"
    assert brand.TRIAD == ("#00d4ff", "#7b61ff", "#ff00ff")
    assert brand.WORDMARK_AMBER == brand.WARN


def test_no_category_colour_is_invented(brand):
    """Scoped colours already in the UI are the only non-status hues tokenised."""
    shipped = {"#a78bfa", "#2dd4bf", "#f472b6", "#7a8699", "#60a5fa", "#d6c7a1"}
    hexes = {v.lower() for v in brand.TOKENS.values() if re.fullmatch(r"#[0-9a-fA-F]{6}", v)}
    known = {brand.CYAN, brand.VIOLET, brand.MAGENTA, brand.OK, brand.WARN, brand.DENY,
             brand.ERROR, brand.SURFACE}
    assert hexes - {h.lower() for h in known} == shipped


# -- the HTML block is generated, so it is byte-identical ----------------------
@pytest.mark.parametrize("rel", UI_FILES)
def test_html_token_block_is_byte_identical_to_the_generated_block(brand, rel):
    text = _read(rel)
    start = text.index(f"/* {brand.BEGIN_MARKER}")
    end = text.index(f"/* {brand.END_MARKER} */") + len(f"/* {brand.END_MARKER} */")
    assert text[start:end] == brand.css_root_block()


def test_the_two_ui_files_carry_the_same_block():
    blocks = []
    for rel in UI_FILES:
        text = _read(rel)
        blocks.append(text[text.index("brand-tokens:begin"):text.index("brand-tokens:end")])
    assert blocks[0] == blocks[1]


# -- the guard ------------------------------------------------------------------
def test_guard_passes_on_the_repository(guard):
    assert guard.find_problems(ROOT) == []


def test_guard_flags_a_drifted_block(guard, tmp_path):
    _stage_tree(tmp_path)
    p = tmp_path / "index.html"
    p.write_text(p.read_text(encoding="utf-8").replace("--fr-ok: #00ff80;", "--fr-ok: #00ff66;"),
                 encoding="utf-8")
    problems = guard.find_problems(tmp_path)
    assert any("index.html" in x and "differs" in x for x in problems), problems


def test_guard_flags_a_private_colour_table(guard, tmp_path):
    _stage_tree(tmp_path)
    p = tmp_path / "src" / "agent_friday" / "services" / "connectors.py"
    p.write_text(p.read_text(encoding="utf-8") + '\n_X = {"connected": "#22c55e"}\n', encoding="utf-8")
    problems = guard.find_problems(tmp_path)
    assert any("connectors.py" in x and "#22c55e" in x for x in problems), problems


def test_guard_flags_an_orbitron_that_falls_back_to_monospace(guard, tmp_path):
    _stage_tree(tmp_path)
    p = tmp_path / "index.html"
    p.write_text(p.read_text(encoding="utf-8") + "\n<style>.x{font-family:'Orbitron',monospace}</style>\n",
                 encoding="utf-8")
    problems = guard.find_problems(tmp_path)
    assert any("Orbitron" in x for x in problems), problems


def test_guard_flags_a_second_status_dot_definition(guard, tmp_path):
    _stage_tree(tmp_path)
    p = tmp_path / "index.html"
    p.write_text(p.read_text(encoding="utf-8") + "\n.status-dot { width:6px; }\n",
                 encoding="utf-8")
    problems = guard.find_problems(tmp_path)
    assert any("status-dot" in x for x in problems), problems


def _stage_tree(dst: Path) -> None:
    """A minimal copy of the files the guard reads."""
    import shutil
    for rel in ("index.html", "ui_parts/head.html", "ui_parts/app.html",
                "ui_parts/styles_and_scene.html", "src/agent_friday/brand.py",
                "src/agent_friday/core/__init__.py",
                "src/agent_friday/notifications_engine.py",
                "src/agent_friday/services/connectors.py",
                "src/agent_friday/services/ptt_indicator.py",
                "src/agent_friday/services/google_accounts.py",
                "src/agent_friday/services/showcase_engine.py"):
        src = ROOT / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst / rel)


# -- Python dicts read brand with meaning-correct roles ----------------------------
def test_connector_status_colours_follow_the_meaning(brand):
    from agent_friday.services import connectors as c
    m = c._STATUS_COLORS
    assert m["connected"] == brand.OK
    assert m["error"] == brand.ERROR            # a failure is error red
    assert m["blocked_by_policy"] == brand.DENY  # a refusal is deny magenta
    assert m["connecting"] == brand.VIOLET       # work in progress is violet
    assert m["connecting"] != brand.WARN         # amber only means "needs you"
    assert m["degraded"] == brand.WARN
    assert m["disconnected"] == brand.NEUTRAL == m["unknown"]
    assert m["needs_setup"] == brand.WARN


def test_notification_priority_colours_follow_the_meaning(brand):
    from agent_friday import notifications_engine as n
    p = n.PRIORITY_COLORS
    assert p["critical"] == brand.WARN
    assert p["high"] == brand.CYAN
    assert p["medium"] == brand.VIOLET_SOFT
    assert p["low"] == brand.NEUTRAL


def test_push_to_talk_colours_follow_the_meaning(brand):
    from agent_friday.services import ptt_indicator as p
    c = p._COLOR
    assert c["error"] == brand.ERROR
    assert c["recording"] == brand.CYAN
    assert c["done"] == brand.OK
    assert c["thinking"] == brand.VIOLET
    assert c["clipboard"] == brand.WARN
    assert c["idle"] == brand.NEUTRAL == c["arming"]


def test_account_palette_and_showcase_read_brand(brand):
    from agent_friday.services import google_accounts as g, showcase_engine as s
    assert tuple(g._PALETTE) == brand.ACCOUNT_PALETTE
    assert s._ACCENT == brand.CYAN and s._ACCENT2 == brand.VIOLET_SOFT
    assert (s._BG, s._PANEL, s._TEXT, s._MUTED) == (
        brand.PAGE_BG, brand.PAGE_PANEL, brand.PAGE_TEXT, brand.PAGE_MUTED)


# -- the UI ------------------------------------------------------------------------
def _resolve(value: str, tokens: dict) -> str:
    m = re.fullmatch(r"var\((--fr-[\w-]+)\)", value.strip())
    return tokens[m.group(1)] if m else value.strip()


def _decls(text: str, selector_regex: str) -> dict:
    m = re.search(selector_regex + r"\s*\{([^}]*)\}", text)
    assert m, selector_regex
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1))}


@pytest.mark.parametrize("rel", UI_FILES)
def test_scoped_sets_point_at_tokens_with_their_shipped_values(brand, rel):
    text = _read(rel)
    t = brand.TOKENS
    news = _decls(text, r"\.news-ws")
    shipped = {"--c-tech": "#00d4ff", "--c-politics": "#60a5fa", "--c-local": "#d6c7a1",
               "--c-business": "#a78bfa", "--c-science": "#2dd4bf", "--c-media": "#f472b6"}
    assert {k: _resolve(news[k], t) for k in shipped} == shipped
    assert all(news[k].startswith("var(--fr-") for k in shipped)


def test_settings_scope_reads_tokens_and_danger_is_error_red(brand):
    text = _read("index.html")
    st = _decls(text, r"\.st-root")
    t = brand.TOKENS
    assert _resolve(st["--st-accent"], t) == "#00d4ff"
    assert _resolve(st["--st-warn"], t) == "#f59e0b"
    assert _resolve(st["--st-ok"], t) == "#00ff80"
    assert _resolve(st["--st-danger"], t) == "#ef4444"
    assert st["--st-danger"] == "var(--fr-error)"
    assert "'Inter'" in _resolve(st["--st-font"], t)


@pytest.mark.parametrize("rel", UI_FILES)
def test_status_dot_is_defined_once_and_covers_every_status(rel):
    text = _read(rel)
    bases = re.findall(r"(?m)^\s*\.status-dot\s*\{([^}]*)\}", text)
    assert len(bases) == 1, "one base .status-dot rule"
    assert "width: 8px" in bases[0].replace("width:8px", "width: 8px")
    for name in ("connected", "degraded", "connecting", "error", "disconnected", "needs_setup",
                 "blocked_by_policy", "unknown", "green", "yellow", "red"):
        assert re.search(r"\.status-dot\.%s\b" % name, text), name
    rules = "\n".join(re.findall(r"(?m)^\s*\.status-dot[^\n]*$", text))
    for legacy in ("#00ff66", "#ff0033", "#ffcc00", "#666"):
        assert legacy not in rules, legacy


@pytest.mark.parametrize("rel", UI_FILES)
def test_body_font_is_inter_not_helvetica(rel):
    text = _read(rel)
    m = re.search(r"(?m)^\s*body\s*\{([^}]*)\}", text)
    assert m and "font-family: var(--fr-font-body)" in m.group(1)
    assert "Helvetica" not in m.group(1)


def test_login_page_is_inter_and_orbitron_falls_back_to_sans_serif():
    from agent_friday.core import LOGIN_HTML
    assert "Orbitron',monospace" not in LOGIN_HTML and "Orbitron\",monospace" not in LOGIN_HTML
    body = re.search(r"body\{[^}]*\}", LOGIN_HTML).group(0)
    assert "font-family:var(--fr-font-body)" in body
    assert re.search(r"h1\{[^}]*var\(--fr-font-display\)", LOGIN_HTML)


def test_no_orbitron_stack_falls_back_to_monospace_anywhere_in_the_ui_sources():
    for rel in ("index.html", "ui_parts/head.html", "ui_parts/app.html",
                "ui_parts/styles_and_scene.html", "static/live/friday_live.html",
                "src/agent_friday/routes/creations.py",
                "src/agent_friday/services/misc_engine.py"):
        text = _read(rel)
        bad = re.findall(r"Orbitron\\?['\"]?\s*,\s*(?:\\?'Courier New\\?'\s*,\s*)?monospace", text)
        assert not bad, (rel, bad[:3])


# -- the document ----------------------------------------------------------------------
def test_brand_md_documents_every_token_and_the_rules(brand):
    doc = _read("docs/brand/BRAND.md")
    for name in brand.TOKENS:
        assert f"`{name}`" in doc, f"{name} is not documented"
    assert "positrons" in doc.lower() and "negatrons" in doc.lower()
    for phrase in ("Proof of Integrity", "Asimov's cLaws",
                   "Asimov's Mind", "FutureSpeak.AI", "workspace_registry.js", "## The name",
                   "Agent Friday™", "brand.spoken", "## Consolidations",
                   "## Known debt", "## Audio identity", "not yet made"):
        assert phrase in doc, phrase
    assert re.search(r"failure is error red", doc, re.I)
    assert re.search(r"refusal is deny magenta", doc, re.I)
    assert re.search(r"amber only means .needs you.", doc, re.I)
    assert re.search(r"violet .{0,4}working", doc, re.I)


def test_brand_md_names_no_real_journalist():
    doc = _read("docs/brand/BRAND.md")
    for name in ("Jennings", "Maddow", "Cronkite", "Murrow"):
        assert name not in doc


def test_consolidations_are_recorded_as_decisions():
    doc = _read("docs/brand/BRAND.md")
    ids = re.findall(r"\|\s*(B\d+)\s*\|", doc)
    assert {"B7", "B8", "B9"} <= set(ids)
    for before in ("#00ff66", "#ff0033", "#ff6b8a"):
        assert before in doc


# -- the mark ------------------------------------------------------------------------------
def _png_color_type(path: Path) -> int:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">B", data[25:26])[0]


def test_mark_png_has_an_alpha_channel_and_transparent_corners():
    from PIL import Image
    p = ROOT / "assets" / "icons" / "futurespeak.png"
    assert _png_color_type(p) == 6
    im = Image.open(p).convert("RGBA")
    assert im.getpixel((0, 0))[3] == 0 and im.getpixel((im.width - 1, im.height - 1))[3] == 0


def test_installer_icon_exists_with_alpha_and_the_usual_sizes():
    from PIL import Image
    p = ROOT / "assets" / "friday.ico"
    assert p.is_file()
    im = Image.open(p)
    sizes = im.info.get("sizes")
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(sizes)
    assert im.convert("RGBA").getpixel((0, 0))[3] == 0
    for script in ("install.ps1", "autostart.ps1"):
        assert "assets\\friday.ico" in _read(f"packaging/windows/{script}")
