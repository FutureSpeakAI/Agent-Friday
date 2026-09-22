"""The two UI files must actually parse.

This repo has the failure on record: a silent Babel parse error renders the
Liquid UI as a cube and an empty console — nothing in the server logs, nothing
in the terminal, just a blank app. `index.html` is ~1.5 MB of precompiled
React hand-edited in place, and `ui_parts/app.html` is its JSX twin kept in
sync by hand, so "I did not break it" has been an assumption rather than a
check.

Both halves are checked the way they are actually consumed:

  * index.html by `node --check` on every INLINE <script> block, found by
    scanning for them. An earlier version of this check used a hard-coded line
    offset and silently stopped checking the right block the moment the file
    grew — it was reporting OK on a slice that was no longer the app.
  * ui_parts/app.html by compiling its JSX with the same Babel the app
    vendors. Nothing serves this file today, so a break in it is invisible
    until someone builds — which is exactly the kind of quiet trap this suite
    exists to remove.

Skipped, not failed, where node or Babel is unavailable: a missing toolchain
is not a broken UI, and a test that fails for the wrong reason gets ignored.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
APP = ROOT / "ui_parts" / "app.html"
BABEL = ROOT / "node_modules" / "@babel" / "standalone"

node = shutil.which("node")
needs_node = pytest.mark.skipif(not node, reason="node is not on PATH")


def _inline_scripts(text):
    """Every inline <script> body, with its 1-based start line."""
    out = []
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", text, flags=re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        if "src=" in attrs:
            continue
        if len(body.strip()) < 40:
            continue
        out.append((text[:m.start()].count("\n") + 1, body))
    return out


@needs_node
def test_every_inline_script_in_index_html_parses(tmp_path):
    blocks = _inline_scripts(INDEX.read_text(encoding="utf-8", errors="replace"))
    assert len(blocks) >= 3, (
        f"only {len(blocks)} inline script block(s) found — the scanner is "
        f"broken, and a broken scanner reports OK on nothing")
    biggest = max(len(b) for _, b in blocks)
    assert biggest > 100_000, (
        "the largest block is too small to be the app — the scanner is "
        "matching the wrong thing")
    for line, body in blocks:
        f = tmp_path / f"blk_{line}.js"
        f.write_text(body, encoding="utf-8")
        r = subprocess.run([node, "--check", str(f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"index.html: the inline script at line {line} does not parse — "
            f"this is what renders the app as a blank page.\n{r.stderr[:800]}")


@needs_node
@pytest.mark.skipif(not BABEL.exists(),
                    reason="@babel/standalone is not installed")
def test_app_html_jsx_compiles(tmp_path):
    """Nothing serves app.html today, so a break here is silent until a build."""
    src = APP.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<script[^>]*>(.*)</script>", src, flags=re.S)
    if m:
        src = m.group(1)
    src_file = tmp_path / "app.jsx"
    src_file.write_text(src, encoding="utf-8")
    runner = tmp_path / "run.js"
    runner.write_text(
        "const fs=require('fs');\n"
        f"const babel=require({json.dumps(str(BABEL))});\n"
        f"const s=fs.readFileSync({json.dumps(str(src_file))},'utf8');\n"
        "try{babel.transform(s,{presets:['react'],filename:'app.html'});"
        "console.log('OK')}"
        "catch(e){console.error(String(e.message).slice(0,900));process.exit(1)}",
        encoding="utf-8")
    r = subprocess.run([node, str(runner)], capture_output=True, text=True)
    assert r.returncode == 0, (
        f"ui_parts/app.html does not compile as JSX:\n{r.stderr[:900]}")


@needs_node
def test_the_checker_can_fail(tmp_path):
    """A syntax checker nobody has seen reject anything is a checker nobody
    should trust."""
    bad = tmp_path / "bad.js"
    bad.write_text("const x = (1 +;", encoding="utf-8")
    r = subprocess.run([node, "--check", str(bad)], capture_output=True, text=True)
    assert r.returncode != 0, "node --check accepted a syntax error"
