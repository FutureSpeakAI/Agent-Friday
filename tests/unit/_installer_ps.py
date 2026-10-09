"""Helpers for tests that run the installer's PowerShell for real.

Shared by test_installer_model_picker.py, test_installer_upgrade.py and
test_installer_inno.py. Nothing here touches the real profile: the scripts under
test are pointed at scratch folders, and `FRIDAY_HOME` / `USERPROFILE` are
redirected for the child process.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WINDOWS_DIR = REPO / "packaging" / "windows"
LIB = WINDOWS_DIR / "lib"
INSTALLER_DIR = WINDOWS_DIR / "installer"
ISS = INSTALLER_DIR / "AgentFriday.iss"
SHORTLIST = REPO / "src" / "agent_friday" / "resources" / "model_shortlist.json"
TIERS = REPO / "src" / "agent_friday" / "resources" / "bonsai2-tiers.json"

POWERSHELL = shutil.which("powershell.exe") if sys.platform == "win32" else None

needs_powershell = pytest.mark.skipif(
    POWERSHELL is None, reason="the installer is Windows PowerShell 5.1; this test runs it for real")


def run_ps(script: str, tmp_path: Path, *, env: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run ``script`` as a .ps1 file (UTF-8 with BOM, as Windows PowerShell 5.1 needs)."""
    path = tmp_path / "case.ps1"
    path.write_bytes(b"\xef\xbb\xbf" + script.encode("utf-8"))
    child_env = dict(os.environ)
    child_env.update(env or {})
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=child_env)


def ps_json(script: str, tmp_path: Path, **kw):
    """Run ``script``; its last stdout line must be JSON."""
    out = run_ps(script, tmp_path, **kw)
    assert out.returncode == 0, "PowerShell failed:\n%s\n%s" % (out.stdout[-2000:], out.stderr[-2000:])
    line = [ln for ln in out.stdout.splitlines() if ln.strip()][-1]
    return json.loads(line)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")
