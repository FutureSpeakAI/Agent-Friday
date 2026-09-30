"""Reply text never becomes script in Friday's own origin.

renderFridayMarkdown output is assigned to innerHTML in several components, so
raw HTML in a model reply, a fetched page or an imported note must be reduced
to an allowlist first, and a reply that is a whole HTML document must be shown
in an opaque-origin frame. This runs the real functions from index.html under
node, feeding them the payloads a hostile reply would carry; the mirror must
carry identical code.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
node = shutil.which("node")
NL = chr(10)
PAGES = ("index.html", "ui_parts/app.html")


def _slice(path, start, end):
    text = (ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n")
    a = text.index(start)
    return text[a:text.index(end, a)]


def _code(path):
    return _slice(path, "/* MARKUP ALLOWLIST.", "function renderFridayMarkdown(")


def test_mirror_carries_the_same_sanitizer():
    assert _code("index.html") == _code("ui_parts/app.html")


@pytest.mark.parametrize("path", PAGES)
def test_renderer_sanitizes_and_frames_documents(path):
    body = _slice(path, "function renderFridayMarkdown(", NL + "if")
    assert "fridaySanitizeMarkup(body)" in body
    assert "fridayHtmlDocumentSource(src)" in body
    assert not re.search(r"\.test\(src\)\)\s*return\s+src;", body)


PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    "<script>fetch('/api/run_command')</script>",
    "<svg onload=alert(1)>",
    "<a href=\"javascript:alert(1)\">x</a>",
    "<a href=\"jav&#x09;ascript:alert(1)\">x</a>",
    "<a href=' javascript:alert(1)'>x</a>",
    "<iframe src=\"//evil.example\"></iframe>",
    "<img src=x onerror=alert(1)",
    "<div style=\"background:url(javascript:alert(1))\">x</div>",
    "<details open ontoggle=alert(1)>x</details>",
    "<a href=\"data:text/html,<script>alert(1)</script>\">x</a>",
    "<form action=/api/x><button>go</button></form>",
]

HARNESS = r"""
CODE
const payloads = PAYLOADS;
const out = payloads.map(p => fridaySanitizeMarkup(p));
const keep = fridaySanitizeMarkup('<span class="friday-cite" title="t" data-kw-page="P" style="color:#0ff;cursor:pointer;">chip</span><a href="https://example.com/a" target="_blank" rel="noopener">l</a><p>hi</p>');
console.log(JSON.stringify({out, keep}));
"""


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_hostile_markup_is_reduced_to_inert_text(tmp_path):
    script = tmp_path / "h.js"
    script.write_text(HARNESS.replace("CODE", _code("index.html")).replace(
        "PAYLOADS", json.dumps(PAYLOADS)), encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    res = json.loads(cp.stdout.strip().splitlines()[-1])
    for payload, cleaned in zip(PAYLOADS, res["out"]):
        low = cleaned.lower()
        assert not re.search(r"<(script|svg|iframe|form|button)", low), (payload, cleaned)
        assert not re.search(r"<[^>]*\son\w+\s*=", low), (payload, cleaned)
        assert not re.search(r"(href|src)=\"\s*(javascript|data:text)", low), (payload, cleaned)
        assert "url(" not in low, (payload, cleaned)
    keep = res["keep"]
    assert 'class="friday-cite"' in keep and 'data-kw-page="P"' in keep
    assert '<a href="https://example.com/a" target="_blank" rel="noopener">' in keep
    assert "<p>hi</p>" in keep


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_a_html_document_reply_is_shown_as_source(tmp_path):
    code = _code("index.html")
    rd = _slice("index.html", "function renderFridayMarkdown(", NL + "if")
    script = tmp_path / "h.js"
    script.write_text(
        "function fridayCitationize(s){return s}\nfunction fridayFallbackMd(s){return s}\n"
        + code + rd + "\n"
        "console.log(JSON.stringify(renderFridayMarkdown('<!DOCTYPE html><script>parent.x()</script>')));\n"
        "console.log(JSON.stringify(renderFridayMarkdown('hello <img src=x onerror=alert(1)>')));\n",
        encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    doc, md = [json.loads(x) for x in cp.stdout.strip().splitlines()[-2:]]
    assert "<script" not in doc and "<iframe" not in doc
    assert "&lt;script&gt;parent.x()" in doc and "<pre" in doc
    assert "<img" not in md or "onerror" not in md
