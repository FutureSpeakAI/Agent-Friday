"""The installer's OfficeCLI pin and the record it writes are what
office_engine checks before running the binary. The two must agree on
the record's shape, and the pin must be one coherent release."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "packaging" / "windows" / "lib" / "OfficeCli.ps1"


def _pin() -> dict:
    text = LIB.read_text(encoding="utf-8")
    out = {}
    for key in ("Version", "Asset", "Url", "Size", "Sha256"):
        m = re.search(r"\$script:OfficeCli%s\s*=\s*'?([^'\r\n]+)'?" % key, text)
        assert m, key
        out[key] = m.group(1).strip()
    return out


def test_the_pin_is_one_release():
    p = _pin()
    assert re.fullmatch(r"[0-9a-f]{64}", p["Sha256"]), "lower-case hex sha256"
    assert int(p["Size"]) > 1_000_000
    assert p["Url"] == ("https://github.com/iOfficeAI/OfficeCLI/releases/download/%s/%s"
                        % (p["Version"], p["Asset"]))


def test_the_docs_name_the_pinned_version():
    version = _pin()["Version"].lstrip("v")
    docs = (ROOT / "docs" / "user-guide" / "documents.md").read_text(encoding="utf-8")
    assert version in docs


def test_office_engine_accepts_a_record_in_the_installers_shape(tmp_path, monkeypatch):
    from agent_friday.services import office_engine as oe
    exe = tmp_path / "officecli.exe"
    exe.write_bytes(b"stand-in binary")
    record = {"tool": "officecli", "pinned_version": _pin()["Version"],
              "sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}
    (tmp_path / "INSTALL.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(oe, "BINARY", exe)
    monkeypatch.setattr(oe, "INSTALL_RECORD", tmp_path / "INSTALL.json")
    ok, why = oe.verify_binary(force=True)
    assert ok, why
    exe.write_bytes(b"something else")
    ok, why = oe.verify_binary(force=True)
    assert not ok and "does not match" in why
