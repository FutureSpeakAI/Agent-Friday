"""The Settings panel 'What may interrupt you' is in both copies of the app and says it in plain words."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UIS = ("index.html", "ui_parts/app.html")
NODE = shutil.which("node")


def _flat(ui: str) -> str:
    return re.sub(r"\s+", "", (ROOT / ui).read_text(encoding="utf-8"))


@pytest.mark.parametrize("ui", UIS)
def test_the_panel_is_defined_and_shown_next_to_the_muted_notifications(ui):
    flat = _flat(ui)
    assert "functionSettingsNotificationInterrupts()" in flat, ui
    assert "createElement(SettingsNotificationInterrupts" in flat, ui
    assert flat.index("createElement(SettingsNotificationInterrupts") < flat.index("createElement(SettingsNotificationMutes"), ui


@pytest.mark.parametrize("ui", UIS)
def test_the_panel_reads_and_writes_the_rules_through_the_routes(ui):
    flat = _flat(ui)
    assert "apiFetch('/api/notifications/interrupt-rules')" in flat, ui
    assert "apiFetch('/api/notifications/interrupt-rules',{method:'POST'" in flat, ui
    assert "JSON.stringify({id:r.id,on:next})" in flat, ui
    assert "constnext=!r.on;" in flat, ui


@pytest.mark.parametrize("ui", UIS)
def test_the_panel_says_what_it_does_in_plain_words(ui):
    flat = _flat(ui)
    for words in ("Whatmayinterruptyou", "Failures,approvals,statusandroutingnoticesandnewskeeptheirownrules"):
        assert words in flat, (ui, words)


def test_both_copies_carry_the_same_panel():
    def panel(ui):
        flat = _flat(ui)
        start = flat.index("functionSettingsNotificationInterrupts()")
        return flat[start:flat.index("functionSettingsTabBrowser()", start)]
    assert panel("index.html") == panel("ui_parts/app.html")


@pytest.mark.skipif(not NODE, reason="node is not on PATH")
def test_the_panel_passes_its_behaviour_test_under_node():
    """tests/ui/notification_interrupt_rules.test.cjs renders the panel from both copies and clicks a rule;
    it runs here so the suite runs it."""
    done = subprocess.run([NODE, "--test", str(ROOT / "tests" / "ui" / "notification_interrupt_rules.test.cjs")],
                          capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
