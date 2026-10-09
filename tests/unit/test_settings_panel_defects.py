"""Three Settings defects, each pinned so it cannot return.

1. The consent check in ``voice_setup_install`` is a readable two-line
   condition, not one line with a run of spaces where a break was lost.
2. The local-voice confirm card says "a fixed checksum" only for file and
   archive artifacts. A pip artifact is pinned by version, so its card says
   "a fixed version". The wording is a function in the UI, run here under node.
3. The sticky Settings heading is opaque (the brand's solid surface token),
   sits above the panel's content, and the scroll container leaves room for it
   so a scrolled-to row never lands underneath.

SETTINGS_TEST_ROOT points the whole file at another checkout; it exists so the
red side of each test can be shown against the tree before the fix.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(os.environ.get("SETTINGS_TEST_ROOT") or pathlib.Path(__file__).resolve().parents[2])
HTML = ("index.html", "ui_parts/app.html")


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _function_source(src: str, name: str) -> str:
    start = src.index("function %s(" % name)
    depth = 0
    i = src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("unbalanced function " + name)


# 1 ---------------------------------------------------------------------------

def test_voice_install_consent_check_has_no_lost_line_break():
    src = _text("src/agent_friday/routes/voice.py")
    body = _function_source_py(src, "voice_setup_install")
    for line in body.splitlines():
        stripped = line.strip()
        assert not re.search(r"\S {4,}\S", stripped), "run of spaces inside a line: " + stripped[:80]
    # The behaviour is unchanged: an artifact target without consent is refused.
    assert 'data.get("consent") is not True' in body
    assert '.get("artifact")' in body


def _function_source_py(src: str, name: str) -> str:
    m = re.search(r"^def %s\(.*?(?=^@|^def |\Z)" % name, src, re.S | re.M)
    assert m, name
    return m.group(0)


# 2 ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", HTML)
def test_pin_copy_is_a_function_that_never_promises_a_checksum_for_pip(rel):
    src = _text(rel)
    assert "function voiceArtifactPinCopy(" in src
    # The confirm card calls it; the old fixed sentence is gone.
    assert "voiceArtifactPinCopy(group)" in src
    assert "is checked against a fixed checksum before it is used; a file that does not match is deleted. If the download stops" not in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("rel", HTML)
def test_pin_copy_wording_by_kind(rel, tmp_path):
    fn = _function_source(_text(rel), "voiceArtifactPinCopy")
    js = tmp_path / "pin.js"
    js.write_text(fn + """
const out = {
  pip: voiceArtifactPinCopy([{kind: 'pip'}, {kind: 'pip'}]),
  file: voiceArtifactPinCopy([{kind: 'file'}]),
  archive: voiceArtifactPinCopy([{kind: 'archive'}, {kind: 'pip'}]),
};
console.log(JSON.stringify(out));
""", encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(js)], capture_output=True, text=True, check=True).stdout)
    assert "fixed version" in out["pip"] and "checksum" not in out["pip"]
    assert "fixed checksum" in out["file"] and "version" not in out["file"]
    assert "fixed checksum" in out["archive"] and "fixed version" in out["archive"]


def test_artifact_rows_carry_their_kind():
    src = _text("src/agent_friday/services/voice_artifacts.py")
    assert '"kind": a["kind"]' in _function_source_py(src, "public_rows")


# 3 ---------------------------------------------------------------------------

def _heading_block(src: str) -> str:
    i = src.index('className: "fx-settings-heading"')
    return src[i:i + 900]


@pytest.mark.parametrize("rel", HTML)
def test_sticky_heading_is_opaque_and_above_the_content(rel):
    block = _heading_block(_text(rel))
    assert "position: 'sticky'" in block
    assert "background: 'var(--fr-surface)'" in block, "the heading must use the solid surface token"
    assert "rgba(" not in block.split("borderBottom")[0]
    z = int(re.search(r"zIndex: (\d+)", block).group(1))
    assert z >= 20


@pytest.mark.parametrize("rel", HTML)
def test_scroll_container_reserves_the_headings_height(rel):
    src = _text(rel)
    assert "scrollPaddingTop: 'var(--st-head-h, 0px)'" in src
    assert "--st-head-h" in _function_source(src, "SettingsWS")
