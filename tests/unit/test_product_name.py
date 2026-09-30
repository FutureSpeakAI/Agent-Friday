"""The product is Agent Friday™ wherever the brand shows, and plain in audio.

  * brand.py holds the names; the page reads them from a block generated from
    it, which the brand guard keeps byte-identical.
  * brand.tm (fridayTM on the page) adds the mark to shown text: once, outside
    code, to the words and not to identifiers or addresses.
  * brand.spoken takes it off again at every synthesis entry, so she never
    says "T M".
  * No UI string names the product "Friday by FutureSpeak" or writes "Agent
    Friday" without the mark, and the wordmark is one component that reads the
    product's name, never her own.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_friday import brand

ROOT = Path(__file__).resolve().parents[2]
TM = "™"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


# ── the names ────────────────────────────────────────────────────────────────

def test_the_names_are_one_set_of_constants():
    assert brand.PRODUCT == "Agent Friday"
    assert brand.PRODUCT_NAME == "Agent Friday" + TM
    assert brand.PRODUCT_LOCKUP == "Agent Friday" + TM + " by FutureSpeak.AI"
    assert brand.MADE_WITH == "Made with Agent Friday" + TM


def test_the_page_reads_the_names_from_the_generated_block():
    spec = importlib.util.spec_from_file_location("_cbt", ROOT / "scripts" / "check_brand_tokens.py")
    cbt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cbt)
    problems = [p for p in cbt._token_problems(ROOT, cbt.load_brand(ROOT)) if "names block" in p]
    assert not problems, problems
    for rel in ("index.html", "ui_parts/head.html"):
        m = re.search(r"window\.FRIDAY_BRAND = (\{.*?\});", _read(rel))
        assert m and json.loads(m.group(1)) == brand.PAGE_NAMES, rel


# ── the mark in shown text ───────────────────────────────────────────────────

TM_CASES = [
    ("Hi, I'm Agent Friday.", "Hi, I'm Agent Friday" + TM + "."),
    ("Agent Friday" + TM + " is here", "Agent Friday" + TM + " is here"),
    ("Agent Friday ™", "Agent Friday ™"),
    ("Agent Friday®", "Agent Friday®"),
    ("Agent Friday (TM)", "Agent Friday (TM)"),
    ("AGENT FRIDAY", "AGENT FRIDAY" + TM),
    ("agent friday", "agent friday" + TM),
    ("Agent Friday's view", "Agent Friday" + TM + "'s view"),
    ("`Agent Friday` in code", "`Agent Friday` in code"),
    ("```\nAgent Friday\n```\nand Agent Friday", "```\nAgent Friday\n```\nand Agent Friday" + TM),
    ("agent_friday, agent-friday and https://futurespeak.ai/agent-friday-live",
     "agent_friday, agent-friday and https://futurespeak.ai/agent-friday-live"),
    ("Agent Fridays", "Agent Fridays"),
    ("Agent Friday and Agent Friday", "Agent Friday" + TM + " and Agent Friday" + TM),
    ("unfinished ```Agent Friday", "unfinished ```Agent Friday"),
    ("", ""),
]


@pytest.mark.parametrize("text,shown", TM_CASES)
def test_the_mark_is_added_once_outside_code(text, shown):
    assert brand.tm(text) == shown
    assert brand.tm(brand.tm(text)) == shown, "never twice"


def test_spoken_text_is_plain():
    assert brand.spoken("I'm Agent Friday" + TM + ".") == "I'm Agent Friday."
    assert brand.spoken("Agent Friday (TM) says hi") == "Agent Friday says hi"
    assert brand.spoken("Agent Friday ™") == "Agent Friday"
    for text, _shown in TM_CASES:
        assert TM not in brand.spoken(brand.tm(text)), text


def _page_filter(rel: str) -> str:
    s = _read(rel)
    a, b = s.index("/* fridayTM:begin */"), s.index("/* fridayTM:end */")
    return s[a:b]


def test_the_page_filter_is_one_block_in_both_ui_sources():
    assert _page_filter("index.html") == _page_filter("ui_parts/app.html")


def test_the_page_filter_agrees_with_brand_tm():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = _page_filter("index.html") + (
        "\nconst cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "\nprocess.stdout.write(JSON.stringify(cases.map(fridayTM)));\n")
    out = subprocess.run([node, "-e", script], input=json.dumps([t for t, _s in TM_CASES]),
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [s for _t, s in TM_CASES]


def test_her_words_and_notices_are_shown_through_the_filter():
    ix, app = _read("index.html"), _read("ui_parts/app.html")
    # her replies, typed and spoken, in the chat surface and the workspace chat
    assert "renderFridayMarkdown(fridayTM(m.text || ''), {" in ix
    assert "m.role === 'friday' ? fridayTM(m.text) : m.text" in ix
    assert "text: fridayTM(m.text)," in ix
    assert "renderFridayMarkdown(fridayTM(m.text||''),{compact:true})" in app
    assert "m.role==='friday'?fridayTM(m.text):m.text" in app
    # toasts, the notification list, task results
    assert "var text = fridayTM(msg == null ? '' : String(msg));" in ix
    assert "fridayTM(n.title || n.message || n.text || 'Notification')" in ix
    assert "text: fridayTM(n.detail || n.body || n.message)," in ix
    assert "text: fridayTM(taskResultModal.result)" in ix
    assert "{fridayTM(n.title||n.message||n.text||'Notification')}" in app


# ── audio ────────────────────────────────────────────────────────────────────

def _entries():
    from agent_friday.phone import live_call
    from agent_friday.services import (cloud_voice, elevenlabs_tools, local_voice, podcast_engine,
                                       voice_engine, voice_session)
    return [voice_engine._synthesize_tts_wav, voice_engine._synthesize_tts_wav_gemini,
            voice_engine._synthesize_tts_wav_local, voice_session.VoiceSession._synth,
            local_voice.LocalVoiceEngine.synthesize, cloud_voice.synthesize,
            local_voice.PiperTTS.synthesize, elevenlabs_tools._tool_speak_text,
            podcast_engine._clean_text, live_call.CallBridge.say]


@pytest.mark.parametrize("fn", _entries(), ids=lambda f: f.__qualname__)
def test_every_synthesis_entry_speaks_the_plain_name(fn):
    assert "brand.spoken(" in inspect.getsource(fn), fn.__qualname__


def test_a_voice_session_clause_reaches_the_engine_plain():
    from agent_friday.services import voice_session

    class Engine:
        heard = []

        def synthesize_stream(self, clause, cancel):
            self.heard.append(clause)
            return [b"pcm"]

    class Session:
        gpu_queue = None
        _current_turn = None

    voice_session.VoiceSession._synth(Session(), Engine(), "I'm Agent Friday" + TM + ".", None)
    assert Engine.heard == ["I'm Agent Friday."]


def test_the_local_voice_reaches_its_engine_plain():
    from agent_friday.services import local_voice
    heard = []

    class Tts:
        def synthesize(self, text):
            heard.append(text)
            return b"pcm"

    class Voice:
        last_error_code = ""

        def _get_tts(self):
            return Tts()

        def _record(self, *_a):
            pass

    local_voice.LocalVoiceEngine.synthesize(Voice(), "Agent Friday" + TM + " here")
    assert heard == ["Agent Friday here"]


def test_a_podcast_line_is_plain():
    from agent_friday.services import podcast_engine
    assert TM not in podcast_engine._clean_text("This is Agent Friday" + TM + ".")


# ── the guard ────────────────────────────────────────────────────────────────

#: Page sources, and the Python modules that write a brand surface.
UI_SOURCES = ("index.html", "ui_parts/app.html", "ui_parts/head.html", "ui_parts/styles_and_scene.html")
PY_SURFACES = ("src/agent_friday/friday_tray.py", "src/agent_friday/routes/creations.py",
               "src/agent_friday/services/showcase_engine.py",
               "src/agent_friday/notifications_engine.py",
               # the sign-in page, the draft exports and the research page
               "src/agent_friday/core/__init__.py", "src/agent_friday/services/misc_engine.py",
               "src/agent_friday/routes/workflows.py", "src/agent_friday/services/research/deliver.py")
_NAME = re.compile(r"(?<![\w-])agent[ \t]+friday(?![\w-])", re.I)
_MARKED = re.compile(r"[ \t]*(?:™|\\u2122|&trade;|&#8482;|\\u00ae|®)")
#: The product beside its maker, with any joint: "Friday by FutureSpeak",
#: "FRIDAY · FutureSpeak.AI". The lockup is "Agent Friday™ by FutureSpeak.AI".
_BY = re.compile(r"(?<![\w-])friday[ \t]*(?:by|·|\\u00b7|&middot;|—|\\u2014|–|\||-)[ \t]*futurespeak", re.I)
#: A credit line that names the product without its first word: "Generated by FRIDAY".
_CREDIT = re.compile(r"(?:generated|built|made|powered|created)[ \t]+(?:by|with)[ \t]+friday(?![\w-])", re.I)
#: The product's old name. "the Friday desktop", the place, is not it.
_OLD = re.compile(r"(?<![\w-])(?:Friday|FRIDAY)[ \t]+(?:Desktop|DESKTOP)(?![\w-])")
#: A page title or heading that names Friday names the product: "FRIDAY LIVE"
#: is "Live · Agent Friday™".
_HEAD = re.compile(r"<(title|h1)>([^<]*)</\1>", re.I)
_WORD = re.compile(r"(?<![\w-])friday(?![\w-])", re.I)
_FULL = re.compile(r"agent[ \t]+friday[ \t]*(?:™|\\u2122|&trade;|&#8482;)", re.I)


def _blank(m) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


def _uncommented(text: str) -> str:
    """`text` with comments blanked (lines kept), and the generated names block,
    which is where the plain name for audio lives."""
    text = re.sub(r"/\* brand-names:begin[\s\S]*?brand-names:end \*/", _blank, text)
    text = re.sub(r"/\*[\s\S]*?\*/|<!--[\s\S]*?-->", _blank, text)
    return re.sub(r"(?<![:'\"\\\w])//[^\n]*", _blank, text)


def ui_string_problems(name: str, text: str, raw: str | None = None) -> list[str]:
    raw_lines = (raw if raw is not None else text).split("\n")
    out = []
    for i, line in enumerate(text.split("\n")):
        if "brand: plain" in raw_lines[i]:
            continue
        if _BY.search(line):
            out.append(f"{name}:{i + 1}: names the product 'Friday by FutureSpeak'")
        if _CREDIT.search(line):
            out.append(f"{name}:{i + 1}: credits the product as 'Friday'")
        if _OLD.search(line):
            out.append(f"{name}:{i + 1}: names the product by its old name, 'Friday Desktop'")
        for m in _HEAD.finditer(line):
            if _WORD.search(m.group(2)) and not _FULL.search(m.group(2)):
                out.append(f"{name}:{i + 1}: a <{m.group(1)}> names Friday without 'Agent Friday™'")
        for m in _NAME.finditer(line):
            if not _MARKED.match(line, m.end()):
                out.append(f"{name}:{i + 1}: 'Agent Friday' without the mark")
    return out


def _page_problems(rel: str) -> list[str]:
    raw = _read(rel)
    return ui_string_problems(rel, _uncommented(raw), raw)


def _python_problems(rel: str) -> list[str]:
    raw = _read(rel)
    tree = ast.parse(raw)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(id(body[0].value))
    lines = raw.split("\n")
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            where = lines[node.lineno - 1]
            if "brand: plain" in where:
                continue
            out += ui_string_problems(f"{rel}:{node.lineno}", node.value)
    return out


def test_the_guard_catches_what_it_is_for():
    bad = ["title: 'News \\u00B7 Agent Friday'", "<h1>Agent Friday</h1>", "'Friday by FutureSpeak.AI'",
           "const n = 'AGENT FRIDAY';", "'Open Friday Desktop'", "<title>FRIDAY Desktop</title>",
           "'Generated by FRIDAY · FutureSpeak.AI'", "'FRIDAY \\u00b7 FutureSpeak.AI'",
           "<title>FRIDAY LIVE</title>", "<h1>FRIDAY</h1>", "<title>FRIDAY — Authenticate</title>",
           "'Made with Friday'"]
    good = ["title: 'Agent Friday\\u2122'", "'Agent Friday™'", "'Agent Friday &trade;'",
            "// Agent Friday in a comment", "/* Agent Friday */", "href='/agent-friday-live'",
            "'agent_friday.core'", "'Install Agent Friday.cmd' /* brand: plain (a file name) */",
            "'Back to the Friday desktop'", "<title>Live · Agent Friday™</title>",
            "<h1>AGENT FRIDAY™ · LIVE</h1>", "<title>{title} · {brand.PRODUCT_NAME}</title>",
            "'Agent Friday™ by FutureSpeak.AI'", "'Made with Agent Friday™'", "<h1>Your day</h1>"]
    for s in bad:
        assert _page_problems_of(s), s
    for s in good:
        assert not _page_problems_of(s), s


def _page_problems_of(snippet: str) -> list[str]:
    return ui_string_problems("<t>", _uncommented(snippet), snippet)


@pytest.mark.parametrize("rel", UI_SOURCES)
def test_no_page_source_names_the_product_without_its_mark(rel):
    assert not _page_problems(rel), "\n".join(_page_problems(rel))


def test_no_static_page_or_script_names_the_product_without_its_mark():
    problems = []
    for p in sorted((ROOT / "static").rglob("*")):
        if p.suffix in (".js", ".html", ".json") and "vendor" not in p.parts:
            problems += _page_problems(p.relative_to(ROOT).as_posix())
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("rel", PY_SURFACES)
def test_no_python_brand_surface_names_the_product_without_its_mark(rel):
    assert not _python_problems(rel), "\n".join(_python_problems(rel))


def test_the_wordmark_is_one_component_and_reads_the_product_name():
    for rel in ("index.html", "ui_parts/app.html"):
        s = _read(rel)
        assert s.count("function FridayLockup(") == 1, rel
        assert s.count("futurespeak.ai/agent-friday-live") == 1, (
            rel + ": the FutureSpeak.AI link belongs to FridayLockup alone")
        body = s[s.index("function FridayLockup("):]
        body = body[:body.index("\n}\n")]
        assert "FRIDAY_BRAND" in body and "agent_name" not in body, rel
        assert "agent_name||'AGENT FRIDAY'" not in s.replace(" ", ""), (
            rel + ": the top bar printed her own name as the wordmark")
