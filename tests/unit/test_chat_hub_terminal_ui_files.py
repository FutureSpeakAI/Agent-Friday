"""The Build panel's Terminal (Chat Hub M3b), pinned in the files that ship it:
a Terminal tab lists the codebase's runs, newest first, hears a new run on the
bus, and asks Friday to run a command through the chat, so every command goes
through the one gate (no second channel to a process).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PANEL_JS = ROOT / "static" / "friday_artifacts.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_panel_has_a_terminal_tab_that_reads_the_runs():
    js = _read(PANEL_JS)
    panel = js[js.index("function CodebasePanel("):js.index("function FridayArtifactHost(")]
    assert "tabBtn('terminal'" in panel
    assert "'/runs'" in panel, "the Terminal does not read the codebase's runs"
    assert "data-codebase-runs" in panel and "data-codebase-run" in panel


def test_a_new_run_reaches_the_panel_on_the_bus():
    js = _read(PANEL_JS)
    bus = js[js.index("new EventSource("):][:1500]
    assert "'codebase_run'" in bus, "the bus does not forward codebase_run"


def test_the_terminal_asks_friday_in_chat_never_a_process_directly():
    js = _read(PANEL_JS)
    panel = js[js.index("function CodebasePanel("):js.index("function FridayArtifactHost(")]
    ask = panel[panel.index("const askRun"):panel.index("data-codebase-runs")]
    assert "/api/chat/send" in ask, "a typed command goes through the chat"
    assert not re.search(r"/api/codebases/[^']*/(run|exec|shell)'", panel), "no route runs a command from the page"
