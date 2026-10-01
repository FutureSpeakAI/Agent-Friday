#!/usr/bin/env python3
"""Static check: the brand has one source of truth and nobody keeps a copy.

What it enforces:

1. The `--fr-*` token block in `index.html` and `ui_parts/head.html`
   (delimited by `brand-tokens:begin` / `brand-tokens:end`) is byte-identical
   to `brand.css_root_block()`, and the product-name block
   (`brand-names:begin` / `brand-names:end`, `window.FRIDAY_BRAND`) to
   `brand.js_names_block()`. `--write` regenerates both in both files.
2. The Python modules that keep a colour table (connector states,
   notification priorities, push-to-talk states, account palette, Studio
   showcase) hold no raw brand or status hex; they import `agent_friday.brand`.
3. The reserved status hues are the shipped values (ok, warn, deny, error).
4. Orbitron never falls back to a monospace face in a UI source, and every
   Orbitron `font-family` names a fallback.
5. `.status-dot` has one base definition per UI file.
6. Decoration never carries a status hue: no `--c-*`, `--lane-*` or `--cat-*`
   custom property points at a status token or hex, `ACCOUNT_PALETTE` holds no
   status hue, and the off-brand values earlier surfaces spelled for
   themselves do not come back.

It does not migrate the literals scattered through `index.html`; those are the
tracked debt in `docs/brand/BRAND.md`.

Stdlib only. `brand.py` is loaded by path, so the application package is not
imported (importing it migrates files in the real home directory).
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

UI_FILES = ("index.html", "ui_parts/head.html")
#: Every source that can carry an Orbitron declaration.
FONT_SOURCES = UI_FILES + (
    "ui_parts/app.html", "ui_parts/styles_and_scene.html", "static/live/friday_live.html",
    "src/agent_friday/core/__init__.py", "src/agent_friday/routes/creations.py",
    "src/agent_friday/services/misc_engine.py",
)
#: Modules whose colour tables read the palette from brand.py.
TABLE_MODULES = (
    "src/agent_friday/notifications_engine.py",
    "src/agent_friday/services/connectors.py",
    "src/agent_friday/services/ptt_indicator.py",
    "src/agent_friday/services/google_accounts.py",
    "src/agent_friday/services/showcase_engine.py",
)
#: The reserved status hues. Decision B3: these do not move.
RESERVED_PINS = {"OK": "#00ff80", "WARN": "#f59e0b", "DENY": "#ff0080", "ERROR": "#ef4444"}

#: Status colours earlier code spelled for itself; a table module may not bring one back.
LEGACY_STATUS_HEXES = (
    "#00ff66", "#22c55e", "#3effa1",
    "#ffcc00", "#ffb347", "#e0a030", "#ffd23f", "#ff8a00", "#ff8c42",
    "#ff0033", "#ff6b8a", "#ff5470", "#ff3366", "#f87171", "#ff3c5a",
    "#64748b", "#94a3b8", "#888888",
)

#: Status tokens a decoration property may not reference.
STATUS_TOKENS = ("--fr-ok", "--fr-warn", "--fr-deny", "--fr-error")
#: Off-brand values (lower case) no source may carry.
OFF_BRAND = ("#7c3aed", "124,58,237", "#ff4466", "#ffae5b", "255,174,91", "#22c55e",
             "#e0e0ff", "#0a0a0f")
OFF_BRAND_SOURCES = UI_FILES + (
    "ui_parts/app.html", "ui_parts/styles_and_scene.html", "static/js/friday_push_to_transcribe.js",
    "src/agent_friday/core/__init__.py", "src/agent_friday/routes/creations.py",
    "src/agent_friday/services/misc_engine.py",
)
_DECORATION_PROP = re.compile(r"(--(?:c|lane|cat)-[\w-]+)\s*:\s*([^;}]+)")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def load_brand(root: Path):
    path = root / "src" / "agent_friday" / "brand.py"
    spec = importlib.util.spec_from_file_location("_brand_for_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def block_span(text: str, brand, begin_marker: str = "", end_marker: str = ""):
    """(start, end) of a marked generated block (the token block by default), or None."""
    begin = text.find(f"/* {begin_marker or brand.BEGIN_MARKER}")
    end_marker = f"/* {end_marker or brand.END_MARKER} */"
    end = text.find(end_marker)
    if begin < 0 or end < begin:
        return None
    return begin, end + len(end_marker)


def _generated(brand):
    """(what, begin marker, end marker, the text it must be) for each generated block."""
    return (("token", brand.BEGIN_MARKER, brand.END_MARKER, brand.css_root_block()),
            ("names", brand.NAMES_BEGIN_MARKER, brand.NAMES_END_MARKER, brand.js_names_block()))


def _token_problems(root: Path, brand) -> list:
    problems = []
    for rel in UI_FILES:
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: file is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for what, begin, end, want in _generated(brand):
            span = block_span(text, brand, begin, end)
            if span is None:
                problems.append(f"{rel}: no brand {what} block ({begin} ... {end})")
            elif text[span[0]:span[1]] != want:
                problems.append(f"{rel}: the {what} block differs from brand.py "
                                "(run scripts/check_brand_tokens.py --write)")
    for name, value in RESERVED_PINS.items():
        if getattr(brand, name).lower() != value:
            problems.append(f"brand.py: {name} is {getattr(brand, name)}; the shipped reserved value is {value}")
    return problems


def write_blocks(root: Path = REPO_ROOT) -> list:
    """Regenerate the token block in each UI file; returns the files changed."""
    brand = load_brand(root)
    changed = []
    for rel in UI_FILES:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new = text
        for _what, begin, end, want in _generated(brand):
            span = block_span(new, brand, begin, end)
            if span is not None:
                new = new[:span[0]] + want + new[span[1]:]
        if new != text:
            path.write_bytes(new.encode("utf-8"))
            changed.append(rel)
    return changed


def _python_problems(root: Path, brand) -> list:
    banned = {getattr(brand, n).lower() for n in dir(brand)
              if n.isupper() and isinstance(getattr(brand, n), str) and _HEX.match(getattr(brand, n))}
    banned |= set(LEGACY_STATUS_HEXES)
    problems = []
    for rel in TABLE_MODULES:
        path = root / rel
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"{rel}: cannot be parsed for the colour scan ({exc.__class__.__name__})")
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and _HEX.match(node.value) and node.value.lower() in banned):
                problems.append(f"{rel}:{node.lineno}: raw colour {node.value.lower()} - "
                                "import it from agent_friday.brand instead")
    return problems


_FONT_CTX = re.compile(r"font-?family\s*:?", re.I)


def _font_problems(root: Path) -> list:
    problems = []
    for rel in FONT_SOURCES:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"orbitron", text, re.I):
            line_start = text.rfind("\n", 0, m.start()) + 1
            before = text[max(line_start, m.start() - 60):m.start()]
            if not _FONT_CTX.search(before):
                continue
            window = text[m.start():m.start() + 90]
            cut = re.search(r"[;}\n]|,\s*[A-Za-z]+\s*:", window)
            decl = window[:cut.start()] if cut else window
            line = text.count("\n", 0, m.start()) + 1
            if "monospace" in decl:
                problems.append(f"{rel}:{line}: Orbitron falls back to monospace; use sans-serif")
            elif "sans-serif" not in decl and "var(" not in decl:
                problems.append(f"{rel}:{line}: Orbitron has no sans-serif fallback")
    return problems


def _status_dot_problems(root: Path) -> list:
    problems = []
    for rel in UI_FILES:
        path = root / rel
        if not path.is_file():
            continue
        n = len(re.findall(r"(?m)^\s*\.status-dot\s*\{", path.read_text(encoding="utf-8")))
        if n != 1:
            problems.append(f"{rel}: .status-dot has {n} base definitions; there must be exactly one")
    return problems


def _decoration_problems(root: Path, brand) -> list:
    status = {getattr(brand, n).lower() for n in ("OK", "WARN", "DENY", "ERROR")}
    problems = []
    for hue in brand.ACCOUNT_PALETTE:
        if hue.lower() in status:
            problems.append(f"brand.py: ACCOUNT_PALETTE holds the status hue {hue}; "
                            "decoration must not carry a status colour")
    for rel in OFF_BRAND_SOURCES:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        for value in OFF_BRAND:
            if value in low:
                problems.append(f"{rel}: off-brand value {value}; use a token from brand.py")
        if rel in UI_FILES or rel.startswith("ui_parts/"):
            for name, value in _DECORATION_PROP.findall(text):
                v = value.lower()
                if any(t in v for t in STATUS_TOKENS) or any(h in v for h in status):
                    problems.append(f"{rel}: {name} points at a status colour ({value.strip()}); "
                                    "decoration must not carry a status colour")
    return problems


_PTT_JS = "static/js/friday_push_to_transcribe.js"
#: State -> brand token the in-page push-to-talk card must show.
_PTT_CARD_STATES = {"recording": "CYAN", "arming": "NEUTRAL", "thinking": "VIOLET",
                    "error": "ERROR", "done": "OK"}


def _page_card_problems(root: Path, brand) -> list:
    path = root / _PTT_JS
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    block = re.search(r"var colour = \{(.*?)\}\[state\]", text, re.S)
    if not block:
        return [f"{_PTT_JS}: the state colour table is missing"]
    table = dict(re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", block.group(1)))
    problems = []
    for state, token in _PTT_CARD_STATES.items():
        want = getattr(brand, token).lower()
        got = table.get(state, "").lower()
        if got != want:
            problems.append(f"{_PTT_JS}: {state} is {got or 'missing'}; it reads {token} ({want}) "
                            "from brand.py so a live microphone never looks like a failure")
    meter = re.search(r"bar\.style\.cssText = '([^']*)'", text)
    if meter and brand.ERROR.lower() in meter.group(1).lower():
        problems.append(f"{_PTT_JS}: the level meter is painted with the error colour")
    return problems


def find_problems(root: Path = REPO_ROOT) -> list:
    root = Path(root)
    brand = load_brand(root)
    return (_token_problems(root, brand) + _python_problems(root, brand)
            + _font_problems(root) + _status_dot_problems(root)
            + _decoration_problems(root, brand) + _page_card_problems(root, brand))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--write" in argv:
        for rel in write_blocks():
            print(f"[brand] rewrote the token block in {rel}")
    problems = find_problems()
    for p in problems:
        print(f"[brand] {p}", file=sys.stderr)
    if problems:
        print(f"[brand] {len(problems)} problem(s). The palette lives in src/agent_friday/brand.py.",
              file=sys.stderr)
        return 1
    print("[brand] tokens agree; no private colour tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
