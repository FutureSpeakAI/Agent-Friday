"""Setup never runs a file it could not check against a pin.

Before this change sources.json left get-pip.py and the Ollama installer unpinned
(moving URLs), Assert-FileHash only warned when a pin was empty, Test-FileHash answered
yes to an empty pin, build-installer.ps1 bundled get-pip.py unchecked, and install.ps1 never
passed a pin to Install-Ollama at all.

Now both are pinned to immutable URLs (a pypa/get-pip commit; a GitHub release asset)
with SHA-256 and size, an empty pin refuses exactly like a wrong file, and every caller
asserts. These tests read the files; nothing is downloaded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WIN = REPO / "packaging" / "windows"


def _src(rel: str) -> str:
    return (WIN / rel).read_text(encoding="utf-8-sig")


def _fn(text: str, name: str) -> str:
    start = text.index("function %s" % name)
    nxt = text.find("\nfunction ", start + 10)
    return text[start: nxt if nxt != -1 else len(text)]


def test_every_artefact_setup_runs_is_pinned_to_an_immutable_url():
    s = json.loads(_src("sources.json"))
    for name in ("python", "get_pip", "ollama"):
        e = s[name]
        assert re.fullmatch(r"[0-9A-F]{64}", e.get("sha256", "")), name
        assert int(e.get("size_bytes") or 0) > 0, name
        assert e["url"].startswith("https://"), name
    assert re.search(r"/pypa/get-pip/[0-9a-f]{40}/", s["get_pip"]["url"])
    assert re.search(r"/ollama/ollama/releases/download/v[0-9.]+/OllamaSetup\.exe$", s["ollama"]["url"])
    assert "bootstrap.pypa.io/get-pip.py" not in s["get_pip"]["url"]
    assert "ollama.com/download" not in s["ollama"]["url"]


def test_an_empty_pin_refuses_like_a_wrong_file():
    d = _src("lib/Download.ps1")
    t = _fn(d, "Test-FileHash")
    assert "if (-not $ExpectedSha256) { return $false }" in t
    a = _fn(d, "Assert-FileHash")
    assert "Add-InstallWarning" not in a
    assert "if (-not $ExpectedSha256 -or $actual -ne" in a
    assert "exit 1" in a


def test_every_caller_asserts():
    oll = _fn(_src("lib/Ollama.ps1"), "Install-Ollama")
    assert "if ($ExpectedSha256) { Assert-FileHash" not in oll
    assert "Assert-FileHash -Path $setup -ExpectedSha256 $ExpectedSha256" in oll
    assert "ollama.com/download/OllamaSetup.exe'" not in oll      # no unpinned default URL
    pip = _fn(_src("lib/Python.ps1"), "Install-Pip")
    assert "if ($ExpectedSha256)" not in pip
    assert "Assert-FileHash -Path $GetPipPath" in pip
    inst = _src("install.ps1")
    assert "-ExpectedSha256 $script:Sources.ollama.sha256" in inst
    build = _src("build-installer.ps1")
    assert build.count("-ExpectedSha256 $sources.get_pip.sha256") == 2


def test_a_cached_get_pip_that_is_not_the_pin_is_discarded():
    inst = _src("install.ps1")
    i_cache = inst.index("A cached get-pip.py does not match the pin; discarding it.")
    i_fetch = inst.index("Get-RemoteFile -Uri $script:Sources.get_pip.url")
    assert i_cache < i_fetch


def test_a_cached_ollama_installer_that_is_not_the_pin_is_discarded():
    oll = _fn(_src("lib/Ollama.ps1"), "Install-Ollama")
    i_cache = oll.index("does not match the pin; discarding it")
    i_fetch = oll.index("Get-RemoteFile -Uri $InstallerUrl")
    assert i_cache < i_fetch
