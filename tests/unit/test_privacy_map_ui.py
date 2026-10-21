"""Privacy & Data > Privacy map, and the off-the-record notice (BD-15 slice P1).

The map is run under node so the suite runs it (tests/ui/privacy_map.test.cjs).
The off-the-record notice must say, on screen while off the record is on, that
connected services can still keep their own records (north star §15.8-3).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
node = shutil.which("node")


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_privacy_map_passes_under_node():
    cp = subprocess.run([node, "--test", str(REPO / "tests" / "ui" / "privacy_map.test.cjs")],
                        capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(REPO))
    assert cp.returncode == 0, (cp.stdout[-4000:] + cp.stderr[-2000:])


@pytest.mark.parametrize("source", ["index.html", "ui_parts/app.html"])
def test_the_privacy_map_sits_in_privacy_and_data(source):
    text = (REPO / source).read_text(encoding="utf-8")
    assert "function SettingsPrivacyMap(" in text
    body = text[text.index("function SettingsTabPrivacy("):]
    assert "SettingsPrivacyMap" in body[:6000], "the map is not rendered in Privacy & Data"


@pytest.mark.parametrize("source", ["index.html", "ui_parts/app.html"])
def test_off_the_record_says_connected_services_still_keep_records(source):
    text = (REPO / source).read_text(encoding="utf-8")
    m = re.search(r"const FRIDAY_OFF_RECORD_NOTICE = '([^']+)'", text)
    assert m, "no off-the-record notice"
    assert "can still keep their own records" in m.group(1)
    assert "keeps the minimum receipt" in m.group(1)
    # Shown on screen whenever storage is actually off (the same test the chat box
    # uses), beside its switch.
    assert re.search(r"fridayOffRecordFrom\(agentSettings\)\s*&&[^;]{0,400}FRIDAY_OFF_RECORD_NOTICE", text, re.S)
